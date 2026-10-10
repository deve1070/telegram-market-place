import json
from pathlib import Path

_DIR = Path(__file__).parent / "locales"
_L = {p.stem: json.loads(p.read_text("utf-8")) for p in _DIR.glob("*.json")}
LANGS = {"en": "English", "am": "አማርኛ"}


def t(key: str, lang: str | None = "en", **kw) -> str:
    s = _L.get(lang or "en", {}).get(key) or _L["en"].get(key, key)
    return s.format(**kw) if kw else s


def all_t(key: str) -> set[str]:
    """Every translation of a key (used to match reply-keyboard button texts)."""
    return {_L[l].get(key) or _L["en"][key] for l in _L}
