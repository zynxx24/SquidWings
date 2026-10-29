"""
config.secrets — Secure API key management.

Loads API keys from environment, validates format, and masks
them in log output to prevent accidental exposure.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Secrets:
    """Immutable container for sensitive credentials.

    Attributes:
        api_key: The API key for the model router endpoint.
    """

    api_key: str

    def __post_init__(self) -> None:
        """Validate the API key on construction."""
        if not self.api_key or self.api_key == "your-api-key-here":
            raise ValueError(
                "API_KEY is not set or is still the placeholder value. "
                "Please set a valid API_KEY in your .env file."
            )

    @property
    def masked_key(self) -> str:
        """Return a masked version of the API key for safe logging.

        Shows first 8 and last 4 characters, masks the rest.

        Returns:
            str: Masked API key string.
        """
        if len(self.api_key) <= 12:
            return self.api_key[:4] + "****"
        return self.api_key[:8] + "****" + self.api_key[-4:]

    @property
    def authorization_header(self) -> dict[str, str]:
        """Build the Authorization header dict.

        Returns:
            dict: Header dict with Bearer token.
        """
        return {"Authorization": f"Bearer {self.api_key}"}

    def __repr__(self) -> str:
        """Safe repr that never exposes the full key."""
        return f"Secrets(api_key='{self.masked_key}')"

    def __str__(self) -> str:
        """Safe str that never exposes the full key."""
        return f"Secrets(api_key='{self.masked_key}')"


@functools.lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    """Get cached singleton Secrets instance.

    Loads from environment variables (with .env support).

    Returns:
        Secrets: The global secrets object.

    Raises:
        ValueError: If API_KEY is missing or invalid.
    """
    load_dotenv()
    api_key = os.getenv("API_KEY", "")
    return Secrets(api_key=api_key)
