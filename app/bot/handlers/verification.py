"""Seller identity verification: seller submits ID + selfie (+ trade license); admins approve or reject."""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InputMediaPhoto, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.utils import esc
from app.i18n import t
from app.models import SellerVerification, Shop, User
from app.services import catalog, verification

router = Router()
log = logging.getLogger(__name__)
IS_ADMIN = F.from_user.id.in_(settings.admin_id_set)

DOC_LABEL = {
    "national_id": "National ID (Fayda)",
    "passport": "Passport",
    "driver_license": "Driver's license",
    "kebele_id": "Kebele ID",
}
REASON_LABEL = {
    "blurry": "Photo is blurry / unreadable",
    "mismatch": "Name doesn't match the document",
    "selfie": "Selfie doesn't match the document",
    "invalid": "Document invalid or expired",
    "other": "Other",
}


class VerifyForm(StatesGroup):
    name = State()
    id_photo = State()
    selfie = State()
    license = State()


async def _my_shop(session: AsyncSession, user: User) -> Shop | None:
    shop = await catalog.get_shop_by_owner(session, user.id)
    return shop if shop and shop.status == "active" else None


# =============== seller flow ===============
@router.callback_query(F.data == "s:verify")
async def cb_verify(cb: CallbackQuery, session: AsyncSession, user: User, lang: str):
    shop = await _my_shop(session, user)
    if not shop:
        return await cb.answer(t("not_active", lang), show_alert=True)
    state_ = await verification.status_for_shop(session, shop)
    if state_ == "verified":
        return await cb.answer(t("verify_already", lang), show_alert=True)
    if state_ == "pending":
        return await cb.answer(t("verify_pending", lang), show_alert=True)
    b = InlineKeyboardBuilder()
    for key in verification.DOC_TYPES:
        b.button(text=DOC_LABEL[key], callback_data=f"vkt:{key}")
    b.adjust(2)
    retention = t(
        "verify_ret_kept" if settings.kyc_keep_documents else "verify_ret_deleted", lang
    )
    await cb.message.answer(
        t("verify_intro", lang, retention=retention), reply_markup=b.as_markup()
    )
    await cb.answer()


@router.callback_query(F.data.startswith("vkt:"))
async def cb_doc_type(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, lang: str
):
    key = cb.data.split(":")[1]
    if key not in verification.DOC_TYPES or not await _my_shop(session, user):
        return await cb.answer()
    await state.set_state(VerifyForm.name)
    await state.update_data(doc_type=key)
    await cb.message.answer(t("verify_ask_name", lang) + "\n(/cancel)")
    await cb.answer()


@router.message(VerifyForm.name, F.text)
async def got_name(message: Message, state: FSMContext, lang: str):
    await state.update_data(full_name=message.text.strip()[:100])
    await state.set_state(VerifyForm.id_photo)
    await message.answer(t("verify_ask_id", lang))


@router.message(VerifyForm.id_photo, F.photo)
async def got_id(message: Message, state: FSMContext, lang: str):
    await state.update_data(id_photo=message.photo[-1].file_id)
    await state.set_state(VerifyForm.selfie)
    await message.answer(t("verify_ask_selfie", lang))


@router.message(VerifyForm.selfie, F.photo)
async def got_selfie(message: Message, state: FSMContext, lang: str):
    await state.update_data(selfie=message.photo[-1].file_id)
    await state.set_state(VerifyForm.license)
    skip = InlineKeyboardBuilder()
    skip.button(text=t("btn_skip", lang), callback_data="vkskip")
    await message.answer(t("verify_ask_license", lang), reply_markup=skip.as_markup())


@router.message(VerifyForm.id_photo)
@router.message(VerifyForm.selfie)
@router.message(VerifyForm.license)
async def need_photo(message: Message, lang: str):
    await message.answer(t("verify_need_photo", lang))


@router.message(VerifyForm.license, F.photo)
async def got_license(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    lang: str,
    bot: Bot,
):
    await _submit(message, state, session, user, lang, bot, message.photo[-1].file_id)


@router.callback_query(VerifyForm.license, F.data == "vkskip")
async def skip_license(
    cb: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    lang: str,
    bot: Bot,
):
    await cb.answer()
    await _submit(cb.message, state, session, user, lang, bot, None)


async def _submit(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    lang: str,
    bot: Bot,
    license_photo: str | None,
):
    d = await state.get_data()
    await state.clear()
    shop = await _my_shop(session, user)
    v = (
        await verification.create_submission(
            session,
            shop,
            d["doc_type"],
            d["full_name"],
            d["id_photo"],
            d["selfie"],
            license_photo,
        )
        if shop
        else None
    )
    if v is None:
        return await message.answer(t("verify_pending", lang))
    await message.answer(t("verify_submitted", lang))
    await send_card(bot, list(settings.admin_id_set), v, shop, user)


# =============== admin side ===============
def _card_text(v: SellerVerification, shop: Shop, owner: User) -> str:
    return (
        f"🪪 <b>Seller verification #{v.id}</b>\nShop: {esc(shop.name)} (@{esc(shop.slug)}) · {esc(shop.city)} · {shop.phone}\n"
        f"Owner: {esc(owner.name)} (tg id <code>{owner.telegram_id}</code>)\n"
        f"Document: {DOC_LABEL.get(v.doc_type, v.doc_type)}\nName on document: <b>{esc(v.full_name)}</b>\n\n"
        "Check: name matches · photo readable · selfie matches the ID"
    )


