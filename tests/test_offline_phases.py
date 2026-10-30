"""Offline smoke/behavior tests mapped to implementation-plan phases."""

import json
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient

from brain.classifier.intent_classifier import IntentClassifier
from config.constants import TaskType
from config.settings import Settings, get_settings
from model.registry.model_info import ModelCapabilities, ModelInfo
from web import server


def test_phase_0_configuration_loads_offline() -> None:
    settings = Settings(_env_file=None)
    assert settings.project_root.is_dir()
    assert settings.models_config_path.exists()
    assert get_settings() is get_settings()
    config = yaml.safe_load(settings.models_config_path.read_text(encoding="utf-8"))
    assert "models" not in config
    assert config["routing"] and config["default_model"]




def test_phase_2_classifier_is_rule_based() -> None:
    result = IntentClassifier().classify("buatkan kode python untuk sorting")
    assert result.task_type == TaskType.CODE
    assert result.confidence > 0








def test_phase_9_web_models_with_mocked_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    fake.registry.get_all.return_value = [ModelInfo(id="ag/gemini-3.8-flash-high", provider="ag", capabilities=ModelCapabilities(tools=True))]
    monkeypatch.setattr(server, "get_orchestrator", lambda: fake)
    with TestClient(server.app) as client:
        response = client.get("/api/models")
        assert response.status_code == 200
        data = response.json()
        assert "ag/gemini-3.8-flash-high" in json.dumps(data)
