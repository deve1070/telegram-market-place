"""Admin-side trust & safety: reports, shop suspension, review moderation, platform stats, risk signals."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Order, Product, Report, Review, SellerVerification, Shop, User
from app.services import reviews as reviews_svc

REPORT_REASONS = ("scam", "fake", "abuse", "other")
SHOP_FILTERS = ("pending", "active", "suspended", "all")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- reports ----------
async def create_report(
    session: AsyncSession, shop: Shop, reporter: User, reason: str
) -> tuple[bool, int]:
    """-> (created, distinct open reporters). created=False for a duplicate open report or an invalid reason."""
    if reason not in REPORT_REASONS or shop.owner_id == reporter.id:
        return False, await open_reporter_count(session, shop.id)
    try:
        async with session.begin_nested():
            session.add(Report(shop_id=shop.id, reporter_id=reporter.id, reason=reason))
            await session.flush()
    except IntegrityError:
        return False, await open_reporter_count(session, shop.id)
    return True, await open_reporter_count(session, shop.id)


async def open_reporter_count(session: AsyncSession, shop_id: int) -> int:
    return (
        await session.scalar(
            select(func.count(func.distinct(Report.reporter_id))).where(
                Report.shop_id == shop_id, Report.status == "open"
            )
        )
        or 0
    )


def crossed_threshold(count_after: int) -> bool:
    return (
        count_after == settings.report_alert_threshold
    )  # alert once, when the threshold is reached


async def open_reports_by_shop(
    session: AsyncSession,
) -> list[tuple[Shop, int, dict[str, int]]]:
    rows = (
        await session.execute(
            select(Report, Shop)
            .join(Shop, Shop.id == Report.shop_id)
            .where(Report.status == "open")
            .order_by(Report.shop_id, Report.id)
        )
    ).all()
    grouped: dict[int, tuple[Shop, dict[str, int]]] = {}
    for r, shop in rows:
        reasons = grouped.setdefault(shop.id, (shop, {}))[1]
        reasons[r.reason] = reasons.get(r.reason, 0) + 1
    out = [(s, sum(c.values()), c) for s, c in grouped.values()]
    return sorted(out, key=lambda x: -x[1])


async def dismiss_reports(session: AsyncSession, shop_id: int) -> int:
    rows = (
        await session.scalars(
            select(Report).where(Report.shop_id == shop_id, Report.status == "open")
        )
    ).all()
    for r in rows:
        r.status = "dismissed"
    return len(rows)


# ---------- shops ----------
async def list_shops(
    session: AsyncSession, flt: str, page: int, per: int = 8
) -> tuple[list[Shop], int]:
    q = select(Shop)
    if flt in ("pending", "active", "suspended"):
        q = q.where(Shop.status == flt)
    total = await session.scalar(select(func.count()).select_from(q.subquery()))
    rows = (
        await session.scalars(q.order_by(Shop.id.desc()).limit(per).offset(page * per))
    ).all()
    return list(rows), total


async def set_shop_status(shop: Shop, status: str) -> bool:
    """Only active <-> suspended transitions here (approving/rejecting new shops lives in the shop-request flow)."""
    if (shop.status, status) in (("active", "suspended"), ("suspended", "active")):
        shop.status = status
        return True
    return False


async def risk_signals(session: AsyncSession, shop: Shop) -> dict:
    async def count(model, *where):
        return (
            await session.scalar(select(func.count()).select_from(model).where(*where))
            or 0
        )

    return {
        "orders": await count(Order, Order.shop_id == shop.id),
        "delivered": await count(
            Order, Order.shop_id == shop.id, Order.status == "delivered"
        ),
        "disputes_open": await count(
            Order, Order.shop_id == shop.id, Order.payment_status == "disputed"
        ),
        "reports_open": await open_reporter_count(session, shop.id),
        "products": await count(Product, Product.shop_id == shop.id),
        "age_days": max(0, (_now() - shop.created_at).days) if shop.created_at else 0,
    }


# ---------- reviews ----------
async def recent_reviews(
    session: AsyncSession, page: int, per: int = 5
) -> tuple[list[tuple[Review, Shop, User]], int]:
    total = await session.scalar(select(func.count()).select_from(Review)) or 0
    rows = (
        await session.execute(
            select(Review, Shop, User)
            .join(Shop, Shop.id == Review.shop_id)
            .join(User, User.id == Review.buyer_id)
            .order_by(Review.id.desc())
            .limit(per)
            .offset(page * per)
        )
    ).all()
    return [(r, s, u) for r, s, u in rows], total


async def set_review_status(session: AsyncSession, review: Review, status: str) -> bool:
    if status not in ("visible", "hidden") or review.status == status:
        return False
    review.status = status
    await reviews_svc.refresh_shop_stats(
        session, review.shop_id
    )  # the shop's average must follow
    return True


# ---------- platform stats ----------
async def stats(session: AsyncSession) -> dict:
    async def count(model, *where):
        return (
            await session.scalar(select(func.count()).select_from(model).where(*where))
            or 0
        )

    week = _now() - timedelta(days=7)
    gmv = (
        await session.scalar(
            select(func.coalesce(func.sum(Order.total), 0)).where(
                Order.status == "delivered"
            )
        )
        or 0
    )
    return {
        "users": await count(User),
        "shops_active": await count(Shop, Shop.status == "active"),
        "shops_pending": await count(Shop, Shop.status == "pending"),
        "shops_suspended": await count(Shop, Shop.status == "suspended"),
        "shops_verified": await count(Shop, Shop.verified.is_(True)),
        "shops_paid": await count(
            Shop, Shop.plan_key != "free", Shop.plan_expires_at > _now()
        ),
        "products": await count(Product),
        "orders": await count(Order),
        "orders_7d": await count(Order, Order.created_at > week),
        "delivered": await count(Order, Order.status == "delivered"),
        "gmv_delivered": Decimal(gmv),
        "disputes": await count(Order, Order.payment_status == "disputed"),
        "verifications_pending": await count(
            SellerVerification, SellerVerification.status == "pending"
        ),
        "reports_open": await count(Report, Report.status == "open"),
        "reviews": await count(Review),
    }
