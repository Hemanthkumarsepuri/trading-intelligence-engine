"""Application settings — the single source of truth for configuration.

Loaded from environment variables / `.env` via pydantic-settings. Nothing
outside this module reads `os.environ` directly (ARCHITECTURE.md §23) — a
provider, engine, or LLM adapter that needs a config value receives it via
constructor injection from whatever assembles it (`orchestration/`), never by
reading the environment itself. This keeps every one of those classes
testable without environment-variable juggling.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # populate_by_name=True: every field below has an explicit `alias` (the
    # env var name) so pydantic-settings loads it from the environment
    # correctly; this also lets Settings be constructed directly by its
    # Python attribute name (`Settings(broker_order_execution_enabled=True)`),
    # which tests — including the safety-lock invariant test — need to do
    # without fighting the alias.
    #
    # env_file_encoding="utf-8-sig", not "utf-8": Windows editors/tools
    # (Notepad, PowerShell's default `Out-File`/redirection encoding, some
    # VS Code configurations) commonly save a `.env` file with a leading
    # UTF-8 byte-order-mark (EF BB BF). Plain "utf-8" decoding leaves that
    # BOM as a literal U+FEFF character prepended to the file's first
    # decoded character — which corrupts the *first* variable's key name
    # (e.g. "UPSTOX_ACCESS_TOKEN" silently becomes "﻿UPSTOX_ACCESS_TOKEN"),
    # so python-dotenv never matches it against the field's alias and the
    # value silently falls back to the field's default. "utf-8-sig" strips a
    # leading BOM if present and behaves identically to "utf-8" when one is
    # absent, so this is a strict robustness improvement, not a behavior
    # change for any already-working `.env` file.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8-sig", extra="ignore", populate_by_name=True
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="trading-intelligence-engine", alias="APP_NAME")

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/tire", alias="DATABASE_URL"
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # LLM — provider-agnostic; see ARCHITECTURE.md Addendum A6.
    llm_provider: str = Field(default="ollama", alias="LLM_PROVIDER")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen3:30b", alias="OLLAMA_MODEL")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    # Local Qwen 2.5 via LM Studio -- same endpoint/model as Arqon Civil Interior.
    # Fail-open: research never depends on this being reachable.
    qwen_enabled: bool = Field(default=True, alias="QWEN_ENABLED")
    qwen_base_url: str = Field(default="http://127.0.0.1:1234/v1", alias="QWEN_BASE_URL")
    qwen_model: str = Field(default="qwen2.5-coder-7b-instruct", alias="QWEN_MODEL")
    qwen_timeout_seconds: float = Field(default=8.0, alias="QWEN_TIMEOUT_SECONDS")
    qwen_health_timeout_seconds: float = Field(default=2.0, alias="QWEN_HEALTH_TIMEOUT_SECONDS")

    # Market data — provider-agnostic abstraction (ARCHITECTURE.md Addendum
    # A1); Upstox is the selected primary for the read-only analysis engine
    # (docs/data-sources/PROVIDER_DECISION.md — its Analytics Token is
    # structurally incapable of placing orders, unlike Dhan's access-token).
    # Dhan remains implemented as an alternative, not removed.
    market_data_provider: str = Field(default="upstox", alias="MARKET_DATA_PROVIDER")
    dhan_base_url: str = Field(default="https://api.dhan.co", alias="DHAN_BASE_URL")
    dhan_client_id: str | None = Field(default=None, alias="DHAN_CLIENT_ID")
    dhan_access_token: str | None = Field(default=None, alias="DHAN_ACCESS_TOKEN")
    upstox_base_url: str = Field(default="https://api.upstox.com", alias="UPSTOX_BASE_URL")
    upstox_access_token: str | None = Field(default=None, alias="UPSTOX_ACCESS_TOKEN")

    # Safety — see SAFETY_LOCK.txt. Runtime-enforced, not just documented.
    broker_order_execution_enabled: bool = Field(default=False, alias="BROKER_ORDER_EXECUTION_ENABLED")
    system_halted: bool = Field(default=False, alias="SYSTEM_HALTED")

    # Risk (Phase 4 territory; zeroed placeholders until the risk engine is built)
    max_risk_per_trade: float = Field(default=0.0, alias="MAX_RISK_PER_TRADE")
    max_daily_loss: float = Field(default=0.0, alias="MAX_DAILY_LOSS")
    max_open_positions: int = Field(default=0, alias="MAX_OPEN_POSITIONS")

    # Scheduler
    analysis_interval_minutes: int = Field(default=5, alias="ANALYSIS_INTERVAL_MINUTES")
    timezone: str = Field(default="Asia/Kolkata", alias="TIMEZONE")

    # Data freshness thresholds — ARCHITECTURE.md Addendum A2/A3
    freshness_max_age_real_time_seconds: int = Field(default=15, alias="FRESHNESS_MAX_AGE_REAL_TIME_SECONDS")
    freshness_max_age_short_interval_seconds: int = Field(default=60, alias="FRESHNESS_MAX_AGE_SHORT_INTERVAL_SECONDS")
    freshness_max_age_analysis_interval_seconds: int = Field(
        default=360, alias="FRESHNESS_MAX_AGE_ANALYSIS_INTERVAL_SECONDS"
    )
    freshness_max_age_slow_refresh_seconds: int = Field(default=86400, alias="FRESHNESS_MAX_AGE_SLOW_REFRESH_SECONDS")

    # Cross-provider validation — ARCHITECTURE.md Addendum A4
    provider_disagreement_tolerance: float = Field(default=0.005, alias="PROVIDER_DISAGREEMENT_TOLERANCE")

    def assert_broker_execution_disabled(self) -> None:
        """Fail loudly at startup if the safety lock has been tampered with.

        See SAFETY_LOCK.txt — this is the runtime enforcement of that file's
        intent, not just documentation. Call this once during application
        startup, before anything else initializes.
        """
        if self.broker_order_execution_enabled:
            raise RuntimeError(
                "BROKER_ORDER_EXECUTION_ENABLED=true is not permitted in this phase. See SAFETY_LOCK.txt."
            )


settings = Settings()
