import logging

from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.views import buyer_order_kb, order_text, seller_order_kb
from app.i18n import t
from app.models import Order, User
from app.services import catalog, orders

log = logging.getLogger(__name__)


async def notify_new_order(bot: Bot, session: AsyncSession, order: Order) -> None:
    """Tell the shop owner about an order that is ready for action (COD placed, or payment verified)."""
    shop = await catalog.get_shop(session, order.shop_id)
    owner = await session.get(User, shop.owner_id)
    lang = owner.language or "en"
    key = "new_order_seller" if order.payment_method == "cod" else "new_paid_order"
    items = await orders.get_items(session, order.id)
    try:
        await bot.send_message(owner.telegram_id, f"{t(key, lang)}\n{order_text(order, items, lang)}",
                               reply_markup=seller_order_kb(order, lang))
        if order.lat is not None:
            await bot.send_location(owner.telegram_id, order.lat, order.lng)
    except Exception:
        log.warning("could not notify seller %s about order %s", owner.telegram_id, order.code, exc_info=True)


async def notify_buyer_status(bot: Bot, session: AsyncSession, order: Order) -> None:
    """Tell the buyer their order moved to its current status (shared by the chat and the seller dashboard)."""
    buyer = await session.get(User, order.buyer_id)
    lang = buyer.language or "en"
    try:
        await bot.send_message(buyer.telegram_id, t("order_update", lang, code=order.code, status=t(f"status_{order.status}", lang)),
                               reply_markup=buyer_order_kb(order, lang))
    except Exception:
        log.warning("could not notify buyer %s about order %s", buyer.telegram_id, order.code, exc_info=True)


async def notify_buyer_payment_decision(bot: Bot, session: AsyncSession, order: Order, received: bool) -> None:
    """After the seller confirms/denies a payment: tell the buyer; a confirmed order also goes to the seller as 'paid'."""
    buyer = await session.get(User, order.buyer_id)
    lang = buyer.language or "en"
    try:
        if received:
            await bot.send_message(buyer.telegram_id, t("seller_confirmed_buyer", lang, code=order.code))
        else:
            b = InlineKeyboardBuilder()
            b.button(text=t("pay_send_proof", lang), callback_data=f"payproof:{order.id}")
            b.button(text=t("btn_dispute", lang), callback_data=f"dispute:{order.id}")
            b.adjust(1)
            await bot.send_message(buyer.telegram_id, t("seller_denied_buyer", lang, code=order.code), reply_markup=b.as_markup())
    except Exception:
        log.warning("could not notify buyer %s about the payment decision", buyer.telegram_id, exc_info=True)
    if received:
        await notify_new_order(bot, session, order)