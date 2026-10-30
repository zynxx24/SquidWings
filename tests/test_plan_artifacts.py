"""Static completeness checks, kept separate from behavioral test claims."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_planned_runtime_modules_exist() -> None:
    required = [
        "config/settings.py", "model/connection/api_client.py",
        "model/registry/model_registry.py", "brain/classifier/intent_classifier.py",
        "tool/register_tools.py", "agent/orchestrator.py", "agent/agent_loop.py",
        "main.py", "web/server.py",
    ]
    assert not [name for name in required if not (ROOT / name).is_file()]


@pytest.mark.parametrize("name", ["README.md", ".env.example", ".gitignore", "cli.py"])
def test_documented_plan_artifacts(name: str) -> None:
    assert (ROOT / name).exists(), f"Plan artifact is missing: {name}"
