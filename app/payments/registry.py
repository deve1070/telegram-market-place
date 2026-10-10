from app.payments import cbe, telebirr

# provider key -> async fetch(reference, account_suffix) -> Receipt.  Providers not listed
# (mpesa, boa, dashen, cbebirr, other) automatically use seller confirmation.
# To add one: write app/payments/<name>.py with the same fetch() signature and register it here.
FETCHERS = {
    "telebirr": telebirr.fetch,
    "cbe": cbe.fetch,
}
