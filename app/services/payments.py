import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Order, Payment, ShopPaymentAccount, User
from app.payments.base import (
    PROVIDERS,
    FetchError,
    Outcome,
    VerifyResult,
    normalize_ref,
)
from app.payments.matching import evaluate
from app.payments.registry import FETCHERS
from app.services import orders

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- seller payment accounts ----------
async def list_accounts(session: AsyncSession, shop_id: int, active_only: bool = True):
    q = select(ShopPaymentAccount).where(ShopPaymentAccount.shop_id == shop_id)
    if active_only:
        q = q.where(ShopPaymentAccount.active.is_(True))
    return (await session.scalars(q.order_by(ShopPaymentAccount.id))).all()


def account_label(a: ShopPaymentAccount) -> str:
    name = a.label if a.provider == "other" and a.label else PROVIDERS[a.provider].label
    return f"{name} ···{a.account_number[-4:]}"


# ---------- verification ----------
async def verify_reference(
    account: ShopPaymentAccount, reference: str, order: Order
) -> VerifyResult:
    fetch = FETCHERS.get(account.provider)
    if not fetch:
        return VerifyResult(Outcome.UNAVAILABLE, "no_verifier")
    try:
        receipt = await fetch(reference, account.account_number)
    except FetchError as e:
        log.info(
            "verify %s %s -> unavailable (%s %s)",
            account.provider,
            reference,
            e.reason,
            e.detail,
        )
        return VerifyResult(Outcome.UNAVAILABLE, e.reason)
    except Exception:
        log.exception("verifier crashed")
        return VerifyResult(Outcome.UNAVAILABLE, "fetch_failed")
    result = evaluate(
        receipt,
        order_total=order.total,
        order_created=order.created_at,
        account_number=account.account_number,
        holder_name=account.holder_name,
    )
    log.info(
        "verify %s %s -> %s (%s)",
        account.provider,
        reference,
        result.outcome.value,
        result.reason,
    )
    return result


@dataclass
class SubmitResult:
    kind: str  # bad_format | too_many | duplicate | already | verified | needs_seller | rejected
    payment: Payment | None = None
    reason: str = ""


def mark_paid(order: Order) -> None:
    order.payment_status = "paid"
    order.status = "placed"


async def submit_proof(
    session: AsyncSession,
    order: Order,
    reference: str,
    screenshot_file_id: str | None = None,
) -> SubmitResult:
    account = await session.get(ShopPaymentAccount, order.payment_account_id)
    info = PROVIDERS[account.provider]
    ref = normalize_ref(reference)
    if not info.ref_re.match(ref):
        return SubmitResult("bad_format")

    rejected = await session.scalar(
        select(func.count())
        .select_from(Payment)
        .where(Payment.order_id == order.id, Payment.status == "rejected")
    )
    if rejected >= settings.max_proof_attempts:
        return SubmitResult("too_many")

    existing = await session.scalar(
        select(Payment).where(
            Payment.provider == account.provider,
            Payment.reference == ref,
            Payment.status != "rejected",
        )
    )
    if existing:
        return SubmitResult("already" if existing.order_id == order.id else "duplicate")

    result = await verify_reference(account, ref, order)
    status = {
        Outcome.VERIFIED: "verified",
        Outcome.UNAVAILABLE: "pending_seller",
        Outcome.REJECTED: "rejected",
    }[result.outcome]
    payment = Payment(
        order_id=order.id,
        provider=account.provider,
        reference=ref,
        status=status,
        verified_by="auto" if status == "verified" else None,
        reason=result.reason,
        amount_verified=result.receipt.amount if result.receipt else None,
        receipt=result.receipt.snapshot()
        if result.receipt
        else {"note": result.reason},
        screenshot_file_id=screenshot_file_id,
    )
    try:
        async with (
            session.begin_nested()
        ):  # unique index also guards the race between two buyers
            session.add(payment)
            await session.flush()
    except IntegrityError:
        return SubmitResult("duplicate")

    if result.outcome == Outcome.VERIFIED:
        mark_paid(order)
        return SubmitResult("verified", payment)
    if result.outcome == Outcome.UNAVAILABLE:
        order.payment_status = "review"
        return SubmitResult("needs_seller", payment, result.reason)
    return SubmitResult("rejected", payment, result.reason)


async def seller_decide(
    session: AsyncSession, payment: Payment, order: Order, received: bool
) -> None:
    if received:
        payment.status, payment.verified_by = "seller_confirmed", "seller"
        mark_paid(order)
    else:
        payment.status, payment.reason = "rejected", "seller_denied"
        order.payment_status = "awaiting"  # buyer may retry or open a dispute


async def latest_payment(session: AsyncSession, order_id: int) -> Payment | None:
    return await session.scalar(
        select(Payment)
        .where(Payment.order_id == order_id)
        .order_by(Payment.id.desc())
        .limit(1)
    )


# ---------- expiry ----------
async def expire_unpaid(session: AsyncSession) -> list[tuple[int, str, str]]:
    """Cancel online orders whose payment window passed (not those under seller review/dispute)."""
    rows = (
        await session.scalars(
            select(Order).where(
                Order.status == "awaiting_payment",
                Order.payment_status == "awaiting",
                Order.payment_deadline.is_not(None),
                Order.payment_deadline < utcnow(),
            )
        )
    ).all()
    out = []
    for o in rows:
        await orders.set_status(session, o, "cancelled")
        o.payment_status = "expired"
        buyer = await session.get(User, o.buyer_id)
        out.append((buyer.telegram_id, buyer.language or "en", o.code))
    return out
