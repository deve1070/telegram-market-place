"""JSON API + static page for the Telegram Mini App storefront.

Security model: every /api call (except public product photos) must carry Telegram's signed `initData` in the
X-Telegram-Init-Data header; it is verified with the bot token, so a caller can only ever act as the Telegram user
Telegram says they are. Prices, stock, zones and payment methods are ALWAYS re-checked on the server."""

import io
import logging
import time
from collections import OrderedDict, defaultdict, deque
from pathlib import Path

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers import payments as pay_h
from app.bot.handlers import reviews as reviews_h
from app.bot.notify import notify_new_order
from app.core.config import settings
from app.core.db import session_factory
from app.core.utils import esc, norm_phone
from app.i18n import t
from app.models import (
    Category,
    DeliveryZone,
    Order,
    Product,
    Shop,
    ShopPaymentAccount,
    User,
)
from app.services import cart, catalog, delivery, orders, plans, reviews
from app.services import payments as pay_svc
from app.webapp.auth import validate_init_data
from app.webapp.keys import BOT_KEY, DP_KEY

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
PAGE = 24
MAX_ITEMS, MAX_QTY = 30, 20
PHOTO_CACHE_MAX, PHOTO_MAX_BYTES = 300, 6_000_000
_photo_cache: "OrderedDict[int, bytes]" = OrderedDict()
_checkout_log: dict[int, deque] = defaultdict(deque)


class ApiError(Exception):
    def __init__(self, status: int, code: str, **extra):
        super().__init__(code)
        self.status, self.code, self.extra = status, code, extra


def authed(fn):
    """Verify initData, load/create the user, give the handler a DB session, commit on success."""

    async def wrapper(request: web.Request) -> web.Response:
        auth = validate_init_data(
            request.headers.get("X-Telegram-Init-Data", ""), settings.bot_token
        )
        if not auth:
            return web.json_response({"error": "unauthorized"}, status=401)
        tg = auth["user"]
        async with session_factory() as session:
            user = await session.scalar(
                select(User).where(User.telegram_id == tg["id"])
            )
            if user is None:
                name = " ".join(
                    x for x in (tg.get("first_name"), tg.get("last_name")) if x
                )
                user = User(telegram_id=tg["id"], name=name[:128])
                session.add(user)
                await session.flush()
            request["lang"] = user.language or (
                "am" if tg.get("language_code") == "am" else "en"
            )
            try:
                resp = await fn(request, session, user)
            except ApiError as e:
                await session.rollback()
                return web.json_response({"error": e.code, **e.extra}, status=e.status)
            await session.commit()
            return resp

    return wrapper


async def _json(request: web.Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise ApiError(400, "bad_json")
    if not isinstance(body, dict):
        raise ApiError(400, "bad_json")
    return body


def _int(value, code: str, lo: int = 1, hi: int = 10**9) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, str))
        or not str(value).lstrip("-").isdigit()
    ):
        raise ApiError(400, code)
    n = int(value)
    if not lo <= n <= hi:
        raise ApiError(400, code)
    return n


def _path_id(value: str, code: str) -> int:
    """An id inside the URL path: anything that isn't a positive integer simply doesn't exist (404)."""
    if not value.isdigit() or not 0 < int(value) < 10**9:
        raise ApiError(404, code)
    return int(value)


# ---------------- serializers ----------------
def shop_json(shop: Shop, featured: set[int]) -> dict:
    return {
        "id": shop.id,
        "slug": shop.slug,
        "name": shop.name,
        "city": shop.city,
        "description": shop.description or "",
        "verified": shop.verified,
        "featured": shop.id in featured,
        "rating": float(shop.rating_avg or 0),
        "rating_count": shop.rating_count,
    }


def product_json(p: Product) -> dict:
    return {
        "id": p.id,
        "shop_id": p.shop_id,
        "title": p.title,
        "description": p.description or "",
        "price": float(p.price),
        "stock": p.stock,
        "has_photo": bool(p.photo_file_id),
        "category_id": p.category_id,
    }


async def _active_shop(session: AsyncSession, shop_id: int) -> Shop:
    shop = await session.get(Shop, shop_id)
    if not shop or shop.status != "active":
        raise ApiError(404, "shop_not_found")
    return shop


