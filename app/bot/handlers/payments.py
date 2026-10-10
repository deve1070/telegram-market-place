"""Payment flows: buyer proof submission, seller confirmation, disputes, seller payment accounts."""
import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.notify import notify_buyer_payment_decision, notify_new_order
from app.bot.views import buyer_order_kb, order_text
from app.core.config import settings
from app.core.utils import esc, etb, norm_phone
from app.i18n import t
from app.models import Order, Payment, Shop, ShopPaymentAccount, User
from app.payments.base import PROVIDERS, extract_reference
from app.services import catalog, orders
from app.services import payments as pay

router = Router()
log = logging.getLogger(__name__)


class PayProof(StatesGroup):
    waiting = State()


class AddAcct(StatesGroup):
    provider = State()
    label = State()
    number = State()
    holder = State()


# =============== buyer: choose method / instructions ===============
async def methods_kb(session: AsyncSession, shop: Shop, lang: str):
    b = InlineKeyboardBuilder()
    if shop.accept_cod:
        b.button(text=t("pay_cod", lang), callback_data="pay:cod")
    for a in await pay.list_accounts(session, shop.id):
        b.button(text=f"💳 {pay.account_label(a)}", callback_data=f"pay:{a.id}")
    b.button(text=t("cancel", lang), callback_data="cancel_checkout")
    b.adjust(1)
    return b.as_markup()


async def send_instructions(message: Message, state: FSMContext, order, account: ShopPaymentAccount, lang: str):
    await push_instructions(message.bot, message.chat.id, state, order, account, lang)


async def push_instructions(bot: Bot, chat_id: int, state: FSMContext, order, account: ShopPaymentAccount, lang: str):
    """Send payment instructions and wait for the buyer's reference in the chat (also used by the Mini App)."""
    await state.set_state(PayProof.waiting)
    await state.update_data(order_id=order.id)
    name = account.label if account.provider == "other" and account.label else PROVIDERS[account.provider].label
    await bot.send_message(chat_id, t("pay_instructions", lang, amount=f"<b>{etb(order.total)}</b>", provider=esc(name),
                           number=esc(account.account_number), holder=esc(account.holder_name),
                           minutes=settings.payment_window_minutes, code=order.code))


@router.callback_query(F.data.startswith("payproof:"))
async def cb_payproof(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str):
    o = await orders.get_order(session, int(cb.data.split(":")[1]))
    if not o or o.buyer_id != user.id or o.status != "awaiting_payment" or o.payment_status != "awaiting":
        return await cb.answer()
    account = await session.get(ShopPaymentAccount, o.payment_account_id)
    await send_instructions(cb.message, state, o, account, lang)
    await cb.answer()


@router.callback_query(F.data.startswith("bcancel:"))
async def cb_buyer_cancel(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str):
    o = await orders.get_order(session, int(cb.data.split(":")[1]))
    if not o or o.buyer_id != user.id or o.status != "awaiting_payment" or o.payment_status != "awaiting":
        return await cb.answer()
    await orders.set_status(session, o, "cancelled")
    o.payment_status = "expired"
    await state.clear()
    await cb.message.edit_text(t("order_cancelled", lang))
    await cb.answer()


# =============== buyer: submit reference / screenshot ===============
@router.message(PayProof.waiting, F.text | F.photo)
async def got_proof(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str, bot: Bot):
    data = await state.get_data()
    o = await orders.get_order(session, data["order_id"])
    if not o or o.buyer_id != user.id or o.status != "awaiting_payment" or o.payment_status != "awaiting":
        await state.clear()
        return await message.answer(t("cancelled", lang))
    account = await session.get(ShopPaymentAccount, o.payment_account_id)
    info = PROVIDERS[account.provider]

    shot = message.photo[-1].file_id if message.photo else data.get("shot")
    if message.photo:
        await state.update_data(shot=shot)
    ref = extract_reference(message.text or message.caption or "", account.provider)
    if ref is None:
        if message.photo and not message.caption:
            return await message.answer(t("pay_ask_ref", lang))
        return await message.answer(t("pay_bad_format", lang, provider=info.label, hint=info.ref_hint))

    res = await pay.submit_proof(session, o, ref, shot)
    if res.kind == "bad_format":
        return await message.answer(t("pay_bad_format", lang, provider=info.label, hint=info.ref_hint))
    if res.kind in ("duplicate", "already", "too_many"):
        key = {"duplicate": "pay_duplicate", "already": "pay_already", "too_many": "pay_too_many"}[res.kind]
        return await message.answer(t(key, lang))
    if res.kind == "rejected":
        return await message.answer(t("pay_rejected", lang, reason=t(f"rej_{res.reason}", lang)))

    await state.clear()
    if res.kind == "verified":
        await message.answer(t("pay_verified", lang, code=o.code))
        await notify_new_order(bot, session, o)
    else:  # needs_seller
        await message.answer(t("pay_review", lang))
        if not await _ask_seller_to_confirm(bot, session, o, res.payment, res.reason):
            o.payment_status = "disputed"  # seller unreachable -> an admin decides
            await notify_admins_dispute(bot, session, o)


