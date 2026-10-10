"""Telegram admin panel: /admin opens a menu for shops, verifications, reports, reviews, disputes, plans and stats."""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers import admin as admin_h
from app.bot.handlers import plans as plans_h
from app.bot.handlers import verification as verif_h
from app.core.config import settings
from app.core.utils import esc, etb
from app.i18n import t
from app.models import Review, Shop, User
from app.services import moderation, plans, reviews, verification

router = Router()
log = logging.getLogger(__name__)
IS_ADMIN = F.from_user.id.in_(settings.admin_id_set)
PER_PAGE = 8


# ---------------- home ----------------
async def _home(session: AsyncSession):
    s = await moderation.stats(session)
    b = InlineKeyboardBuilder()
    b.button(
        text=f"🏪 Shops ({s['shops_pending']} pending)", callback_data="ap:shops:all:0"
    )
    b.button(
        text=f"🪪 Verifications ({s['verifications_pending']})", callback_data="ap:vq"
    )
    b.button(text=f"🚩 Reports ({s['reports_open']})", callback_data="ap:reports")
    b.button(text=f"🆘 Disputes ({s['disputes']})", callback_data="ap:disp")
    b.button(text="⭐ Review moderation", callback_data="ap:revs:0")
    b.button(text="💎 Plans", callback_data="ap:plans")
    b.button(text="📊 Stats", callback_data="ap:stats")
    b.adjust(2)
    return "🛠 <b>Admin panel</b>", b.as_markup()


@router.message(Command("admin"), IS_ADMIN)
async def cmd_admin(message: Message, session: AsyncSession):
    text, kb = await _home(session)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "ap:home", IS_ADMIN)
async def cb_home(cb: CallbackQuery, session: AsyncSession):
    text, kb = await _home(session)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


def _back(to: str = "ap:home"):
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Back", callback_data=to)
    return b.as_markup()


# ---------------- stats ----------------
@router.callback_query(F.data == "ap:stats", IS_ADMIN)
async def cb_stats(cb: CallbackQuery, session: AsyncSession):
    s = await moderation.stats(session)
    await cb.message.edit_text(
        "📊 <b>Platform stats</b>\n"
        f"👥 Users: {s['users']}\n"
        f"🏪 Shops: {s['shops_active']} active · {s['shops_pending']} pending · {s['shops_suspended']} suspended\n"
        f"✔️ Verified: {s['shops_verified']} · 💎 Paid plans: {s['shops_paid']}\n"
        f"🛍 Products: {s['products']}\n"
        f"📦 Orders: {s['orders']} total · {s['orders_7d']} in the last 7 days · {s['delivered']} delivered\n"
        f"💰 Delivered sales value: {etb(s['gmv_delivered'])}\n"
        f"⭐ Reviews: {s['reviews']}\n"
        f"🆘 Open disputes: {s['disputes']} · 🚩 Open reports: {s['reports_open']} · 🪪 Pending verifications: {s['verifications_pending']}",
        reply_markup=_back(),
    )
    await cb.answer()


# ---------------- shops ----------------
def _shops_kb(shops, flt: str, page: int, total: int):
    b = InlineKeyboardBuilder()
    for sh in shops:
        mark = {
            "active": "🟢",
            "pending": "🕓",
            "suspended": "⛔",
            "rejected": "✖️",
        }.get(sh.status, "•")
        b.button(
            text=f"{mark} {'✔️ ' if sh.verified else ''}{sh.name} · {sh.city}",
            callback_data=f"ap:shop:{sh.id}",
        )
    b.adjust(1)
    nav = InlineKeyboardBuilder()
    for f in moderation.SHOP_FILTERS:
        nav.button(
            text=("• " if f == flt else "") + f.capitalize(),
            callback_data=f"ap:shops:{f}:0",
        )
    nav.adjust(4)
    pager = InlineKeyboardBuilder()
    if page > 0:
        pager.button(text="◀️", callback_data=f"ap:shops:{flt}:{page - 1}")
    if (page + 1) * PER_PAGE < total:
        pager.button(text="▶️", callback_data=f"ap:shops:{flt}:{page + 1}")
    pager.button(text="⬅️ Menu", callback_data="ap:home")
    b.attach(nav)
    b.attach(pager)
    return b.as_markup()


@router.callback_query(F.data.regexp(r"^ap:shops:\w+:\d+$"), IS_ADMIN)
async def cb_shops(cb: CallbackQuery, session: AsyncSession):
    _, _, flt, page = cb.data.split(":")
    if flt not in moderation.SHOP_FILTERS:
        return await cb.answer()
    shops, total = await moderation.list_shops(session, flt, int(page), PER_PAGE)
    await cb.message.edit_text(
        f"🏪 <b>Shops</b> · {flt} · {total}",
        reply_markup=_shops_kb(shops, flt, int(page), total),
    )
    await cb.answer()


