from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DeliveryZone

ADDIS_SUBCITIES = [
    "Bole",
    "Yeka",
    "Kirkos",
    "Arada",
    "Lideta",
    "Addis Ketema",
    "Gulele",
    "Kolfe Keranio",
    "Nifas Silk-Lafto",
    "Akaki Kaliti",
    "Lemi Kura",
]


async def list_zones(session: AsyncSession, shop_id: int) -> list[DeliveryZone]:
    return list(
        (
            await session.scalars(
                select(DeliveryZone)
                .where(DeliveryZone.shop_id == shop_id, DeliveryZone.active.is_(True))
                .order_by(DeliveryZone.id)
            )
        ).all()
    )


async def add_zone(
    session: AsyncSession, shop_id: int, name: str, fee: Decimal
) -> DeliveryZone:
    z = DeliveryZone(shop_id=shop_id, name=name[:60], fee=fee)
    session.add(z)
    await session.flush()
    return z


async def add_addis_preset(session: AsyncSession, shop_id: int, fee: Decimal) -> int:
    """Create one zone per Addis Ababa sub-city (skips names the shop already has)."""
    existing = {z.name.lower() for z in await list_zones(session, shop_id)}
    added = 0
    for name in ADDIS_SUBCITIES:
        if name.lower() not in existing:
            session.add(DeliveryZone(shop_id=shop_id, name=name, fee=fee))
            added += 1
    await session.flush()
    return added
