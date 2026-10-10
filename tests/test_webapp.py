"""Mini App backend: Telegram login validation + the whole API against a real database and a fake Telegram."""

import asyncio
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

TOKEN = "1:test"  # conftest sets BOT_TOKEN to this
URL = os.environ.get("TEST_DATABASE_URL")


def make_init_data(
    user_id, token=TOKEN, first="Tester", lang="en", auth_date=None, is_bot=False
):
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAHdF6IQ",
        "signature": "sig-is-part-of-the-hashed-data",
        "user": json.dumps(
            {
                "id": user_id,
                "first_name": first,
                "language_code": lang,
                "is_bot": is_bot,
            }
        ),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


# ---------------------------------------------------------------- login validation (no database)
def test_init_data_validation():
    from app.webapp.auth import validate_init_data as v

    ok = v(make_init_data(42), TOKEN)
    assert ok and ok["user"]["id"] == 42
    assert v(make_init_data(42), "9:other-bot-token") is None  # signed for another bot
    assert (
        v(make_init_data(42, auth_date=int(time.time()) - 90000), TOKEN) is None
    )  # older than 24h
    assert v(
        make_init_data(42, auth_date=int(time.time()) - 3600), TOKEN
    )  # 1h old is fine
    assert v(make_init_data(42, is_bot=True), TOKEN) is None
    good = make_init_data(42)
    assert v(good.replace("Tester", "Hacker"), TOKEN) is None  # tampered field
    assert (
        v(good.replace("%22id%22%3A+42", "%22id%22%3A+43"), TOKEN) is None
    )  # impersonating another id
    assert (
        v("hash=abc", TOKEN) is None
        and v("", TOKEN) is None
        and v("garbage", TOKEN) is None
    )
    assert (
        v("&".join(p for p in good.split("&") if not p.startswith("hash=")), TOKEN)
        is None
    )  # no hash


def test_mini_app_buttons_only_with_public_https(monkeypatch):
    from app.bot.keyboards import main_menu
    from app.core.config import settings

    def web_buttons():
        return [b for row in main_menu("en").keyboard for b in row if b.web_app]

    monkeypatch.setattr(settings, "webhook_base_url", "")
    monkeypatch.setattr(settings, "webapp_base_url", "")
    assert (
        settings.webapp_url == "" and web_buttons() == []
    )  # local polling: no Mini App buttons
    monkeypatch.setattr(settings, "webhook_base_url", "http://insecure.example.com")
    assert settings.webapp_url == "" and web_buttons() == []  # Telegram requires https
    monkeypatch.setattr(settings, "webhook_base_url", "https://bot.example.com/")
    assert settings.webapp_url == "https://bot.example.com/app"
    assert [b.web_app.url for b in web_buttons()] == ["https://bot.example.com/app"]
    assert (
        len(main_menu("en").keyboard[0]) == 1 and len(main_menu("en").keyboard) == 4
    )  # app button on its own row
    monkeypatch.setattr(settings, "webhook_base_url", "")
    monkeypatch.setattr(
        settings, "webapp_base_url", "https://tunnel.example.org"
    )  # explicit override for dev tunnels
    assert settings.webapp_url == "https://tunnel.example.org/app"


# ---------------------------------------------------------------- API (real DB)
pytestmark_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytestmark_db
def test_webapp_api():
    asyncio.run(_api())


