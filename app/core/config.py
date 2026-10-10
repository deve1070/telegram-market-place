from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    database_url: str = "postgresql+asyncpg://market:market@localhost:5432/market"
    redis_url: str = "redis://localhost:6379/0"
    admin_ids: str = ""

    # payments
    payment_window_minutes: int = 60
    max_proof_attempts: int = 5
    # Public receipt endpoints used by the auto-verifiers. Defaults are based on the endpoints the
    # open-source Ethiopian verifier projects use; CONFIRM them and override via .env if they change.
    telebirr_receipt_base: str = "https://transactioninfo.ethiotelecom.et/receipt"
    cbe_receipt_base: str = "https://apps.cbe.com.et:100/"
    cbe_verify_ssl: bool = True
    verifier_proxy: str = (
        ""  # e.g. http://user:pass@host:port (an Ethiopian IP may be required)
    )
    verifier_timeout: int = 15

    # where SELLERS pay the platform for plans. Format: provider|number|holder name[|bank label] ; separated
    # e.g. telebirr|0911223344|Abebe Kebede;other|1000123456789|Abebe Kebede|Awash Bank
    platform_accounts: str = ""

    # --- trust & safety ---
    kyc_keep_documents: bool = False  # False: ID/selfie photo references are erased as soon as an admin decides
    report_alert_threshold: int = (
        3  # distinct open reports that trigger an alert to the admins
    )

    # --- deployment ---
    # Empty WEBHOOK_BASE_URL = local polling mode (development). Set it to https://your.domain for production.
    webhook_base_url: str = ""
    webhook_secret: str = ""  # A-Z a-z 0-9 _ -  (16+ chars). Used in the URL path AND as Telegram's secret header.
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    throttle_limit: int = (
        20  # max updates per user per window; the rest are dropped silently
    )
    throttle_window: int = 5  # seconds

    webapp_base_url: str = ""  # optional override (e.g. an https tunnel); normally derived from WEBHOOK_BASE_URL

    @property
    def webapp_url(self) -> str:
        """https URL of the Mini App, or '' when no public https address is configured (local polling mode)."""
        base = self.webapp_base_url or self.webhook_base_url
        return f"{base.rstrip('/')}/app" if base.startswith("https://") else ""

    @property
    def admin_id_set(self) -> set[int]:
        return {int(x) for x in self.admin_ids.split(",") if x.strip().isdigit()}


settings = Settings()
