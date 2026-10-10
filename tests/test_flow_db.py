"""Integration test of the payment flow against a real Postgres.
Run:  TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:5432/markettest pytest tests/test_flow_db.py"""
import asyncio
import os
from datetime import timedelta
from decimal import Decimal

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def test_payment_flow():
    os.environ["DATABASE_URL"] = URL
    os.environ.setdefault("BOT_TOKEN", "1:a")
    asyncio.run(_run())


async def _run():
    from sqlalchemy.exc import IntegrityError
    from app.core.db import Base, engine, init_db, session_factory
    from app.models import Payment, Product, Shop, ShopPaymentAccount, User
    from app.payments import registry
    from app.payments.base import FetchError, Receipt
    from app.services import cart, orders
    from app.services import payments as pay
    from app.services.payments import utcnow

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
    await init_db()

    async with session_factory() as s:
        owner, buyer = User(telegram_id=1, name="Seller"), User(telegram_id=2, name="Buyer", phone="+251911000000")
        s.add_all([owner, buyer]); await s.flush()
        shop = Shop(owner_id=owner.id, slug="abebe", name="Abebe", phone="+251911223344", city="Addis", status="active")
        s.add(shop); await s.flush()
        tele = ShopPaymentAccount(shop_id=shop.id, provider="telebirr", account_number="251911223344", holder_name="Abebe Kebede Tadesse")
        bank = ShopPaymentAccount(shop_id=shop.id, provider="other", label="Awash", account_number="01320123456", holder_name="Abebe Kebede")
        prod = Product(shop_id=shop.id, title="Shoes", price=Decimal("1500"), stock=5)
        s.add_all([tele, bank, prod]); await s.flush()

        async def new_order(account):
            await cart.add(s, buyer.id, prod)
            return await orders.place_order(s, buyer, buyer.phone, "Bole", None, None, account)

        def fake(receiver="Abebe Kebede Tadesse", acct="2519****3344", amount="1500", fail=None):
            async def f(ref, suffix):
                if fail: raise FetchError(fail)
                return Receipt("telebirr", ref, Decimal(amount), True, "Hanna", receiver, acct, utcnow() + timedelta(minutes=1))
            return f

        # 1. online order reserves stock and waits for payment
        o1 = await new_order(tele)
        assert (o1.status, o1.payment_status) == ("awaiting_payment", "awaiting") and o1.payment_deadline
        assert prod.stock == 4

        # 2. wrong receiver is rejected; retry with a good receipt verifies (same reference reusable after reject)
        registry.FETCHERS["telebirr"] = fake(acct="2519****9999")
        r = await pay.submit_proof(s, o1, "ce2513001xyt")
        assert (r.kind, r.reason) == ("rejected", "wrong_receiver")
        registry.FETCHERS["telebirr"] = fake()
        r = await pay.submit_proof(s, o1, "CE2513001XYT")
        assert r.kind == "verified" and (o1.status, o1.payment_status) == ("placed", "paid")

        # 3. the same reference can't pay a second order
        o2 = await new_order(tele)
        assert (await pay.submit_proof(s, o2, "CE2513001XYT")).kind == "duplicate"
        assert (await pay.submit_proof(s, o2, "bad")).kind == "bad_format"

        # 4. verifier down -> seller confirmation; denial returns the order to awaiting, approval marks paid
        registry.FETCHERS["telebirr"] = fake(fail="fetch_failed")
        r = await pay.submit_proof(s, o2, "CE9999999AAA")
        assert r.kind == "needs_seller" and o2.payment_status == "review"
        await pay.seller_decide(s, r.payment, o2, received=False)
        assert (o2.payment_status, r.payment.status) == ("awaiting", "rejected")
        r = await pay.submit_proof(s, o2, "CE9999999AAB")
        await pay.seller_decide(s, r.payment, o2, received=True)
        assert (o2.status, o2.payment_status, r.payment.verified_by) == ("placed", "paid", "seller")

        # 5. provider without a verifier ("other" bank) always goes to the seller
        o3 = await new_order(bank)
        r = await pay.submit_proof(s, o3, "AWB-99887766")
        assert (r.kind, r.reason) == ("needs_seller", "no_verifier")

        # 6. unpaid orders expire and stock is restored
        o4 = await new_order(tele)
        stock_before = prod.stock
        o4.payment_deadline = utcnow() - timedelta(minutes=1)
        expired = await pay.expire_unpaid(s)
        assert [e[2] for e in expired] == [o4.code] and o4.status == "cancelled" and prod.stock == stock_before + 1
        assert o3.status == "awaiting_payment"  # orders under seller review are NOT expired

        # 7. DB-level guard: two active payments can never share (provider, reference)
        s.add(Payment(order_id=o3.id, provider="telebirr", reference="CE2513001XYT", status="verified"))
        with pytest.raises(IntegrityError):
            await s.flush()
        await s.rollback()
