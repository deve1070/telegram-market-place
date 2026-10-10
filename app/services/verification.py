"""Seller identity verification (KYC-lite): ID photo + selfie holding the ID (+ optional trade license) -> admin decision."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import SellerVerification, Shop

DOC_TYPES = ("national_id", "passport", "driver_license", "kebele_id")
REJECT_REASONS = ("blurry", "mismatch", "selfie", "invalid", "other")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def latest_for_shop(
    session: AsyncSession, shop_id: int
) -> SellerVerification | None:
    return await session.scalar(
        select(SellerVerification)
        .where(SellerVerification.shop_id == shop_id)
        .order_by(SellerVerification.id.desc())
        .limit(1)
    )


async def status_for_shop(session: AsyncSession, shop: Shop) -> str:
    """'verified' | 'pending' | 'rejected' | 'none'"""
    if shop.verified:
        return "verified"
    v = await latest_for_shop(session, shop.id)
    return v.status if v and v.status in ("pending", "rejected") else "none"


async def create_submission(
    session: AsyncSession,
    shop: Shop,
    doc_type: str,
    full_name: str,
    id_photo: str,
    selfie_photo: str,
    license_photo: str | None = None,
) -> SellerVerification | None:
    """None when the shop is already verified, a submission is already pending, or the input is invalid."""
    if shop.verified or doc_type not in DOC_TYPES or not full_name.strip():
        return None
    existing = await latest_for_shop(session, shop.id)
    if existing and existing.status == "pending":
        return None
    v = SellerVerification(
        shop_id=shop.id,
        doc_type=doc_type,
        full_name=full_name.strip()[:100],
        id_photo=id_photo,
        selfie_photo=selfie_photo,
        license_photo=license_photo,
    )
    session.add(v)
    await session.flush()
    return v


async def list_pending(session: AsyncSession) -> list[SellerVerification]:
    return list(
        (
            await session.scalars(
                select(SellerVerification)
                .where(SellerVerification.status == "pending")
                .order_by(SellerVerification.id)
            )
        ).all()
    )


def _finish(v: SellerVerification, admin_tg_id: int) -> None:
    v.decided_at, v.decided_by = _now(), admin_tg_id
    if (
        not settings.kyc_keep_documents
    ):  # data minimisation: we only need the decision, not the photos
        v.id_photo = v.selfie_photo = v.license_photo = None


async def approve(
    session: AsyncSession, v: SellerVerification, admin_tg_id: int
) -> Shop | None:
    if v.status != "pending":
        return None
    v.status = "approved"
    shop = await session.get(Shop, v.shop_id)
    shop.verified = True
    _finish(v, admin_tg_id)
    return shop


async def reject(
    session: AsyncSession, v: SellerVerification, admin_tg_id: int, reason: str
) -> Shop | None:
    if v.status != "pending" or reason not in REJECT_REASONS:
        return None
    v.status, v.reject_reason = "rejected", reason
    _finish(v, admin_tg_id)
    return await session.get(Shop, v.shop_id)
