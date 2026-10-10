"""Buyers can report a shop; admins review reports in the admin panel (nothing is automatic)."""

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.utils import esc
from app.i18n import t
from app.models import User
from app.services import catalog, moderation

router = Router()
log = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("rpt:"))
async def cb_report_start(cb: CallbackQuery, lang: str):
    shop_id = cb.data.split(":")[1]
    b = InlineKeyboardBuilder()
    for code in moderation.REPORT_REASONS:
        b.button(text=t(f"report_{code}", lang), callback_data=f"rp:{shop_id}:{code}")
    b.adjust(1)
    await cb.message.answer(t("report_pick", lang), reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(F.data.regexp(r"^rp:\d+:\w+$"))
async def cb_report(
    cb: CallbackQuery, session: AsyncSession, user: User, lang: str, bot: Bot
):
    _, sid, code = cb.data.split(":")
    shop = await catalog.get_shop(session, int(sid))
    if not shop:
        return await cb.answer()
    created, count = await moderation.create_report(session, shop, user, code)
    await cb.message.edit_text(
        t("report_thanks" if created else "report_duplicate", lang)
    )
    await cb.answer()
    if created and moderation.crossed_threshold(count):
        for admin_id in settings.admin_id_set:
            try:
                await bot.send_message(
                    admin_id,
                    f"🚩 <b>{esc(shop.name)}</b> (@{esc(shop.slug)}) now has {count} open reports. "
                    "Review it: /admin → Reports",
                )
            except Exception:
                log.warning(
                    "could not alert admin %s about reports", admin_id, exc_info=True
                )
