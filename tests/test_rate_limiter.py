"""Tests for RateLimiter and APIClient rate limit retry handling."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from config.constants import TaskType
from model.connection.api_client import APIClient
from model.registry.model_info import ModelInfo
from model.registry.model_registry import ModelRegistry
from model.settings.fallback import FallbackStrategy
from model.settings.rate_limiter import RateLimiter, TokenBucket


def test_token_bucket_consume_and_refill() -> None:
    bucket = TokenBucket(capacity=2.0, refill_rate=10.0)
    assert bucket.consume(1.0) is True
    assert bucket.consume(1.0) is True
    assert bucket.consume(1.0) is False  # Empty


def test_rate_limiter_free_model_defaults() -> None:
    limiter = RateLimiter()
    # Free model gets conservative capacity
    bucket_free = limiter._get_bucket("oc/deepseek-v4.1-flash-free")
    assert bucket_free.capacity == RateLimiter.FREE_MODEL_CAPACITY
    assert bucket_free.refill_rate == RateLimiter.FREE_MODEL_REFILL_RATE

    # Standard model gets standard capacity
    bucket_std = limiter._get_bucket("openai/gpt-4o")
    assert bucket_std.capacity == RateLimiter.DEFAULT_CAPACITY
    assert bucket_std.refill_rate == RateLimiter.DEFAULT_REFILL_RATE


def test_rate_limiter_penalize() -> None:
    limiter = RateLimiter()
    model = "test-model"
    assert limiter.acquire(model) is True
    limiter.penalize(model, penalty_seconds=2.0)
    assert limiter.get_wait_time(model) >= 1.5
    assert limiter.acquire(model) is False


@pytest.mark.asyncio
async def test_rate_limiter_wait_and_acquire() -> None:
    limiter = RateLimiter(default_capacity=1.0, default_refill_rate=50.0)
    model = "fast-model"
    assert await limiter.wait_and_acquire(model) is True
    # Immediately acquire again, which will wait briefly and succeed
    assert await limiter.wait_and_acquire(model, max_wait=1.0) is True


@pytest.mark.asyncio
async def test_api_client_rate_limit_retry() -> None:
    limiter = RateLimiter()
    client = APIClient("http://fake-api", "fake-key", rate_limiter=limiter, max_retries=2)

    # Mock client.post: first call returns 429, second returns 200
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "0.05"}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"choices": [{"message": {"content": "Success"}}]}

    call_count = 0

    async def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_resp_429
        return mock_resp_200

    mock_httpx = MagicMock()
    mock_httpx.is_closed = False
    mock_httpx.post = AsyncMock(side_effect=fake_post)
    client._client = mock_httpx

    res = await client.chat_completion(
        model="oc/deepseek-v4.1-flash-free",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert res["choices"][0]["message"]["content"] == "Success"
    assert call_count == 2


def test_fallback_dynamic_models() -> None:
    ModelRegistry.reset_singleton()
    registry = ModelRegistry()
    registry.clear()
    registry.register(ModelInfo(id="oc/deepseek-v4.1-flash-free"))
    registry.register(ModelInfo(id="oc/glm-5.3-flash-free"))

    fallback = FallbackStrategy(registry=registry)
    # No chain defined in config, should fallback dynamically to other registered model
    next_model = fallback.get_next(
        task_type=TaskType.ACTIVITY,
        failed_model="oc/deepseek-v4.1-flash-free",
        session_key="test-sess",
    )
    assert next_model is not None
    assert next_model.id == "oc/glm-5.3-flash-free"