async def _ask_seller_to_confirm(bot: Bot, session: AsyncSession, order, payment: Payment, reason: str) -> bool:
    shop = await catalog.get_shop(session, order.shop_id)
    owner = await session.get(User, shop.owner_id)
    lang = owner.language or "en"
    notes = ", ".join(t(f"note_{r}", lang) for r in reason.split(",") if r)
    b = InlineKeyboardBuilder()
    b.button(text=t("btn_received", lang), callback_data=f"pconf:{payment.id}:ok")
    b.button(text=t("btn_not_received", lang), callback_data=f"pconf:{payment.id}:no")
    b.adjust(2)
    text = t("seller_confirm_req", lang, code=order.code, amount=etb(order.total),
             provider=esc(PROVIDERS[payment.provider].label), ref=esc(payment.reference), note=notes)
    try:
        if payment.screenshot_file_id:
            await bot.send_photo(owner.telegram_id, payment.screenshot_file_id, caption=text, reply_markup=b.as_markup())
        else:
            await bot.send_message(owner.telegram_id, text, reply_markup=b.as_markup())
        return True
    except Exception:
        log.warning("could not reach seller %s for payment %s", owner.telegram_id, payment.id, exc_info=True)
        return False


# =============== seller: confirm / deny ===============
@router.callback_query(F.data.startswith("pconf:"))
async def cb_seller_confirm(cb: CallbackQuery, session: AsyncSession, user: User, lang: str, bot: Bot):
    _, pid, verdict = cb.data.split(":")
    p = await session.get(Payment, int(pid))
    o = await orders.get_order(session, p.order_id) if p else None
    shop = await catalog.get_shop(session, o.shop_id) if o else None
    if not (p and o and shop and shop.owner_id == user.id and p.status == "pending_seller"):
        return await cb.answer()
    ok = verdict == "ok"
    await pay.seller_decide(session, p, o, ok)
    note = "✅" if ok else "❌"
    try:
        await cb.message.edit_caption(caption=f"{cb.message.caption or ''}\n\n{note}") if cb.message.photo \
            else await cb.message.edit_text(f"{cb.message.html_text}\n\n{note}")
    except Exception:
        pass
    await cb.answer()

    await notify_buyer_payment_decision(bot, session, o, ok)


# =============== dispute ===============
@router.callback_query(F.data.startswith("dispute:"))
async def cb_dispute(cb: CallbackQuery, session: AsyncSession, user: User, lang: str, bot: Bot):
    o = await orders.get_order(session, int(cb.data.split(":")[1]))
    if not o or o.buyer_id != user.id or o.status != "awaiting_payment" or o.payment_status in ("disputed", "paid"):
        return await cb.answer()
    o.payment_status = "disputed"
    await cb.message.answer(t("dispute_opened", lang))
    await cb.answer()
    await notify_admins_dispute(bot, session, o)


async def notify_admins_dispute(bot: Bot, session: AsyncSession, o):
    p = await pay.latest_payment(session, o.id)
    items = await orders.get_items(session, o.id)
    ev = f"\n\n🧾 Last proof: {esc(p.provider)} <code>{esc(p.reference)}</code> · {p.status} · {esc(p.reason or '')}" if p else ""
    b = InlineKeyboardBuilder()
    b.button(text="✅ Mark paid", callback_data=f"adm_paid:{o.id}")
    b.button(text="🚫 Cancel order", callback_data=f"adm_cancel:{o.id}")
    for admin_id in settings.admin_id_set:
        try:
            if p and p.screenshot_file_id:
                await bot.send_photo(admin_id, p.screenshot_file_id)
            await bot.send_message(admin_id, f"🆘 <b>Payment dispute</b>\n{order_text(o, items, 'en')}{ev}", reply_markup=b.as_markup())
        except Exception:
            pass


# =============== seller: payment accounts ===============
async def _accounts_view(session: AsyncSession, shop: Shop, lang: str):
    accounts = await pay.list_accounts(session, shop.id)
    b = InlineKeyboardBuilder()
    for a in accounts:
        b.button(text=f"🗑 {pay.account_label(a)} · {a.holder_name[:20]}", callback_data=f"acdel:{a.id}")
    b.button(text=t("btn_add_account", lang), callback_data="acadd")
    b.button(text=t("btn_cod_toggle", lang), callback_data="codtog")
    b.adjust(1)
    text = t("accounts_title", lang, cod="ON ✅" if shop.accept_cod else "OFF ❌")
    return text, b.as_markup()


