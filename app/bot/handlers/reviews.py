"""Verified-purchase reviews: buyers rate a delivered order, everyone can read a shop's latest reviews."""

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import main_menu
from app.core.utils import esc
from app.i18n import t
from app.models import Review, User
from app.services import catalog, orders, reviews

router = Router()
log = logging.getLogger(__name__)


class ReviewForm(StatesGroup):
    comment = State()


def rating_kb(order_id: int):
    b = InlineKeyboardBuilder()
    for n in range(1, 6):
        b.button(text=f"{n}⭐", callback_data=f"rate:{order_id}:{n}")
    b.adjust(5)
    return b.as_markup()


async def _notify_seller(bot: Bot, session: AsyncSession, order, key: str, **kw):
    shop = await catalog.get_shop(session, order.shop_id)
    owner = await session.get(User, shop.owner_id)
    try:
        await bot.send_message(
            owner.telegram_id, t(key, owner.language or "en", code=order.code, **kw)
        )
    except Exception:
        log.warning("could not notify seller about review", exc_info=True)


@router.callback_query(F.data.startswith("rateform:"))
async def cb_rateform(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    o = await orders.get_order(session, int(cb.data.split(":")[1]))
    if not o or o.buyer_id != user.id or o.status != "delivered":
        return await cb.answer()
    if await reviews.get_for_order(session, o.id):
        return await cb.answer(t("already_reviewed", lang), show_alert=True)
    await cb.message.answer(t("rate_prompt", lang), reply_markup=rating_kb(o.id))
    await cb.answer()


@router.callback_query(F.data.startswith("rate:"))
async def cb_rate(
    cb: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    lang: str,
    bot: Bot,
):
    _, oid, n = cb.data.split(":")
    o = await orders.get_order(session, int(oid))
    if not o or o.buyer_id != user.id or o.status != "delivered":
        return await cb.answer()
    if await reviews.get_for_order(session, o.id):
        return await cb.answer(t("already_reviewed", lang), show_alert=True)
    review = await reviews.add_review(session, o, int(n))
    if not review:
        return await cb.answer()
    await state.set_state(ReviewForm.comment)
    await state.update_data(review_id=review.id)
    skip = InlineKeyboardBuilder()
    skip.button(text=t("btn_skip", lang), callback_data="rskip")
    await cb.message.edit_text(
        t("rate_comment", lang, stars=reviews.stars(review.rating)),
        reply_markup=skip.as_markup(),
    )
    await cb.answer()
    await _notify_seller(
        bot, session, o, "review_new_seller", stars=reviews.stars(review.rating)
    )


@router.message(ReviewForm.comment, F.text)
async def got_comment(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    lang: str,
    bot: Bot,
):
    data = await state.get_data()
    await state.clear()
    review = await session.get(Review, data.get("review_id"))
    if not review or review.buyer_id != user.id:
        return
    review.comment = message.text.strip()[:300]
    await message.answer(t("rate_done", lang), reply_markup=main_menu(lang))
    order = await orders.get_order(session, review.order_id)
    await _notify_seller(
        bot, session, order, "review_comment_seller", comment=esc(review.comment)
    )


@router.callback_query(F.data == "rskip")
async def cb_skip(cb: CallbackQuery, state: FSMContext, lang: str):
    await state.clear()
    await cb.message.edit_text(t("rate_done", lang))
    await cb.answer()


# ---------------- reading reviews ----------------
def _render(rows, lang: str) -> str:
    blocks = []
    for r, u in rows:
        who = esc((u.name or "").split(" ")[0] or "—")
        line = f"{reviews.stars(r.rating)} — {who}"
        if r.comment:
            line += f"\n“{esc(r.comment)}”"
        blocks.append(line)
    return "\n\n".join(blocks)


@router.callback_query(F.data.startswith("shoprev:"))
async def cb_shop_reviews(cb: CallbackQuery, session: AsyncSession, lang: str):
    shop = await catalog.get_shop(session, int(cb.data.split(":")[1]))
    if not shop:
        return await cb.answer()
    rows = await reviews.list_reviews(session, shop.id, 5)
    head = t(
        "reviews_title",
        lang,
        name=esc(shop.name),
        rating=reviews.rating_label(shop) or t("no_reviews_yet", lang),
    )
    await cb.message.answer(head + (("\n\n" + _render(rows, lang)) if rows else ""))
    await cb.answer()


@router.callback_query(F.data == "s:reviews")
async def cb_my_reviews(
    cb: CallbackQuery, session: AsyncSession, user: User, lang: str
):
    shop = await catalog.get_shop_by_owner(session, user.id)
    if not shop or shop.status != "active":
        return await cb.answer(t("not_active", lang), show_alert=True)
    rows = await reviews.list_reviews(session, shop.id, 10)
    head = t(
        "reviews_title",
        lang,
        name=esc(shop.name),
        rating=reviews.rating_label(shop) or t("no_reviews_yet", lang),
    )
    await cb.message.answer(head + (("\n\n" + _render(rows, lang)) if rows else ""))
    await cb.answer()
