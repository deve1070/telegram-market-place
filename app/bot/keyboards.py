from aiogram.types import WebAppInfo
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from app.core.config import settings
from app.i18n import t


def main_menu(lang: str):
    b = ReplyKeyboardBuilder()
    app_url = (
        settings.webapp_url
    )  # '' unless the bot runs behind public https (the Mini App needs it)
    if app_url:
        b.button(text=t("menu_app", lang), web_app=WebAppInfo(url=app_url))
    for k in ("menu_browse", "menu_cart", "menu_orders", "menu_sell", "menu_lang"):
        b.button(text=t(k, lang))
    b.adjust(*((1, 2, 2, 1) if app_url else (2, 2, 1)))
    return b.as_markup(resize_keyboard=True)