async def _my_shop(session: AsyncSession, user: User):
    shop = await catalog.get_shop_by_owner(session, user.id)
    return shop if shop and shop.status == "active" else None


@router.callback_query(F.data == "s:pay")
async def cb_accounts(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    shop = await _my_shop(session, user)
    if not shop:
        return await cb.answer(t("not_active", lang), show_alert=True)
    text, kb = await _accounts_view(session, shop, lang)
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "codtog")
async def cb_cod(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    shop = await _my_shop(session, user)
    if not shop:
        return await cb.answer()
    if shop.accept_cod and not await pay.list_accounts(session, shop.id):
        return await cb.answer(t("cod_need_account", lang), show_alert=True)
    shop.accept_cod = not shop.accept_cod
    text, kb = await _accounts_view(session, shop, lang)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("acdel:"))
async def cb_acct_delete(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    shop = await _my_shop(session, user)
    a = await session.get(ShopPaymentAccount, int(cb.data.split(":")[1]))
    if not (shop and a and a.shop_id == shop.id):
        return await cb.answer()
    if not shop.accept_cod and len(await pay.list_accounts(session, shop.id)) <= 1:
        return await cb.answer(t("cod_need_account", lang), show_alert=True)
    a.active = False  # soft delete: old orders keep pointing at it
    text, kb = await _accounts_view(session, shop, lang)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "acadd")
async def cb_acct_add(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str):
    if not await _my_shop(session, user):
        return await cb.answer()
    b = InlineKeyboardBuilder()
    for key, info in PROVIDERS.items():
        b.button(text=info.label, callback_data=f"prov:{key}")
    b.adjust(2)
    await state.set_state(AddAcct.provider)
    await cb.message.answer(t("acc_pick_provider", lang), reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(AddAcct.provider, F.data.startswith("prov:"))
async def acct_provider(cb: CallbackQuery, state: FSMContext, lang: str):
    key = cb.data.split(":")[1]
    await state.update_data(provider=key)
    if key == "other":
        await state.set_state(AddAcct.label)
        await cb.message.answer(t("acc_ask_label", lang))
    else:
        await state.set_state(AddAcct.number)
        await cb.message.answer(t("acc_ask_number_wallet" if PROVIDERS[key].wallet else "acc_ask_number_bank", lang))
    await cb.answer()


@router.message(AddAcct.label, F.text)
async def acct_label(message: Message, state: FSMContext, lang: str):
    await state.update_data(label=message.text.strip()[:60])
    await state.set_state(AddAcct.number)
    await message.answer(t("acc_ask_number_bank", lang))


@router.message(AddAcct.number, F.text)
async def acct_number(message: Message, state: FSMContext, lang: str):
    d = await state.get_data()
    if PROVIDERS[d["provider"]].wallet:
        phone = norm_phone(message.text)
        number = phone.lstrip("+") if phone else None
    else:
        digits = "".join(ch for ch in message.text if ch.isdigit())
        number = digits if 6 <= len(digits) <= 20 else None
    if not number:
        return await message.answer(t("acc_bad_number", lang))
    await state.update_data(number=number)
    await state.set_state(AddAcct.holder)
    await message.answer(t("acc_ask_holder", lang))


@router.message(AddAcct.holder, F.text)
async def acct_holder(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str):
    d = await state.get_data()
    shop = await _my_shop(session, user)
    await state.clear()
    if not shop:
        return
    session.add(ShopPaymentAccount(shop_id=shop.id, provider=d["provider"], label=d.get("label", ""),
                                   account_number=d["number"], holder_name=message.text.strip()[:100]))
    await message.answer(t("acc_saved", lang))


# =============== seller: list payments still waiting for confirmation ===============
@router.callback_query(F.data == "s:review")
async def cb_review_list(cb: CallbackQuery, session: AsyncSession, user: User, lang: str, bot: Bot):
    shop = await _my_shop(session, user)
    if not shop:
        return await cb.answer(t("not_active", lang), show_alert=True)
    rows = (await session.execute(
        select(Payment, Order).join(Order, Order.id == Payment.order_id)
        .where(Order.shop_id == shop.id, Payment.status == "pending_seller").order_by(Payment.id))).all()
    await cb.answer()
    if not rows:
        return await cb.message.answer(t("review_none", lang))
    for payment, order in rows:
        await _ask_seller_to_confirm(bot, session, order, payment, payment.reason or "")