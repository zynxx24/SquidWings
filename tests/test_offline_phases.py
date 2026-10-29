"""Offline smoke/behavior tests mapped to implementation-plan phases."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient

from brain.classifier.intent_classifier import IntentClassifier
from config.constants import TaskType
from config.settings import Settings, get_settings
from model.connection.request_builder import RequestBuilder
from model.connection.response_handler import ResponseHandler
from model.registry.model_info import ModelCapabilities, ModelInfo
from model.registry.model_registry import ModelRegistry
from tool.planning.database.plan_store import PlanStore
from tool.planning.database.schema import init_database
from tool.planning.flow.flow_builder import FlowBuilder, FlowStep
from tool.planning.logic.decomposer import TaskDecomposer
from utils.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError, CircuitState
from utils.metrics import MetricsCollector
from utils.retry import with_retry
from web import server


def test_phase_0_configuration_loads_offline() -> None:
    settings = Settings(_env_file=None)
    assert settings.project_root.is_dir()
    assert settings.models_config_path.exists()
    assert get_settings() is get_settings()
    config = yaml.safe_load(settings.models_config_path.read_text(encoding="utf-8"))
    assert len(config["models"]) >= 10


def test_phase_1_registry_request_and_response_contracts(tmp_path: Path) -> None:
    registry = ModelRegistry()
    registry.clear()
    registry.register(ModelInfo(id="mock/code", capabilities=ModelCapabilities(tools=True, reasoning=True), context_window=1000))
    assert registry.get("mock/code").supports_tools
    payload = RequestBuilder.build_chat_request("mock/code", [], tools=[{"type": "function", "function": {"name": "x"}}])
    assert payload["tool_choice"] == "auto"
    parsed = ResponseHandler.handle({"model": "mock/code", "choices": [{"message": {"content": "<think>reason</think>answer", "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": '{"a":1}'}}]}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}})
    assert parsed.content == "answer"
    assert parsed.thinking == "reason"
    assert parsed.tool_calls[0]["arguments"] == {"a": 1}
    assert parsed.total_tokens == 5


def test_phase_2_classifier_is_rule_based() -> None:
    result = IntentClassifier().classify("buatkan kode python untuk sorting")
    assert result.task_type == TaskType.CODE
    assert result.confidence > 0


def test_phase_3_planning_database_flow_and_sandbox(tmp_path: Path) -> None:
    conn = init_database(tmp_path / "plans.db")
    store = PlanStore(conn)
    plan_id = store.create_plan("offline", session_id="test")
    assert store.get_plan(plan_id)["name"] == "offline"
    assert store.update_plan(plan_id, status="completed")
    assert store.get_plan(plan_id)["status"] == "completed"
    flow = FlowBuilder().build([FlowStep("a", "A"), FlowStep("b", "B", depends_on=["a"])])
    assert flow.execution_order == ["a", "b"]
    with pytest.raises(ValueError, match="Cycle"):
        FlowBuilder().build([FlowStep("a", "A", depends_on=["b"]), FlowStep("b", "B", depends_on=["a"])])
    assert len(TaskDecomposer().decompose("fix bug in parser")) == 4
    conn.close()


@pytest.mark.asyncio
async def test_phase_6_retry_and_circuit_breaker() -> None:
    attempts = 0
    @with_retry(max_attempts=3, backoff_factor=0, jitter=False, retryable_exceptions=(ConnectionError,))
    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("mock only")
        return "ok"
    assert await flaky() == "ok"
    assert attempts == 3
    breaker = AsyncCircuitBreaker("mock", failure_threshold=1, recovery_timeout=60)
    async def fail():
        raise RuntimeError("mock failure")
    with pytest.raises(RuntimeError):
        await breaker.call(fail)
    assert breaker.state == CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        await breaker.call(fail)


def test_phase_7_metrics_count_success_failure_tokens_and_tools() -> None:
    metrics = MetricsCollector()
    metrics.record_request("mock", success=True, latency_ms=10, prompt_tokens=3, completion_tokens=4)
    metrics.record_request("mock", success=False, latency_ms=30, fallback_used=True)
    metrics.record_tool("mock_tool", success=True, latency_ms=5)
    snapshot = metrics.snapshot()
    assert snapshot["total"]["requests"] == 2
    assert snapshot["total"]["failures"] == 1
    assert snapshot["total"]["total_tokens"] == 7
    assert snapshot["models"]["mock"]["fallbacks"] == 1
    assert snapshot["tools"]["mock_tool"]["requests"] == 1


def test_phase_9_web_models_with_mocked_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    fake.registry.get_all.return_value = [ModelInfo(id="ag/gemini-3.8-flash-high", provider="ag", capabilities=ModelCapabilities(tools=True))]
    monkeypatch.setattr(server, "get_orchestrator", lambda: fake)
    with TestClient(server.app) as client:
        response = client.get("/api/models")
        assert response.status_code == 200
        data = response.json()
        assert "ag/gemini-3.8-flash-high" in json.dumps(data)
