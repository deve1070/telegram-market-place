"""Seller screens: manage delivery zones and fees."""

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import esc, etb
from app.i18n import t
from app.models import DeliveryZone, Shop, User
from app.services import catalog, delivery

router = Router()


class AddZone(StatesGroup):
    name = State()
    fee = State()


class Preset(StatesGroup):
    fee = State()


def _fee(text: str) -> Decimal | None:
    try:
        v = Decimal(text.replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None
    return v.quantize(Decimal("0.01")) if 0 <= v <= 100000 else None


async def _my_shop(session: AsyncSession, user: User) -> Shop | None:
    shop = await catalog.get_shop_by_owner(session, user.id)
    return shop if shop and shop.status == "active" else None


async def _view(session: AsyncSession, shop: Shop, lang: str):
    b = InlineKeyboardBuilder()
    for z in await delivery.list_zones(session, shop.id):
        b.button(
            text=f"🗑 {z.name} — {etb(z.fee) if z.fee else t('free', lang)}",
            callback_data=f"zdel:{z.id}",
        )
    b.button(text=t("btn_add_zone", lang), callback_data="zadd")
    b.button(text=t("btn_preset_addis", lang), callback_data="zpreset")
    b.adjust(1)
    return t("zones_title", lang), b.as_markup()


@router.callback_query(F.data == "s:zones")
async def cb_zones(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    shop = await _my_shop(session, user)
    if not shop:
        return await cb.answer(t("not_active", lang), show_alert=True)
    text, kb = await _view(session, shop, lang)
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("zdel:"))
async def cb_zone_delete(
    cb: CallbackQuery, session: AsyncSession, user: User, lang: str
):
    shop = await _my_shop(session, user)
    z = await session.get(DeliveryZone, int(cb.data.split(":")[1]))
    if not (shop and z and z.shop_id == shop.id):
        return await cb.answer()
    z.active = False
    text, kb = await _view(session, shop, lang)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "zadd")
async def cb_zone_add(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    if not await _my_shop(session, user):
        return await cb.answer()
    await state.set_state(AddZone.name)
    await cb.message.answer(t("zone_ask_name", lang))
    await cb.answer()


@router.message(AddZone.name, F.text)
async def zone_name(message: Message, state: FSMContext, lang: str):
    await state.update_data(name=message.text.strip()[:60])
    await state.set_state(AddZone.fee)
    await message.answer(t("zone_ask_fee", lang))


@router.message(AddZone.fee, F.text)
async def zone_fee(
    message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    fee = _fee(message.text)
    if fee is None:
        return await message.answer(t("bad_number", lang))
    d = await state.get_data()
    shop = await _my_shop(session, user)
    await state.clear()
    if shop:
        await delivery.add_zone(session, shop.id, d["name"], fee)
        await message.answer(t("zone_saved", lang, name=esc(d["name"])))


@router.callback_query(F.data == "zpreset")
async def cb_preset(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    if not await _my_shop(session, user):
        return await cb.answer()
    await state.set_state(Preset.fee)
    await cb.message.answer(t("zone_preset_fee", lang))
    await cb.answer()


@router.message(Preset.fee, F.text)
async def preset_fee(
    message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    fee = _fee(message.text)
    if fee is None:
        return await message.answer(t("bad_number", lang))
    shop = await _my_shop(session, user)
    await state.clear()
    if shop:
        n = await delivery.add_addis_preset(session, shop.id, fee)
        await message.answer(t("zone_preset_done", lang, n=n))
