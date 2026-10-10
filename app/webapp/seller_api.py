"""Seller dashboard API for the Mini App. Same login as the buyer API (signed Telegram initData); in addition every
endpoint requires that the caller OWNS an active shop, and that shop is always derived on the server from the caller,
never taken from the request, so one seller can never see or change another seller's data."""

import io
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram.types import BufferedInputFile
from aiohttp import web
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.notify import notify_buyer_payment_decision, notify_buyer_status
from app.core.utils import norm_phone
from app.models import (
    Category,
    DeliveryZone,
    Order,
    Payment,
    Product,
    Shop,
    ShopPaymentAccount,
    User,
)
from app.payments.base import PROVIDERS
from app.services import catalog, delivery, orders, plans
from app.services import payments as pay_svc
from app.webapp import api as base
from app.webapp.api import ApiError, _int, _json, _path_id, authed
from app.webapp.keys import BOT_KEY

log = logging.getLogger(__name__)
PAGE = 20
MAX_UPLOAD = 5_000_000
MAX_PRICE, MAX_STOCK = Decimal("10000000"), 100_000
_upload_log: dict[int, list[float]] = {}


def seller(fn):
    """authed + 'caller owns an active shop'. The handler receives (request, session, user, shop)."""

    @authed
    async def wrapper(request: web.Request, session: AsyncSession, user: User):
        shop = await catalog.get_shop_by_owner(session, user.id)
        if not shop or shop.status != "active":
            raise ApiError(403, "not_a_seller")
        return await fn(request, session, user, shop)

    return wrapper


# ---------------- validation ----------------
def _text(value, code: str, lo: int, hi: int) -> str:
    s = (
        str(value).strip()
        if isinstance(value, (str, int, float)) and not isinstance(value, bool)
        else ""
    )
    if not lo <= len(s) <= hi:
        raise ApiError(400, code)
    return s


def _money(
    value, code: str, allow_zero: bool = False, hi: Decimal = MAX_PRICE
) -> Decimal:
    try:
        v = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ApiError(400, code)
    if not v.is_finite() or v < 0 or (v == 0 and not allow_zero) or v > hi:
        raise ApiError(400, code)
    return v.quantize(Decimal("0.01"))


def _stock(value) -> int:
    return _int(value, "stock", 0, MAX_STOCK)


async def _category(session: AsyncSession, value) -> int | None:
    if value in (None, ""):
        return None
    cid = _int(value, "category")
    if not await session.get(Category, cid):
        raise ApiError(400, "category")
    return cid


# ---------------- serializers ----------------
def product_json(p: Product) -> dict:
    return {**base.product_json(p), "status": p.status}


