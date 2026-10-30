"""Model providers: which URL, key, and limits serve a given model id."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "providers.yaml"


@dataclass(frozen=True)
class Provider:
    """One OpenAI-compatible endpoint. See config/providers.yaml for the fields."""

    id: str
    prefix: str = ""
    strip_prefix: bool = False
    base_url: str = ""
    base_url_env: str = ""
    api_key: str = ""
    api_key_env: str = ""
    transient_retries: int = 0
    max_concurrency: int = 0
    models: tuple[str, ...] = field(default_factory=tuple)

    @property
    def url(self) -> str:
        # Read per call so "/bansos" picks up edits to .env without a restart.
        configured = os.getenv(self.base_url_env, "") if self.base_url_env else ""
        return (configured or self.base_url).rstrip("/")

    @property
    def key(self) -> str:
        return os.getenv(self.api_key_env, "") if self.api_key_env else self.api_key

    @property
    def max_retries(self) -> int:
        raw = os.getenv(f"{self.id.upper()}_MAX_RETRIES", "")
        return int(raw) if raw.isdigit() else self.transient_retries

    def api_model(self, model_id: str) -> str:
        """The id this provider expects on the wire."""
        if self.strip_prefix and self.prefix and model_id.startswith(self.prefix):
            return model_id[len(self.prefix):]
        return model_id

    def tag_model(self, model_id: str) -> str:
        """The id used inside this application for a model the provider listed."""
        return model_id if model_id.startswith(self.prefix) else f"{self.prefix}{model_id}"

    def owns(self, model_id: str) -> bool:
        return bool(self.prefix and model_id.startswith(self.prefix)) or model_id in self.models


def load_providers(path: Path | None = None) -> list[Provider]:
    """Read extra providers from YAML. A broken file must not stop the agent."""
    source = path or Path(os.getenv("PROVIDERS_CONFIG", "") or _DEFAULT_PATH)
    try:
        config = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(f"[providers] Could not read {source}: {exc}")
        return []

    providers: list[Provider] = []
    for item in config.get("providers") or []:
        if not isinstance(item, dict) or not item.get("id") or not item.get("prefix"):
            logger.warning(f"[providers] Skipping entry without id and prefix: {item}")
            continue
        providers.append(Provider(
            id=str(item["id"]),
            prefix=str(item["prefix"]),
            strip_prefix=bool(item.get("strip_prefix", False)),
            base_url=str(item.get("base_url", "")),
            base_url_env=str(item.get("base_url_env", "")),
            api_key=str(item.get("api_key", "")),
            api_key_env=str(item.get("api_key_env", "")),
            transient_retries=int(item.get("transient_retries", 0)),
            max_concurrency=int(item.get("max_concurrency", 0)),
            models=tuple(str(m) for m in item.get("models") or []),
        ))
    # Longest prefix first, so "vk/special/" wins over "vk/".
    return sorted(providers, key=lambda p: len(p.prefix), reverse=True)
