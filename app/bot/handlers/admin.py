from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.notify import notify_new_order
from app.bot.views import order_text
from app.core.config import settings
from app.core.utils import esc
from app.i18n import t
from app.i18n import t as _t
from app.models import Order, Product, Shop, User
from app.payments.debug import inspect as inspect_receipt
from app.services import orders as orders_svc
from app.services import payments as pay_svc

router = Router()
router.message.filter(F.from_user.id.in_(settings.admin_id_set))
router.callback_query.filter(F.from_user.id.in_(settings.admin_id_set))


@router.message(Command("pending"))
async def pending(message: Message, session: AsyncSession):
    shops = (await session.scalars(select(Shop).where(Shop.status == "pending"))).all()
    if not shops:
        return await message.answer("No pending shops.")
    for s in shops:
        b = InlineKeyboardBuilder()
        b.button(text="✅ Approve", callback_data=f"shopok:{s.id}")
        b.button(text="❌ Reject", callback_data=f"shopno:{s.id}")
        await message.answer(
            f"<b>{esc(s.name)}</b> (@{esc(s.slug)}) · {esc(s.city)} · {s.phone}",
            reply_markup=b.as_markup(),
        )


@router.message(Command("stats"))
async def stats(message: Message, session: AsyncSession):
    async def count(model, *where):
        return await session.scalar(
            select(func.count()).select_from(model).where(*where)
        )

    await message.answer(
        f"👥 Users: {await count(User)}\n🏪 Active shops: {await count(Shop, Shop.status == 'active')}\n"
        f"⏳ Pending shops: {await count(Shop, Shop.status == 'pending')}\n"
        f"🛍 Products: {await count(Product)}\n📦 Orders: {await count(Order)}"
    )


@router.callback_query(F.data.regexp(r"^shop(ok|no):\d+$"))
async def decide(cb: CallbackQuery, session: AsyncSession, bot: Bot):
    action, sid = cb.data.split(":")
    shop = await session.get(Shop, int(sid))
    if not shop or shop.status != "pending":
        return await cb.answer("Already handled.", show_alert=True)
    approved = action == "shopok"
    shop.status = "active" if approved else "rejected"
    owner = await session.get(User, shop.owner_id)
    lang = owner.language or "en"
    key = "shop_approved" if approved else "shop_rejected"
    try:
        await bot.send_message(owner.telegram_id, t(key, lang, name=esc(shop.name)))
    except Exception:
        pass
    await cb.message.edit_text(
        f"{cb.message.html_text}\n\n{'✅ Approved' if approved else '❌ Rejected'}"
    )
    await cb.answer()


@router.message(Command("disputes"))
async def disputes(message: Message, session: AsyncSession):
    rows = (
        await session.scalars(select(Order).where(Order.payment_status == "disputed"))
    ).all()
    if not rows:
        return await message.answer("No open disputes.")
    for o in rows:
        b = InlineKeyboardBuilder()
        b.button(text="✅ Mark paid", callback_data=f"adm_paid:{o.id}")
        b.button(text="🚫 Cancel order", callback_data=f"adm_cancel:{o.id}")
        p = await pay_svc.latest_payment(session, o.id)
        ev = (
            f"\n🧾 {p.provider} <code>{esc(p.reference)}</code> · {p.status}"
            if p
            else ""
        )
        await message.answer(
            order_text(o, await orders_svc.get_items(session, o.id), "en") + ev,
            reply_markup=b.as_markup(),
        )


@router.callback_query(F.data.regexp(r"^adm_(paid|cancel):\d+$"))
async def resolve_dispute(cb: CallbackQuery, session: AsyncSession, bot: Bot):
    action, oid = cb.data.split(":")
    o = await orders_svc.get_order(session, int(oid))
    if not o or o.payment_status != "disputed":
        return await cb.answer("Already handled.", show_alert=True)
    buyer = await session.get(User, o.buyer_id)
    blang = buyer.language or "en"
    if action == "adm_paid":
        p = await pay_svc.latest_payment(session, o.id)
        if p and p.status == "pending_seller":
            p.status, p.verified_by = "admin_approved", "admin"
        pay_svc.mark_paid(o)
        await cb.message.edit_text(f"{cb.message.html_text}\n\n✅ Marked paid")
        await bot.send_message(
            buyer.telegram_id, _t("pay_verified", blang, code=o.code)
        )
        await notify_new_order(bot, session, o)
    else:
        await orders_svc.set_status(session, o, "cancelled")
        o.payment_status = "expired"
        await cb.message.edit_text(f"{cb.message.html_text}\n\n🚫 Cancelled")
        await bot.send_message(buyer.telegram_id, _t("order_cancelled", blang))
    await cb.answer()


@router.message(Command("checkref"))
async def checkref(message: Message, command: CommandObject):
    """Diagnostics: /checkref telebirr CE2513001XYT  ·  /checkref cbe FT26093JCD32 18872366  ·  /checkref cbe <mbreciept link>"""
    args = (command.args or "").split()
    if len(args) < 2:
        return await message.answer(
            "Usage:\n<code>/checkref telebirr CE2513001XYT</code>\n"
            "<code>/checkref cbe FT26093JCD32 18872366</code>\n<code>/checkref cbe https://mbreciept.cbe.com.et/…</code>"
        )
    await message.answer("⏳ fetching…")
    text = await inspect_receipt(
        args[0].lower(), args[1], args[2] if len(args) > 2 else ""
    )
    for i in range(
        0, min(len(text), 11000), 3800
    ):  # Telegram caps messages at 4096 chars
        await message.answer(f"<pre>{esc(text[i : i + 3800])}</pre>")
