"""Seller plans: pick a plan, pay the platform, send the reference. Plus admin commands to manage plans."""

import logging
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.utils import esc, etb
from app.i18n import t
from app.models import Payment, Shop, SubscriptionInvoice, User
from app.payments.base import PROVIDERS, extract_reference
from app.services import catalog, plans

router = Router()
log = logging.getLogger(__name__)
IS_ADMIN = F.from_user.id.in_(settings.admin_id_set)


class PlanProof(StatesGroup):
    waiting = State()


def _fmt_date(dt) -> str:
    return dt.strftime("%d %b %Y")


async def _my_shop(session: AsyncSession, user: User) -> Shop | None:
    shop = await catalog.get_shop_by_owner(session, user.id)
    return shop if shop and shop.status == "active" else None


def plan_row(p, lang: str) -> str:
    price = (
        t("plan_per_month", lang, price=etb(p.price))
        if p.price
        else t("plan_price_free", lang)
    )
    extra = t("plan_extra_featured", lang) if p.featured else ""
    return t(
        "plan_row",
        lang,
        name=esc(p.name),
        price=price,
        products=plans.limit_label(p.max_products),
        extra=extra,
    )


async def plan_status_line(session: AsyncSession, shop: Shop, lang: str) -> str:
    plan = await plans.effective_plan(session, shop)
    used, limit = await plans.product_quota(session, shop)
    until = (
        (" · " + t("plan_until", lang, date=_fmt_date(shop.plan_expires_at)))
        if plans.is_paid_active(shop)
        else ""
    )
    return t(
        "plan_line",
        lang,
        plan=esc(plan.name),
        until=until,
        used=used,
        limit=plans.limit_label(limit),
    )


