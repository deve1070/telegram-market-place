"""Seller dashboard API: access control, order handling, payment confirmation, products + photo upload, settings."""

import asyncio
import os
from decimal import Decimal

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from tests.test_webapp import make_init_data

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200


def test_seller_api():
    asyncio.run(_run())


async def _run():
    from sqlalchemy import select
    from app.bot import server
    from app.bot.main import prepare_database
    from app.core.db import Base, engine, init_db, session_factory
    from app.models import (
        DeliveryZone,
        Order,
        Payment,
        Product,
        Shop,
        ShopPaymentAccount,
        User,
    )
    from app.payments import registry
    from app.payments.base import FetchError
    from app.services import cart, orders
    from app.services import payments as pay_svc
    from app.webapp import api as webapp_api
    from app.webapp import seller_api
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
    seller_api._upload_log.clear()

    A, B, BUYER, SUSP = (
        2001,
        2002,
        3001,
        2003,
    )  # seller A, seller B, a buyer, a suspended seller
    async with session_factory() as s:
        ua, ub, ubuyer, usus = (
            User(telegram_id=i, name=n, language="en")
            for i, n in (
                (A, "Abel Sam"),
                (B, "Berhane"),
                (BUYER, "Hanna G"),
                (SUSP, "Sus"),
            )
        )
        s.add_all([ua, ub, ubuyer, usus])
        await s.flush()
        shop_a = Shop(
            owner_id=ua.id,
            slug="alpha",
            name="Alpha",
            phone="+251911000001",
            city="Addis",
            status="active",
            verified=True,
        )
        shop_b = Shop(
            owner_id=ub.id,
            slug="beta",
            name="Beta",
            phone="+251911000002",
            city="Hawassa",
            status="active",
        )
        shop_s = Shop(
            owner_id=usus.id,
            slug="gamma",
            name="Gamma",
            phone="+251911000003",
            city="Adama",
            status="suspended",
        )
        s.add_all([shop_a, shop_b, shop_s])
        await s.flush()
        acct_a = ShopPaymentAccount(
            shop_id=shop_a.id,
            provider="telebirr",
            account_number="251911000001",
            holder_name="Abel Sam",
        )
        s.add_all(
            [
                acct_a,
                Product(
                    shop_id=shop_a.id,
                    category_id=1,
                    title="Shoes",
                    price=1500,
                    stock=5,
                    photo_file_id="F1",
                ),
                Product(
                    shop_id=shop_a.id, category_id=2, title="Radio", price=200, stock=9
                ),
                Product(shop_id=shop_b.id, title="Beta thing", price=5, stock=3),
                DeliveryZone(shop_id=shop_b.id, name="Beta zone", fee=10),
                ShopPaymentAccount(
                    shop_id=shop_b.id,
                    provider="cbe",
                    account_number="1000123456789",
                    holder_name="Berhane",
                ),
            ]
        )
        await s.flush()
        pshoes = await s.scalar(select(Product).where(Product.title == "Shoes"))
        pbeta = await s.scalar(select(Product).where(Product.title == "Beta thing"))

        zone_b = await s.scalar(
            select(DeliveryZone).where(DeliveryZone.name == "Beta zone")
        )

        async def order(shop, product, account=None, zone=None):
            await cart.add(s, ubuyer.id, product)
            o = await orders.place_order(
                s,
                ubuyer,
                "+251922334455",
                "Bole, near Edna Mall",
                9.01,
                38.76,
                account,
                zone,
            )
            await s.flush()
            return o

        o_cod = await order(shop_a, pshoes)  # COD, placed
        o_online = await order(
            shop_a, pshoes, acct_a
        )  # online: will be waiting for the seller's confirmation
        o_beta = await order(shop_b, pbeta, zone=zone_b)  # belongs to the OTHER seller
        r = await pay_svc.submit_proof(s, o_online, "CE1234567AB", "SHOT-1")
        assert r.kind == "needs_seller"
        pay_id = r.payment.id
        await s.commit()
        ids = {
            "cod": o_cod.id,
            "online": o_online.id,
            "beta": o_beta.id,
            "pshoes": pshoes.id,
            "pbeta": pbeta.id,
            "acct_a": acct_a.id,
            "zone_b": (
                await s.scalar(
                    select(DeliveryZone).where(DeliveryZone.name == "Beta zone")
                )
            ).id,
            "acct_b": (
                await s.scalar(
                    select(ShopPaymentAccount).where(
                        ShopPaymentAccount.provider == "cbe"
                    )
                )
            ).id,
        }

    tg = Tg()
    app = server.create_app(tg.bot, tg.dp, secret="s" * 20, path="/webhook/" + "s" * 20)
    client = TestClient(TestServer(app))
    await client.start_server()

    async def call(method, path, uid=A, body=None, data=None, init=None):
        headers = {
            "X-Telegram-Init-Data": init if init is not None else make_init_data(uid)
        }
        r = await client.request(method, path, json=body, data=data, headers=headers)
        return r.status, (
            await r.json() if r.content_type == "application/json" else await r.read()
        )

    try:
        # ---------------- access control
        everywhere = [
            ("GET", "/api/seller/overview"),
            ("GET", "/api/seller/orders"),
            ("GET", "/api/seller/products"),
            ("GET", "/api/seller/settings"),
            ("POST", "/api/seller/products"),
            ("PATCH", "/api/seller/shop"),
            ("POST", f"/api/seller/orders/{ids['cod']}/status"),
        ]
        for m, path in everywhere:
            assert (await call(m, path, init=""))[0] == 401, path  # not signed in
            assert (await call(m, path, BUYER, body={}))[0] == 403, (
                path
            )  # a plain buyer
            assert (await call(m, path, SUSP, body={}))[0] == 403, (
                path
            )  # owner of a suspended shop
        assert (await client.get("/app/seller.js")).status == 200

        # ---------------- overview
        st, d = await call("GET", "/api/seller/overview")
        assert (
            st == 200 and d["shop"]["name"] == "Alpha" and d["shop"]["verified"] is True
        )
        assert d["todo"] == {
            "new": 1,
            "pack": 0,
            "shipped": 0,
            "payments": 1,
            "disputes": 0,
        }
        assert (
            d["stats"]["today"] == 1
            and d["stats"]["sales_30d"] == 0
            and d["plan"]["name"] == "Free"
        )
        assert (d["plan"]["products_used"], d["plan"]["products_limit"]) == (2, 10)
        assert (await call("GET", "/api/seller/overview", B))[1]["todo"][
            "new"
        ] == 1  # B only counts B's own order

        # ---------------- orders: filters, details, isolation
        st, d = await call("GET", "/api/seller/orders")
        assert [o["id"] for o in d["orders"]] == [ids["cod"]]
        o = d["orders"][0]
        assert (
            o["phone"],
            o["address"],
            o["lat"],
            o["buyer"],
            o["next_status"],
            o["can_cancel"],
        ) == ("+251922334455", "Bole, near Edna Mall", 9.01, "Hanna", "accepted", True)
        assert o["items"] == [{"title": "Shoes", "qty": 1, "price": 1500.0}]
        assert [
            x["id"]
            for x in (await call("GET", "/api/seller/orders?filter=payment"))[1][
                "orders"
            ]
        ] == [ids["online"]]
        assert {
            x["id"]
            for x in (await call("GET", "/api/seller/orders?filter=all"))[1]["orders"]
        } == {ids["cod"], ids["online"]}
        assert (await call("GET", "/api/seller/orders?filter=nope"))[0] == 400
        assert (await call("GET", "/api/seller/orders", B))[1]["orders"][0][
            "id"
        ] == ids["beta"]
        for path, body in (
            (f"/api/seller/orders/{ids['beta']}/status", {"status": "accepted"}),
        ):
            assert (await call("POST", path, A, body))[
                0
            ] == 404  # another seller's order
        assert (
            await call(
                "POST", "/api/seller/orders/abc/status", A, {"status": "accepted"}
            )
        )[0] == 404

        # ---------------- order progression + buyer notifications
        sp = lambda oid, status, uid=A: call(
            "POST", f"/api/seller/orders/{oid}/status", uid, {"status": status}
        )
        assert (await sp(ids["cod"], "shipped"))[0] == 409  # can't skip steps
        assert (await sp(ids["cod"], "delivered"))[
            0
        ] == 409  # only the buyer confirms delivery
        assert (await sp(ids["online"], "accepted"))[
            0
        ] == 409  # payment not confirmed yet
        n = len(tg.texts(BUYER))
        for status in ("accepted", "packed", "shipped"):
            st, d = await sp(ids["cod"], status)
            assert st == 200 and d["order"]["status"] == status
        assert len(tg.texts(BUYER)) == n + 3 and "Shipped" in tg.last(
            BUYER
        )  # the buyer hears about every step
        assert d["order"]["next_status"] is None and d["order"]["can_cancel"] is False
        assert (await sp(ids["cod"], "cancelled"))[0] == 409  # too late to cancel

        # ---------------- payment confirmation
        st, d = await call("GET", "/api/seller/orders?filter=payment")
        pend = d["orders"][0]["pending_payment"]
        assert (
            pend["id"] == pay_id
            and pend["reference"] == "CE1234567AB"
            and pend["provider"] == "Telebirr"
            and pend["has_screenshot"]
        )
        st, shot = await call("GET", f"/api/seller/payments/{pay_id}/screenshot")
        assert st == 200 and shot.startswith(b"\xff\xd8")
        assert (await call("GET", f"/api/seller/payments/{pay_id}/screenshot", B))[
            0
        ] == 404  # not B's payment
        assert (
            await call(
                "POST", f"/api/seller/payments/{pay_id}/decide", B, {"received": True}
            )
        )[0] == 404
        assert (
            await call(
                "POST", f"/api/seller/payments/{pay_id}/decide", A, {"received": "yes"}
            )
        )[0] == 400
        n_owner = len(tg.texts(A))
        st, d = await call(
            "POST", f"/api/seller/payments/{pay_id}/decide", A, {"received": True}
        )
        assert (
            st == 200
            and (d["order"]["status"], d["order"]["payment_status"])
            == ("placed", "paid")
            and d["order"]["pending_payment"] is None
        )
        assert (
            any("confirmed your payment" in t_ for t_ in tg.texts(BUYER))
            and len(tg.texts(A)) == n_owner + 1
        )  # buyer told; seller gets the paid order
        assert (
            await call(
                "POST", f"/api/seller/payments/{pay_id}/decide", A, {"received": True}
            )
        )[0] == 409  # decided once
        st, d = await sp(ids["online"], "accepted")
        assert st == 200
        # denial path on a fresh order
        async with session_factory() as s:
            u = await s.scalar(select(User).where(User.telegram_id == BUYER))
            acct = await s.get(ShopPaymentAccount, ids["acct_a"])
            prod = await s.get(Product, ids["pshoes"])
            await cart.add(s, u.id, prod)
            o2 = await orders.place_order(
                s, u, "+251922334455", "Piassa", None, None, acct
            )
            r2 = await pay_svc.submit_proof(s, o2, "CE7654321ZZ")
            await s.commit()
            pay2 = r2.payment.id
        st, d = await call(
            "POST", f"/api/seller/payments/{pay2}/decide", A, {"received": False}
        )
        assert (
            st == 200
            and d["order"]["payment_status"] == "awaiting"
            and any("hasn't arrived" in t_ for t_ in tg.texts(BUYER))
        )
        assert (await call("GET", f"/api/seller/payments/{pay2}/screenshot"))[
            0
        ] == 404  # no screenshot was sent for this one

        # ---------------- products
        st, d = await call("GET", "/api/seller/products")
        assert [p["title"] for p in d["items"]] == ["Radio", "Shoes"] and d[
            "quota"
        ] == {"used": 2, "limit": 10, "unlimited": False}

        async def bad(body, code, status=400):
            st_, d_ = await call("POST", "/api/seller/products", A, body)
            assert (st_, d_["error"]) == (status, code), (body, st_, d_)

        good = {
            "title": "Lamp",
            "price": "90.5",
            "stock": 4,
            "description": "Brass",
            "category_id": 1,
        }
        await bad({**good, "title": "  "}, "title")
        await bad({**good, "price": 0}, "price")
        await bad({**good, "price": -5}, "price")
        await bad({**good, "price": "abc"}, "price")
        await bad({**good, "price": "NaN"}, "price")
        await bad({**good, "price": 10**9}, "price")
        await bad({**good, "stock": -1}, "stock")
        await bad({**good, "stock": "lots"}, "stock")
        await bad({**good, "category_id": 9999}, "category")
        st, d = await call("POST", "/api/seller/products", A, good)
        lamp = d["product"]
        assert (
            st == 200
            and lamp["price"] == 90.5
            and lamp["stock"] == 4
            and lamp["status"] == "active"
            and lamp["category_id"] == 1
        )
        # edit
        st, d = await call(
            "PATCH",
            f"/api/seller/products/{lamp['id']}",
            A,
            {"price": 120, "stock": 0, "title": "Brass lamp", "status": "hidden"},
        )
        assert (
            d["product"]["price"] == 120.0
            and d["product"]["stock"] == 0
            and d["product"]["status"] == "hidden"
        )
        assert (
            await call(
                "PATCH", f"/api/seller/products/{lamp['id']}", A, {"status": "gone"}
            )
        )[0] == 400
        assert (
            await call("PATCH", f"/api/seller/products/{lamp['id']}", B, {"price": 1})
        )[0] == 404  # not B's product
        assert (
            await call("PATCH", f"/api/seller/products/{ids['pbeta']}", A, {"price": 1})
        )[0] == 404
        my_shop_id = (await call("GET", "/api/seller/overview"))[1]["shop"]["id"]
        assert "Brass lamp" not in str(
            (await call("GET", f"/api/shops/{my_shop_id}/products", BUYER))[1]
        )  # hidden from buyers
        assert [
            p["title"]
            for p in (await call("GET", "/api/seller/products?q=lamp"))[1]["items"]
        ] == ["Brass lamp"]  # sellers still see it
        # plan limit: Free = 10 products
        for i in range(7):
            assert (
                await call(
                    "POST", "/api/seller/products", A, {**good, "title": f"Filler {i}"}
                )
            )[0] == 200
        st, d = await call("POST", "/api/seller/products", A, good)
        assert (st, d["error"], d["limit"]) == (402, "plan_limit", 10)

        # ---------------- photo upload
        def upload(data, name="file"):
            f = FormData()
            f.add_field(name, data, filename="p.jpg", content_type="image/jpeg")
            return f

        n_del = len(tg.sent("DeleteMessage"))
        st, d = await call(
            "POST", f"/api/seller/products/{ids['pshoes']}/photo", A, data=upload(JPEG)
        )
        assert st == 200 and d["product"]["has_photo"]
        async with session_factory() as s:
            assert (await s.get(Product, ids["pshoes"])).photo_file_id.startswith(
                "uploaded-"
            )  # new Telegram file_id stored
        assert (
            len(tg.sent("DeleteMessage")) == n_del + 1
        )  # the helper message was removed from the chat
        assert (
            await call(
                "POST",
                f"/api/seller/products/{ids['pshoes']}/photo",
                A,
                data=upload(PNG),
            )
        )[0] == 200
        assert (
            await call(
                "POST",
                f"/api/seller/products/{ids['pshoes']}/photo",
                A,
                data=upload(b"GIF89a" + b"0" * 50),
            )
        )[0] == 415  # not jpeg/png
        assert (
            await call(
                "POST",
                f"/api/seller/products/{ids['pshoes']}/photo",
                A,
                data=upload(b"<html>" + b"0" * 50),
            )
        )[0] == 415
        assert (
            await call(
                "POST",
                f"/api/seller/products/{ids['pshoes']}/photo",
                A,
                data=upload(JPEG + b"0" * 5_100_000),
            )
        )[0] == 413
        assert (
            await call(
                "POST",
                f"/api/seller/products/{ids['pshoes']}/photo",
                A,
                data=upload(JPEG, name="other"),
            )
        )[0] == 400
        assert (
            await call(
                "POST",
                f"/api/seller/products/{ids['pshoes']}/photo",
                B,
                data=upload(JPEG),
            )
        )[0] == 404

        # ---------------- settings
        st, d = await call("GET", "/api/seller/settings")
        assert (
            d["shop"]["slug"] == "alpha"
            and d["share_link"] == "https://t.me/testbot?start=shop_alpha"
        )
        assert [a["provider"] for a in d["accounts"]] == ["telebirr"] and {
            c["name"] for c in d["categories"]
        } >= {"Fashion & Clothing", "Electronics"}
        assert {p["key"] for p in d["providers"]} == {
            "telebirr",
            "mpesa",
            "cbebirr",
            "cbe",
            "boa",
            "dashen",
            "other",
        }
        st, d = await call(
            "PATCH",
            "/api/seller/shop",
            A,
            {
                "description": "Best shoes",
                "city": "Bole",
                "phone": "0944556677",
                "name": "Alpha Shoes",
            },
        )
        assert (
            d["shop"]["description"],
            d["shop"]["city"],
            d["shop"]["phone"],
            d["shop"]["name"],
        ) == ("Best shoes", "Bole", "+251944556677", "Alpha Shoes")
        assert (await call("PATCH", "/api/seller/shop", A, {"phone": "123"}))[1][
            "error"
        ] == "phone"
        assert (await call("PATCH", "/api/seller/shop", A, {"accept_cod": "no"}))[1][
            "error"
        ] == "accept_cod"
        # a zone, then check the buyer API sees it
        assert (
            await call("POST", "/api/seller/zones", A, {"name": "Bole", "fee": -1})
        )[0] == 400
        assert (await call("POST", "/api/seller/zones", A, {"name": "", "fee": 10}))[
            0
        ] == 400
        st, d = await call("POST", "/api/seller/zones", A, {"name": "Bole", "fee": 50})
        zone_id = d["zones"][0]["id"]
        assert d["zones"] == [{"id": zone_id, "name": "Bole", "fee": 50.0}]
        assert (await call("DELETE", f"/api/seller/zones/{ids['zone_b']}", A))[
            0
        ] == 404  # B's zone
        # accounts: normalisation, validation, ownership
        assert (
            await call(
                "POST",
                "/api/seller/accounts",
                A,
                {"provider": "bitcoin", "number": "1", "holder": "x"},
            )
        )[1]["error"] == "provider"
        assert (
            await call(
                "POST",
                "/api/seller/accounts",
                A,
                {"provider": "telebirr", "number": "12", "holder": "x"},
            )
        )[1]["error"] == "number"
        assert (
            await call(
                "POST",
                "/api/seller/accounts",
                A,
                {"provider": "other", "number": "123456789", "holder": "x"},
            )
        )[1]["error"] == "label"
        st, d = await call(
            "POST",
            "/api/seller/accounts",
            A,
            {"provider": "mpesa", "number": "0711 22 33 44", "holder": "Abel Sam"},
        )
        assert d["accounts"][-1]["number"] == "251711223344"
        st, d = await call(
            "POST",
            "/api/seller/accounts",
            A,
            {
                "provider": "other",
                "label": "Awash",
                "number": "0132-0123-456",
                "holder": "Abel",
            },
        )
        assert d["accounts"][-1]["number"] == "01320123456" and d["accounts"][-1][
            "label"
        ].startswith("Awash")
        assert (await call("DELETE", f"/api/seller/accounts/{ids['acct_b']}", A))[
            0
        ] == 404  # B's account
        # COD can only be switched off while another way to pay exists
        st, d = await call("PATCH", "/api/seller/shop", A, {"accept_cod": False})
        assert st == 200 and d["shop"]["accept_cod"] is False
        st, shop_view = await call("GET", f"/api/shops/{my_shop_id}", BUYER)
        assert (
            shop_view["methods"]["cod"] is False
            and len(shop_view["methods"]["accounts"]) == 3
            and shop_view["zones"][0]["name"] == "Bole"
        )
        accts = (await call("GET", "/api/seller/settings"))[1]["accounts"]
        for a in accts[:-1]:
            assert (await call("DELETE", f"/api/seller/accounts/{a['id']}", A))[
                0
            ] == 200
        st, d = await call("DELETE", f"/api/seller/accounts/{accts[-1]['id']}", A)
        assert (st, d["error"]) == (
            409,
            "need_account",
        )  # last way to pay stays while COD is off
        assert (await call("PATCH", "/api/seller/shop", A, {"accept_cod": True}))[
            0
        ] == 200
        assert (await call("DELETE", f"/api/seller/accounts/{accts[-1]['id']}", A))[
            0
        ] == 200
        assert (await call("PATCH", "/api/seller/shop", A, {"accept_cod": False}))[1][
            "error"
        ] == "need_account"  # no account left
        assert (await call("DELETE", f"/api/seller/zones/{zone_id}", A))[1][
            "zones"
        ] == []
    finally:
        await client.close()
        await engine.dispose()