def _card_kb(v: SellerVerification):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Approve", callback_data=f"vk:ok:{v.id}")
    b.button(text="❌ Reject", callback_data=f"vk:no:{v.id}")
    return b.as_markup()


async def send_card(
    bot: Bot, admin_ids: list[int], v: SellerVerification, shop: Shop, owner: User
) -> None:
    sent_ids: dict[str, list[int]] = {}
    photos = [
        (v.id_photo, "🪪 ID"),
        (v.selfie_photo, "🤳 Selfie with ID"),
        (v.license_photo, "📄 Trade license"),
    ]
    media = [InputMediaPhoto(media=fid, caption=cap) for fid, cap in photos if fid]
    for admin_id in admin_ids:
        try:
            if media:
                sent = await bot.send_media_group(admin_id, media)
                sent_ids[str(admin_id)] = [m.message_id for m in sent]
            await bot.send_message(
                admin_id, _card_text(v, shop, owner), reply_markup=_card_kb(v)
            )
        except Exception:
            log.warning(
                "could not send verification card to admin %s", admin_id, exc_info=True
            )
    if sent_ids:
        v.admin_msgs = {**(v.admin_msgs or {}), **sent_ids}


async def erase_admin_photos(bot: Bot, v: SellerVerification) -> None:
    """After a decision, delete the ID/selfie photo messages from every admin's chat (unless KYC_KEEP_DOCUMENTS is on).
    Telegram only lets a bot delete its own messages for ~48h, so decide promptly."""
    if settings.kyc_keep_documents or not v.admin_msgs:
        return
    for admin_id, ids in v.admin_msgs.items():
        for mid in ids:
            try:
                await bot.delete_message(int(admin_id), mid)
            except Exception:
                log.info(
                    "could not delete verification photo %s in chat %s", mid, admin_id
                )
    v.admin_msgs = None


async def send_pending(bot: Bot, chat_id: int, session: AsyncSession) -> int:
    pending = await verification.list_pending(session)
    for v in pending:
        shop = await session.get(Shop, v.shop_id)
        owner = await session.get(User, shop.owner_id)
        await send_card(bot, [chat_id], v, shop, owner)
    return len(pending)


@router.message(Command("verifications"), IS_ADMIN)
async def cmd_verifications(message: Message, session: AsyncSession, bot: Bot):
    if not await send_pending(bot, message.chat.id, session):
        await message.answer("No verifications waiting.")


@router.callback_query(F.data.regexp(r"^vk:ok:\d+$"), IS_ADMIN)
async def cb_approve(cb: CallbackQuery, session: AsyncSession, bot: Bot):
    v = await session.get(SellerVerification, int(cb.data.split(":")[2]))
    shop = await verification.approve(session, v, cb.from_user.id) if v else None
    if shop is None:
        return await cb.answer("Already handled.", show_alert=True)
    await cb.message.edit_text(
        f"{cb.message.html_text}\n\n✅ Approved by {esc(cb.from_user.full_name)}"
    )
    await cb.answer()
    await erase_admin_photos(bot, v)
    await _tell_owner(bot, session, shop, "verify_approved")


@router.callback_query(F.data.regexp(r"^vk:no:\d+$"), IS_ADMIN)
async def cb_reject_pick(cb: CallbackQuery):
    vid = cb.data.split(":")[2]
    b = InlineKeyboardBuilder()
    for code in verification.REJECT_REASONS:
        b.button(text=REASON_LABEL[code], callback_data=f"vkr:{vid}:{code}")
    b.adjust(1)
    await cb.message.edit_reply_markup(reply_markup=b.as_markup())
    await cb.answer("Pick a reason")


@router.callback_query(F.data.regexp(r"^vkr:\d+:\w+$"), IS_ADMIN)
async def cb_reject(cb: CallbackQuery, session: AsyncSession, bot: Bot):
    _, vid, code = cb.data.split(":")
    v = await session.get(SellerVerification, int(vid))
    shop = await verification.reject(session, v, cb.from_user.id, code) if v else None
    if shop is None:
        return await cb.answer("Already handled.", show_alert=True)
    await cb.message.edit_text(
        f"{cb.message.html_text}\n\n❌ Rejected: {REASON_LABEL[code]}"
    )
    await cb.answer()
    await erase_admin_photos(bot, v)
    await _tell_owner(bot, session, shop, "verify_rejected", reason_code=code)


async def _tell_owner(
    bot: Bot,
    session: AsyncSession,
    shop: Shop,
    key: str,
    reason_code: str | None = None,
) -> None:
    owner = await session.get(User, shop.owner_id)
    lang = owner.language or "en"
    kw = {"reason": t(f"vrej_{reason_code}", lang)} if reason_code else {}
    try:
        await bot.send_message(owner.telegram_id, t(key, lang, **kw))
    except Exception:
        log.warning("could not notify seller about verification result", exc_info=True)
