"""Central application settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    app_name: str = "SquidWings"
    app_env: str = "development"
    log_level: str = "INFO"
    debug: bool = False

    # --- API keys ---
    openai_api_key: str = Field(default="", repr=False)
    anthropic_api_key: str = Field(default="", repr=False)
    google_api_key: str = Field(default="", repr=False)
    deepseek_api_key: str = Field(default="", repr=False)
    graphify_api_key: str = Field(default="", repr=False)

    # --- Base URLs ---
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    graphify_base_url: str = "https://api.graphify.ai/v1"

    # --- Web feature flags ---
    # ``None`` means "decide from APP_ENV": development is chat-only, other
    # environments keep the team canvas. Set WEB_TEAMS_ENABLED explicitly to
    # force the behaviour either way.
    web_teams_enabled: bool | None = None
    # A team shares one budget. Without these, one request can run for most of an hour.
    team_run_seconds: int = 600
    team_member_seconds: int = 240
    team_member_rounds: int = 10
    team_member_tokens: int = 120_000
    # Teams in video mode build and render, which takes far longer than writing.
    team_video_run_seconds: int = 3600
    team_video_member_seconds: int = 1800
    team_video_member_rounds: int = 40
    team_video_member_tokens: int = 400_000
    # Where a flow can send results. Secrets stay here, never in a team's settings.
    telegram_bot_token: str = Field(default="", repr=False)
    telegram_chat_id: str = ""
    team_db_sqlite_path: str = "web/data/teams/flow.sqlite3"
    team_db_postgres_url: str = Field(default="", repr=False)
    team_db_mysql_url: str = Field(default="", repr=False)
    team_db_mongodb_url: str = Field(default="", repr=False)

    # --- Web public access ---
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    # Comma-separated. Empty means same-origin only / loopback only.
    web_allowed_origins: str = ""
    web_allowed_hosts: str = ""
    web_max_users: int = 10
    web_rate_limit_rpm: int = 600

    # --- Command sandbox (see security/sandbox.py) ---
    # auto uses bubblewrap, then Docker, and refuses commands if neither works.
    sandbox_mode: str = "auto"
    sandbox_enabled: bool = True
    sandbox_network: bool = False
    sandbox_memory_mb: int = 0
    sandbox_cpu_seconds: int = 900
    sandbox_file_size_mb: int = 1024
    sandbox_image: str = "python:3.12-slim"
    sandbox_ro_paths: str = ""

    # --- Web Search ---
    web_search_enabled: bool = True
    # Search APIs, tried before the public result pages. Much better results.
    searxng_url: str = ""
    brave_search_api_key: str = Field(default="", repr=False)
    # Render JavaScript-only pages in a local headless Chrome or Chromium.
    web_browser_enabled: bool = True
    web_search_timeout_seconds: float = 15.0
    web_search_max_results: int = 10
    web_search_cache_ttl_seconds: int = 21600
    web_search_min_interval_seconds: float = 2.0
    web_search_retries: int = 1
    web_search_max_cache_entries: int = 200

    # --- Obsidian local memory ---
    web_search_obsidian_enabled: bool = False
    obsidian_vault_path: str = ""
    obsidian_search_folder: str = "SquidWings/Web Search"

    # --- Graphify local memory ---
    graphify_memory_enabled: bool = False
    graphify_memory_endpoint: str = ""
    graphify_memory_timeout_seconds: float = 5.0

    # --- API connection ---
    # These keys are present in .env (API_BASE_URL, DEFAULT_MODEL,
    # TIMEOUT_SECONDS, STREAM_ENABLED) but were never declared here. Because
    # ``extra="ignore"`` silently drops unknown keys, the values loaded fine
    # and then vanished — every ``settings.api_base_url`` in main.py raised
    # AttributeError and main() died before the first prompt.
    api_base_url: str = "https://api.9router.ai/v1"
    default_model: str = ""
    default_temperature: float = 0.7
    default_max_tokens: int = 8192
    stream_enabled: bool = True
    timeout_seconds: float = 120.0
    retry_backoff_factor: float = 1.5

    # --- Retry / limits ---
    request_timeout_seconds: float = 120.0
    max_retries: int = 2
    stream_timeout_seconds: float = 120.0

    # --- Paths (computed) ---
    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent)

    @property
    def models_config_path(self) -> Path:
        """Path to ``config/models_config.yaml``.

        Referenced by ``main.py``, ``agent/orchestrator.py`` and the offline
        tests; without it every orchestrator construction raises
        ``AttributeError`` and the web server cannot start.
        """
        return self.project_root / "config" / "models_config.yaml"

    @property
    def brain_dir(self) -> Path:
        """Root of the ``brain`` package.

        ``main.py`` builds ``settings.brain_dir / "memory" / "store"``, so this
        must be ``project_root/brain`` — not ``memory_dir``, which is already
        one level deeper.
        """
        return self.project_root / "brain"

    @property
    def config_dir(self) -> Path:
        """Root of the ``config`` package (``terminal_format.json`` lives here)."""
        return self.project_root / "config"

    @property
    def memory_dir(self) -> Path:
        return self.project_root / "brain" / "memory"

    @property
    def chat_store_dir(self) -> Path:
        return self.memory_dir / "chat_store"

    @property
    def session_store_dir(self) -> Path:
        return self.memory_dir / "store"

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def teams_enabled(self) -> bool:
        """Whether the multi-agent team canvas is available.

        Development runs chat-only: the team API is not mounted and the UI
        hides the canvas tab. Other environments keep the canvas unless the
        flag is switched off explicitly.
        """
        if self.web_teams_enabled is not None:
            return self.web_teams_enabled
        return self.app_env.lower() not in {"development", "dev", "local"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