async def order_json(session: AsyncSession, o: Order) -> dict:
    buyer = await session.get(User, o.buyer_id)
    items = await orders.get_items(session, o.id)
    pending = None
    if o.payment_status == "review":
        p = await session.scalar(
            select(Payment)
            .where(Payment.order_id == o.id, Payment.status == "pending_seller")
            .order_by(Payment.id.desc())
            .limit(1)
        )
        if p:
            pending = {
                "id": p.id,
                "provider": PROVIDERS[p.provider].label,
                "reference": p.reference,
                "has_screenshot": bool(p.screenshot_file_id),
                "reason": p.reason or "",
            }
    return {
        "id": o.id,
        "code": o.code,
        "status": o.status,
        "payment_method": o.payment_method,
        "payment_status": o.payment_status,
        "payment_label": PROVIDERS[o.payment_method].label
        if o.payment_method in PROVIDERS
        else None,
        "subtotal": float(o.subtotal),
        "delivery_fee": float(o.delivery_fee or 0),
        "total": float(o.total),
        "delivery_zone": o.delivery_zone,
        "created_at": o.created_at.isoformat() + "Z",
        "address": o.address_text,
        "phone": o.phone,
        "lat": o.lat,
        "lng": o.lng,
        "buyer": (buyer.name or "").split(" ")[0],
        "items": [
            {"title": i.title, "qty": i.qty, "price": float(i.price)} for i in items
        ],
        "next_status": orders.NEXT_STATUS.get(o.status),
        "can_cancel": o.status in orders.CANCELLABLE and o.status != "awaiting_payment",
        "pending_payment": pending,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------- overview ----------------
@seller
async def overview(request, session, user, shop):
    async def count(*where):
        return (
            await session.scalar(
                select(func.count())
                .select_from(Order)
                .where(Order.shop_id == shop.id, *where)
            )
            or 0
        )

    eat_midnight = (_now() + timedelta(hours=3)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(hours=3)  # Ethiopian "today"
    live = Order.status.notin_(["cancelled", "awaiting_payment"])
    sales = (
        await session.scalar(
            select(func.coalesce(func.sum(Order.total), 0)).where(
                Order.shop_id == shop.id,
                Order.status == "delivered",
                Order.created_at >= _now() - timedelta(days=30),
            )
        )
        or 0
    )
    to_confirm = (
        await session.scalar(
            select(func.count())
            .select_from(Payment)
            .join(Order, Order.id == Payment.order_id)
            .where(Order.shop_id == shop.id, Payment.status == "pending_seller")
        )
        or 0
    )
    plan = await plans.effective_plan(session, shop)
    used, limit = await plans.product_quota(session, shop)
    return web.json_response(
        {
            "shop": {
                "id": shop.id,
                "name": shop.name,
                "slug": shop.slug,
                "verified": shop.verified,
                "rating": float(shop.rating_avg or 0),
                "rating_count": shop.rating_count,
                "accept_cod": shop.accept_cod,
            },
            "plan": {
                "name": plan.name,
                "until": shop.plan_expires_at.isoformat() + "Z"
                if plans.is_paid_active(shop)
                else None,
                "products_used": used,
                "products_limit": limit,
                "unlimited": limit >= plans.UNLIMITED,
            },
            "todo": {
                "new": await count(Order.status == "placed"),
                "pack": await count(Order.status.in_(["accepted", "packed"])),
                "shipped": await count(Order.status == "shipped"),
                "payments": to_confirm,
                "disputes": await count(Order.payment_status == "disputed"),
            },
            "stats": {
                "today": await count(live, Order.created_at >= eat_midnight),
                "week": await count(
                    live, Order.created_at >= _now() - timedelta(days=7)
                ),
                "sales_30d": float(sales),
                "delivered": await count(Order.status == "delivered"),
            },
        }
    )


# ---------------- orders ----------------
FILTERS = {
    "todo": lambda: Order.status.in_(["placed", "accepted", "packed"]),
    "shipped": lambda: Order.status == "shipped",
    "payment": lambda: Order.payment_status == "review",
    "done": lambda: Order.status.in_(["delivered", "cancelled"]),
    "all": lambda: Order.id > 0,
}


@seller
async def list_orders(request, session, user, shop):
    flt = request.query.get("filter", "todo")
    if flt not in FILTERS:
        raise ApiError(400, "filter")
    page = _int(request.query.get("page", "0"), "bad_page", 0, 1000)
    rows = (
        await session.scalars(
            select(Order)
            .where(Order.shop_id == shop.id, FILTERS[flt]())
            .order_by(Order.id.desc())
            .limit(PAGE + 1)
            .offset(page * PAGE)
        )
    ).all()
    return web.json_response(
        {
            "orders": [await order_json(session, o) for o in rows[:PAGE]],
            "has_more": len(rows) > PAGE,
        }
    )


async def _my_order(session: AsyncSession, shop: Shop, oid: str) -> Order:
    o = await orders.get_order(session, _path_id(oid, "order_not_found"))
    if not o or o.shop_id != shop.id:
        raise ApiError(404, "order_not_found")
    return o


@seller
async def order_status(request, session, user, shop):
    o = await _my_order(session, shop, request.match_info["oid"])
    new = (await _json(request)).get("status")
    allowed = orders.NEXT_STATUS.get(o.status) == new or (
        new == "cancelled"
        and o.status in orders.CANCELLABLE
        and o.status != "awaiting_payment"
    )
    if not allowed:
        raise ApiError(409, "bad_state")
    await orders.set_status(session, o, new)
    await session.commit()
    await notify_buyer_status(request.app[BOT_KEY], session, o)
    return web.json_response({"ok": True, "order": await order_json(session, o)})


async def _my_payment(
    session: AsyncSession, shop: Shop, pid: str
) -> tuple[Payment, Order]:
    p = await session.get(Payment, _path_id(pid, "payment_not_found"))
    o = await orders.get_order(session, p.order_id) if p and p.order_id else None
    if not p or not o or o.shop_id != shop.id:
        raise ApiError(404, "payment_not_found")
    return p, o


@seller
async def payment_decide(request, session, user, shop):
    p, o = await _my_payment(session, shop, request.match_info["pid"])
    body = await _json(request)
    if not isinstance(body.get("received"), bool):
        raise ApiError(400, "received")
    if p.status != "pending_seller":
        raise ApiError(409, "bad_state")
    await pay_svc.seller_decide(session, p, o, body["received"])
    await session.commit()
    await notify_buyer_payment_decision(
        request.app[BOT_KEY], session, o, body["received"]
    )
    return web.json_response({"ok": True, "order": await order_json(session, o)})


@seller
async def payment_screenshot(request, session, user, shop):
    p, _o = await _my_payment(session, shop, request.match_info["pid"])
    if not p.screenshot_file_id:
        raise ApiError(404, "no_screenshot")
    buf = io.BytesIO()
    try:
        await request.app[BOT_KEY].download(p.screenshot_file_id, destination=buf)
    except Exception:
        log.warning("screenshot download failed", exc_info=True)
        raise ApiError(502, "download_failed")
    return web.Response(
        body=buf.getvalue(),
        content_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


# ---------------- products ----------------
@seller
async def list_products(request, session, user, shop):
    q = select(Product).where(Product.shop_id == shop.id)
    text = request.query.get("q", "").strip()[:60]
    if text:
        q = q.where(
            Product.title.ilike(
                "%" + text.replace("%", r"\%").replace("_", r"\_") + "%", escape="\\"
            )
        )
    page = _int(request.query.get("page", "0"), "bad_page", 0, 1000)
    rows = (
        await session.scalars(
            q.order_by(Product.id.desc()).limit(PAGE + 1).offset(page * PAGE)
        )
    ).all()
    used, limit = await plans.product_quota(session, shop)
    return web.json_response(
        {
            "items": [product_json(p) for p in rows[:PAGE]],
            "has_more": len(rows) > PAGE,
            "quota": {
                "used": used,
                "limit": limit,
                "unlimited": limit >= plans.UNLIMITED,
            },
        }
    )


async def _my_product(session: AsyncSession, shop: Shop, pid: str) -> Product:
    p = await session.get(Product, _path_id(pid, "product_not_found"))
    if not p or p.shop_id != shop.id:
        raise ApiError(404, "product_not_found")
    return p


@seller
async def create_product(request, session, user, shop):
    b = await _json(request)
    ok, used, limit = await plans.can_add_product(session, shop)
    if not ok:
        raise ApiError(402, "plan_limit", used=used, limit=limit)
    p = Product(
        shop_id=shop.id,
        title=_text(b.get("title"), "title", 1, 150),
        description=str(b.get("description") or "").strip()[:1000],
        price=_money(b.get("price"), "price"),
        stock=_stock(b.get("stock", 1)),
        category_id=await _category(session, b.get("category_id")),
    )
    session.add(p)
    await session.flush()
    return web.json_response({"ok": True, "product": product_json(p)})


@seller
async def update_product(request, session, user, shop):
    p = await _my_product(session, shop, request.match_info["pid"])
    b = await _json(request)
    if "title" in b:
        p.title = _text(b["title"], "title", 1, 150)
    if "description" in b:
        p.description = str(b["description"] or "").strip()[:1000]
    if "price" in b:
        p.price = _money(b["price"], "price")
    if "stock" in b:
        p.stock = _stock(b["stock"])
    if "category_id" in b:
        p.category_id = await _category(session, b["category_id"])
    if "status" in b:
        if b["status"] not in ("active", "hidden"):
            raise ApiError(400, "status")
        p.status = b["status"]
    base._photo_cache.pop(p.id, None)
    return web.json_response({"ok": True, "product": product_json(p)})


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    return None


@seller
async def upload_photo(request, session, user, shop):
    """Receives the picture, hands it to Telegram (sent to the seller's own chat, then deleted) and stores the file_id,
    so photos need no separate file hosting."""
    import time

    window = _upload_log.setdefault(user.id, [])
    window[:] = [x for x in window if time.monotonic() - x < 60]
    if len(window) >= 20:
        raise ApiError(429, "too_many_requests")
    window.append(time.monotonic())
    p = await _my_product(session, shop, request.match_info["pid"])
    try:
        field = await (await request.multipart()).next()
    except Exception:
        raise ApiError(400, "file")
    if field is None or field.name != "file":
        raise ApiError(400, "file")
    data = bytearray()
    while chunk := await field.read_chunk(65536):
        data += chunk
        if len(data) > MAX_UPLOAD:
            raise ApiError(413, "too_large")
    kind = _sniff(bytes(data))
    if not kind:
        raise ApiError(415, "bad_image")
    bot = request.app[BOT_KEY]
    try:
        sent = await bot.send_photo(
            user.telegram_id, BufferedInputFile(bytes(data), filename=f"product.{kind}")
        )
        file_id = sent.photo[-1].file_id
    except Exception:
        log.warning("photo upload to Telegram failed", exc_info=True)
        raise ApiError(502, "upload_failed")
    try:
        await bot.delete_message(user.telegram_id, sent.message_id)
    except Exception:
        pass
    p.photo_file_id = file_id
    base._photo_cache.pop(p.id, None)
    return web.json_response({"ok": True, "product": product_json(p)})


# ---------------- settings: shop info, delivery zones, payment accounts ----------------
async def _settings_json(
    request: web.Request, session: AsyncSession, shop: Shop
) -> dict:
    accounts = await pay_svc.list_accounts(session, shop.id)
    try:
        username = (await request.app[BOT_KEY].me()).username
    except Exception:
        username = None
    cats = await catalog.list_categories(session)
    return {
        "shop": {
            "name": shop.name,
            "slug": shop.slug,
            "description": shop.description or "",
            "city": shop.city,
            "phone": shop.phone,
            "accept_cod": shop.accept_cod,
            "verified": shop.verified,
        },
        "zones": [
            {"id": z.id, "name": z.name, "fee": float(z.fee)}
            for z in await delivery.list_zones(session, shop.id)
        ],
        "accounts": [
            {
                "id": a.id,
                "provider": a.provider,
                "label": pay_svc.account_label(a),
                "number": a.account_number,
                "holder": a.holder_name,
            }
            for a in accounts
        ],
        "providers": [
            {"key": k, "label": v.label, "wallet": v.wallet}
            for k, v in PROVIDERS.items()
        ],
        "categories": [
            {"id": c.id, "name": catalog.cat_name(c, request["lang"])} for c in cats
        ],
        "share_link": f"https://t.me/{username}?start=shop_{shop.slug}"
        if username
        else None,
    }


@seller
async def get_settings(request, session, user, shop):
    return web.json_response(await _settings_json(request, session, shop))


@seller
async def update_shop(request, session, user, shop):
    b = await _json(request)
    if "name" in b:
        shop.name = _text(b["name"], "name", 1, 100)
    if "description" in b:
        shop.description = str(b["description"] or "").strip()[:500]
    if "city" in b:
        shop.city = _text(b["city"], "city", 1, 60)
    if "phone" in b:
        phone = norm_phone(str(b["phone"]))
        if not phone:
            raise ApiError(400, "phone")
        shop.phone = phone
    if "accept_cod" in b:
        if not isinstance(b["accept_cod"], bool):
            raise ApiError(400, "accept_cod")
        if not b["accept_cod"] and not await pay_svc.list_accounts(session, shop.id):
            raise ApiError(409, "need_account")  # buyers must always have a way to pay
        shop.accept_cod = b["accept_cod"]
    return web.json_response(await _settings_json(request, session, shop))


@seller
async def add_zone(request, session, user, shop):
    b = await _json(request)
    await delivery.add_zone(
        session,
        shop.id,
        _text(b.get("name"), "name", 1, 60),
        _money(b.get("fee", 0), "fee", allow_zero=True, hi=Decimal("100000")),
    )
    return web.json_response(await _settings_json(request, session, shop))


@seller
async def delete_zone(request, session, user, shop):
    z = await session.get(
        DeliveryZone, _path_id(request.match_info["zid"], "zone_not_found")
    )
    if not z or z.shop_id != shop.id:
        raise ApiError(404, "zone_not_found")
    z.active = False
    return web.json_response(await _settings_json(request, session, shop))


@seller
async def add_account(request, session, user, shop):
    b = await _json(request)
    provider = b.get("provider")
    if provider not in PROVIDERS:
        raise ApiError(400, "provider")
    raw = str(b.get("number", ""))
    if PROVIDERS[provider].wallet:
        phone = norm_phone(raw)
        number = phone.lstrip("+") if phone else None
    else:
        digits = "".join(ch for ch in raw if ch.isdigit())
        number = digits if 6 <= len(digits) <= 20 else None
    if not number:
        raise ApiError(400, "number")
    label = _text(b.get("label"), "label", 1, 60) if provider == "other" else ""
    session.add(
        ShopPaymentAccount(
            shop_id=shop.id,
            provider=provider,
            label=label,
            account_number=number,
            holder_name=_text(b.get("holder"), "holder", 1, 100),
        )
    )
    await session.flush()
    return web.json_response(await _settings_json(request, session, shop))


@seller
async def delete_account(request, session, user, shop):
    a = await session.get(
        ShopPaymentAccount, _path_id(request.match_info["aid"], "account_not_found")
    )
    if not a or a.shop_id != shop.id or not a.active:
        raise ApiError(404, "account_not_found")
    if not shop.accept_cod and len(await pay_svc.list_accounts(session, shop.id)) <= 1:
        raise ApiError(409, "need_account")
    a.active = False  # soft delete: existing orders still point at it
    return web.json_response(await _settings_json(request, session, shop))


def register(app: web.Application) -> None:
    r = app.router
    r.add_get("/app/seller.js", seller_script)
    r.add_get("/api/seller/overview", overview)
    r.add_get("/api/seller/orders", list_orders)
    r.add_post("/api/seller/orders/{oid}/status", order_status)
    r.add_post("/api/seller/payments/{pid}/decide", payment_decide)
    r.add_get("/api/seller/payments/{pid}/screenshot", payment_screenshot)
    r.add_get("/api/seller/products", list_products)
    r.add_post("/api/seller/products", create_product)
    r.add_patch("/api/seller/products/{pid}", update_product)
    r.add_post("/api/seller/products/{pid}/photo", upload_photo)
    r.add_get("/api/seller/settings", get_settings)
    r.add_patch("/api/seller/shop", update_shop)
    r.add_post("/api/seller/zones", add_zone)
    r.add_delete("/api/seller/zones/{zid}", delete_zone)
    r.add_post("/api/seller/accounts", add_account)
    r.add_delete("/api/seller/accounts/{aid}", delete_account)


async def seller_script(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(
        base.STATIC / "seller.js", headers={"Cache-Control": "no-cache"}
    )
