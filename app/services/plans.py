"""Seller plans. Buyers pay sellers directly, so the platform earns from subscriptions that sellers pay to
the platform's own accounts (settings.platform_accounts) using the same reference-verification pipeline."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.utils import norm_phone
from app.models import Payment, Plan, Product, Shop, SubscriptionInvoice, User
from app.payments.base import PROVIDERS, Outcome, normalize_ref
from app.services import payments as pay
from app.services.payments import SubmitResult, utcnow

log = logging.getLogger(__name__)

PLAN_DAYS = 30
REMIND_DAYS = 3
UNLIMITED = 10_000
DEFAULT_PLANS = [
    ("free", "Free", 0, 10, False),
    ("basic", "Basic", 300, 100, False),
    ("pro", "Pro", 800, UNLIMITED, True),
]


@dataclass(frozen=True)
class PlatformAccount:
    idx: int
    provider: str
    number: str
    holder: str
    label: str = ""


def platform_accounts() -> list[PlatformAccount]:
    out: list[PlatformAccount] = []
    for chunk in (c for c in settings.platform_accounts.split(";") if c.strip()):
        parts = [p.strip() for p in chunk.split("|")]
        if len(parts) < 3 or parts[0].lower() not in PROVIDERS:
            continue
        key = parts[0].lower()
        if PROVIDERS[key].wallet:
            phone = norm_phone(parts[1])
            number = phone.lstrip("+") if phone else re.sub(r"\D", "", parts[1])
        else:
            number = re.sub(r"\D", "", parts[1])
        if number:
            out.append(
                PlatformAccount(
                    len(out), key, number, parts[2], parts[3] if len(parts) > 3 else ""
                )
            )
    return out


def limit_label(limit: int) -> str:
    return "∞" if limit >= UNLIMITED else str(limit)


# ---------- plan catalogue ----------
async def seed_plans(session: AsyncSession) -> None:
    if (await session.execute(select(Plan.key).limit(1))).first():
        return
    session.add_all(
        Plan(key=k, name=n, price=Decimal(p), max_products=m, featured=f)
        for k, n, p, m, f in DEFAULT_PLANS
    )


async def list_plans(session: AsyncSession) -> list[Plan]:
    return list((await session.scalars(select(Plan).order_by(Plan.price))).all())


async def get_plan(session: AsyncSession, key: str) -> Plan | None:
    return await session.get(Plan, key)


# ---------- what a shop is entitled to right now ----------
def is_paid_active(shop: Shop, now: datetime | None = None) -> bool:
    return (
        shop.plan_key != "free"
        and shop.plan_expires_at is not None
        and shop.plan_expires_at > (now or utcnow())
    )


async def effective_plan(session: AsyncSession, shop: Shop) -> Plan:
    plan = await get_plan(session, shop.plan_key if is_paid_active(shop) else "free")
    return plan or Plan(
        key="free", name="Free", price=0, max_products=10, featured=False
    )  # transient fallback


async def product_quota(session: AsyncSession, shop: Shop) -> tuple[int, int]:
    count = await session.scalar(
        select(func.count()).select_from(Product).where(Product.shop_id == shop.id)
    )
    return count, (await effective_plan(session, shop)).max_products


async def can_add_product(session: AsyncSession, shop: Shop) -> tuple[bool, int, int]:
    used, limit = await product_quota(session, shop)
    return used < limit, used, limit


async def featured_ids(session: AsyncSession) -> set[int]:
    keys = (
        await session.scalars(select(Plan.key).where(Plan.featured.is_(True)))
    ).all()
    if not keys:
        return set()
    return set(
        (
            await session.scalars(
                select(Shop.id).where(
                    Shop.status == "active",
                    Shop.plan_key.in_(keys),
                    Shop.plan_expires_at > utcnow(),
                )
            )
        ).all()
    )


def activate(shop: Shop, plan: Plan, days: int = PLAN_DAYS) -> datetime:
    """Start (or extend, when renewing the same plan) a paid period."""
    now = utcnow()
    renewing = (
        shop.plan_key == plan.key
        and shop.plan_expires_at
        and shop.plan_expires_at > now
    )
    shop.plan_key = plan.key
    shop.plan_expires_at = (shop.plan_expires_at if renewing else now) + timedelta(
        days=days
    )
    shop.plan_reminded = False
    return shop.plan_expires_at


# ---------- buying a plan ----------
async def create_invoice(
    session: AsyncSession, shop: Shop, plan: Plan, acct: PlatformAccount
) -> SubscriptionInvoice:
    inv = SubscriptionInvoice(
        shop_id=shop.id,
        plan_key=plan.key,
        amount=plan.price,
        days=PLAN_DAYS,
        provider=acct.provider,
        label=acct.label,
        account_number=acct.number,
        holder_name=acct.holder,
        created_at=utcnow(),
    )
    session.add(inv)
    await session.flush()
    return inv


async def submit_invoice_proof(
    session: AsyncSession,
    inv: SubscriptionInvoice,
    reference: str,
    screenshot_file_id: str | None = None,
) -> SubmitResult:
    """Same checks as order payments (format, one-time reference, receipt lookup); an inconclusive check goes to an admin."""
    info = PROVIDERS[inv.provider]
    ref = normalize_ref(reference)
    if not info.ref_re.match(ref):
        return SubmitResult("bad_format")
    rejected = await session.scalar(
        select(func.count())
        .select_from(Payment)
        .where(Payment.invoice_id == inv.id, Payment.status == "rejected")
    )
    if rejected >= settings.max_proof_attempts:
        return SubmitResult("too_many")
    existing = await session.scalar(
        select(Payment).where(
            Payment.provider == inv.provider,
            Payment.reference == ref,
            Payment.status != "rejected",
        )
    )
    if existing:
        return SubmitResult("already" if existing.invoice_id == inv.id else "duplicate")

    result = await pay.verify_reference(
        inv, ref, inv
    )  # an invoice quacks like both the account and the order
    status = {
        Outcome.VERIFIED: "verified",
        Outcome.UNAVAILABLE: "pending_admin",
        Outcome.REJECTED: "rejected",
    }[result.outcome]
    payment = Payment(
        order_id=None,
        invoice_id=inv.id,
        provider=inv.provider,
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
        async with session.begin_nested():
            session.add(payment)
            await session.flush()
    except IntegrityError:
        return SubmitResult("duplicate")

    if result.outcome == Outcome.VERIFIED:
        inv.status = "paid"
        activate(
            await session.get(Shop, inv.shop_id),
            await get_plan(session, inv.plan_key),
            inv.days,
        )
        return SubmitResult("verified", payment)
    if result.outcome == Outcome.UNAVAILABLE:
        inv.status = "review"
        return SubmitResult("needs_admin", payment, result.reason)
    return SubmitResult("rejected", payment, result.reason)


async def admin_decide(
    session: AsyncSession, payment: Payment, approve: bool
) -> Shop | None:
    """Admin verdict on a plan payment the auto-check couldn't decide. None if it was already handled."""
    if payment.status != "pending_admin":
        return None
    inv = await session.get(SubscriptionInvoice, payment.invoice_id)
    shop = await session.get(Shop, inv.shop_id)
    if approve:
        payment.status, payment.verified_by, inv.status = (
            "admin_approved",
            "admin",
            "paid",
        )
        activate(shop, await get_plan(session, inv.plan_key), inv.days)
    else:
        payment.status, payment.reason, inv.status = (
            "rejected",
            "admin_denied",
            "awaiting",
        )
    return shop


# ---------- periodic notices ----------
async def plan_notices(session: AsyncSession) -> list[tuple[int, str, str, str, int]]:
    """Downgrade expired plans and find shops to remind. -> [(telegram_id, lang, 'soon'|'expired', plan_name, days_left)]"""
    now, out = utcnow(), []
    rows = (
        await session.execute(
            select(Shop, User)
            .join(User, User.id == Shop.owner_id)
            .where(Shop.plan_key != "free", Shop.plan_expires_at.is_not(None))
        )
    ).all()
    for shop, owner in rows:
        plan = await get_plan(session, shop.plan_key)
        name = plan.name if plan else shop.plan_key
        if shop.plan_expires_at <= now:
            shop.plan_key, shop.plan_expires_at = "free", None
            out.append((owner.telegram_id, owner.language or "en", "expired", name, 0))
        elif (
            shop.plan_expires_at - now <= timedelta(days=REMIND_DAYS)
            and not shop.plan_reminded
        ):
            shop.plan_reminded = True
            out.append(
                (
                    owner.telegram_id,
                    owner.language or "en",
                    "soon",
                    name,
                    max(1, (shop.plan_expires_at - now).days),
                )
            )
    return out
