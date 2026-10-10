import asyncio
import logging
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import ErrorEvent, MenuButtonWebApp, WebAppInfo
from aiohttp import web

from app.bot import server
from app.bot.handlers import (
    admin,
    adminpanel,
    buyer,
    delivery,
    payments,
    plans,
    reports,
    reviews,
    seller,
    start,
    verification,
)
from app.bot.middlewares.core import DbSessionMiddleware, UserMiddleware
from app.bot.middlewares.throttle import ThrottleMiddleware
from app.core.config import settings
from app.core.db import init_db, session_factory
from app.i18n import t
from app.services import payments as pay_svc
from app.services import plans as plans_svc
from app.services.catalog import seed_categories

log = logging.getLogger("market")
_last_alert = 0.0


async def expiry_loop(bot: Bot) -> None:
    """Every minute: cancel unpaid online orders past their window (stock restored); plan reminders and downgrades."""
    while True:
        try:
            async with session_factory() as s:
                expired = await pay_svc.expire_unpaid(s)
                await s.commit()
            for tg_id, lang, code in expired:
                try:
                    await bot.send_message(tg_id, t("order_expired", lang, code=code))
                except Exception:
                    pass
            async with session_factory() as s:
                notices = await plans_svc.plan_notices(s)
                free = await plans_svc.get_plan(s, "free")
                await s.commit()
            for tg_id, lang, kind, name, days in notices:
                try:
                    msg = (
                        t("plan_soon", lang, plan=name, days=days)
                        if kind == "soon"
                        else t(
                            "plan_expired",
                            lang,
                            plan=name,
                            limit=plans_svc.limit_label(
                                free.max_products if free else 10
                            ),
                        )
                    )
                    await bot.send_message(tg_id, msg)
                except Exception:
                    pass
        except Exception:
            logging.exception("background loop failed")
        await asyncio.sleep(60)


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    """Log every unhandled error and tell the admins (at most one alert per minute, so a bug can't flood you)."""
    global _last_alert
    log.error("unhandled error: %r", event.exception, exc_info=event.exception)
    if time.monotonic() - _last_alert > 60:
        _last_alert = time.monotonic()
        for admin_id in settings.admin_id_set:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ Bot error: <code>{type(event.exception).__name__}</code>: "
                    f"{str(event.exception)[:300]!r}\nSee: docker compose logs bot",
                    parse_mode="HTML",
                )
            except Exception:
                pass
    return True


def build_dispatcher(storage=None) -> Dispatcher:
    storage = storage or RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)
    if getattr(storage, "redis", None) is not None:
        # throttle first: flooding users are dropped before any database work happens
        dp.update.outer_middleware(
            ThrottleMiddleware(
                storage.redis,
                settings.throttle_limit,
                settings.throttle_window,
                exempt=settings.admin_id_set,
            )
        )
    dp.update.outer_middleware(DbSessionMiddleware())
    dp.update.outer_middleware(UserMiddleware())
    # order matters: menu/start first so menu buttons always win over FSM text steps
    dp.include_routers(
        start.router,
        admin.router,
        adminpanel.router,
        verification.router,
        seller.router,
        payments.router,
        delivery.router,
        plans.router,
        reviews.router,
        reports.router,
        buyer.router,
    )
    dp.errors.register(on_error)
    return dp


async def prepare_database() -> None:
    await init_db()
    async with session_factory() as s:
        await seed_categories(s)
        await plans_svc.seed_plans(s)
        await s.commit()


def make_bot() -> Bot:
    return Bot(
        settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )


async def run_polling() -> None:
    """Development: long polling. NOTE: this deletes any webhook set for the token, so never run it with the
    production bot's token while production is live; use a separate test bot from @BotFather."""
    await prepare_database()
    bot, dp = make_bot(), build_dispatcher()
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(expiry_loop(bot))
    await dp.start_polling(bot)


def run_webhook() -> None:
    server.validate_webhook_settings()
    bot, dp = make_bot(), build_dispatcher()
    url = f"{settings.webhook_base_url.rstrip('/')}{server.webhook_path()}"

    async def set_hook(bot: Bot) -> None:
        await bot.set_webhook(
            url,
            secret_token=settings.webhook_secret,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=False,
        )  # keep queued updates across restarts
        log.info("webhook registered for %s", settings.webhook_base_url)
        if (
            settings.webapp_url
        ):  # Telegram's own menu button next to the message box opens the storefront
            try:
                await bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(
                        text="🛍 Shop", web_app=WebAppInfo(url=settings.webapp_url)
                    )
                )
            except Exception:
                log.warning("could not set the Mini App menu button", exc_info=True)

    dp.startup.register(set_hook)
    app = server.create_app(
        bot,
        dp,
        secret=settings.webhook_secret,
        path=server.webhook_path(),
        on_start=prepare_database,
        background=expiry_loop,
    )
    web.run_app(app, host=settings.web_host, port=settings.web_port, access_log=None)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if settings.webhook_base_url:
        run_webhook()
    else:
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
