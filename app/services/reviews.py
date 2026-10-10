from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, Review, Shop, User


def stars(n: int) -> str:
    return "⭐" * max(0, min(5, int(n)))


def rating_label(shop: Shop) -> str:
    """'⭐ 4.7 (23)' or '' when the shop has no reviews yet."""
    return (
        f"⭐ {Decimal(shop.rating_avg):.1f} ({shop.rating_count})"
        if shop.rating_count
        else ""
    )


async def get_for_order(session: AsyncSession, order_id: int) -> Review | None:
    return await session.scalar(select(Review).where(Review.order_id == order_id))


async def refresh_shop_stats(session: AsyncSession, shop_id: int) -> None:
    avg, count = (
        await session.execute(
            select(func.coalesce(func.avg(Review.rating), 0), func.count()).where(
                Review.shop_id == shop_id, Review.status == "visible"
            )
        )
    ).one()
    shop = await session.get(Shop, shop_id)
    shop.rating_avg, shop.rating_count = Decimal(avg).quantize(Decimal("0.01")), count


async def add_review(
    session: AsyncSession, order: Order, rating: int, comment: str | None = None
) -> Review | None:
    """Only the buyer's DELIVERED order can be reviewed, once. Returns None when not allowed."""
    if (
        order.status != "delivered"
        or not 1 <= rating <= 5
        or await get_for_order(session, order.id)
    ):
        return None
    review = Review(
        order_id=order.id,
        shop_id=order.shop_id,
        buyer_id=order.buyer_id,
        rating=rating,
        comment=comment,
    )
    session.add(review)
    await session.flush()
    await refresh_shop_stats(session, order.shop_id)
    return review


async def list_reviews(
    session: AsyncSession, shop_id: int, limit: int = 5
) -> list[tuple[Review, User]]:
    rows = await session.execute(
        select(Review, User)
        .join(User, User.id == Review.buyer_id)
        .where(Review.shop_id == shop_id, Review.status == "visible")
        .order_by(Review.id.desc())
        .limit(limit)
    )
    return [(r, u) for r, u in rows.all()]
