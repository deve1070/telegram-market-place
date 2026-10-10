from aiogram import F, Bot, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers import buyer, seller
from app.bot.keyboards import main_menu
from app.i18n import LANGS, all_t, t
from app.models import User
from app.services import catalog

router = Router()


def lang_kb():
    b = InlineKeyboardBuilder()
    for code, name in LANGS.items():
        b.button(text=name, callback_data=f"lang:{code}")
    return b.as_markup()


async def apply_payload(message: Message, session: AsyncSession, user: User, lang: str, payload: str | None):
    """Deep link: t.me/<bot>?start=shop_<slug> opens that seller's storefront."""
    if payload and payload.startswith("shop_"):
        shop = await catalog.get_shop_by_slug(session, payload[5:])
        if shop and shop.status == "active":
            await buyer.open_shop(message, session, user, lang, shop)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext,
                    session: AsyncSession, user: User, lang: str):
    await state.clear()
    if user.language is None:
        await state.update_data(payload=command.args)
        return await message.answer(t("choose_lang"), reply_markup=lang_kb())
    await message.answer(t("welcome", lang), reply_markup=main_menu(lang))
    await apply_payload(message, session, user, lang, command.args)


@router.callback_query(F.data.startswith("lang:"))
async def set_lang(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User):
    user.language = cb.data.split(":")[1]
    payload = (await state.get_data()).get("payload")
    await state.clear()
    await cb.message.answer(t("welcome", user.language), reply_markup=main_menu(user.language))
    await apply_payload(cb.message, session, user, user.language, payload)
    await cb.answer(t("lang_set", user.language))


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext, lang: str):
    await state.clear()
    await message.answer(t("cancelled", lang), reply_markup=main_menu(lang))


# ---- main menu buttons (registered first so they work even mid-FSM) ----
@router.message(F.text.in_(all_t("menu_browse")))
async def m_browse(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str):
    await state.clear()
    await buyer.show_shops(message, session, lang)


@router.message(F.text.in_(all_t("menu_cart")))
async def m_cart(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str):
    await state.clear()
    await buyer.show_cart(message, session, user, lang)


@router.message(F.text.in_(all_t("menu_orders")))
async def m_orders(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str):
    await state.clear()
    await buyer.show_orders(message, session, user, lang)


@router.message(F.text.in_(all_t("menu_sell")))
async def m_sell(message: Message, state: FSMContext, session: AsyncSession, user: User, lang: str, bot: Bot):
    await state.clear()
    await seller.sell_entry(message, state, session, user, lang)


@router.message(F.text.in_(all_t("menu_lang")))
async def m_lang(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(t("choose_lang"), reply_markup=lang_kb())
