import os

# must run before any `app` import so Settings picks these up
os.environ.setdefault("BOT_TOKEN", "1:test")
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
if os.environ.get("TEST_REDIS_URL"):
    os.environ["REDIS_URL"] = os.environ["TEST_REDIS_URL"]
os.environ.setdefault("ADMIN_IDS", "9001")
