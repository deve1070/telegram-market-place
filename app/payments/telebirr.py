"""Telebirr receipt adapter (HTML page at transactioninfo.ethiotelecom.et/receipt/<ref>).

Layout notes (from public reports of the live page, NOT yet verified by us on real receipts):
  * labels are bilingual: "የከፋይ ስም/Payer Name", "የገንዘብ ተቀባይ ቴሌብር ቁ./Credited party account no"
  * values come either right after their label, or as a header row of labels followed by a row of values
  * the amount that reached the seller is "Settled Amount" (the page even spells it "Setted Amount"); the
    "Total Paid Amount" also includes the sender's fees, so it is never used
  * the credited account is masked, e.g. "2519**1717" -> we compare the visible digits
Anything unexpected raises FetchError -> the buyer's proof goes to the seller for confirmation.
"""

import html
import re

from app.core.config import settings
from app.payments.base import FetchError, Receipt
from app.payments.http import fetch_bytes
from app.payments.matching import parse_amount, parse_dt

_GEEZ = re.compile(r"[\u1200-\u137F]")

# first match wins, so more specific patterns come first
FIELDS = [
    ("service_fee_vat", re.compile(r"service fee vat|\bvat\b", re.I)),
    ("service_fee", re.compile(r"service fee", re.I)),
    ("payer_no", re.compile(r"payer telebirr", re.I)),
    ("payer_name", re.compile(r"payer name", re.I)),
    ("credited_name", re.compile(r"credited party name", re.I)),
    ("credited_account", re.compile(r"credited party account", re.I)),
    ("status", re.compile(r"transaction status", re.I)),
    ("receipt_no", re.compile(r"receipt no|invoice no", re.I)),
    ("paid_date", re.compile(r"payment date", re.I)),
    ("settled", re.compile(r"set+l?ed amount", re.I)),
    ("total", re.compile(r"total paid amount", re.I)),
    ("bank", re.compile(r"bank name", re.I)),
    ("mode", re.compile(r"payment mode", re.I)),
    ("reason", re.compile(r"payment reason", re.I)),
    ("channel", re.compile(r"payment channel", re.I)),
]


def tokenize(page: str) -> list[str]:
    page = re.sub(r"(?is)<(script|style).*?</\1>", " ", page)
    page = re.sub(r"(?s)<[^>]+>", "\n", page)
    return [t.strip() for t in html.unescape(page).splitlines() if t.strip()]


def _field_of(token: str) -> tuple[bool, str | None]:
    """(is this token a label?, which known field it is)"""
    if len(token) > 90:
        return False, None
    for name, rx in FIELDS:
        if rx.search(token):
            return True, name
    return (bool(_GEEZ.search(token)) and "/" in token), None  # unknown bilingual label


def extract_fields(tokens: list[str]) -> dict[str, str]:
    """Pair labels with values for both layouts: [label, value, ...] and [label x k, value x k]."""
    flags = [_field_of(t) for t in tokens]
    out: dict[str, str] = {}
    i, n = 0, len(tokens)
    while i < n:
        if not flags[i][0]:
            i += 1
            continue
        j = i
        while j < n and flags[j][0]:
            j += 1
        k = j - i
        values = tokens[j : j + k]
        if len(values) == k and not any(flags[j + x][0] for x in range(k)):
            for x in range(k):
                name = flags[i + x][1]
                if name and name not in out:
                    out[name] = values[x]
            i = j + k
        else:
            i = j
    return out


def parse_telebirr_html(page: str, reference: str) -> Receipt:
    tokens = tokenize(page)
    f = extract_fields(tokens)
    amount = parse_amount(
        f.get("settled")
    )  # never fall back to "total": it includes the sender's fees
    if amount is None:
        raise FetchError("parse_failed", "no settled amount")
    ref = reference.upper()
    if ref not in " ".join(tokens).upper():
        raise FetchError("not_found", "reference not on page")
    status = (f.get("status") or "").lower()
    paid_at, alt = parse_dt(f.get("paid_date"))
    return Receipt(
        provider="telebirr",
        reference=reference,
        amount=amount,
        success=("complet" in status or "success" in status) if status else None,
        payer_name=f.get("payer_name"),
        receiver_name=f.get("credited_name"),
        receiver_account=f.get("credited_account"),
        paid_at=paid_at,
        paid_at_alt=alt,
        raw={
            "status": status,
            "date_raw": f.get("paid_date"),
            "total_paid": f.get("total"),
            "bank": f.get("bank"),
            "receipt_no": f.get("receipt_no"),
        },
    )


async def fetch(reference: str, account_suffix: str) -> Receipt:
    body, _ = await fetch_bytes(
        f"{settings.telebirr_receipt_base.rstrip('/')}/{reference}"
    )
    return parse_telebirr_html(body.decode("utf-8", "ignore"), reference)
