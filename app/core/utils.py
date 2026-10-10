import html
import re
from decimal import Decimal

esc = html.escape


def etb(amount) -> str:
    return f"{Decimal(amount):,.2f} ETB"


def norm_phone(raw: str) -> str | None:
    """09xxxxxxxx / 07xxxxxxxx / 9xxxxxxxx / 251... / +251... -> +2519xxxxxxxx"""
    digits = re.sub(r"[\s\-()]", "", raw)
    m = re.fullmatch(r"(?:\+?251|0)?([79]\d{8})", digits)
    return f"+251{m.group(1)}" if m else None


SLUG_RE = re.compile(r"^[a-z0-9_]{3,30}$")
