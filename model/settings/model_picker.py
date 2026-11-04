"""Learned model choice for routing.

The static routing table always sent a task to the same few models, and models that the
API added later were never tried, because they were not in the YAML. This module fixes both:

* Candidates come from the routing table AND from the live registry (anything the API
  lists that looks like a model for the job). New models join the rotation on their own.
* Each choice is a Thompson draw over the model's success record. A model with little data
  still gets tried sometimes; a model that keeps failing rarely does.
* A small rotation bonus favours models that have not been used lately, so one model does
  not win every time.
* For easy tasks the cheap models get a bonus, so a low-budget setup stays cheap.

The stats are model reliability, not chat content, so they are kept in one file under
``.agent_memory``. Nothing about a user or a chat is stored there.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

# Names that suggest a low-cost model. Used for discovery and for the easy-task bonus.
CHEAP_PATTERN = re.compile(r"flash|haiku|\bmini\b|-mini|lite|nano|free|gemma|glm|deepseek|qwen|kimi", re.I)
# Names that are not chat models, so they never enter the rotation.
NOT_CHAT_PATTERN = re.compile(r"embed|rerank|moderat|whisper|tts|speech|transcri|dall|imagen|image|audio", re.I)

CHEAP_BONUS = 0.15
ROTATION_BONUS = 0.05
LATENCY_PENALTY = 0.05
LATENCY_SCALE_MS = 20_000.0


def is_cheap(model_id: str) -> bool:
    return bool(CHEAP_PATTERN.search(model_id))


def is_chat_model(model_id: str) -> bool:
    return not NOT_CHAT_PATTERN.search(model_id)


class ModelPicker:
    """Keeps a success record per model and picks among candidates."""

    def __init__(self, path: Path | None = None, rng: random.Random | None = None) -> None:
        self._path = path
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._stats: dict[str, dict[str, float]] = {}
        self._load()

    # ------------------------------------------------------------------ storage
    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"[picker] Could not read model stats, starting fresh: {exc}")
            return
        if isinstance(data, dict):
            self._stats = {str(k): v for k, v in data.items() if isinstance(v, dict)}

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._stats, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            logger.debug(f"[picker] Could not save model stats: {exc}")

    # ------------------------------------------------------------------ learning
    def record(self, model_id: str, ok: bool, latency_ms: float | None = None) -> None:
        with self._lock:
            s = self._stats.setdefault(model_id, {"ok": 0.0, "fail": 0.0, "lat": 0.0, "last": 0.0})
            s["ok" if ok else "fail"] += 1
            if ok and latency_ms:
                s["lat"] = latency_ms if not s["lat"] else 0.7 * s["lat"] + 0.3 * latency_ms
            s["last"] = time.time()
            self._save()

    def stats(self, model_id: str) -> dict[str, float]:
        with self._lock:
            return dict(self._stats.get(model_id, {}))

    # ------------------------------------------------------------------ choosing
    def score(self, model_id: str, now: float, *, prefer_cheap: bool) -> float:
        s = self.stats(model_id)
        draw = self._rng.betavariate(s.get("ok", 0.0) + 1.0, s.get("fail", 0.0) + 1.0)
        latency_penalty = LATENCY_PENALTY * min(s.get("lat", 0.0) / LATENCY_SCALE_MS, 1.0)
        last = s.get("last", 0.0)
        idle_hours = min((now - last) / 3600.0, 24.0) if last else 24.0
        rotation = ROTATION_BONUS * idle_hours / 24.0
        cheap = CHEAP_BONUS if prefer_cheap and is_cheap(model_id) else 0.0
        return draw - latency_penalty + rotation + cheap

    def choose(self, candidates: list[str], *, prefer_cheap: bool = False, now: float | None = None) -> str | None:
        """The candidate with the best draw. Duplicates are ignored. None when there are no candidates."""
        unique = list(dict.fromkeys(candidates))
        if not unique:
            return None
        now = time.time() if now is None else now
        return max(unique, key=lambda mid: self.score(mid, now, prefer_cheap=prefer_cheap))


_picker: ModelPicker | None = None
_picker_lock = threading.Lock()


def get_model_picker() -> ModelPicker:
    """The process-wide picker, backed by the stats file under the project's .agent_memory."""
    global _picker
    with _picker_lock:
        if _picker is None:
            import os

            from config.settings import get_settings

            override = os.getenv("MODEL_STATS_PATH", "").strip()   # tests point this at a temp file
            path = Path(override) if override else get_settings().project_root / ".agent_memory" / "model_stats.json"
            _picker = ModelPicker(path)
        return _picker


def discover(registry_models: list[Any], *, prefer_cheap: bool, needs_tools: bool,
             is_frontier: Any) -> list[str]:
    """Registry models that fit a task, so models the API adds later are tried without a config edit.

    Easy tasks get only the cheap names. Medium tasks get anything that is not frontier.
    Hard tasks are not sent here: they stay on the configured model.
    """
    out: list[str] = []
    for model in registry_models:
        mid = model.id
        if not is_chat_model(mid):
            continue
        if needs_tools and not getattr(getattr(model, "capabilities", None), "tools", True):
            continue
        if prefer_cheap and not (is_cheap(mid) or getattr(model, "is_free", False)):
            continue
        if not prefer_cheap and is_frontier(mid):
            continue
        out.append(mid)
    return out


__all__ = [
    "ModelPicker", "discover", "get_model_picker", "is_cheap", "is_chat_model",
    "CHEAP_PATTERN", "NOT_CHAT_PATTERN",
]
