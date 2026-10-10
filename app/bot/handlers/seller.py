import re
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.notify import notify_buyer_status
from app.bot.views import order_text, seller_order_kb
from app.core.config import settings
from app.core.utils import SLUG_RE, esc, etb, norm_phone
from app.i18n import t
from app.models import Product, Shop, User
from app.bot.handlers.plans import plan_status_line
from app.services import catalog, orders, plans, verification

router = Router()


class RegShop(StatesGroup):
    name = State()
    slug = State()
    phone = State()
    city = State()


class AddProduct(StatesGroup):
    photo = State()
    title = State()
    price = State()
    stock = State()
    category = State()


def _num(text: str, integer=False):
    try:
        v = Decimal(text.replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None
    if v <= 0 or (integer and v != v.to_integral_value()):
        return None
    return int(v) if integer else v.quantize(Decimal("0.01"))


# ---------------- entry ----------------
async def sell_entry(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str):
    shop = await catalog.get_shop_by_owner(session, user.id)
    if not shop:
        await state.set_state(RegShop.name)
        return await message.answer(t("sell_intro", lang) + "\n(/cancel)")
    if shop.status != "active":
        return await message.answer(t("shop_pending", lang))
    b = InlineKeyboardBuilder()
    if settings.webapp_url:  # the full dashboard inside Telegram
        b.button(text=t("btn_dashboard", lang), web_app=WebAppInfo(url=f"{settings.webapp_url}?view=seller"))
    b.button(text=t("add_product", lang), callback_data="s:add")
    b.button(text=t("my_products", lang), callback_data="s:prods")
    b.button(text=t("shop_orders", lang), callback_data="s:orders")
    b.button(text=t("btn_review", lang), callback_data="s:review")
    b.button(text=t("btn_payments", lang), callback_data="s:pay")
    b.button(text=t("btn_zones", lang), callback_data="s:zones")
    b.button(text=t("btn_my_reviews", lang), callback_data="s:reviews")
    b.button(text=t("btn_plan", lang), callback_data="s:plan")
    b.button(text=t("btn_verify", lang), callback_data="s:verify")
    b.button(text=t("share_link", lang), callback_data="s:link")
    b.adjust(*((1, 2) if settings.webapp_url else (2,)))
    vstatus = await verification.status_for_shop(session, shop)
    vline = f"\n{t('verify_line_' + vstatus, lang)}" if vstatus != "none" else ""
    await message.answer(f"{t('seller_menu', lang, name=esc(shop.name))}\n{await plan_status_line(session, shop, lang)}{vline}",
                         reply_markup=b.as_markup())


# ---------------- shop registration ----------------
@router.message(RegShop.name, F.text)
async def reg_name(message: Message, state: FSMContext, lang: str, bot: Bot):
    await state.update_data(name=message.text.strip()[:100])
    await state.set_state(RegShop.slug)
    await message.answer(t("reg_slug", lang, bot=(await bot.me()).username))


@router.message(RegShop.slug, F.text)
async def reg_slug(message: Message, state: FSMContext, session: AsyncSession, lang: str):
    slug = message.text.strip().lower()
    if not SLUG_RE.match(slug):
        return await message.answer(t("slug_invalid", lang))
    if await catalog.get_shop_by_slug(session, slug):
        return await message.answer(t("slug_taken", lang))
    await state.update_data(slug=slug)
    await state.set_state(RegShop.phone)
    await message.answer(t("reg_phone", lang))


@router.message(RegShop.phone, F.text)
async def reg_phone(message: Message, state: FSMContext, lang: str):
    phone = norm_phone(message.text)
    if not phone:
        return await message.answer(t("bad_phone", lang))
    await state.update_data(phone=phone)
    await state.set_state(RegShop.city)
    await message.answer(t("reg_city", lang))


@router.message(RegShop.city, F.text)
async def reg_city(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str, bot: Bot):
    d = await state.get_data()
    await state.clear()
    shop = Shop(owner_id=user.id, slug=d["slug"], name=d["name"], phone=d["phone"], city=message.text.strip()[:60])
    session.add(shop)
    await session.flush()
    await message.answer(t("reg_done", lang))
    b = InlineKeyboardBuilder()
    b.button(text="✅ Approve", callback_data=f"shopok:{shop.id}")
    b.button(text="❌ Reject", callback_data=f"shopno:{shop.id}")
    text = (f"🆕 <b>Shop request</b>\n{esc(shop.name)} (@{esc(shop.slug)})\n📍 {esc(shop.city)} · 📞 {shop.phone}\n"
            f"Owner: {esc(user.name)} (id {user.telegram_id})")
    for admin_id in settings.admin_id_set:
        try:
            await bot.send_message(admin_id, text, reply_markup=b.as_markup())
        except Exception:
            pass


# ---------------- seller menu ----------------
async def _my_active_shop(session: AsyncSession, user: User):
    shop = await catalog.get_shop_by_owner(session, user.id)
    return shop if shop and shop.status == "active" else None


@router.callback_query(F.data == "s:link")
async def cb_link(cb: CallbackQuery, session: AsyncSession, user: User, lang: str, bot: Bot):
    shop = await _my_active_shop(session, user)
    if shop:
        await cb.message.answer(t("your_link", lang, link=f"https://t.me/{(await bot.me()).username}?start=shop_{shop.slug}"))
    await cb.answer()


@router.callback_query(F.data == "s:orders")
async def cb_orders(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    shop = await _my_active_shop(session, user)
    if not shop:
        return await cb.answer(t("not_active", lang), show_alert=True)
    rows = await orders.list_shop_orders(session, shop.id)
    if not rows:
        await cb.message.answer(t("no_orders", lang))
    for o in rows:
        await cb.message.answer(order_text(o, await orders.get_items(session, o.id), lang),
                                reply_markup=seller_order_kb(o, lang))
    await cb.answer()


@router.callback_query(F.data.startswith("ostat:"))
async def cb_order_status(cb: CallbackQuery, session: AsyncSession, user: User, lang: str, bot: Bot):
    _, oid, new = cb.data.split(":")
    o = await orders.get_order(session, int(oid))
    shop = await _my_active_shop(session, user)
    allowed = o and shop and o.shop_id == shop.id and (
        orders.NEXT_STATUS.get(o.status) == new or (new == "cancelled" and o.status in orders.CANCELLABLE))
    if not allowed:
        return await cb.answer()
    await orders.set_status(session, o, new)
    items = await orders.get_items(session, o.id)
    await cb.message.edit_text(order_text(o, items, lang), reply_markup=seller_order_kb(o, lang))
    await cb.answer()
    await notify_buyer_status(bot, session, o)


def _products_view(products, lang: str):
    b = InlineKeyboardBuilder()
    for p in products:
        icon = t("hide", lang) if p.status == "active" else t("show", lang)
        b.button(text=f"{icon} · {p.title[:25]} · {etb(p.price)} ({p.stock})", callback_data=f"ptog:{p.id}")
    b.adjust(1)
    return b.as_markup()


@router.callback_query(F.data == "s:prods")
async def cb_products(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    shop = await _my_active_shop(session, user)
    if not shop:
        return await cb.answer(t("not_active", lang), show_alert=True)
    products = await catalog.list_products(session, shop.id, include_hidden=True, limit=40)
    await cb.message.answer(t("my_products", lang), reply_markup=_products_view(products, lang))
    await cb.answer()


@router.callback_query(F.data.startswith("ptog:"))
async def cb_toggle(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    shop = await _my_active_shop(session, user)
    p = await catalog.get_product(session, int(cb.data.split(":")[1]))
    if not (shop and p and p.shop_id == shop.id):
        return await cb.answer()
    p.status = "hidden" if p.status == "active" else "active"
    products = await catalog.list_products(session, shop.id, include_hidden=True, limit=40)
    await cb.message.edit_reply_markup(reply_markup=_products_view(products, lang))
    await cb.answer()


# ---------------- add product ----------------
@router.callback_query(F.data == "s:add")
async def cb_add_product(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str):
    shop = await _my_active_shop(session, user)
    if not shop:
        return await cb.answer(t("not_active", lang), show_alert=True)
    ok, used, limit = await plans.can_add_product(session, shop)
    if not ok:
        plan = await plans.effective_plan(session, shop)
        return await cb.answer(t("plan_limit", lang, plan=plan.name, limit=plans.limit_label(limit), used=used), show_alert=True)
    await state.set_state(AddProduct.photo)
    await cb.message.answer(t("send_photo", lang) + "\n(/cancel)")
    await cb.answer()


async def _ask_category(message: Message, state: FSMContext, session: AsyncSession, lang: str):
    b = InlineKeyboardBuilder()
    for c in await catalog.list_categories(session):
        b.button(text=catalog.cat_name(c, lang), callback_data=f"pcat:{c.id}")
    b.adjust(2)
    await state.set_state(AddProduct.category)
    await message.answer(t("ask_cat", lang), reply_markup=b.as_markup())


@router.message(AddProduct.photo, F.photo)
async def add_photo(message: Message, state: FSMContext, session: AsyncSession, lang: str):
    await state.update_data(photo=message.photo[-1].file_id)
    parts = [x.strip() for x in (message.caption or "").split("|")]
    if len(parts) == 3 and parts[0] and _num(parts[1]) and _num(parts[2], integer=True):
        await state.update_data(title=parts[0][:150], price=str(_num(parts[1])), stock=_num(parts[2], integer=True))
        return await _ask_category(message, state, session, lang)
    await state.set_state(AddProduct.title)
    await message.answer(t("ask_title", lang))


@router.message(AddProduct.photo)
async def add_photo_retry(message: Message, lang: str):
    await message.answer(t("send_photo", lang))


@router.message(AddProduct.title, F.text)
async def add_title(message: Message, state: FSMContext, lang: str):
    await state.update_data(title=message.text.strip()[:150])
    await state.set_state(AddProduct.price)
    await message.answer(t("ask_price", lang))


@router.message(AddProduct.price, F.text)
async def add_price(message: Message, state: FSMContext, lang: str):
    v = _num(message.text)
    if v is None:
        return await message.answer(t("bad_number", lang))
    await state.update_data(price=str(v))
    await state.set_state(AddProduct.stock)
    await message.answer(t("ask_stock", lang))


@router.message(AddProduct.stock, F.text)
async def add_stock(message: Message, state: FSMContext, session: AsyncSession, lang: str):
    v = _num(message.text, integer=True)
    if v is None:
        return await message.answer(t("bad_number", lang))
    await state.update_data(stock=v)
    await _ask_category(message, state, session, lang)


@router.callback_query(AddProduct.category, F.data.startswith("pcat:"))
async def add_category(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str):
    d = await state.get_data()
    await state.clear()
    shop = await _my_active_shop(session, user)
    if not shop:
        return await cb.answer(t("not_active", lang), show_alert=True)
    ok, used, limit = await plans.can_add_product(session, shop)  # re-check: the limit could have been reached meanwhile
    if not ok:
        plan = await plans.effective_plan(session, shop)
        return await cb.answer(t("plan_limit", lang, plan=plan.name, limit=plans.limit_label(limit), used=used), show_alert=True)
    session.add(Product(shop_id=shop.id, category_id=int(cb.data.split(":")[1]), title=d["title"],
                        price=Decimal(d["price"]), stock=d["stock"], photo_file_id=d["photo"]))
    await cb.message.answer(t("product_saved", lang))
    await cb.answer()