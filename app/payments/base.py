import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Outcome(str, Enum):
    VERIFIED = "verified"  # all checks passed -> auto-verified
    REJECTED = "rejected"  # proof is clearly wrong (wrong receiver, low amount, ...)
    UNAVAILABLE = "unavailable"  # couldn't decide -> fall back to seller confirmation


@dataclass
class Receipt:
    provider: str
    reference: str
    amount: Decimal | None = None
    success: bool | None = None
    payer_name: str | None = None
    receiver_name: str | None = None
    receiver_account: str | None = None
    paid_at: datetime | None = None  # naive UTC
    paid_at_alt: datetime | None = (
        None  # same time if day/month were swapped (ambiguous formats)
    )
    raw: dict = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {
            "provider": self.provider,
            "reference": self.reference,
            "amount": str(self.amount) if self.amount is not None else None,
            "success": self.success,
            "payer_name": self.payer_name,
            "receiver_name": self.receiver_name,
            "receiver_account": self.receiver_account,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "raw": self.raw,
        }


@dataclass
class VerifyResult:
    outcome: Outcome
    reason: str
    receipt: Receipt | None = None


class FetchError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason} {detail}".strip())
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    label: str
    wallet: bool  # account is a phone number (vs bank account number)
    ref_re: re.Pattern
    ref_hint: str


_WALLET_REF = re.compile(r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9]{8,14}$")
_FT_REF = re.compile(r"^FT[A-Z0-9]{8,18}$")

PROVIDERS: dict[str, ProviderInfo] = {
    p.key: p
    for p in [
        ProviderInfo("telebirr", "Telebirr", True, _WALLET_REF, "e.g. CE2513001XYT"),
        ProviderInfo(
            "mpesa", "M-Pesa (Safaricom)", True, _WALLET_REF, "e.g. SAB1C2D3E4"
        ),
        ProviderInfo("cbebirr", "CBE Birr", True, _WALLET_REF, "e.g. CGU9REIHHB"),
        ProviderInfo(
            "cbe", "Commercial Bank of Ethiopia", False, _FT_REF, "starts with FT"
        ),
        ProviderInfo("boa", "Bank of Abyssinia", False, _FT_REF, "starts with FT"),
        ProviderInfo(
            "dashen",
            "Dashen Bank",
            False,
            re.compile(r"^(?=.*\d)[A-Z0-9\-]{6,24}$"),
            "transaction reference",
        ),
        ProviderInfo(
            "other",
            "Other bank",
            False,
            re.compile(r"^[A-Z0-9\-]{4,30}$"),
            "transaction reference",
        ),
    ]
}


def normalize_ref(raw: str) -> str:
    return re.sub(r"[\s]", "", raw or "").upper()


def extract_reference(text: str, provider: str) -> str | None:
    """Find the first token in a message (typed ref, caption, pasted SMS) that fits the provider's pattern."""
    info = PROVIDERS[provider]
    for tok in re.findall(r"[A-Za-z0-9\-]+", text or ""):
        if info.ref_re.match(tok.upper()):
            return tok.upper()
    return None
