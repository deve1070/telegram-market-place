"""Run the REAL dispatcher, handlers, middlewares and database against a FAKE Telegram: updates go in as raw JSON
(exactly like a webhook delivers them) and every Bot API call the handlers make is recorded for assertions."""
import itertools
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import File, Message, User

BOT_ID = 999
_ids = itertools.count(1)


def fresh_dispatcher(storage=None):
    """Production builds ONE dispatcher per process. Tests build several, and aiogram lets a router belong to only
    one parent, so detach the module-level routers before building another dispatcher."""
    from app.bot import main
    from app.bot.handlers import (admin, adminpanel, buyer, delivery, payments, plans, reports, reviews, seller, start,
                                  verification)
    for module in (admin, adminpanel, buyer, delivery, payments, plans, reports, reviews, seller, start, verification):
        module.router._parent_router = None
    return main.build_dispatcher(storage)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []

    async def close(self):
        pass

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield b"\xff\xd8FAKE-JPEG-BYTES\xff\xd9"  # what bot.download() reads for any file

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        data = method.model_dump(exclude_none=True)
        self.calls.append((name, data))
        chat_id = data.get("chat_id", 1)

        def msg():
            return Message.model_validate({"message_id": next(_ids) + 5000, "date": datetime.now(timezone.utc),
                                           "chat": {"id": int(chat_id), "type": "private"}, "text": data.get("text")})
        if name == "GetFile":
            return File(file_id=data["file_id"], file_unique_id="u-" + data["file_id"], file_path="photos/" + data["file_id"] + ".jpg")
        if name == "GetMe":
            return User(id=BOT_ID, is_bot=True, first_name="Test", username="testbot")
        if name == "SendMediaGroup":
            return [msg() for _ in data["media"]]
        if name == "SendPhoto":  # a real Telegram answer carries the new file_id; the Mini App photo upload stores it
            n = next(_ids)
            return Message.model_validate({"message_id": n + 5000, "date": datetime.now(timezone.utc), "chat": {"id": int(chat_id), "type": "private"},
                                           "photo": [{"file_id": f"uploaded-{n}", "file_unique_id": f"uu{n}", "width": 10, "height": 10}]})
        if name.startswith("Send"):
            return msg()
        return True


class Tg:
    """A tiny Telegram client: say(), photo(), press() and helpers to read what the bot replied."""

    def __init__(self):
        self.session = FakeSession()
        self.bot = Bot("1:test", session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = fresh_dispatcher(MemoryStorage())
        self.dp.errors.handlers.clear()  # let handler exceptions fail the test instead of being swallowed

    # ---- sending updates ----
    @staticmethod
    def _user(uid, name):
        return {"id": uid, "is_bot": False, "first_name": name}

    async def _feed(self, update):
        update["update_id"] = next(_ids)
        await self.dp.feed_raw_update(self.bot, update)

    async def say(self, uid, text, name="User"):
        await self._feed({"message": {"message_id": next(_ids), "date": 1700000000, "chat": {"id": uid, "type": "private"},
                                      "from": self._user(uid, name), "text": text}})

    async def photo(self, uid, caption=None, name="User"):
        n = next(_ids)
        m = {"message_id": n, "date": 1700000000, "chat": {"id": uid, "type": "private"}, "from": self._user(uid, name),
             "photo": [{"file_id": f"photo-{n}", "file_unique_id": f"u{n}", "width": 10, "height": 10}]}
        if caption:
            m["caption"] = caption
        await self._feed({"message": m})

    async def click(self, uid, data, name="User"):
        await self._feed({"callback_query": {
            "id": str(next(_ids)), "from": self._user(uid, name), "chat_instance": "ci", "data": data,
            "message": {"message_id": next(_ids), "date": 1700000000, "chat": {"id": uid, "type": "private"},
                        "from": {"id": BOT_ID, "is_bot": True, "first_name": "Test"}, "text": "x"}}})

    # ---- reading what the bot did ----
    def _buttons(self, data):
        mk = data.get("reply_markup") or {}
        return [b for row in mk.get("inline_keyboard", []) for b in row]

    def find_button(self, uid, label):
        """Newest inline button whose text contains `label` in any message the bot sent/edited for this chat."""
        for name, data in reversed(self.session.calls):
            if data.get("chat_id") != uid:
                continue
            for b in self._buttons(data):
                if label in b["text"] and b.get("callback_data"):
                    return b["callback_data"]
        return None

    async def press(self, uid, label, name="User"):
        data = self.find_button(uid, label)
        assert data, f"no button containing {label!r} for chat {uid}. Recent: {self.texts(uid)[-3:]}"
        await self.click(uid, data, name)
        return data

    def texts(self, uid):
        out = []
        for name, data in self.session.calls:
            if data.get("chat_id") == uid and name in ("SendMessage", "EditMessageText", "SendPhoto", "EditMessageCaption"):
                out.append(data.get("text") or data.get("caption") or "")
        return out

    def last(self, uid):
        t = self.texts(uid)
        return t[-1] if t else ""

    def alerts(self, uid=None):
        """Text of the answerCallbackQuery calls (toasts/alerts)."""
        return [d.get("text", "") for n, d in self.session.calls if n == "AnswerCallbackQuery"]

    def sent(self, name):
        return [d for n, d in self.session.calls if n == name]