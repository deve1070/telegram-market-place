from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    # MVP: auto-create tables. Switch to Alembic migrations before production.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # additive column migrations for databases created by earlier versions
        for stmt in (
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS accept_cod BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_account_id INTEGER",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_deadline TIMESTAMP",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_zone VARCHAR(60)",
            "ALTER TABLE orders ALTER COLUMN status TYPE VARCHAR(16)",
            "ALTER TABLE orders ALTER COLUMN payment_method TYPE VARCHAR(12)",
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS rating_avg NUMERIC(3,2) NOT NULL DEFAULT 0",
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS rating_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS plan_key VARCHAR(12) NOT NULL DEFAULT 'free'",
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS plan_expires_at TIMESTAMP",
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS plan_reminded BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE payments ALTER COLUMN order_id DROP NOT NULL",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS invoice_id INTEGER",
        ):
            await conn.execute(text(stmt))