async def _api():
    from sqlalchemy import select

    from app.bot import server
    from app.bot.handlers.payments import PayProof
    from app.bot.main import prepare_database
    from app.core.db import Base, engine, init_db, session_factory
    from app.models import DeliveryZone, Order, Product, Shop, ShopPaymentAccount, User
    from app.payments import registry
    from app.payments.base import FetchError
    from app.webapp import api as webapp_api
    from tests.harness import Tg

    async def lookup_down(ref, suffix):
        raise FetchError("fetch_failed")

    registry.FETCHERS["telebirr"] = lookup_down

    await engine.dispose()
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
    await init_db()
    await prepare_database()
    webapp_api._photo_cache.clear()
    webapp_api._checkout_log.clear()

    OWNER, BUYER, OTHER = 2001, 3001, 3002
    async with session_factory() as s:
        owner = User(telegram_id=OWNER, name="Seller Sam", language="en")
        s.add(owner)
        await s.flush()
        shop = Shop(
            owner_id=owner.id,
            slug="stall",
            name="Stall <b>One</b>",
            phone="+251911223344",
            city="Addis",
            status="active",
            verified=True,
        )
        other_shop = Shop(
            owner_id=owner.id,
            slug="second",
            name="Second",
            phone="+251911223344",
            city="Hawassa",
            status="active",
        )
        s.add_all([shop, other_shop])
        await s.flush()
        s.add_all(
            [
                Product(
                    shop_id=shop.id,
                    category_id=1,
                    title="Shoes",
                    description="Leather",
                    price=1500,
                    stock=5,
                    photo_file_id="FILE1",
                ),
                Product(
                    shop_id=shop.id,
                    category_id=2,
                    title="Radio 100%",
                    price=200,
                    stock=1,
                ),
                Product(
                    shop_id=shop.id,
                    category_id=2,
                    title="Hidden thing",
                    price=10,
                    stock=1,
                    status="hidden",
                ),
                Product(shop_id=other_shop.id, title="Elsewhere", price=5, stock=3),
                DeliveryZone(shop_id=shop.id, name="Bole", fee=50),
                ShopPaymentAccount(
                    shop_id=shop.id,
                    provider="telebirr",
                    account_number="251911223344",
                    holder_name="Abebe Kebede",
                ),
            ]
        )
        await s.commit()
        sid, osid = shop.id, other_shop.id
        pid = {p.title: p.id for p in (await s.scalars(select(Product))).all()}

    tg = Tg()
    app = server.create_app(tg.bot, tg.dp, secret="s" * 20, path="/webhook/" + "s" * 20)
    client = TestClient(TestServer(app))
    await client.start_server()

    limiter = {"test": False}

    async def call(method, path, uid=BUYER, body=None, init=None):
        if path == "/api/checkout" and not limiter["test"]:
            webapp_api._checkout_log.clear()  # the rate limit has its own test at the end
        headers = {
            "X-Telegram-Init-Data": init if init is not None else make_init_data(uid)
        }
        r = await client.request(method, path, json=body, headers=headers)
        return r.status, (
            await r.json() if r.content_type == "application/json" else None
        )

    try:
        # ---- the chat links into the app: deep link shows an "Open in the shop app" button for THAT shop
        from app.core.config import settings

        settings.webhook_base_url = "https://bot.example.com"
        try:
            await tg.say(BUYER, "/start shop_stall", name="Tester")
            await tg.press(BUYER, "English", name="Tester")
            buttons = [
                b
                for n, d in tg.session.calls
                if d.get("chat_id") == BUYER
                for b in tg._buttons(d)
                if b.get("web_app")
            ]
            assert (
                buttons
                and buttons[-1]["web_app"]["url"]
                == f"https://bot.example.com/app?shop={sid}"
            )
            welcome = next(
                d
                for n, d in tg.session.calls
                if d.get("chat_id") == BUYER
                and "keyboard" in (d.get("reply_markup") or {})
            )
            assert any(
                b.get("web_app")
                for row in welcome["reply_markup"]["keyboard"]
                for b in row
            )  # main menu has the app button
        finally:
            settings.webhook_base_url = ""
            await call("POST", "/api/lang", body={"language": "en"})

        # ---- authentication is enforced everywhere except the page and product photos
        assert (await client.get("/app")).status == 200 and "no-store" in (
            await client.get("/app")
        ).headers["Cache-Control"]
        for path in ("/api/me", "/api/shops", f"/api/shops/{sid}", "/api/orders"):
            assert (await call("GET", path, init=""))[0] == 401
            assert (await call("GET", path, init=make_init_data(BUYER, "9:wrong")))[
                0
            ] == 401
        assert (await call("POST", "/api/checkout", body={}, init="x=1"))[0] == 401

        # ---- me / language
        st, me = await call("GET", "/api/me")
        assert (
            st == 200
            and me["name"] == "Tester"
            and me["language"] == "en"
            and not me["is_seller"]
        )
        assert (await call("GET", "/api/me", OWNER))[1]["is_seller"] is True
        assert (await call("POST", "/api/lang", body={"language": "am"}))[0] == 200
        assert (await call("GET", "/api/me"))[1]["language"] == "am"
        assert (await call("POST", "/api/lang", body={"language": "xx"}))[0] == 400
        await call("POST", "/api/lang", body={"language": "en"})

        # ---- browsing
        st, d = await call("GET", "/api/shops")
        assert [s_["name"] for s_ in d["shops"]] == [
            "Stall <b>One</b>",
            "Second",
        ] and d["shops"][0]["verified"]  # verified first
        st, d = await call("GET", f"/api/shops/{sid}")
        assert [c["name"] for c in d["categories"]] == [
            "Fashion & Clothing",
            "Electronics",
        ]
        assert (
            d["zones"] == [{"id": 1, "name": "Bole", "fee": 50.0}]
            and d["methods"]["cod"] is True
        )
        assert d["methods"]["accounts"][0]["label"].startswith(
            "Telebirr"
        ) and "251911223344" not in json.dumps(d)  # number not exposed
        assert (await call("GET", "/api/shops/9999"))[0] == 404 and (
            await call("GET", "/api/shops/abc")
        )[0] == 404
        st, d = await call("GET", f"/api/shops/{sid}/products")
        assert [p["title"] for p in d["items"]] == ["Radio 100%", "Shoes"] and not d[
            "has_more"
        ]  # hidden product not listed
        assert [
            p["title"]
            for p in (await call("GET", f"/api/shops/{sid}/products?q=shoe"))[1][
                "items"
            ]
        ] == ["Shoes"]
        assert [
            p["title"]
            for p in (await call("GET", f"/api/shops/{sid}/products?q=100%25"))[1][
                "items"
            ]
        ] == ["Radio 100%"]  # % is literal
        assert (
            len((await call("GET", f"/api/shops/{sid}/products?cat=1"))[1]["items"])
            == 1
        )
        assert (await call("GET", f"/api/shops/{sid}/products?page=-1"))[0] == 400
        assert (await call("GET", f"/api/products/{pid['Shoes']}"))[1]["product"][
            "has_photo"
        ] is True
        assert (await call("GET", f"/api/products/{pid['Hidden thing']}"))[0] == 404

        # ---- photo proxy: by product id, only for visible products, cached
        r = await client.get(f"/api/photo/{pid['Shoes']}")
        assert (
            r.status == 200
            and r.content_type == "image/jpeg"
            and (await r.read()).startswith(b"\xff\xd8")
        )
        n_get_file = len(tg.sent("GetFile"))
        assert (await client.get(f"/api/photo/{pid['Shoes']}")).status == 200 and len(
            tg.sent("GetFile")
        ) == n_get_file  # served from cache
        assert (
            await client.get(f"/api/photo/{pid['Radio 100%']}")
        ).status == 404  # no photo
        assert (await client.get(f"/api/photo/{pid['Hidden thing']}")).status == 404
        assert (
            await client.get("/api/photo/FILE1")
        ).status == 404  # file_ids are never accepted

        # ---- checkout validation (every rule is enforced server-side)
        base = {
            "shop_id": sid,
            "items": [{"id": pid["Shoes"], "qty": 2}],
            "phone": "0922334455",
            "address": "Bole, Edna Mall",
            "zone_id": 1,
            "method": "cod",
        }

        async def bad(changes, code, status=400):
            st_, d_ = await call("POST", "/api/checkout", body={**base, **changes})
            assert (st_, d_["error"]) == (status, code), (changes, st_, d_)

        await bad({"phone": "12345"}, "phone")
        await bad({"address": "   "}, "address")
        await bad({"items": []}, "items")
        await bad({"items": [{"id": pid["Shoes"], "qty": 0}]}, "items")
        await bad({"items": [{"id": pid["Shoes"], "qty": 999}]}, "items")
        await bad(
            {"items": [{"id": pid["Elsewhere"], "qty": 1}]}, "items"
        )  # another shop's product
        await bad(
            {"items": [{"id": pid["Shoes"], "qty": 6}]}, "stock", 409
        )  # more than in stock
        await bad({"zone_id": None}, "zone")  # zone mandatory once zones exist
        await bad({"zone_id": 424242}, "zone")
        await bad({"method": 999}, "method")
        await bad({"method": "bitcoin"}, "method")
        await bad({"lat": 200, "lng": 1}, "location")
        assert (await call("POST", "/api/checkout", body={**base, "shop_id": 9999}))[
            0
        ] == 404
        async with session_factory() as s:
            assert (
                await s.scalars(select(Order))
            ).first() is None  # nothing was created by the failures
            assert (await s.get(Product, pid["Shoes"])).stock == 5

        # ---- cash on delivery: price comes from the server (client "price" is ignored), seller is notified
        st, d = await call(
            "POST",
            "/api/checkout",
            body={
                **base,
                "lat": 9.01,
                "lng": 38.76,
                "items": [{"id": pid["Shoes"], "qty": 2, "price": 1}],
            },
        )
        assert (
            st == 200 and d["next"] == "cod" and d["order"]["total"] == 3050.0
        )  # 2 x 1500 + 50 delivery
        assert any("New order" in t_ for t_ in tg.texts(OWNER)) and any(
            "placed" in t_ for t_ in tg.texts(BUYER)
        )
        async with session_factory() as s:
            o = (await s.scalars(select(Order))).one()
            assert (
                o.status,
                o.payment_method,
                o.delivery_zone,
                float(o.delivery_fee),
            ) == ("placed", "cod", "Bole", 50.0)
            assert (await s.get(Product, pid["Shoes"])).stock == 3
            assert (
                await s.scalar(select(User).where(User.telegram_id == BUYER))
            ).phone == "+251922334455"

        # ---- online payment: order waits for payment and the CHAT takes over (state + instructions)
        st, d = await call(
            "POST",
            "/api/checkout",
            body={**base, "method": 1, "items": [{"id": pid["Radio 100%"], "qty": 1}]},
        )
        assert st == 200 and d["next"] == "pay" and d["order"]["total"] == 250.0
        assert any("Pay" in t_ and "251911223344" in t_ for t_ in tg.texts(BUYER))
        ctx = tg.dp.fsm.get_context(tg.bot, chat_id=BUYER, user_id=BUYER)
        assert await ctx.get_state() == PayProof.waiting.state
        pay_order_id = d["order"]["id"]
        n_msgs = len(tg.texts(BUYER))
        assert (await call("POST", f"/api/orders/{pay_order_id}/pay"))[
            0
        ] == 200 and len(tg.texts(BUYER)) == n_msgs + 1  # instructions re-sent
        # the buyer's reference, sent in the chat, goes through the normal bot verification (lookup is down -> seller confirms)
        await tg.say(BUYER, "My reference is XX123456YY")
        assert any("seller was asked" in t_ for t_ in tg.texts(BUYER)[-2:]) and any(
            "confirm this payment" in t_ for t_ in tg.texts(OWNER)
        )
        assert (await call("POST", f"/api/orders/{pay_order_id}/pay"))[
            0
        ] == 409  # now it is the seller's turn
        assert (await call("POST", f"/api/orders/{pay_order_id}/cancel"))[0] == 409
        assert (
            await call(
                "POST",
                "/api/checkout",
                body={**base, "items": [{"id": pid["Radio 100%"], "qty": 1}]},
            )
        )[1]["error"] == "stock"  # 1 left was reserved

        # ---- orders list, isolation between users
        st, d = await call("GET", "/api/orders")
        assert [o_["status"] for o_ in d["orders"]] == [
            "awaiting_payment",
            "placed",
        ]  # newest first
        cod_id = next(o_["id"] for o_ in d["orders"] if o_["payment_method"] == "cod")
        assert (await call("GET", "/api/orders", OTHER))[1]["orders"] == []
        for action in ("received", "cancel", "pay"):
            assert (await call("POST", f"/api/orders/{cod_id}/{action}", OTHER))[
                0
            ] == 404  # can't touch someone else's order
        assert (await call("POST", f"/api/orders/{cod_id}/received"))[
            0
        ] == 409  # not shipped yet
        assert (await call("POST", f"/api/orders/{cod_id}/review", body={"rating": 5}))[
            0
        ] == 409  # not delivered yet

        # ---- unpaid order: cancel gives the stock back (below)
        # ---- delivery: seller ships, buyer confirms and reviews in the app
        async with session_factory() as s:
            (await s.get(Order, cod_id)).status = "shipped"
            await s.commit()
        assert (await call("GET", "/api/orders"))[1]["orders"][-1][
            "can_receive"
        ] is True
        assert (await call("POST", f"/api/orders/{cod_id}/received"))[0] == 200
        assert any("delivered" in t_.lower() for t_ in tg.texts(OWNER))
        assert (await call("POST", f"/api/orders/{cod_id}/review", body={"rating": 9}))[
            0
        ] == 400
        assert (
            await call(
                "POST",
                f"/api/orders/{cod_id}/review",
                body={"rating": 5, "comment": "Great <script>"},
            )
        )[0] == 200
        assert (await call("POST", f"/api/orders/{cod_id}/review", body={"rating": 4}))[
            0
        ] == 409  # one review per order
        st, d = await call("GET", f"/api/shops/{sid}/reviews")
        assert d["reviews"][0]["rating"] == 5 and d["reviews"][0]["name"] == "Tester"
        assert (await call("GET", f"/api/shops/{sid}"))[1]["shop"]["rating"] == 5.0
        async with session_factory() as s:
            stock_before = (await s.get(Product, pid["Shoes"])).stock
        st, d = await call(
            "POST",
            "/api/checkout",
            body={**base, "method": 1, "items": [{"id": pid["Shoes"], "qty": 1}]},
        )
        assert st == 200
        async with session_factory() as s:
            assert (
                await s.get(Product, pid["Shoes"])
            ).stock == stock_before - 1  # reserved while waiting for payment
        assert (await call("POST", f"/api/orders/{d['order']['id']}/cancel"))[0] == 200
        async with session_factory() as s:
            assert (
                await s.get(Product, pid["Shoes"])
            ).stock == stock_before  # ... and given back on cancel
            assert (await s.get(Order, d["order"]["id"])).status == "cancelled"

        # ---- suspended shops disappear and can't be ordered from
        async with session_factory() as s:
            (await s.get(Shop, sid)).status = "suspended"
            await s.commit()
        assert [s_["name"] for s_ in (await call("GET", "/api/shops"))[1]["shops"]] == [
            "Second"
        ]
        assert (await call("POST", "/api/checkout", body=base))[1][
            "error"
        ] == "shop_not_found"
        assert (
            await client.get(f"/api/photo/{pid['Shoes']}")
        ).status == 200  # (cached copy) ... but a fresh process would 404:
        webapp_api._photo_cache.clear()
        assert (await client.get(f"/api/photo/{pid['Shoes']}")).status == 404

        # ---- checkout rate limit (5/min per user)
        limiter["test"] = True
        webapp_api._checkout_log.clear()
        codes = [
            (await call("POST", "/api/checkout", OTHER, body={"shop_id": osid}))[0]
            for _ in range(7)
        ]
        assert codes[:5] == [400] * 5 and codes[5:] == [429, 429]
    finally:
        await client.close()
        await engine.dispose()
