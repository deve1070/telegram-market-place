"""Telegram Mini App login: validate `initData` (HMAC-SHA256 keyed by the bot token) as Telegram specifies."""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_init_data(
    init_data: str, bot_token: str, max_age: int = 86400, now: float | None = None
) -> dict | None:
    """Returns {"user": {...}, "auth_date": int} for genuine, fresh data, otherwise None."""
    if not init_data or len(init_data) > 4096:
        return None
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    their_hash = data.pop("hash", None)
    if not their_hash:
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    mine = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mine, their_hash):
        return None
    try:
        auth_date = int(data["auth_date"])
        user = json.loads(data["user"])
    except (KeyError, ValueError):
        return None
    if (now if now is not None else time.time()) - auth_date > max_age:
        return None  # a leaked/old initData stops working after max_age
    if (
        not isinstance(user, dict)
        or not isinstance(user.get("id"), int)
        or user.get("is_bot")
    ):
        return None
    return {"user": user, "auth_date": auth_date}
