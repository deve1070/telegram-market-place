from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers import payments as pay_h
from app.bot.handlers import reviews as reviews_h
from app.bot.keyboards import main_menu
from app.bot.notify import notify_new_order
from app.bot.views import buyer_order_kb, order_text
from app.core.config import settings
from app.core.utils import esc, etb, norm_phone
from app.i18n import t
from app.models import DeliveryZone, ShopPaymentAccount, User
from app.services import cart, catalog, delivery, orders, plans, reviews

router = Router()


class Checkout(StatesGroup):
    phone = State()
    location = State()
    landmark = State()
    zone = State()
    confirm = State()


# ---------------- browsing ----------------
async def show_shops(message: Message, session: AsyncSession, lang: str):
    shops = await catalog.list_active_shops(session)
    if not shops:
        return await message.answer(t("no_shops", lang))
    featured = await plans.featured_ids(session)
    shops = sorted(
        shops, key=lambda s: (s.id not in featured, not s.verified, s.name.lower())
    )  # featured, then verified
    b = InlineKeyboardBuilder()
    for s in shops:
        rating = f" · {reviews.rating_label(s)}" if s.rating_count else ""
        b.button(
            text=f"{'🌟 ' if s.id in featured else ''}{'✔️ ' if s.verified else ''}{s.name} · {s.city}{rating}",
            callback_data=f"shop:{s.id}",
        )
    b.adjust(1)
    await message.answer(t("shops_title", lang), reply_markup=b.as_markup())


async def open_shop(
    message: Message, session: AsyncSession, user: User, lang: str, shop
):
    user.current_shop_id = shop.id
    b = InlineKeyboardBuilder()
    if settings.webapp_url:  # the visual storefront, opened straight on this shop
        b.button(
            text=t("btn_open_app", lang),
            web_app=WebAppInfo(url=f"{settings.webapp_url}?shop={shop.id}"),
        )
    b.button(text=t("all_items", lang), callback_data=f"cat:{shop.id}:0")
    for c in await catalog.list_categories(session):
        b.button(text=catalog.cat_name(c, lang), callback_data=f"cat:{shop.id}:{c.id}")
    b.button(text=t("btn_shop_reviews", lang), callback_data=f"shoprev:{shop.id}")
    b.button(text=t("btn_report", lang), callback_data=f"rpt:{shop.id}")
    b.adjust(*((1, 1, 2) if settings.webapp_url else (1, 2)))
    head = t(
        "shop_header",
        lang,
        name=esc(shop.name),
        desc=esc(shop.description),
        city=esc(shop.city),
        badge=t("verified_line", lang) if shop.verified else "",
        rating=reviews.rating_label(shop) or t("no_reviews_yet", lang),
    )
    await message.answer(
        f"{head}\n\n{t('pick_category', lang)}", reply_markup=b.as_markup()
    )