async def _shop_view(session: AsyncSession, shop: Shop):
    owner = await session.get(User, shop.owner_id)
    sig = await moderation.risk_signals(session, shop)
    plan = await plans.effective_plan(session, shop)
    vstatus = await verification.status_for_shop(session, shop)
    flags = []
    if sig["reports_open"] >= settings.report_alert_threshold:
        flags.append("🚩 many open reports")
    if sig["disputes_open"]:
        flags.append("🆘 open payment dispute")
    if shop.rating_count >= 3 and shop.rating_avg < 3:
        flags.append("⭐ low rating")
    text = (
        f"🏪 <b>{esc(shop.name)}</b> (@{esc(shop.slug)})\n"
        f"Status: <b>{shop.status}</b> · {'✔️ verified' if shop.verified else 'not verified'} (ID check: {vstatus})\n"
        f"📍 {esc(shop.city)} · 📞 {shop.phone}\nOwner: {esc(owner.name)} · tg id <code>{owner.telegram_id}</code>\n"
        f"Plan: {esc(plan.name)} · products {sig['products']}\n"
        f"Orders: {sig['orders']} ({sig['delivered']} delivered) · open disputes: {sig['disputes_open']}\n"
        f"Open reports: {sig['reports_open']} · Rating: {reviews.rating_label(shop) or 'no reviews'} · age {sig['age_days']} days"
        + (f"\n\n⚠️ <b>Flags:</b> {', '.join(flags)}" if flags else "")
    )
    b = InlineKeyboardBuilder()
    if shop.status == "pending":
        b.button(text="✅ Approve", callback_data=f"shopok:{shop.id}")
        b.button(text="❌ Reject", callback_data=f"shopno:{shop.id}")
    elif shop.status == "active":
        b.button(text="⛔ Suspend", callback_data=f"ap:sus:{shop.id}")
    elif shop.status == "suspended":
        b.button(text="✅ Reactivate", callback_data=f"ap:react:{shop.id}")
    b.button(
        text="Remove badge" if shop.verified else "✔️ Mark verified",
        callback_data=f"ap:ver:{shop.id}",
    )
    if sig["reports_open"]:
        b.button(text="🗑 Dismiss reports", callback_data=f"ap:rdis:{shop.id}")
    b.button(text="⬅️ Shops", callback_data="ap:shops:all:0")
    b.adjust(2, 1)
    return text, b.as_markup()


async def _render_shop(cb: CallbackQuery, session: AsyncSession, shop: Shop):
    text, kb = await _shop_view(session, shop)
    await cb.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^ap:shop:\d+$"), IS_ADMIN)
async def cb_shop(cb: CallbackQuery, session: AsyncSession):
    shop = await session.get(Shop, int(cb.data.split(":")[2]))
    if shop:
        await _render_shop(cb, session, shop)
    await cb.answer()


async def _tell(bot: Bot, session: AsyncSession, shop: Shop, key: str) -> None:
    owner = await session.get(User, shop.owner_id)
    try:
        await bot.send_message(
            owner.telegram_id, t(key, owner.language or "en", name=esc(shop.name))
        )
    except Exception:
        log.warning("could not notify shop owner", exc_info=True)


@router.callback_query(F.data.regexp(r"^ap:sus:\d+$"), IS_ADMIN)
async def cb_suspend_ask(cb: CallbackQuery):
    sid = cb.data.split(":")[2]
    b = InlineKeyboardBuilder()
    b.button(text="Yes, suspend", callback_data=f"ap:susy:{sid}")
    b.button(text="No", callback_data=f"ap:shop:{sid}")
    await cb.message.edit_text(
        "⛔ Suspend this shop? It disappears from the directory, buyers can't order from it, "
        "and the owner can't manage it until you reactivate.",
        reply_markup=b.as_markup(),
    )
    await cb.answer()


@router.callback_query(F.data.regexp(r"^ap:(susy|react):\d+$"), IS_ADMIN)
async def cb_suspend_do(cb: CallbackQuery, session: AsyncSession, bot: Bot):
    action, sid = cb.data.split(":")[1:]
    shop = await session.get(Shop, int(sid))
    if not shop or not await moderation.set_shop_status(
        shop, "suspended" if action == "susy" else "active"
    ):
        return await cb.answer("Nothing to change.", show_alert=True)
    await _render_shop(cb, session, shop)
    await cb.answer("Done")
    await _tell(
        bot, session, shop, "shop_suspended" if action == "susy" else "shop_reactivated"
    )


