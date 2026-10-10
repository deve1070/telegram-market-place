"""Pure decision logic: does this receipt prove payment for THIS order to THIS seller account?"""

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app.payments.base import Outcome, Receipt, VerifyResult

EAT = timezone(timedelta(hours=3))  # Ethiopia has no DST
TIME_TOLERANCE = timedelta(minutes=10)


def parse_amount(s: str | None) -> Decimal | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def _to_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=EAT).astimezone(timezone.utc).replace(tzinfo=None)


_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ],
        1,
    )
}


def parse_dt(s: str | None) -> tuple[datetime | None, datetime | None]:
    """Parse a receipt timestamp (Ethiopian local time) -> (utc, utc with day/month swapped if ambiguous).
    Accepts 2026-10-03 12:30:00 / 03-10-2026 12:30:00 / 10/3/2026, 12:30 PM / 03-Oct-2026 ... A missing time
    means end of that day (so a date-only receipt is never treated as older than the order)."""
    s = s or ""
    tm = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?", s)
    hh, mi, ss, ampm = (
        (
            int(tm.group(1)),
            int(tm.group(2)),
            int(tm.group(3) or 0),
            (tm.group(4) or "").upper(),
        )
        if tm
        else (23, 59, 59, "")
    )
    if ampm == "PM" and hh < 12:
        hh += 12
    if ampm == "AM" and hh == 12:
        hh = 0
    rest = s.replace(tm.group(0), " ") if tm else s

    def build(y, mo, d):
        try:
            return _to_utc(datetime(int(y), int(mo), int(d), hh, mi, ss))
        except ValueError:
            return None

    if m := re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", rest):
        return build(m.group(1), m.group(2), m.group(3)), None
    if m := re.search(r"(\d{1,2})[-/.\s]([A-Za-z]{3,9})[-/.\s,]*(\d{4})", rest):
        mon = _MONTHS.get(m.group(2)[:3].lower())
        return (build(m.group(3), mon, m.group(1)) if mon else None), None
    if m := re.search(r"(\d{1,2})([-/.])(\d{1,2})[-/.](\d{4})", rest):
        a_, sep, b_, y = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        if a_ > 12:
            return build(y, b_, a_), None
        if b_ > 12:
            return build(y, a_, b_), None
        month_first = bool(ampm) and sep == "/"  # US-style "10/3/2026, 12:30 PM"
        day, month = (b_, a_) if month_first else (a_, b_)
        alt = (
            build(y, day, month) if day != month else None
        )  # the same digits read the other way round
        return build(y, month, day), alt
    return None, None


def account_match(receipt_account: str | None, my_account: str) -> bool | None:
    """True/False when comparable, None when the receipt masks too much."""
    if not receipt_account:
        return None
    m = re.search(r"(\d+)\s*$", re.sub(r"[\s\-]", "", receipt_account))
    if not m or len(m.group(1)) < 3:
        return None
    tail, mine = m.group(1), re.sub(r"\D", "", my_account)
    n = min(len(tail), 9)
    return mine[-n:] == tail[-n:]


def _tokens(s: str | None) -> list[str]:
    return [w for w in re.split(r"[^a-z*]+", (s or "").lower()) if w]


def _tok_eq(a: str, b: str) -> bool:
    if "*" in a:
        vis = a.split("*")[0]
        return bool(vis) and b.startswith(vis)
    if "*" in b:
        vis = b.split("*")[0]
        return bool(vis) and a.startswith(vis)
    return a == b


def name_match(receipt_name: str | None, holder: str) -> str | None:
    """'match' | 'mismatch' | None (uncertain: masked or only partly matching)."""
    r, h = _tokens(receipt_name), _tokens(holder)
    if not r or not h:
        return None
    hits = sum(1 for ht in h if any(_tok_eq(rt, ht) for rt in r))
    if hits >= min(2, len(h)):
        return "match"
    if hits == 0 and "*" not in (receipt_name or ""):
        return "mismatch"
    return None


def evaluate(
    receipt: Receipt,
    *,
    order_total: Decimal,
    order_created: datetime,
    account_number: str,
    holder_name: str,
    now: datetime | None = None,
) -> VerifyResult:
    rejects: list[str] = []
    unsure: list[str] = []

    if receipt.success is False:
        rejects.append("not_successful")

    if receipt.amount is None:
        unsure.append("uncertain_amount")
    elif receipt.amount + Decimal("0.005") < Decimal(order_total):
        rejects.append("amount_low")

    acct = account_match(receipt.receiver_account, account_number)
    if acct is False:
        rejects.append("wrong_receiver")
    elif acct is None:
        nm = name_match(receipt.receiver_name, holder_name)
        if nm == "mismatch":
            rejects.append("wrong_receiver")
        elif nm is None:
            unsure.append("uncertain_receiver")

    options = [d for d in (receipt.paid_at, receipt.paid_at_alt) if d]
    if not options:
        unsure.append("uncertain_time")
    elif not any(d >= order_created - TIME_TOLERANCE for d in options):
        rejects.append("too_old")

    if rejects:
        return VerifyResult(Outcome.REJECTED, rejects[0], receipt)
    if unsure:
        return VerifyResult(Outcome.UNAVAILABLE, ",".join(unsure), receipt)
    return VerifyResult(Outcome.VERIFIED, "ok", receipt)
