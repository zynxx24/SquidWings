"""
brain.max_token.max_token_config — Max token limits per model.

Loads max output token configurations from models_config.yaml
and provides per-model and per-task-type lookup.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from config.constants import TaskType
from utils.logger import get_logger

logger = get_logger(__name__)

# Default max output tokens per task type (used when config is missing)
DEFAULT_MAX_OUTPUT: dict[str, int] = {
    TaskType.CODE.value: 16384,
    TaskType.MATH.value: 4096,
    TaskType.ACTIVITY.value: 8192,
    TaskType.SCHOOL_TASK.value: 16384,
    TaskType.GENERATE_IMG.value: 4096,
    TaskType.REASON.value: 32768,
    TaskType.MULTIMODAL.value: 8192,
}

# Model max output limits (from models_config.yaml)
MODEL_MAX_OUTPUT: dict[str, int] = {
    # AG provider
    "ag/gemini-3.8-flash-high": 65536,
    "ag/claude-sonnet-4-6": 128000,
    "ag/claude-opus-4-6-thinking": 64000,
    # GEMINI provider (Direct from 9Router)
    "gemini/gemini-4-argon": 32768,
    "gemini/gemini-3.8-flash": 65536,
    "gemini/gemma-4-31b-it": 32768,
    # KG provider — DeepSeek
    "kg/deepseek-4-1-flash": 64000,
    "kg/dp4.1ftest": 64000,
    "kg/deepseek-v4-flash": 384000,
    "kg/deepseek-v4-pro": 384000,
    "kg/deepseek-v4.1-flash-1017": 128000,
    "kg/deepseek-v4.1-flash-expires-on-0910": 128000,
    # KG provider — Gemini
    "kg/gemini-3-8-flash": 65536,
    "kg/gemini-3.6-flash": 65536,
    "kg/gemini-3.7-flash": 65536,
    # KG provider — GLM (Zhipu AI)
    "kg/glm-5-3-flash": 128000,
    "kg/glm5-2": 64000,
    "kg/glm5-3": 64000,
    # KG provider — OpenAI GPT & Poolside
    "kg/gpt-5-6-luna": 128000,
    "kg/gpt-5-6-sol": 128000,
    "kg/gpt-6-astra": 128000,
    "kg/gpt-6-luna": 128000,
    "kg/gpt-6-sol": 128000,
    "kg/laguna-s-2.1": 32000,
    # KG provider — Moonshot Kimi
    "kg/kimi-k2-7-code": 262144,
    "kg/kimi-k3": 131072,
    "kg/kimi-k3.1-pro": 131072,
    # KG provider — Alibaba Qwen
    "kg/qwen3.8-plus": 65536,
    "kg/qwen3.8-max": 65536,
    "kg/qwen3.8-abliterated-cyber": 65536,
    "kg/qwen3.8-uncensored": 65536,
    # KG provider — MiniMax & MiMo
    "kg/minimax-m2.7": 131072,
    "kg/mimo-v2.6-flash": 131072,
    # KG provider — Muse & LongCat
    "kg/muse-spark-1.2-contributor": 131072,
    "kg/muse-spark-1.3-contributor": 131072,
    "kg/muse-wonder-1.0": 64000,
    "kg/longcat-2.5-preview": 64000,
    # KG provider — Claude
    "kg/claude-haiku-5-5": 64000,
    "kg/claude-sonnet-4-6": 128000,
    "kg/kagiro": 64000,
    # Bansos provider (direct Kagiro API)
    "bansos/deepseek-v4.1-flash-1017": 128000,
}


class MaxTokenConfig:
    """Configuration for max output tokens.

    Provides per-model and per-task-type max output token limits.
    Loaded from models_config.yaml or uses defaults.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Initialize the config.

        Args:
            config_path: Path to models_config.yaml. If None, uses defaults.
        """
        self._model_limits: dict[str, int] = dict(MODEL_MAX_OUTPUT)
        self._task_limits: dict[str, int] = dict(DEFAULT_MAX_OUTPUT)

        if config_path:
            self.load_config(config_path)

    def load_config(self, config_path: str | Path) -> None:
        """Load limits from YAML config.

        Args:
            config_path: Path to models_config.yaml.
        """
        path = Path(config_path)
        if not path.exists():
            return

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Load model limits
        models = config.get("models", {})
        for model_id, model_config in models.items():
            max_out = model_config.get("max_output")
            if max_out:
                self._model_limits[model_id] = max_out

        # Load task-type limits
        token_limits = config.get("token_limits", {})
        for task, limits in token_limits.items():
            default_max = limits.get("default_max_output")
            if default_max:
                self._task_limits[task] = default_max

        logger.info(
            f"[max_token] Loaded limits: {len(self._model_limits)} models, "
            f"{len(self._task_limits)} task types"
        )

    def get_model_max(self, model_id: str) -> int:
        """Get the max output tokens for a specific model.

        Args:
            model_id: The model identifier.

        Returns:
            int: Max output tokens.
        """
        return self._model_limits.get(model_id, 8192)

    def get_task_default_max(self, task_type: TaskType) -> int:
        """Get the default max output for a task type.

        Args:
            task_type: The task type.

        Returns:
            int: Default max output tokens for this task type.
        """
        return self._task_limits.get(task_type.value, 8192)

    def get_effective_max(
        self,
        model_id: str,
        task_type: TaskType,
        override: int | None = None,
    ) -> int:
        """Get the effective max tokens (min of model limit and task default).

        Args:
            model_id: The model to use.
            task_type: The task type.
            override: Optional explicit override.

        Returns:
            int: Effective max output tokens.
        """
        requested = (
            override
            if override is not None and override > 0
            else self.get_task_default_max(task_type)
        )
        return max(1, min(self.get_model_max(model_id), requested))
