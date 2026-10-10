from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CartItem, Product


async def get_items(session: AsyncSession, user_id: int) -> list[tuple[CartItem, Product]]:
    rows = await session.execute(
        select(CartItem, Product).join(Product, Product.id == CartItem.product_id)
        .where(CartItem.user_id == user_id).order_by(CartItem.id)
    )
    return [(c, p) for c, p in rows.all()]


async def clear(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(CartItem).where(CartItem.user_id == user_id))


async def add(session: AsyncSession, user_id: int, product: Product) -> tuple[bool, bool]:
    """Returns (ok, cart_was_reset). A cart holds items from ONE shop only."""
    reset = False
    items = await get_items(session, user_id)
    if items and items[0][1].shop_id != product.shop_id:
        await clear(session, user_id)
        items, reset = [], True
    existing = next((c for c, p in items if p.id == product.id), None)
    new_qty = (existing.qty if existing else 0) + 1
    if product.status != "active" or new_qty > product.stock:
        return False, reset
    if existing:
        existing.qty = new_qty
    else:
        session.add(CartItem(user_id=user_id, product_id=product.id, qty=1))
    return True, reset
