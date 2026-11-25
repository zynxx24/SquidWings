"""Static completeness checks, kept separate from behavioral test claims."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_planned_runtime_modules_exist() -> None:
    required = [
        "brain/config/settings.py", "brain/model/connection/api_client.py",
        "brain/model/registry/model_registry.py", "brain/classifier/intent_classifier.py",
        "tools/register_tools.py", "brain/agent/orchestrator.py", "brain/agent/agent_loop.py",
        "main.py", "web/server.py",
    ]
    assert not [name for name in required if not (ROOT / name).is_file()]


@pytest.mark.parametrize("name", ["README.md", ".env.example", ".gitignore", "main.py"])
def test_documented_plan_artifacts(name: str) -> None:
    assert (ROOT / name).exists(), f"Plan artifact is missing: {name}"
