"""End-to-end journey through the REAL handlers/middlewares/database with a fake Telegram (see harness.py).
Run with TEST_DATABASE_URL set (it drops and recreates the tables of that database)."""

import asyncio
import os

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
SELLER, BUYER, ADMIN = 1001, 1002, 9001


def test_full_journey():
    asyncio.run(_journey())


async def _journey():
    from sqlalchemy import select
    from app.core.db import Base, engine, init_db, session_factory
    from app.models import Order, Product, Report, Review, SellerVerification, Shop
    from tests.harness import Tg

    await (
        engine.dispose()
    )  # drop pooled connections created in another test's event loop
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
    await init_db()
    from app.bot.main import prepare_database

    await prepare_database()
    tg = Tg()

    async def db(coro_fn):
        async with session_factory() as s:
            return await coro_fn(s)

    async def shop():
        return await db(lambda s: s.scalar(select(Shop).where(Shop.slug == "awesome")))

    # ---------------- onboarding + shop registration + admin approval ----------------
    for uid in (SELLER, BUYER):
        await tg.say(uid, "/start")
        await tg.press(uid, "English")
    await tg.say(SELLER, "🏪 Sell / My shop")
    for answer in ("Awesome Shop", "awesome", "0911223344", "Addis Ababa"):
        await tg.say(SELLER, answer)
    assert "Shop request" in tg.last(ADMIN)
    await tg.press(ADMIN, "Approve")
    assert "approved" in tg.last(SELLER)

    # seller menu shows plan + verification entry; add a product with the caption shortcut
    await tg.say(SELLER, "🏪 Sell / My shop")
    assert "Plan:" in tg.last(SELLER) and tg.find_button(SELLER, "Get verified")
    await tg.press(SELLER, "Add product")
    await tg.photo(SELLER, caption="Shoes | 1500 | 5")
    await tg.press(SELLER, "Fashion")
    assert "saved" in tg.last(SELLER)

    # ---------------- seller verification: reject (with reason), resubmit, approve ----------------
    async def submit_verification():
        await tg.press(SELLER, "Get verified")
        await tg.press(SELLER, "National ID")
        await tg.say(SELLER, "Abebe Kebede Tadesse")
        await tg.say(SELLER, "here is my id")  # text where a photo is expected
        assert "send a photo" in tg.last(SELLER)
        await tg.photo(SELLER)
        await tg.photo(SELLER)
        await tg.press(SELLER, "Skip")
        assert "Submitted" in tg.last(SELLER)

    await submit_verification()
    assert len(tg.sent("SendMediaGroup")) == 1 and "Seller verification" in tg.last(
        ADMIN
    )
    await tg.click(SELLER, "vk:ok:1")  # a seller can NOT approve themselves
    assert not (await shop()).verified
    await tg.press(ADMIN, "Reject")
    await tg.press(ADMIN, "blurry")
    assert "blurry" in tg.last(SELLER)
    deleted = [
        d for n, d in tg.session.calls if n == "DeleteMessage" and d["chat_id"] == ADMIN
    ]
    assert len(deleted) == 2  # the ID + selfie photos are removed from the admin's chat
    v1 = await db(lambda s: s.get(SellerVerification, 1))
    assert (
        v1.status == "rejected" and v1.id_photo is None and v1.selfie_photo is None
    )  # photos erased after the decision

    await tg.say(SELLER, "🏪 Sell / My shop")
    assert "rejected" in tg.last(SELLER)
    await submit_verification()
    await tg.press(ADMIN, "Approve")
    assert "verified" in tg.last(SELLER).lower() and (await shop()).verified
    assert (await db(lambda s: s.get(SellerVerification, 2))).decided_by == ADMIN

    # ---------------- buyer: deep link, badge, order (cash on delivery), delivery, review ----------------
    await tg.say(BUYER, "/start shop_awesome")
    assert "Verified seller" in tg.last(BUYER)
    await tg.say(BUYER, "🛍 Shops")
    assert any("✔️" in b["text"] for b in tg._buttons(tg.session.calls[-1][1]))
    await tg.press(BUYER, "All products")
    await tg.press(BUYER, "Shoes")
    await tg.press(BUYER, "Add to cart")
    await tg.say(BUYER, "🛒 Cart")
    await tg.press(BUYER, "Checkout")
    await tg.say(BUYER, "0922334455")
    await tg.say(BUYER, "Bole, near Edna Mall")
    await tg.press(BUYER, "Cash on delivery")
    assert "placed" in tg.last(BUYER) and "New order" in tg.last(SELLER)
    for step in ("Accepted", "Packed", "Shipped"):
        await tg.press(SELLER, step)
    assert "Shipped" in tg.last(BUYER)
    await tg.press(BUYER, "I received it")
    await tg.press(BUYER, "5⭐")
    await tg.say(BUYER, "Great seller")
    assert "Great seller" in tg.last(SELLER)
    s = await shop()
    assert (s.rating_count, float(s.rating_avg)) == (1, 5.0)
    order = await db(lambda s_: s_.scalar(select(Order)))
    assert (
        order.status == "delivered"
        and order.payment_status == "paid"
        and order.address_text.startswith("Bole")
    )

    # ---------------- reports: dedupe + alert at 3 distinct reporters ----------------
    await tg.press(BUYER, "Report")
    await tg.press(BUYER, "Scam")
    assert "Thanks" in tg.last(BUYER)
    await tg.press(BUYER, "Report")
    await tg.press(BUYER, "Scam")
    assert "already" in tg.last(BUYER)
    await tg.click(1003, f"rp:{s.id}:fake")
    assert "3 open reports" not in tg.last(ADMIN)
    await tg.click(1004, f"rp:{s.id}:abuse")
    assert "3 open reports" in tg.last(ADMIN)
    await tg.click(SELLER, f"rp:{s.id}:scam")  # owners can't report their own shop
    assert (
        await db(lambda s_: s_.scalar(select(Report).where(Report.reporter_id == 1)))
        is None
    )

    # ---------------- admin panel ----------------
    await tg.say(BUYER, "/admin")  # non-admins get nothing
    assert "Admin panel" not in tg.last(BUYER)
    n_before = len(tg.texts(BUYER))
    await tg.click(BUYER, "ap:stats")
    await tg.click(BUYER, "ap:sus:1")
    assert len(tg.texts(BUYER)) == n_before and (await shop()).status == "active"

    await tg.say(ADMIN, "/admin")
    assert "Admin panel" in tg.last(ADMIN) and tg.find_button(ADMIN, "Reports (3)")
    await tg.press(ADMIN, "Stats")
    assert "Users:" in tg.last(
        ADMIN
    ) and "Delivered sales value: 1,500.00 ETB" in tg.last(ADMIN)
    await tg.say(ADMIN, "/admin")
    await tg.press(ADMIN, "Reports")
    assert "Awesome Shop" in tg.last(ADMIN) and "3 report" in tg.last(ADMIN)
    await tg.press(ADMIN, "Open Awesome")
    assert "many open reports" in tg.last(ADMIN) and "verified" in tg.last(ADMIN)

    # suspend -> shop disappears, buyers can't order, owner is told; reactivate -> everything returns
    await tg.press(ADMIN, "Suspend")
    await tg.press(ADMIN, "Yes, suspend")
    assert "suspended" in tg.last(SELLER) and (await shop()).status == "suspended"
    await tg.say(BUYER, "🛍 Shops")
    assert "No shops" in tg.last(BUYER)
    await tg.press(BUYER, "Add to cart")
    await tg.say(BUYER, "🛒 Cart")
    await tg.press(BUYER, "Checkout")
    await tg.say(BUYER, "Bole again")
    await tg.press(BUYER, "Cash on delivery")
    assert "unavailable" in tg.alerts()[-1]
    assert (
        len((await db(lambda s_: s_.scalars(select(Order)))).all()) == 1
    )  # no new order from the suspended shop
    await tg.press(ADMIN, "Reactivate")
    assert "active again" in tg.last(SELLER) and (await shop()).status == "active"

    # dismiss reports; manual badge toggle
    await tg.press(ADMIN, "Dismiss reports")
    assert (
        await db(lambda s_: s_.scalar(select(Report.id).where(Report.status == "open")))
        is None
    )
    await tg.press(ADMIN, "Remove badge")
    assert not (await shop()).verified
    await tg.press(ADMIN, "Mark verified")
    assert (await shop()).verified

    # review moderation keeps the shop's average in sync
    await tg.say(ADMIN, "/admin")
    await tg.press(ADMIN, "Review moderation")
    assert "Great seller" in tg.last(ADMIN)
    await tg.press(ADMIN, "Hide")
    assert (await shop()).rating_count == 0
    await tg.press(ADMIN, "Show")
    assert (await shop()).rating_count == 1 and float((await shop()).rating_avg) == 5.0

    # shop list filters
    await tg.say(ADMIN, "/admin")
    await tg.press(ADMIN, "Shops")
    await tg.press(ADMIN, "Suspended")
    await tg.press(ADMIN, "Active")
    markup = next(
        d["reply_markup"]
        for n, d in reversed(tg.session.calls)
        if d.get("chat_id") == ADMIN and "reply_markup" in d
    )
    assert "Awesome Shop" in str(markup)
    await tg.say(ADMIN, "/verifications")
    assert "No verifications" in tg.last(ADMIN)

    # every callback_data the bot ever produced fits Telegram's 64-byte limit
    for name, data in tg.session.calls:
        for b in tg._buttons(data):
            assert len(b.get("callback_data", "").encode()) <= 64, b
    await engine.dispose()