# =============== seller: view plans / buy ===============
@router.callback_query(F.data == "s:plan")
async def cb_plan(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    shop = await _my_shop(session, user)
    if not shop:
        return await cb.answer(t("not_active", lang), show_alert=True)
    all_plans = await plans.list_plans(session)
    b = InlineKeyboardBuilder()
    for p in all_plans:
        if p.price > 0:
            b.button(
                text=f"⬆️ {p.name} — {etb(p.price)}", callback_data=f"planbuy:{p.key}"
            )
    b.adjust(1)
    text = (
        (await plan_status_line(session, shop, lang))
        + "\n\n"
        + t("plans_title", lang, plans="\n".join(plan_row(p, lang) for p in all_plans))
    )
    await cb.message.answer(text, reply_markup=b.as_markup())
    await cb.answer()


async def _start_invoice(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    shop: Shop,
    plan,
    acct,
    lang: str,
):
    inv = await plans.create_invoice(session, shop, plan, acct)
    await state.set_state(PlanProof.waiting)
    await state.update_data(invoice_id=inv.id)
    name = (
        acct.label
        if acct.provider == "other" and acct.label
        else PROVIDERS[acct.provider].label
    )
    await message.answer(
        t(
            "plan_instructions",
            lang,
            amount=f"<b>{etb(plan.price)}</b>",
            plan=esc(plan.name),
            days=plans.PLAN_DAYS,
            provider=esc(name),
            number=esc(acct.number),
            holder=esc(acct.holder),
        )
    )


@router.callback_query(F.data.startswith("planbuy:"))
async def cb_planbuy(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    shop, plan = (
        await _my_shop(session, user),
        await plans.get_plan(session, cb.data.split(":")[1]),
    )
    if not shop or not plan or plan.price <= 0:
        return await cb.answer()
    accounts = plans.platform_accounts()
    if not accounts:
        return await cb.answer(t("plan_not_configured", lang), show_alert=True)
    await cb.answer()
    if len(accounts) == 1:
        return await _start_invoice(
            cb.message, state, session, shop, plan, accounts[0], lang
        )
    b = InlineKeyboardBuilder()
    for a in accounts:
        name = (
            a.label
            if a.provider == "other" and a.label
            else PROVIDERS[a.provider].label
        )
        b.button(
            text=f"{name} ···{a.number[-4:]}",
            callback_data=f"planacct:{plan.key}:{a.idx}",
        )
    b.adjust(1)
    await cb.message.answer(t("plan_pick_account", lang), reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("planacct:"))
async def cb_planacct(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    _, key, idx = cb.data.split(":")
    shop, plan = await _my_shop(session, user), await plans.get_plan(session, key)
    accounts = plans.platform_accounts()
    if not shop or not plan or plan.price <= 0 or not 0 <= int(idx) < len(accounts):
        return await cb.answer()
    await cb.answer()
    await _start_invoice(
        cb.message, state, session, shop, plan, accounts[int(idx)], lang
    )


# =============== seller: send proof ===============
@router.message(PlanProof.waiting, F.text | F.photo)
async def got_plan_proof(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    lang: str,
    bot: Bot,
):
    data = await state.get_data()
    inv = await session.get(SubscriptionInvoice, data.get("invoice_id"))
    shop = await _my_shop(session, user)
    if not inv or not shop or inv.shop_id != shop.id or inv.status == "paid":
        await state.clear()
        return await message.answer(t("cancelled", lang))
    info = PROVIDERS[inv.provider]
    shot = message.photo[-1].file_id if message.photo else data.get("shot")
    if message.photo:
        await state.update_data(shot=shot)
    ref = extract_reference(message.text or message.caption or "", inv.provider)
    if ref is None:
        if message.photo and not message.caption:
            return await message.answer(t("pay_ask_ref", lang))
        return await message.answer(
            t("pay_bad_format", lang, provider=info.label, hint=info.ref_hint)
        )

    res = await plans.submit_invoice_proof(session, inv, ref, shot)
    if res.kind == "bad_format":
        return await message.answer(
            t("pay_bad_format", lang, provider=info.label, hint=info.ref_hint)
        )
    if res.kind in ("duplicate", "already", "too_many"):
        return await message.answer(
            t(
                {
                    "duplicate": "pay_duplicate",
                    "already": "pay_already",
                    "too_many": "pay_too_many",
                }[res.kind],
                lang,
            )
        )
    if res.kind == "rejected":
        return await message.answer(
            t("pay_rejected", lang, reason=t(f"rej_{res.reason}", lang))
        )

    await state.clear()
    if res.kind == "verified":
        plan = await plans.get_plan(session, inv.plan_key)
        return await message.answer(
            t(
                "plan_activated",
                lang,
                plan=esc(plan.name),
                date=_fmt_date(shop.plan_expires_at),
            )
        )
    await message.answer(t("plan_review", lang))
    await _ask_admins(bot, session, inv, shop, res.payment, res.reason, user)


async def _ask_admins(
    bot: Bot,
    session: AsyncSession,
    inv,
    shop: Shop,
    payment: Payment,
    reason: str,
    owner: User,
):
    plan = await plans.get_plan(session, inv.plan_key)
    b = InlineKeyboardBuilder()
    b.button(text="✅ Approve", callback_data=f"planok:{payment.id}")
    b.button(text="❌ Reject", callback_data=f"planno:{payment.id}")
    text = (
        f"💎 <b>Plan payment to confirm</b>\nShop: {esc(shop.name)} (@{esc(shop.slug)}) · owner {esc(owner.name)}\n"
        f"Plan: {esc(plan.name)} · expected {etb(inv.amount)} via {PROVIDERS[inv.provider].label}\n"
        f"Reference: <code>{esc(payment.reference)}</code>\nAuto-check: {esc(reason)}"
    )
    for admin_id in settings.admin_id_set:
        try:
            if payment.screenshot_file_id:
                await bot.send_photo(
                    admin_id,
                    payment.screenshot_file_id,
                    caption=text,
                    reply_markup=b.as_markup(),
                )
            else:
                await bot.send_message(admin_id, text, reply_markup=b.as_markup())
        except Exception:
            log.warning(
                "could not reach admin %s about plan payment", admin_id, exc_info=True
            )


@router.callback_query(F.data.regexp(r"^plan(ok|no):\d+$"), IS_ADMIN)
async def cb_admin_plan(cb: CallbackQuery, session: AsyncSession, bot: Bot):
    action, pid = cb.data.split(":")
    payment = await session.get(Payment, int(pid))
    approve = action == "planok"
    shop = await plans.admin_decide(session, payment, approve) if payment else None
    if shop is None:
        return await cb.answer("Already handled.", show_alert=True)
    mark = "✅ Approved" if approve else "❌ Rejected"
    try:
        if cb.message.photo:
            await cb.message.edit_caption(caption=f"{cb.message.caption}\n\n{mark}")
        else:
            await cb.message.edit_text(f"{cb.message.html_text}\n\n{mark}")
    except Exception:
        pass
    await cb.answer()
    owner = await session.get(User, shop.owner_id)
    lang = owner.language or "en"
    plan = await plans.get_plan(
        session,
        shop.plan_key
        if approve
        else (await session.get(SubscriptionInvoice, payment.invoice_id)).plan_key,
    )
    msg = (
        t(
            "plan_activated",
            lang,
            plan=esc(plan.name),
            date=_fmt_date(shop.plan_expires_at),
        )
        if approve
        else t("plan_denied", lang)
    )
    try:
        await bot.send_message(owner.telegram_id, msg)
    except Exception:
        log.warning("could not notify seller about plan decision", exc_info=True)


# =============== admin commands ===============
@router.message(Command("plans"), IS_ADMIN)
async def cmd_plans(message: Message, session: AsyncSession):
    rows = "\n".join(
        f"<code>{p.key}</code> {esc(p.name)} — {etb(p.price)}/30d · {plans.limit_label(p.max_products)} products"
        f"{' · featured' if p.featured else ''}"
        for p in await plans.list_plans(session)
    )
    accounts = plans.platform_accounts()
    acct = (
        "\n".join(
            f"• {PROVIDERS[a.provider].label} {a.number} ({esc(a.holder)})"
            for a in accounts
        )
        or "⚠️ none set: add PLATFORM_ACCOUNTS to .env"
    )
    await message.answer(
        f"💎 <b>Plans</b>\n{rows}\n\n<b>Where sellers pay you:</b>\n{acct}\n\n"
        "Edit: <code>/setplan basic 300 100</code> (key, price ETB, max products)\n"
        "Grant: <code>/grant shopslug pro 30</code> (free trial or gift)"
    )


@router.message(Command("setplan"), IS_ADMIN)
async def cmd_setplan(message: Message, command: CommandObject, session: AsyncSession):
    args = (command.args or "").split()
    plan = await plans.get_plan(session, args[0]) if args else None
    try:
        price, maxp = Decimal(args[1]), int(args[2])
    except (IndexError, InvalidOperation, ValueError):
        return await message.answer("Usage: <code>/setplan basic 300 100</code>")
    if not plan or price < 0 or maxp < 1:
        return await message.answer("Unknown plan key or invalid numbers. See /plans")
    plan.price, plan.max_products = price, maxp
    await message.answer(
        f"✅ {esc(plan.name)}: {etb(price)} · {plans.limit_label(maxp)} products"
    )


@router.message(Command("grant"), IS_ADMIN)
async def cmd_grant(
    message: Message, command: CommandObject, session: AsyncSession, bot: Bot
):
    args = (command.args or "").split()
    try:
        slug, key, days = (
            args[0],
            args[1],
            int(args[2]) if len(args) > 2 else plans.PLAN_DAYS,
        )
    except (IndexError, ValueError):
        return await message.answer("Usage: <code>/grant shopslug pro 30</code>")
    shop, plan = (
        await catalog.get_shop_by_slug(session, slug),
        await plans.get_plan(session, key),
    )
    if not shop or not plan or days < 1:
        return await message.answer("Unknown shop or plan. See /plans")
    until = plans.activate(shop, plan, days)
    await message.answer(
        f"✅ {esc(shop.name)} → {esc(plan.name)} until {_fmt_date(until)}"
    )
    owner = await session.get(User, shop.owner_id)
    try:
        await bot.send_message(
            owner.telegram_id,
            t(
                "plan_activated",
                owner.language or "en",
                plan=esc(plan.name),
                date=_fmt_date(until),
            ),
        )
    except Exception:
        pass
