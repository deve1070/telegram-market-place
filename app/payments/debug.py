"""Admin diagnostics: show exactly what the bot gets from a provider, so parsers can be fixed from real data."""

import json
import re

from app.core.config import settings
from app.payments import cbe, telebirr
from app.payments.base import FetchError
from app.payments.http import fetch_bytes

MB_CBE_API = "https://mb.cbe.com.et/api/v1/transactions/public/transaction-detail/"  # reported by a third-party project; unverified


async def inspect(provider: str, arg: str, suffix: str = "") -> str:
    out: list[str] = []
    try:
        if provider == "telebirr":
            url = f"{settings.telebirr_receipt_base.rstrip('/')}/{arg}"
            body, ctype = await fetch_bytes(url)
            page = body.decode("utf-8", "ignore")
            out.append(f"GET {url}\nHTTP 200 · {len(body)} bytes · {ctype}")
            tokens = telebirr.tokenize(page)
            try:
                r = telebirr.parse_telebirr_html(page, arg)
                out.append(
                    "PARSED OK:\n"
                    + json.dumps(r.snapshot(), ensure_ascii=False, indent=1)
                )
            except FetchError as e:
                out.append(f"PARSE FAILED: {e}")
            out.append(
                "FIELDS FOUND: "
                + json.dumps(telebirr.extract_fields(tokens), ensure_ascii=False)
            )
            out.append(
                "TOKENS (first 60):\n"
                + "\n".join(f"{i}: {t[:70]}" for i, t in enumerate(tokens[:60]))
            )
        elif provider == "cbe" and "mbreciept" in arg.lower():
            token = (
                re.sub(r"\W", "", arg.rstrip("/").split("/")[-1].split("?")[0]) or arg
            )
            body, ctype = await fetch_bytes(MB_CBE_API + token)
            out.append(
                f"GET {MB_CBE_API}<token>\nHTTP 200 · {len(body)} bytes · {ctype}\n"
                + body.decode("utf-8", "ignore")[:2500]
            )
        elif provider == "cbe":
            if not suffix:
                return "usage: /checkref cbe <FT reference> <8 account digits>   or   /checkref cbe <mbreciept link>"
            url = f"{settings.cbe_receipt_base}?id={arg}{suffix[-8:]}"
            body, ctype = await fetch_bytes(url, ssl=settings.cbe_verify_ssl)
            out.append(
                f"GET {url}\nHTTP 200 · {len(body)} bytes · {ctype}\nstarts with: {body[:12]!r}"
            )
            if body.startswith(b"%PDF"):
                try:
                    r = await cbe.fetch(arg, suffix)
                    out.append(
                        "PARSED OK:\n"
                        + json.dumps(r.snapshot(), ensure_ascii=False, indent=1)
                    )
                except FetchError as e:
                    out.append(f"PARSE FAILED: {e}")
        else:
            return "providers with a checker: telebirr, cbe"
    except FetchError as e:
        out.append(f"FETCH FAILED: {e.reason} {e.detail}")
        out.append(f"proxy: {settings.verifier_proxy or '(none)'}")
    return "\n\n".join(out)
