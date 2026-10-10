"""CBE receipt adapter (PDF). Needs the last 8 digits of an account number on the transfer."""
import io
import re

from app.core.config import settings
from app.payments.base import FetchError, Receipt
from app.payments.http import fetch_bytes
from app.payments.matching import parse_amount, parse_dt


def parse_cbe_text(text: str, reference: str) -> Receipt:
    flat = re.sub(r"[ \t]+", " ", text)
    amount = parse_amount((re.search(r"Transferred Amount\s*:?\s*([\d,\.]+)", flat, re.I) or [None, None])[1])
    if amount is None:
        raise FetchError("parse_failed")
    accounts = re.findall(r"Account\s*:?\s*([\dXx\*]{4,})", flat, re.I)  # [payer, receiver]
    receiver = re.search(r"Receiver\s*:?\s*([^\n]+)", flat, re.I)
    payer = re.search(r"Payer\s*:?\s*([^\n]+)", flat, re.I)
    when = re.search(r"(\d{1,2}/\d{1,2}/\d{4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)", flat, re.I)
    paid_at, alt = parse_dt(when.group(1).upper() if when else None)
    return Receipt(
        provider="cbe", reference=reference, amount=amount, success=True,  # a receipt exists only for completed transfers
        payer_name=payer.group(1).strip() if payer else None,
        receiver_name=receiver.group(1).strip() if receiver else None,
        receiver_account=accounts[1] if len(accounts) > 1 else None,
        paid_at=paid_at, paid_at_alt=alt, raw={"accounts": accounts[:2]},
    )


async def fetch(reference: str, account_suffix: str) -> Receipt:
    from pypdf import PdfReader  # lazy: only needed when CBE is used

    suffix = re.sub(r"\D", "", account_suffix)[-8:]
    if len(suffix) < 8:
        raise FetchError("fetch_failed")
    body, _ = await fetch_bytes(f"{settings.cbe_receipt_base}?id={reference}{suffix}", ssl=settings.cbe_verify_ssl)
    if not body.startswith(b"%PDF"):
        raise FetchError("not_found")
    try:
        text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(body)).pages)
    except Exception:
        raise FetchError("parse_failed")
    return parse_cbe_text(text, reference)