async def order_json(session: AsyncSession, o: Order) -> dict:
    shop = await session.get(Shop, o.shop_id)
    items = await orders.get_items(session, o.id)
    reviewed = (
        o.status == "delivered"
        and await reviews.get_for_order(session, o.id) is not None
    )
    return {
        "id": o.id,
        "code": o.code,
        "status": o.status,
        "payment_method": o.payment_method,
        "payment_status": o.payment_status,
        "subtotal": float(o.subtotal),
        "delivery_fee": float(o.delivery_fee or 0),
        "total": float(o.total),
        "delivery_zone": o.delivery_zone,
        "created_at": o.created_at.isoformat() + "Z",
        "shop": {"id": shop.id, "name": shop.name},
        "items": [
            {"title": i.title, "qty": i.qty, "price": float(i.price)} for i in items
        ],
        "can_receive": o.status == "shipped",
        "can_review": o.status == "delivered" and not reviewed,
        "reviewed": reviewed,
        "awaiting_payment": o.status == "awaiting_payment"
        and o.payment_status == "awaiting",
    }


# ---------------- pages & photos ----------------
async def page(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(
        STATIC / "index.html", headers={"Cache-Control": "no-store"}
    )


async def photo(request: web.Request) -> web.Response:
    """Product photos are public marketplace content. Only photos of ACTIVE products of ACTIVE shops are served,
    looked up by product id (never by a caller-supplied Telegram file_id)."""
    try:
        pid = int(request.match_info["pid"])
    except ValueError:
        raise web.HTTPNotFound()
    headers = {"Cache-Control": "public, max-age=86400"}
    if pid in _photo_cache:
        _photo_cache.move_to_end(pid)
        return web.Response(
            body=_photo_cache[pid], content_type="image/jpeg", headers=headers
        )
    async with session_factory() as s:
        file_id = await s.scalar(
            select(Product.photo_file_id)
            .join(Shop, Shop.id == Product.shop_id)
            .where(
                Product.id == pid, Product.status == "active", Shop.status == "active"
            )
        )
    if not file_id:
        raise web.HTTPNotFound()
    buf = io.BytesIO()
    try:
        await request.app[BOT_KEY].download(file_id, destination=buf)
    except Exception:
        log.warning("photo download failed for product %s", pid, exc_info=True)
        raise web.HTTPBadGateway()
    data = buf.getvalue()
    if not data or len(data) > PHOTO_MAX_BYTES:
        raise web.HTTPBadGateway()
    _photo_cache[pid] = data
    while len(_photo_cache) > PHOTO_CACHE_MAX:
        _photo_cache.popitem(last=False)
    return web.Response(body=data, content_type="image/jpeg", headers=headers)


# ---------------- browsing ----------------
@authed
async def me(request, session, user):
    shop = await catalog.get_shop_by_owner(session, user.id)
    return web.json_response(
        {
            "id": user.id,
            "name": user.name,
            "phone": user.phone,
            "language": request["lang"],
            "is_seller": bool(shop and shop.status == "active"),
        }
    )


@authed
async def set_lang(request, session, user):
    body = await _json(request)
    if body.get("language") not in ("en", "am"):
        raise ApiError(400, "language")
    user.language = body["language"]
    return web.json_response({"ok": True})


@authed
async def list_shops(request, session, user):
    featured = await plans.featured_ids(session)
    shops = sorted(
        await catalog.list_active_shops(session),
        key=lambda s: (s.id not in featured, not s.verified, s.name.lower()),
    )
    return web.json_response({"shops": [shop_json(s, featured) for s in shops]})


@authed
async def shop_detail(request, session, user):
    shop = await _active_shop(
        session, _path_id(request.match_info["sid"], "shop_not_found")
    )
    featured = await plans.featured_ids(session)
    cat_ids = select(Product.category_id).where(
        Product.shop_id == shop.id, Product.status == "active"
    )
    cats = (
        await session.scalars(
            select(Category).where(Category.id.in_(cat_ids)).order_by(Category.id)
        )
    ).all()
    accounts = await pay_svc.list_accounts(session, shop.id)
    return web.json_response(
        {
            "shop": shop_json(shop, featured),
            "categories": [
                {"id": c.id, "name": catalog.cat_name(c, request["lang"])} for c in cats
            ],
            "zones": [
                {"id": z.id, "name": z.name, "fee": float(z.fee)}
                for z in await delivery.list_zones(session, shop.id)
            ],
            "methods": {
                "cod": shop.accept_cod,
                "accounts": [
                    {"id": a.id, "label": pay_svc.account_label(a)} for a in accounts
                ],
            },
        }
    )


@authed
async def shop_products(request, session, user):
    shop = await _active_shop(
        session, _path_id(request.match_info["sid"], "shop_not_found")
    )
    q = select(Product).where(Product.shop_id == shop.id, Product.status == "active")
    cat = request.query.get("cat")
    if cat:
        q = q.where(Product.category_id == _int(cat, "bad_category"))
    text = request.query.get("q", "").strip()[:60]
    if text:
        q = q.where(
            Product.title.ilike(
                "%" + text.replace("%", r"\%").replace("_", r"\_") + "%", escape="\\"
            )
        )
    page_no = _int(request.query.get("page", "0"), "bad_page", 0, 1000)
    rows = (
        await session.scalars(
            q.order_by(Product.id.desc()).limit(PAGE + 1).offset(page_no * PAGE)
        )
    ).all()
    return web.json_response(
        {"items": [product_json(p) for p in rows[:PAGE]], "has_more": len(rows) > PAGE}
    )


@authed
async def product_detail(request, session, user):
    p = await session.get(
        Product, _path_id(request.match_info["pid"], "product_not_found")
    )
    if not p or p.status != "active":
        raise ApiError(404, "product_not_found")
    await _active_shop(session, p.shop_id)
    return web.json_response({"product": product_json(p)})


@authed
async def shop_reviews(request, session, user):
    shop = await _active_shop(
        session, _path_id(request.match_info["sid"], "shop_not_found")
    )
    rows = await reviews.list_reviews(session, shop.id, 10)
    return web.json_response(
        {
            "reviews": [
                {
                    "rating": r.rating,
                    "comment": r.comment,
                    "name": (u.name or "").split(" ")[0],
                    "created_at": r.created_at.isoformat() + "Z",
                }
                for r, u in rows
            ]
        }
    )


# ---------------- checkout ----------------
def _rate_limit(user_id: int) -> None:
    now, window = time.monotonic(), _checkout_log[user_id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= 5:
        raise ApiError(429, "too_many_requests")
    window.append(now)


@authed
async def checkout(request, session, user):
    _rate_limit(user.id)
    body = await _json(request)
    shop = await _active_shop(session, _int(body.get("shop_id"), "shop_not_found"))
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_ITEMS:
        raise ApiError(400, "items")
    wanted: dict[int, int] = {}
    for it in raw_items:
        if not isinstance(it, dict):
            raise ApiError(400, "items")
        wanted[_int(it.get("id"), "items")] = wanted.get(
            _int(it.get("id"), "items"), 0
        ) + _int(it.get("qty"), "items", 1, MAX_QTY)
    phone = norm_phone(str(body.get("phone", "")))
    if not phone:
        raise ApiError(400, "phone")
    address = str(body.get("address", "")).strip()[:300]
    if not address:
        raise ApiError(400, "address")
    lat, lng = body.get("lat"), body.get("lng")
    if lat is not None or lng is not None:
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            raise ApiError(400, "location")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ApiError(400, "location")

    method = body.get("method")
    account = None
    if method == "cod":
        if not shop.accept_cod:
            raise ApiError(400, "method")
    else:
        account = await session.get(ShopPaymentAccount, _int(method, "method"))
        if not account or account.shop_id != shop.id or not account.active:
            raise ApiError(400, "method")
    zone = None
    if body.get("zone_id") is not None:
        zone = await session.get(DeliveryZone, _int(body["zone_id"], "zone"))

    # rebuild the server-side cart from the request so price/stock/shop rules are the same as in the chat flow
    await cart.clear(session, user.id)
    for pid, qty in wanted.items():
        product = await session.get(Product, pid)
        if not product or product.shop_id != shop.id:
            raise ApiError(400, "items")
        for _ in range(qty):
            ok, _reset = await cart.add(session, user.id, product)
            if not ok:
                raise ApiError(409, "stock", product=product.title)
    user.phone = phone
    try:
        order = await orders.place_order(
            session, user, phone, address, lat, lng, account, zone
        )
    except orders.OutOfStock as e:
        raise ApiError(409, "stock", product=str(e))
    except orders.BadPaymentMethod:
        raise ApiError(400, "method")
    except orders.BadDeliveryZone:
        raise ApiError(400, "zone")
    except orders.ShopUnavailable:
        raise ApiError(410, "shop_unavailable")
    await session.commit()  # the order exists before anyone is notified about it

    bot, lang = request.app[BOT_KEY], request["lang"]
    try:
        if account is None:
            await bot.send_message(
                user.telegram_id, t("order_placed", lang, code=order.code)
            )
            await notify_new_order(bot, session, order)
        else:
            state = request.app[DP_KEY].fsm.get_context(
                bot, chat_id=user.telegram_id, user_id=user.telegram_id
            )
            await pay_h.push_instructions(
                bot, user.telegram_id, state, order, account, lang
            )
    except Exception:
        log.warning(
            "order %s saved but a notification failed", order.code, exc_info=True
        )
    return web.json_response(
        {
            "ok": True,
            "next": "cod" if account is None else "pay",
            "order": {"id": order.id, "code": order.code, "total": float(order.total)},
        }
    )


# ---------------- orders ----------------
async def _my_order(session: AsyncSession, user: User, oid: str) -> Order:
    o = await orders.get_order(session, _path_id(oid, "order_not_found"))
    if not o or o.buyer_id != user.id:
        raise ApiError(404, "order_not_found")
    return o


@authed
async def my_orders(request, session, user):
    rows = await orders.list_buyer_orders(session, user.id, 20)
    return web.json_response({"orders": [await order_json(session, o) for o in rows]})


@authed
async def order_received(request, session, user):
    o = await _my_order(session, user, request.match_info["oid"])
    if o.status != "shipped":
        raise ApiError(409, "bad_state")
    await orders.set_status(session, o, "delivered")
    if o.payment_method == "cod":
        o.payment_status = "paid"
    await session.commit()
    shop = await catalog.get_shop(session, o.shop_id)
    owner = await session.get(User, shop.owner_id)
    try:
        await request.app[BOT_KEY].send_message(
            owner.telegram_id,
            t(
                "order_update",
                owner.language or "en",
                code=o.code,
                status=t("status_delivered", owner.language or "en"),
            ),
        )
    except Exception:
        log.warning("could not tell the seller about delivery", exc_info=True)
    return web.json_response({"ok": True})


@authed
async def order_review(request, session, user):
    o = await _my_order(session, user, request.match_info["oid"])
    body = await _json(request)
    rating = _int(body.get("rating"), "rating", 1, 5)
    comment = str(body.get("comment") or "").strip()[:300] or None
    review = await reviews.add_review(session, o, rating, comment)
    if review is None:
        raise ApiError(409, "bad_state")
    await session.commit()
    bot = request.app[BOT_KEY]
    await reviews_h._notify_seller(
        bot, session, o, "review_new_seller", stars=reviews.stars(rating)
    )
    if comment:
        await reviews_h._notify_seller(
            bot, session, o, "review_comment_seller", comment=esc(comment)
        )
    return web.json_response({"ok": True})


@authed
async def order_cancel(request, session, user):
    o = await _my_order(session, user, request.match_info["oid"])
    if not (o.status == "awaiting_payment" and o.payment_status == "awaiting"):
        raise ApiError(409, "bad_state")
    await orders.set_status(session, o, "cancelled")
    o.payment_status = "expired"
    return web.json_response({"ok": True})


@authed
async def order_pay(request, session, user):
    """Re-send the payment instructions in the chat and wait there for the reference/screenshot."""
    o = await _my_order(session, user, request.match_info["oid"])
    if not (o.status == "awaiting_payment" and o.payment_status == "awaiting"):
        raise ApiError(409, "bad_state")
    account = await session.get(ShopPaymentAccount, o.payment_account_id)
    bot = request.app[BOT_KEY]
    state = request.app[DP_KEY].fsm.get_context(
        bot, chat_id=user.telegram_id, user_id=user.telegram_id
    )
    await pay_h.push_instructions(
        bot, user.telegram_id, state, o, account, request["lang"]
    )
    return web.json_response({"ok": True})


def register(app: web.Application) -> None:
    r = app.router
    r.add_get("/app", page)
    r.add_get("/app/", page)
    r.add_get("/api/photo/{pid}", photo)
    r.add_get("/api/me", me)
    r.add_post("/api/lang", set_lang)
    r.add_get("/api/shops", list_shops)
    r.add_get("/api/shops/{sid}", shop_detail)
    r.add_get("/api/shops/{sid}/products", shop_products)
    r.add_get("/api/shops/{sid}/reviews", shop_reviews)
    r.add_get("/api/products/{pid}", product_detail)
    r.add_post("/api/checkout", checkout)
    r.add_get("/api/orders", my_orders)
    r.add_post("/api/orders/{oid}/received", order_received)
    r.add_post("/api/orders/{oid}/review", order_review)
    r.add_post("/api/orders/{oid}/cancel", order_cancel)
    r.add_post("/api/orders/{oid}/pay", order_pay)