@router.callback_query(F.data.regexp(r"^ap:ver:\d+$"), IS_ADMIN)
async def cb_toggle_verified(cb: CallbackQuery, session: AsyncSession):
    shop = await session.get(Shop, int(cb.data.split(":")[2]))
    if shop:
        shop.verified = not shop.verified
        await _render_shop(cb, session, shop)
    await cb.answer("Updated")


@router.callback_query(F.data.regexp(r"^ap:rdis:\d+$"), IS_ADMIN)
async def cb_dismiss(cb: CallbackQuery, session: AsyncSession):
    shop = await session.get(Shop, int(cb.data.split(":")[2]))
    if shop:
        n = await moderation.dismiss_reports(session, shop.id)
        await _render_shop(cb, session, shop)
        await cb.answer(f"{n} report(s) dismissed")
    else:
        await cb.answer()


# ---------------- reports ----------------
@router.callback_query(F.data == "ap:reports", IS_ADMIN)
async def cb_reports(cb: CallbackQuery, session: AsyncSession):
    rows = await moderation.open_reports_by_shop(session)
    if not rows:
        await cb.message.edit_text("🚩 No open reports.", reply_markup=_back())
        return await cb.answer()
    b = InlineKeyboardBuilder()
    lines = []
    for shop, n, reasons in rows[:10]:
        lines.append(
            f"• <b>{esc(shop.name)}</b>: {n} report(s) — "
            + ", ".join(f"{k}×{v}" for k, v in reasons.items())
        )
        b.button(text=f"Open {shop.name[:24]}", callback_data=f"ap:shop:{shop.id}")
    b.button(text="⬅️ Menu", callback_data="ap:home")
    b.adjust(1)
    await cb.message.edit_text(
        "🚩 <b>Open reports</b>\n" + "\n".join(lines), reply_markup=b.as_markup()
    )
    await cb.answer()


# ---------------- review moderation ----------------
async def _reviews_view(session: AsyncSession, page: int):
    rows, total = await moderation.recent_reviews(session, page, 5)
    lines, b = [], InlineKeyboardBuilder()
    for r, shop, buyer in rows:
        hidden = r.status == "hidden"
        lines.append(
            f"#{r.id} {'🙈 ' if hidden else ''}{reviews.stars(r.rating)} · {esc(shop.name)} · {esc((buyer.name or '').split(' ')[0])}"
            + (f"\n   “{esc(r.comment)}”" if r.comment else "")
        )
        b.button(
            text=("👁 Show" if hidden else "🙈 Hide") + f" #{r.id}",
            callback_data=f"ap:rv:{r.id}:{page}",
        )
    b.adjust(2)
    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="◀️", callback_data=f"ap:revs:{page - 1}")
    if (page + 1) * 5 < total:
        nav.button(text="▶️", callback_data=f"ap:revs:{page + 1}")
    nav.button(text="⬅️ Menu", callback_data="ap:home")
    b.attach(nav)
    return "⭐ <b>Reviews</b> (newest first)\n\n" + (
        "\n\n".join(lines) or "No reviews yet."
    ), b.as_markup()


@router.callback_query(F.data.regexp(r"^ap:revs:\d+$"), IS_ADMIN)
async def cb_reviews(cb: CallbackQuery, session: AsyncSession):
    text, kb = await _reviews_view(session, int(cb.data.split(":")[2]))
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^ap:rv:\d+:\d+$"), IS_ADMIN)
async def cb_review_toggle(cb: CallbackQuery, session: AsyncSession):
    _, _, rid, page = cb.data.split(":")
    review = await session.get(Review, int(rid))
    if review:
        await moderation.set_review_status(
            session, review, "visible" if review.status == "hidden" else "hidden"
        )
    text, kb = await _reviews_view(session, int(page))
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Updated")


# ---------------- shortcuts to existing tools ----------------
@router.callback_query(F.data == "ap:vq", IS_ADMIN)
async def cb_vq(cb: CallbackQuery, session: AsyncSession, bot: Bot):
    await cb.answer()
    if not await verif_h.send_pending(bot, cb.message.chat.id, session):
        await cb.message.answer("No verifications waiting.")


@router.callback_query(F.data == "ap:disp", IS_ADMIN)
async def cb_disp(cb: CallbackQuery, session: AsyncSession):
    await cb.answer()
    await admin_h.disputes(cb.message, session)


@router.callback_query(F.data == "ap:plans", IS_ADMIN)
async def cb_plans(cb: CallbackQuery, session: AsyncSession):
    await cb.answer()
    await plans_h.cmd_plans(cb.message, session)
