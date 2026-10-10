"""Tests for the production layer: rate limiting, webhook secret checking, health endpoint, settings validation."""

import asyncio
import os

import pytest
from aiohttp.test_utils import TestClient, TestServer

SECRET = "a" * 8 + "B" * 8 + "_-" + "9" * 8
HARMLESS = {
    "update_id": 1,
    "edited_channel_post": {
        "message_id": 1,
        "date": 1,
        "chat": {"id": 1, "type": "channel", "title": "x"},
    },
}


class FakeRedis:
    def __init__(self, fail=False):
        self.data, self.ttl, self.fail = {}, {}, fail

    async def incr(self, k):
        if self.fail:
            raise ConnectionError("redis down")
        self.data[k] = self.data.get(k, 0) + 1
        return self.data[k]

    async def expire(self, k, s):
        self.ttl[k] = s


class User:
    def __init__(self, id):
        self.id = id


def run(coro):
    return asyncio.run(coro)


def test_throttle_limits_per_user_and_exempts_admins():
    from app.bot.middlewares.throttle import ThrottleMiddleware

    r = FakeRedis()
    mw = ThrottleMiddleware(r, limit=3, window=5, exempt={99})
    calls = []

    async def handler(event, data):
        calls.append(data["event_from_user"].id)
        return "ok"

    async def go():
        out = [
            await mw(handler, object(), {"event_from_user": User(1)}) for _ in range(5)
        ]
        out_admin = [
            await mw(handler, object(), {"event_from_user": User(99)})
            for _ in range(10)
        ]
        out_other = await mw(handler, object(), {"event_from_user": User(2)})
        return out, out_admin, out_other

    out, out_admin, out_other = run(go())
    assert out == ["ok", "ok", "ok", None, None]  # 4th and 5th updates dropped
    assert all(x == "ok" for x in out_admin)  # admins never throttled
    assert out_other == "ok"  # another user unaffected
    assert (
        r.ttl["thr:1"] == 5 and "thr:99" not in r.data
    )  # window set once; admins not even counted


def test_throttle_fails_open_when_redis_is_down():
    from app.bot.middlewares.throttle import ThrottleMiddleware

    mw = ThrottleMiddleware(FakeRedis(fail=True), limit=1)

    async def handler(event, data):
        return "ok"

    assert run(mw(handler, object(), {"event_from_user": User(1)})) == "ok"


def test_webhook_settings_validation(monkeypatch):
    from app.bot import server
    from app.core.config import settings

    monkeypatch.setattr(settings, "webhook_base_url", "https://bot.example.com")
    monkeypatch.setattr(settings, "webhook_secret", SECRET)
    server.validate_webhook_settings()  # fine
    for bad_secret in ("short", "has spaces in it, definitely", ""):
        monkeypatch.setattr(settings, "webhook_secret", bad_secret)
        with pytest.raises(SystemExit):
            server.validate_webhook_settings()
    monkeypatch.setattr(settings, "webhook_secret", SECRET)
    monkeypatch.setattr(settings, "webhook_base_url", "http://insecure.example.com")
    with pytest.raises(SystemExit):
        server.validate_webhook_settings()


def test_webhook_secret_enforced_and_health():
    from app.bot.main import make_bot
    from app.bot import server
    from tests.harness import fresh_dispatcher

    async def go():
        bot, dp = make_bot(), fresh_dispatcher()
        app = server.create_app(bot, dp, secret=SECRET, path=f"/webhook/{SECRET}")
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            path, hdr = (
                f"/webhook/{SECRET}",
                {"X-Telegram-Bot-Api-Secret-Token": SECRET},
            )
            res = {
                "no_header": (await client.post(path, json=HARMLESS)).status,
                "wrong_header": (
                    await client.post(
                        path,
                        json=HARMLESS,
                        headers={"X-Telegram-Bot-Api-Secret-Token": "x" * 20},
                    )
                ).status,
                "wrong_path": (
                    await client.post("/webhook/nope", json=HARMLESS, headers=hdr)
                ).status,
                "ok": (await client.post(path, json=HARMLESS, headers=hdr)).status,
                "other_route": (await client.get("/admin")).status,
                "health": (await client.get("/health")).status,
            }
        finally:
            await client.close()
            await bot.session.close()
            from app.core.db import engine

            await engine.dispose()
        return res

    res = run(go())
    assert (
        res["no_header"] == 401 and res["wrong_header"] == 401
    )  # forged calls rejected
    assert res["wrong_path"] == 404 and res["other_route"] == 404
    assert res["ok"] == 200
    # health is 200 only when Postgres AND Redis answer; in plain unit-test runs one of them may be absent
    assert res["health"] in (200, 503)
    if os.environ.get("TEST_DATABASE_URL") and os.environ.get("TEST_REDIS_URL"):
        assert res["health"] == 200
