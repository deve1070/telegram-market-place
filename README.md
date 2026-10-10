# Ethiopia Telegram Marketplace Bot (Phase 1)

Hybrid design: one engine, two entry points.
- **Directory:** `/start` → 🛍 Shops lists every active shop.
- **Storefront:** `https://t.me/<bot>?start=shop_<slug>` opens one seller's shop directly.

## Run
1. Create a bot with @BotFather, copy the token.
2. `cp .env.example .env` and set `BOT_TOKEN` and `ADMIN_IDS` (your Telegram numeric id, from @userinfobot).
3. `docker compose up --build`

Local (no Docker): run Postgres + Redis, set URLs to `localhost` in `.env`, then
`pip install -r requirements.txt && python -m app.bot.main`.

## Try the flow
1. Seller: `/start` → 🏪 Sell / My shop → answer the questions.
2. Admin: gets an Approve/Reject message (or `/pending`).
3. Seller: open the menu again → ➕ Add product (send photo, caption `Nike shoes | 3500 | 10`).
4. Buyer (another account): open the shop link → browse → add to cart → Checkout (phone, location pin, landmark) → Place order (COD).
5. Seller gets the order, moves it Accepted → Packed → Shipped; buyer taps "I received it".

Admin commands: `/pending`, `/stats`. Anyone: `/cancel`.

## Payments (Phase 2): direct payment + tiered verification
Buyers pay the **seller directly** (any bank / Telebirr / M-Pesa / CBE Birr); the platform never holds money.

1. Seller: Sell / My shop → 💳 Payment accounts → add bank/wallet accounts (and toggle cash on delivery).
2. Buyer checks out → picks a method → bot shows account + exact amount → buyer pays in their own app →
   sends the **reference** (typed, pasted SMS, or screenshot with the reference as caption).
3. Bot checks, in order: reference format → **reference never used before (platform-wide)** → official receipt
   lookup → receiver account/name, amount ≥ total, status, time after order creation.
   - 🟢 all pass → order marked paid and sent to the seller
   - 🟡 can't decide (provider has no verifier, lookup down, masked name) → seller taps **Received / Not received**
   - 🔴 clearly wrong (wrong receiver, low amount, old receipt) → buyer told why, can retry (max 5)
4. Seller denial → buyer can resend or **open a dispute** → admins use `/disputes` (Mark paid / Cancel).
5. Unpaid online orders auto-cancel after `PAYMENT_WINDOW_MINUTES` and stock is restored.

Auto-verifiers exist for **Telebirr** and **CBE**. Every other provider (M-Pesa, BOA, Dashen, CBE Birr, other banks)
uses seller confirmation until you add an adapter: write `app/payments/<name>.py` with
`async fetch(reference, account_suffix) -> Receipt`, then register it in `app/payments/registry.py`.

⚠️ **Before relying on auto-verify:** the Telebirr/CBE adapters read public receipt pages the way the open-source
Ethiopian verifier projects do. The endpoint defaults and the parsers were written from their documentation and tested
only on *synthetic* receipts. Test with 2-3 real payments, fix parsing if needed, and keep seller confirmation as the
safety net (any adapter failure already falls back to it). Telebirr lookups may require an Ethiopian IP (`VERIFIER_PROXY`).
CBE lookups use the last 8 digits of the seller's account; confirm that is what CBE expects for your transfers.

## Tests
`pytest tests` (unit). Add `TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/dbname` to include the
Postgres integration test of the whole payment flow (it drops and recreates tables, use a scratch DB).

## Layout
- `app/bot/handlers/` start (menu + deep link), buyer, seller, admin
- `app/payments/` provider types, matching rules, Telebirr/CBE adapters; `app/services/payments.py` proof/verification service
- `app/services/` catalog, cart, orders (business logic, reusable by the future Mini App/API)
- `app/models.py` SQLAlchemy models, `app/locales/` en + am strings (missing keys fall back to English)

## Known MVP shortcuts (next steps)
- Tables auto-created at startup → add Alembic migrations before production.
- One photo per product, no variants yet; delivery fee fixed at 0.
- Polling mode → switch to webhooks (FastAPI) on the server.
- Add Afaan Oromo / Tigrigna locale files (drop `om.json`, `ti.json` in `app/locales/` and add to `LANGS`).
