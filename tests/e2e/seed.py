"""Seeds a scratch database for the Mini App browser test:  python tests/e2e/seed.py"""

import asyncio
from decimal import Decimal

from sqlalchemy import select


async def main():
    from app.bot.main import prepare_database
    from app.core.db import Base, engine, init_db, session_factory
    from app.models import DeliveryZone, Product, Shop, ShopPaymentAccount, User

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
    await init_db()
    await prepare_database()
    async with session_factory() as s:
        owner = User(telegram_id=2001, name="Seller Sam", language="en")
        s.add(owner)
        await s.flush()
        shop = Shop(
            owner_id=owner.id,
            slug="stall",
            name='Awesome <img src=x onerror="window.__xss=1"> Stall',
            phone="+251911223344",
            city="Addis Ababa",
            status="active",
            verified=True,
            description="Shoes & radios <b>bold</b>",
        )
        quiet = Shop(
            owner_id=owner.id,
            slug="quiet",
            name="Quiet Corner",
            phone="+251911223344",
            city="Hawassa",
            status="active",
        )
        s.add_all([shop, quiet])
        await s.flush()
        s.add_all(
            [
                Product(
                    shop_id=shop.id,
                    category_id=1,
                    title="Leather shoes",
                    description="Handmade",
                    price=Decimal("1500"),
                    stock=5,
                    photo_file_id="FILE1",
                ),
                Product(
                    shop_id=shop.id,
                    category_id=2,
                    title="Old radio",
                    price=Decimal("200"),
                    stock=1,
                ),
                Product(
                    shop_id=shop.id,
                    category_id=2,
                    title="Sold out lamp",
                    price=Decimal("90"),
                    stock=0,
                ),
                Product(
                    shop_id=quiet.id,
                    title="Coffee beans",
                    price=Decimal("350"),
                    stock=9,
                ),
                DeliveryZone(shop_id=shop.id, name="Bole", fee=Decimal("50")),
                DeliveryZone(shop_id=shop.id, name="Pickup at shop", fee=Decimal("0")),
                ShopPaymentAccount(
                    shop_id=shop.id,
                    provider="telebirr",
                    account_number="251911223344",
                    holder_name="Abebe Kebede",
                ),
            ]
        )
        await s.commit()
    await engine.dispose()
    print("seeded")


asyncio.run(main())
