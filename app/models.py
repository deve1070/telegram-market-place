from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    phone: Mapped[str | None] = mapped_column(String(20))
    language: Mapped[str | None] = mapped_column(String(5))  # None = not chosen yet
    current_shop_id: Mapped[int | None] = mapped_column(
        default=None
    )  # storefront context
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Shop(Base):
    __tablename__ = "shops"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    slug: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(20))
    city: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(
        String(12), default="pending"
    )  # pending|active|rejected|suspended
    verified: Mapped[bool] = mapped_column(default=False)
    accept_cod: Mapped[bool] = mapped_column(default=True)
    rating_avg: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=0
    )  # denormalised from reviews
    rating_count: Mapped[int] = mapped_column(default=0)
    plan_key: Mapped[str] = mapped_column(String(12), default="free")
    plan_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=None
    )  # naive UTC
    plan_reminded: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name_en: Mapped[str] = mapped_column(String(60))
    name_am: Mapped[str] = mapped_column(String(60))


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(default=1)
    photo_file_id: Mapped[str | None] = mapped_column(
        String(255)
    )  # Telegram file_id, no hosting needed
    status: Mapped[str] = mapped_column(String(10), default="active")  # active|hidden
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("user_id", "product_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[int] = mapped_column(default=1)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    phone: Mapped[str] = mapped_column(String(20))
    address_text: Mapped[str] = mapped_column(Text, default="")
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[str] = mapped_column(
        String(12), default="cod"
    )  # cod | provider key
    payment_status: Mapped[str] = mapped_column(
        String(10), default="unpaid"
    )  # unpaid|awaiting|review|paid|disputed|expired
    payment_account_id: Mapped[int | None] = mapped_column(default=None)
    payment_deadline: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    delivery_zone: Mapped[str | None] = mapped_column(
        String(60), default=None
    )  # snapshot of the zone name
    status: Mapped[str] = mapped_column(String(16), default="placed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )  # naive UTC


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    title: Mapped[str] = mapped_column(String(150))  # snapshot
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # snapshot
    qty: Mapped[int]


class ShopPaymentAccount(Base):
    """Where buyers pay a seller directly (bank account or wallet phone)."""

    __tablename__ = "shop_payment_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    provider: Mapped[str] = mapped_column(
        String(12)
    )  # key in app.payments.base.PROVIDERS
    label: Mapped[str] = mapped_column(
        String(60), default=""
    )  # bank name for provider "other"
    account_number: Mapped[str] = mapped_column(String(30))
    holder_name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=True)


class Payment(Base):
    """One payment-proof attempt for an order. A (provider, reference) may be used ONCE platform-wide."""

    __tablename__ = "payments"
    __table_args__ = (
        Index(
            "uq_payment_ref_active",
            "provider",
            "reference",
            unique=True,
            postgresql_where=text("status <> 'rejected'"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True)
    invoice_id: Mapped[int | None] = mapped_column(
        default=None, index=True
    )  # set for plan payments
    provider: Mapped[str] = mapped_column(String(12))
    reference: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(
        String(16)
    )  # verified | pending_seller | seller_confirmed | admin_approved | rejected
    verified_by: Mapped[str | None] = mapped_column(String(8))  # auto | seller | admin
    reason: Mapped[str | None] = mapped_column(
        String(120)
    )  # reject reason / why auto-check was inconclusive
    amount_verified: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    receipt: Mapped[dict | None] = mapped_column(
        JSONB
    )  # parsed receipt snapshot = dispute evidence
    screenshot_file_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


class DeliveryZone(Base):
    """A seller-defined delivery area with a flat fee (0 = free / pickup)."""

    __tablename__ = "delivery_zones"
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    active: Mapped[bool] = mapped_column(default=True)


class Review(Base):
    """One verified-purchase review per delivered order."""

    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    rating: Mapped[int]
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(8), default="visible"
    )  # visible | hidden (admin moderation)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


class Plan(Base):
    __tablename__ = "plans"
    key: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(30))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)  # ETB per 30 days
    max_products: Mapped[int] = mapped_column(default=10)
    featured: Mapped[bool] = mapped_column(
        default=False
    )  # listed first in the shop directory


class SubscriptionInvoice(Base):
    """A seller's request to buy a plan: where to pay, how much. Proofs are stored in `payments` (invoice_id)."""

    __tablename__ = "subscription_invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    plan_key: Mapped[str] = mapped_column(String(12))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    days: Mapped[int] = mapped_column(default=30)
    provider: Mapped[str] = mapped_column(String(12))
    label: Mapped[str] = mapped_column(String(60), default="")
    account_number: Mapped[str] = mapped_column(String(30))
    holder_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(10), default="awaiting"
    )  # awaiting | review | paid
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    @property
    def total(
        self,
    ) -> Decimal:  # lets the receipt verifier treat an invoice like an order
        return self.amount


class SellerVerification(Base):
    """Identity check for a shop owner. Photos are Telegram file_ids; they are erased after the decision
    unless settings.kyc_keep_documents is on."""

    __tablename__ = "seller_verifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    doc_type: Mapped[str] = mapped_column(
        String(16)
    )  # national_id | passport | driver_license | kebele_id
    full_name: Mapped[str] = mapped_column(String(100))
    id_photo: Mapped[str | None] = mapped_column(String(255))
    selfie_photo: Mapped[str | None] = mapped_column(String(255))
    license_photo: Mapped[str | None] = mapped_column(
        String(255)
    )  # optional trade/business license
    status: Mapped[str] = mapped_column(
        String(10), default="pending"
    )  # pending | approved | rejected
    reject_reason: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    decided_by: Mapped[int | None] = mapped_column(
        BigInteger, default=None
    )  # admin's telegram id
    admin_msgs: Mapped[dict | None] = mapped_column(
        JSONB, default=None
    )  # {admin_id: [photo message ids]} so they can be deleted


class Report(Base):
    """A buyer's report about a shop. One OPEN report per (shop, reporter); admins decide, nothing is automatic."""

    __tablename__ = "reports"
    __table_args__ = (
        Index(
            "uq_report_open",
            "shop_id",
            "reporter_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(16))  # scam | fake | abuse | other
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | dismissed
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
