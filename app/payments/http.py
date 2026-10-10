import logging

import aiohttp

from app.core.config import settings
from app.payments.base import FetchError

log = logging.getLogger(__name__)


async def fetch_bytes(url: str, *, ssl: bool = True) -> tuple[bytes, str]:
    """GET with timeout and optional proxy. Raises FetchError (never anything else)."""
    timeout = aiohttp.ClientTimeout(total=settings.verifier_timeout)
    try:
        async with aiohttp.ClientSession(
            timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}
        ) as s:
            async with s.get(url, ssl=ssl, proxy=settings.verifier_proxy or None) as r:
                if r.status == 404:
                    raise FetchError("not_found", "HTTP 404")
                if r.status != 200:
                    raise FetchError("fetch_failed", f"HTTP {r.status}")
                return await r.read(), r.headers.get("Content-Type", "")
    except FetchError as e:
        log.warning("receipt fetch failed: %s (%s)", e, url)
        raise
    except Exception as e:
        log.warning("receipt fetch error: %r (%s)", e, url)
        raise FetchError("fetch_failed", repr(e))
