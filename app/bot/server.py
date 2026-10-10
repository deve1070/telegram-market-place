"""Production web layer: Telegram webhook + /health endpoint (served behind Caddy, which provides HTTPS)."""

import asyncio
import re

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from sqlalchemy import text

from app.core.config import settings
from app.core.db import session_factory
from app.webapp import api as webapp_api
from app.webapp import seller_api
from app.webapp.keys import BOT_KEY, DP_KEY

SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def validate_webhook_settings() -> None:
    if not SECRET_RE.match(settings.webhook_secret):
        raise SystemExit(
            "WEBHOOK_SECRET must be 16-128 characters of A-Z a-z 0-9 _ - (generate one: openssl rand -hex 24)"
        )
    if not settings.webhook_base_url.startswith("https://"):
        raise SystemExit(
            "WEBHOOK_BASE_URL must start with https:// (Telegram only delivers webhooks over HTTPS)"
        )


def webhook_path() -> str:
    return f"/webhook/{settings.webhook_secret}"


async def health(request: web.Request) -> web.Response:
    """200 only if Postgres and Redis answer. Used by Docker's healthcheck and external uptime monitors."""
    try:
        async with session_factory() as s:
            await s.execute(text("SELECT 1"))
        await request.app[DP_KEY].storage.redis.ping()
    except Exception:
        return web.json_response({"ok": False}, status=503)
    return web.json_response({"ok": True})


def create_app(
    bot: Bot, dp: Dispatcher, *, secret: str, path: str, on_start=None, background=None
) -> web.Application:
    """`on_start`: async fn run once at startup (DB init...). `background`: async fn(bot) run for the app's lifetime."""
    app = web.Application()
    app[BOT_KEY], app[DP_KEY] = bot, dp
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(
        app, path=path
    )
    setup_application(app, dp, bot=bot)
    app.router.add_get("/health", health)
    webapp_api.register(app)
    seller_api.register(app)

    if on_start:

        async def _start(_app):
            await on_start()

        app.on_startup.append(_start)

    if background:

        async def _bg(_app):
            task = asyncio.create_task(background(bot))
            yield
            task.cancel()

        app.cleanup_ctx.append(_bg)
    return app