@router.callback_query(F.data.startswith("shop:"))
async def cb_shop(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    shop = await catalog.get_shop(session, int(cb.data.split(":")[1]))
    if shop and shop.status == "active":
        await open_shop(cb.message, session, user, lang, shop)
    await cb.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(cb: CallbackQuery, session: AsyncSession, lang: str):
    _, shop_id, cat_id = cb.data.split(":")
    products = await catalog.list_products(session, int(shop_id), int(cat_id) or None)
    if not products:
        return await cb.answer(t("no_products", lang), show_alert=True)
    b = InlineKeyboardBuilder()
    for p in products:
        b.button(text=f"{p.title} — {etb(p.price)}", callback_data=f"prod:{p.id}")
    b.adjust(1)
    await cb.message.answer("👇", reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("prod:"))
async def cb_product(cb: CallbackQuery, session: AsyncSession, lang: str):
    p = await catalog.get_product(session, int(cb.data.split(":")[1]))
    if not p or p.status != "active":
        return await cb.answer(t("no_products", lang), show_alert=True)
    text = t(
        "product_card",
        lang,
        title=esc(p.title),
        desc=esc(p.description),
        price=etb(p.price),
        stock=p.stock,
    )
    b = InlineKeyboardBuilder()
    if p.stock > 0:
        b.button(text=t("add_to_cart", lang), callback_data=f"add:{p.id}")
    if p.photo_file_id:
        await cb.message.answer_photo(
            p.photo_file_id, caption=text, reply_markup=b.as_markup()
        )
    else:
        await cb.message.answer(text, reply_markup=b.as_markup())
    await cb.answer()


# ---------------- cart ----------------
@router.callback_query(F.data.startswith("add:"))
async def cb_add(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    p = await catalog.get_product(session, int(cb.data.split(":")[1]))
    if not p:
        return await cb.answer()
    ok, reset = await cart.add(session, user.id, p)
    if not ok:
        return await cb.answer(t("out_of_stock", lang), show_alert=True)
    await cb.answer(
        t("switched_shop", lang) if reset else t("added", lang), show_alert=reset
    )


async def show_cart(message: Message, session: AsyncSession, user: User, lang: str):
    rows = await cart.get_items(session, user.id)
    if not rows:
        return await message.answer(t("cart_empty", lang))
    total = sum(p.price * c.qty for c, p in rows)
    lines = "\n".join(
        f"• {esc(p.title)} × {c.qty} — {etb(p.price * c.qty)}" for c, p in rows
    )
    b = InlineKeyboardBuilder()
    b.button(text=t("checkout", lang), callback_data="checkout")
    b.button(text=t("clear_cart", lang), callback_data="cart_clear")
    await message.answer(
        f"{t('cart_title', lang)}\n{lines}\n\n💰 <b>{etb(total)}</b>",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data == "cart_clear")
async def cb_clear(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    await cart.clear(session, user.id)
    await cb.message.edit_text(t("cart_cleared", lang))
    await cb.answer()


# ---------------- checkout (cash on delivery) ----------------
def _kb(text: str, contact=False, location=False):
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=text, request_contact=contact, request_location=location
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def _ask_location(message: Message, lang: str):
    await message.answer(
        t("ask_location", lang) + "\n(/cancel)",
        reply_markup=_kb(t("share_location", lang), location=True),
    )


@router.callback_query(F.data == "checkout")
async def cb_checkout(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    if not await cart.get_items(session, user.id):
        return await cb.answer(t("cart_empty", lang), show_alert=True)
    await cb.answer()
    if user.phone:
        await state.set_state(Checkout.location)
        await _ask_location(cb.message, lang)
    else:
        await state.set_state(Checkout.phone)
        await cb.message.answer(
            t("ask_phone", lang) + "\n(/cancel)",
            reply_markup=_kb(t("share_phone", lang), contact=True),
        )


@router.message(Checkout.phone)
async def got_phone(message: Message, state: FSMContext, user: User, lang: str):
    phone = norm_phone(
        message.contact.phone_number if message.contact else (message.text or "")
    )
    if not phone:
        return await message.answer(t("bad_phone", lang))
    user.phone = phone
    await state.set_state(Checkout.location)
    await _ask_location(message, lang)


@router.message(Checkout.location, F.location)
async def got_pin(message: Message, state: FSMContext, lang: str):
    await state.update_data(
        lat=message.location.latitude, lng=message.location.longitude
    )
    await state.set_state(Checkout.landmark)
    await message.answer(t("ask_landmark", lang))


async def _after_address(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    lang: str,
    address: str,
):
    """Address captured -> ask for a delivery zone if the shop defined any, otherwise go straight to confirmation."""
    await state.update_data(address=address[:300], zone_id=None)
    rows = await cart.get_items(session, user.id)
    zones = await delivery.list_zones(session, rows[0][1].shop_id)
    if not zones:
        return await _confirm(message, state, session, user, lang)
    b = InlineKeyboardBuilder()
    for z in zones:
        b.button(
            text=f"{z.name} — {etb(z.fee) if z.fee else t('free', lang)}",
            callback_data=f"zone:{z.id}",
        )
    b.button(text=t("cancel", lang), callback_data="cancel_checkout")
    b.adjust(2)
    await state.set_state(Checkout.zone)
    await message.answer(t("choose_zone", lang), reply_markup=b.as_markup())


@router.callback_query(Checkout.zone, F.data.startswith("zone:"))
async def cb_zone(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    z = await session.get(DeliveryZone, int(cb.data.split(":")[1]))
    rows = await cart.get_items(session, user.id)
    if not (z and z.active and rows and z.shop_id == rows[0][1].shop_id):
        return await cb.answer()
    await state.update_data(zone_id=z.id)
    await cb.answer()
    await _confirm(cb.message, state, session, user, lang)


async def _confirm(
    message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    data = await state.get_data()
    rows = await cart.get_items(session, user.id)
    subtotal = sum(p.price * c.qty for c, p in rows)
    items = "\n".join(f"• {esc(p.title)} × {c.qty}" for c, p in rows)
    shop = await catalog.get_shop(session, rows[0][1].shop_id)
    zone = (
        await session.get(DeliveryZone, data["zone_id"])
        if data.get("zone_id")
        else None
    )
    fee = zone.fee if zone else 0
    delivery_line = (
        f"🚚 {esc(zone.name)} — {etb(fee) if fee else t('free', lang)}\n"
        if zone
        else ""
    )
    await state.set_state(Checkout.confirm)
    await message.answer(
        t(
            "confirm_order",
            lang,
            items=items,
            delivery=delivery_line,
            total=etb(subtotal + fee),
            address=esc(data.get("address", "")),
            phone=user.phone,
        )
        + "\n\n"
        + t("pay_choose", lang),
        reply_markup=await pay_h.methods_kb(session, shop, lang),
    )


@router.message(Checkout.location, F.text)
async def got_address_text(
    message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    await _after_address(message, state, session, user, lang, message.text.strip())


@router.message(Checkout.landmark, F.text)
async def got_landmark(
    message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    await _after_address(message, state, session, user, lang, message.text.strip())


@router.callback_query(
    StateFilter(Checkout.zone, Checkout.confirm), F.data == "cancel_checkout"
)
async def cb_cancel_checkout(cb: CallbackQuery, state: FSMContext, lang: str):
    await state.clear()
    await cb.message.answer(t("cancelled", lang), reply_markup=main_menu(lang))
    await cb.answer()


@router.callback_query(Checkout.confirm, F.data.startswith("pay:"))
async def cb_pay(
    cb: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    lang: str,
    bot: Bot,
):
    data = await state.get_data()
    choice = cb.data.split(":")[1]
    zone = (
        await session.get(DeliveryZone, data["zone_id"])
        if data.get("zone_id")
        else None
    )
    account = (
        None if choice == "cod" else await session.get(ShopPaymentAccount, int(choice))
    )
    if choice != "cod" and account is None:
        return await cb.answer()
    try:
        order = await orders.place_order(
            session,
            user,
            user.phone,
            data.get("address", ""),
            data.get("lat"),
            data.get("lng"),
            account,
            zone,
        )
    except orders.OutOfStock as e:
        await state.clear()
        return await cb.answer(f"{e}: {t('out_of_stock', lang)}", show_alert=True)
    except orders.BadPaymentMethod:
        return await cb.answer(t("no_methods", lang), show_alert=True)
    except orders.ShopUnavailable:
        await state.clear()
        await cart.clear(session, user.id)
        return await cb.answer(t("shop_unavailable", lang), show_alert=True)
    except orders.BadDeliveryZone:
        await state.clear()
        return await cb.answer(t("choose_zone", lang), show_alert=True)
    if not order:
        await state.clear()
        return await cb.answer(t("cart_empty", lang), show_alert=True)
    await cb.answer()
    if account is None:  # cash on delivery: seller is notified right away
        await state.clear()
        await cb.message.answer(
            t("order_placed", lang, code=order.code), reply_markup=main_menu(lang)
        )
        await notify_new_order(bot, session, order)
    else:  # pay first, then send the reference; seller hears about it once payment is verified
        await pay_h.send_instructions(cb.message, state, order, account, lang)


# ---------------- orders ----------------
async def show_orders(message: Message, session: AsyncSession, user: User, lang: str):
    rows = await orders.list_buyer_orders(session, user.id)
    if not rows:
        return await message.answer(t("no_orders", lang))
    for o in rows:
        await message.answer(
            order_text(o, await orders.get_items(session, o.id), lang),
            reply_markup=buyer_order_kb(o, lang),
        )


@router.callback_query(F.data.startswith("recv:"))
async def cb_received(
    cb: CallbackQuery, session: AsyncSession, user: User, lang: str, bot: Bot
):
    o = await orders.get_order(session, int(cb.data.split(":")[1]))
    if not o or o.buyer_id != user.id or o.status != "shipped":
        return await cb.answer()
    await orders.set_status(session, o, "delivered")
    if o.payment_method == "cod":
        o.payment_status = "paid"  # cash collected
    await cb.message.edit_text(t("received_thanks", lang))
    await cb.message.answer(
        t("rate_prompt", lang), reply_markup=reviews_h.rating_kb(o.id)
    )
    await cb.answer()
    shop = await catalog.get_shop(session, o.shop_id)
    owner = await session.get(User, shop.owner_id)
    try:
        await bot.send_message(
            owner.telegram_id,
            t(
                "order_update",
                owner.language or "en",
                code=o.code,
                status=t("status_delivered", owner.language or "en"),
            ),
        )
    except Exception:
        pass
