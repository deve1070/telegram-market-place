import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    DeliveryZone,
    Order,
    OrderItem,
    Product,
    Shop,
    ShopPaymentAccount,
    User,
)
from app.services import cart

# seller-driven transitions; buyer confirms "delivered"
NEXT_STATUS = {"placed": "accepted", "accepted": "packed", "packed": "shipped"}
CANCELLABLE = {"awaiting_payment", "placed", "accepted"}


class OutOfStock(ValueError):
    pass


class BadPaymentMethod(ValueError):
    pass


class BadDeliveryZone(ValueError):
    pass


class ShopUnavailable(ValueError):
    pass


async def _new_code(session: AsyncSession) -> str:
    while True:
        code = f"M-{secrets.randbelow(900000) + 100000}"
        if not await session.scalar(select(Order.id).where(Order.code == code)):
            return code


async def place_order(
    session: AsyncSession,
    user: User,
    phone: str,
    address: str,
    lat: float | None,
    lng: float | None,
    account: ShopPaymentAccount | None = None,
    zone: DeliveryZone | None = None,
) -> Order | None:
    rows = await cart.get_items(session, user.id)
    if not rows:
        return None
    subtotal = Decimal(0)
    for ci, p in rows:
        if p.status != "active" or p.stock < ci.qty:
            raise OutOfStock(p.title)
        subtotal += p.price * ci.qty
    shop_id = rows[0][1].shop_id
    shop = await session.get(Shop, shop_id)
    if (
        shop is None or shop.status != "active"
    ):  # suspended / removed shops can't take new orders
        raise ShopUnavailable()
    if account is not None and (account.shop_id != shop_id or not account.active):
        raise BadPaymentMethod()
    zones_exist = await session.scalar(
        select(func.count())
        .select_from(DeliveryZone)
        .where(DeliveryZone.shop_id == shop_id, DeliveryZone.active.is_(True))
    )
    if (zones_exist and zone is None) or (
        zone is not None and (zone.shop_id != shop_id or not zone.active)
    ):
        raise BadDeliveryZone()
    fee = Decimal(zone.fee) if zone is not None else Decimal(0)
    order = Order(
        code=await _new_code(session),
        buyer_id=user.id,
        shop_id=shop_id,
        phone=phone,
        address_text=address,
        lat=lat,
        lng=lng,
        subtotal=subtotal,
        delivery_fee=fee,
        total=subtotal + fee,
        delivery_zone=zone.name if zone is not None else None,
    )
    if account is None:  # cash on delivery
        order.payment_method, order.payment_status, order.status = (
            "cod",
            "unpaid",
            "placed",
        )
    else:  # pay-then-verify; stock is reserved until the payment window ends
        order.payment_method, order.payment_account_id = account.provider, account.id
        order.payment_status, order.status = "awaiting", "awaiting_payment"
        order.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
        order.payment_deadline = order.created_at + timedelta(
            minutes=settings.payment_window_minutes
        )
    session.add(order)
    await session.flush()
    for ci, p in rows:
        p.stock -= ci.qty
        session.add(
            OrderItem(
                order_id=order.id,
                product_id=p.id,
                title=p.title,
                price=p.price,
                qty=ci.qty,
            )
        )
    await cart.clear(session, user.id)
    await session.flush()
    return order


async def get_order(session: AsyncSession, order_id: int):
    return await session.get(Order, order_id)


async def get_items(session: AsyncSession, order_id: int):
    return (
        await session.scalars(select(OrderItem).where(OrderItem.order_id == order_id))
    ).all()


async def list_buyer_orders(session: AsyncSession, user_id: int, limit: int = 10):
    return (
        await session.scalars(
            select(Order)
            .where(Order.buyer_id == user_id)
            .order_by(Order.id.desc())
            .limit(limit)
        )
    ).all()


async def list_shop_orders(session: AsyncSession, shop_id: int, limit: int = 15):
    return (
        await session.scalars(
            select(Order)
            .where(
                Order.shop_id == shop_id,
                Order.status.notin_(["delivered", "cancelled", "awaiting_payment"]),
            )
            .order_by(Order.id.desc())
            .limit(limit)
        )
    ).all()


async def set_status(session: AsyncSession, order: Order, status: str) -> None:
    if status == "cancelled" and order.status in CANCELLABLE:
        for it in await get_items(session, order.id):  # restock
            p = await session.get(Product, it.product_id)
            if p:
                p.stock += it.qty
    order.status = status
