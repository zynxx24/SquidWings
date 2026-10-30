"""
tool.execute.execute_auto_generate.auto_generate_tool — Auto-enhance prompts for app creation.

Tool "auto_generate" digunakan di mode bansos.
AI mengirimkan prompt mentah user, tool ini mengembalikan
prompt yang sudah diperkaya secara detail agar hasil pembuatan
aplikasi lebih baik dan lengkap.

Flow:
1. Terima prompt mentah user (misal: "buatkan aplikasi kasir")
2. Gunakan bansos model untuk generate prompt yang lebih detail
3. Return prompt yang sudah diperkaya ke AI
4. AI lalu menggunakan prompt tersebut sebagai konteks
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from dotenv import load_dotenv

from utils.logger import get_logger

logger = get_logger(__name__)

_MAX_ATTEMPTS = 6

# System prompt untuk auto-enhance
_ENHANCER_SYSTEM_PROMPT = """Kamu adalah AI Prompt Enhancer spesialis pembuatan aplikasi.
Tugas kamu HANYA satu: menerima deskripsi singkat aplikasi dari user,
lalu menghasilkan prompt yang SANGAT DETAIL dan TERSTRUKTUR untuk membuat aplikasi tersebut.

Panduan output:
1. Breakdown fitur-fitur yang dibutuhkan (minimal 5 fitur)
2. Tentukan tech stack yang cocok (bahasa, framework, database jika perlu)
3. Deskripsikan UI/UX yang diharapkan (warna, layout, responsif)
4. Tentukan struktur file/folder yang ideal
5. Berikan contoh data dummy jika relevan
6. Berikan instruksi langkah demi langkah untuk implementasi
7. Tambahkan error handling dan edge cases yang perlu diperhatikan

Format output harus berupa prompt terstruktur yang siap digunakan untuk membangun aplikasi.
JANGAN menulis kode — hanya tulis prompt detail yang nanti akan digunakan AI lain untuk generate kode.
Tulis dalam bahasa Indonesia."""


async def auto_generate(
    user_prompt: str,
    bansos_base_url: str | None = None,
    bansos_api_key: str | None = None,
    bansos_model: str | None = None,
) -> dict[str, Any]:
    """Auto-enhance a user prompt for better application generation.

    Memanggil bansos model untuk memperkaya prompt user agar
    menghasilkan kode aplikasi yang lebih baik dan lengkap.
    Menerapkan fallback / retry tiada henti jika terjadi error API.

    Args:
        user_prompt: Prompt mentah dari user tentang aplikasi yang ingin dibuat.
        bansos_base_url: Base URL API bansos (optional, uses env/settings).
        bansos_api_key: API key bansos (optional, uses env/settings).
        bansos_model: Model ID bansos (optional, uses env/settings).

    Returns:
        dict with keys: success, original_prompt, enhanced_prompt, model_used, error.
    """
    # Always reload .env so edits to key/url take effect immediately
    load_dotenv(override=True)

    base_url = bansos_base_url or os.getenv("BANSOS_API_BASE_URL", "https://api.kagiro.net/v1")
    api_key = bansos_api_key or os.getenv("BANSOS_API_KEY", "")
    model = bansos_model or os.getenv("BANSOS_MODEL", "deepseek-v4.1-flash-1017")

    if not api_key:
        return {
            "success": False,
            "original_prompt": user_prompt,
            "enhanced_prompt": "",
            "error": "BANSOS_API_KEY belum di-set. Silakan isi di file .env.",
        }

    logger.info(f"[auto_generate] Enhancing prompt with {model} via {base_url}")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _ENHANCER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Buatkan prompt detail untuk aplikasi berikut:\n\n{user_prompt}"},
        ],
        "temperature": 0.7,
        "max_tokens": 4096,
        "stream": False,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    if not base_url:
        raise ValueError("base_url is required for auto_generate")

    attempt = 0
    while attempt < _MAX_ATTEMPTS:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                response = await client.post(
                    f"{(base_url or '').rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                )

            if response.status_code != 200:
                error_msg = response.text[:200]
                # A rejected request or key will not fix itself by retrying.
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    return {
                        "success": False,
                        "original_prompt": user_prompt,
                        "error": f"status={response.status_code} body={error_msg}",
                    }
                backoff = min(attempt * 1.5, 6.0)
                logger.warning(
                    f"[auto_generate] API error (attempt {attempt}): "
                    f"status={response.status_code} body={error_msg}. Retrying in {backoff:.1f}s..."
                )
                await asyncio.sleep(backoff)
                continue

            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                backoff = min(attempt * 1.5, 6.0)
                logger.warning(f"[auto_generate] Empty choices (attempt {attempt}). Retrying...")
                await asyncio.sleep(backoff)
                continue

            enhanced = choices[0].get("message", {}).get("content", "")
            if not enhanced.strip():
                backoff = min(attempt * 1.5, 6.0)
                logger.warning(f"[auto_generate] Blank content from model (attempt {attempt}). Retrying...")
                await asyncio.sleep(backoff)
                continue

            logger.info(f"[auto_generate] Enhanced prompt: {len(enhanced)} chars (attempt {attempt})")
            return {
                "success": True,
                "original_prompt": user_prompt,
                "enhanced_prompt": enhanced,
                "model_used": model,
                "tokens_used": data.get("usage", {}),
            }

        except asyncio.CancelledError:
            raise
        except Exception as e:
            backoff = min(attempt * 1.5, 6.0)
            logger.warning(f"[auto_generate] Exception (attempt {attempt}): {e}. Retrying in {backoff:.1f}s...")
            await asyncio.sleep(backoff)
            continue

    return {
        "success": False,
        "original_prompt": user_prompt,
        "error": f"No usable response after {_MAX_ATTEMPTS} attempts",
    }
