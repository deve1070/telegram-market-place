"""Shared message/keyboard builders used by buyer, seller and admin handlers."""

from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.utils import esc, etb
from app.i18n import t
from app.payments.base import PROVIDERS
from app.services.orders import CANCELLABLE, NEXT_STATUS


def payment_line(order, lang: str) -> str:
    if order.payment_method == "cod":
        return t("pay_cod", lang)
    info = PROVIDERS.get(order.payment_method)
    return f"💳 {info.label if info else order.payment_method} — {t('pay_state_' + order.payment_status, lang)}"


def order_text(order, items, lang: str) -> str:
    lines = "\n".join(
        f"• {esc(i.title)} × {i.qty} — {etb(i.price * i.qty)}" for i in items
    )
    delivery = ""
    if order.delivery_zone:
        fee = etb(order.delivery_fee) if order.delivery_fee else t("free", lang)
        delivery = f"🚚 {esc(order.delivery_zone)} — {fee}\n"
    return t(
        "order_card",
        lang,
        code=order.code,
        status=t(f"status_{order.status}", lang),
        items=lines,
        delivery=delivery,
        total=etb(order.total),
        payment=payment_line(order, lang),
        address=esc(order.address_text or "—"),
        phone=order.phone,
    )


def seller_order_kb(order, lang: str):
    b = InlineKeyboardBuilder()
    nxt = NEXT_STATUS.get(order.status)
    if nxt:
        b.button(
            text="➡️ " + t(f"status_{nxt}", lang),
            callback_data=f"ostat:{order.id}:{nxt}",
        )
    if order.status in CANCELLABLE and order.status != "awaiting_payment":
        b.button(text=t("cancel", lang), callback_data=f"ostat:{order.id}:cancelled")
    b.adjust(1)
    return b.as_markup() if (nxt or order.status in CANCELLABLE) else None


def buyer_order_kb(order, lang: str):
    b = InlineKeyboardBuilder()
    if order.status == "shipped":
        b.button(text=t("confirm_received", lang), callback_data=f"recv:{order.id}")
    elif order.status == "awaiting_payment" and order.payment_status == "awaiting":
        b.button(text=t("pay_send_proof", lang), callback_data=f"payproof:{order.id}")
        b.button(text=t("btn_cancel_order", lang), callback_data=f"bcancel:{order.id}")
    elif order.status == "delivered":
        b.button(text=t("btn_rate", lang), callback_data=f"rateform:{order.id}")
    elif order.status == "awaiting_payment" and order.payment_status == "review":
        b.button(text=t("btn_dispute", lang), callback_data=f"dispute:{order.id}")
    else:
        return None
    b.adjust(1)
    return b.as_markup()
