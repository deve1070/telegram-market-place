from aiogram import BaseMiddleware
from sqlalchemy import select

from app.core.db import session_factory
from app.models import User


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with session_factory() as session:
            data["session"] = session
            result = await handler(event, data)
            await session.commit()
            return result


class UserMiddleware(BaseMiddleware):
    """Loads/creates the User row and exposes `user` and `lang` to handlers."""

    async def __call__(self, handler, event, data):
        tg = data.get("event_from_user")
        if tg is None or tg.is_bot:
            return await handler(event, data)
        session = data["session"]
        user = await session.scalar(select(User).where(User.telegram_id == tg.id))
        if user is None:
            user = User(telegram_id=tg.id, name=tg.full_name)
            session.add(user)
            await session.flush()
        data["user"] = user
        data["lang"] = user.language or "en"
        return await handler(event, data)
