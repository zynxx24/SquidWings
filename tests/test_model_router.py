"""The built-in model router: provider store, smart rotation, Claude translation, token log, dispatch, API.

Offline. HTTP goes through httpx.MockTransport, so no request leaves the process.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from model.router import anthropic as A
from model.router import dispatch as D
from model.router import usage as U
from model.router.rotation import NoAccountError, Rotator, get_rotator
from model.router.store import Store, StoreError, get_store
from utils.exceptions import ModelError, RateLimitError


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_STORE_DIR", str(tmp_path / "router"))
    for p in get_store().providers():
        get_store().delete_provider(p.id)
    get_rotator()._health.clear()
    get_rotator()._sticky.clear()
    get_rotator()._family_rest.clear()
    D._clients.clear()
    yield


def _provider(kind="openai", keys=("key-aaaa-1111", "key-bbbb-2222"), **extra):
    return get_store().add_provider({"name": f"Test {kind}", "kind": kind, "base_url": "https://up.example/v1",
                                     "prefix": "t/" if kind == "openai" else "c/", "api_keys": list(keys), **extra})


# ---------------------------------------------------------------- store


def test_store_saves_with_owner_only_permissions_and_masks_keys(tmp_path):
    p = _provider()
    path = get_store().path
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    public = json.dumps(get_store().public(), ensure_ascii=False)
    assert "key-aaaa-1111" not in public and "key-aa…1111" in public
    assert get_store().route("t/gpt-x").id == p.id and get_store().route("other/x") is None
    assert Store(get_store().folder).providers()[0].accounts[1].api_key == "key-bbbb-2222"   # survives a reload


def test_store_refuses_bad_input():
    _provider()
    with pytest.raises(StoreError, match="Prefix"):
        get_store().add_provider({"name": "Dup", "base_url": "https://x/v1", "prefix": "t/"})
    with pytest.raises(StoreError, match="http"):
        get_store().add_provider({"name": "Bad", "base_url": "ftp://x"})
    with pytest.raises(StoreError, match="sudah ada"):
        get_store().add_account(get_store().providers()[0].id, {"api_key": "key-aaaa-1111"})


def test_pasted_full_endpoint_and_multi_line_keys():
    p = get_store().add_provider({"name": "Paste", "base_url": "https://x.example/v1/chat/completions/"})
    assert p.base_url == "https://x.example/v1" and p.prefix == "paste/"
    added = get_store().add_account(p.id, {"api_key": "k1-xxxxxxxx\nk2-xxxxxxxx\n\n", "label": "free"})
    assert [a.label for a in added] == ["free 1", "free 2"]


# ---------------------------------------------------------------- rotation


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _picks(r, p, n, session=""):
    out = []
    for _ in range(n):
        a = r.pick(p, session).account
        r.release(p.id, a.id)
        out.append(a.label)
    return out


def test_round_robin_follows_weights_without_bursts():
    p = _provider(keys=("k-1xxxxxxx", "k-2xxxxxxx", "k-3xxxxxxx"), strategy="round_robin")
    get_store().update_account(p.id, p.accounts[0].id, {"weight": 2})
    p = get_store().get(p.id)
    labels = _picks(Rotator(Clock()), p, 8)
    assert labels.count("akun 1") == 4 and labels.count("akun 2") == 2 and labels.count("akun 3") == 2
    assert all(labels[i] != labels[i + 1] or labels[i] == "akun 1" for i in range(7))
    assert "akun 1akun 1akun 1" not in "".join(labels)


def test_rate_limited_account_rests_and_traffic_moves():
    clock = Clock()
    r = Rotator(clock)
    p = _provider()
    first = r.pick(p).account
    r.release(p.id, first.id)
    rest = r.failure(p.id, first.id, status=429, retry_after=30)
    assert rest == 30
    assert {_picks(r, p, 1)[0] for _ in range(4)} == {next(a.label for a in p.accounts if a.id != first.id)}
    clock.t += 31
    assert first.label in _picks(r, p, 4)


def test_a_spent_upstream_rests_only_its_model_family():
    """One key in front of a router: kagiro's empty budget must not stop Gemini on the same account."""
    clock = Clock()
    r = Rotator(clock)
    p = _provider(keys=("only-key-1111",))
    account = p.accounts[0]
    rest = r.failure(p.id, account.id, status=402, message="token budget exhausted", model="kg/minimax-m2.7")
    assert rest == 60 * 60
    assert r.pick(p, model="gemini/gemma-4-31b-it").account.id == account.id
    with pytest.raises(NoAccountError, match="kg/"):
        r.pick(p, model="kg/deepseek-v4-pro")
    snapshot = r.snapshot(p)[account.id]
    assert snapshot["status"] == "siap" and snapshot["resting_families"]["kg"]["reason"] == "kuota habis"
    r.failure(p.id, account.id, status=429, message="You exceeded your current quota", model="gemini/gemini-4-argon")
    assert r.pick(p, model="vk/openai/gpt-6-luna").account.id == account.id
    clock.t += 3601
    assert r.pick(p, model="kg/minimax-m2.7").account.id == account.id


def test_a_bad_key_still_rests_the_whole_account_whatever_the_model():
    r = Rotator(Clock())
    p = _provider(keys=("only-key-1111",))
    r.failure(p.id, p.accounts[0].id, status=401, message="invalid key", model="kg/minimax-m2.7")
    with pytest.raises(NoAccountError):
        r.pick(p, model="gemini/gemma-4-31b-it")


def test_reset_clears_family_rests():
    r = Rotator(Clock())
    p = _provider(keys=("only-key-1111",))
    r.failure(p.id, p.accounts[0].id, status=402, model="kg/x")
    r.reset(p.id)
    assert r.pick(p, model="kg/x").account.id == p.accounts[0].id


def test_bad_key_rests_long_and_everything_resting_says_when():
    clock = Clock()
    r = Rotator(clock)
    p = _provider()
    for a in p.accounts:
        r.failure(p.id, a.id, status=401, message="invalid key")
    with pytest.raises(NoAccountError) as info:
        r.pick(p)
    assert info.value.retry_after == pytest.approx(30 * 60)


def test_smart_keeps_a_session_on_one_account_for_its_cache():
    r = Rotator(Clock())
    p = _provider(keys=("k-1xxxxxxx", "k-2xxxxxxx", "k-3xxxxxxx"))
    chat_a = _picks(r, p, 5, session="chat-a")
    assert len(set(chat_a)) == 1
    others = {_picks(r, p, 1, session=f"chat-{i}")[0] for i in range(6)}
    assert len(others) == 3, "different chats still spread over every account"
    # The sticky account fails: the session moves, and stays on its new account.
    stuck = next(a for a in p.accounts if a.label == chat_a[0])
    r.failure(p.id, stuck.id, status=429, retry_after=60)
    moved = _picks(r, p, 3, session="chat-a")
    assert stuck.label not in moved and len(set(moved)) == 1


def test_smart_gives_a_slow_flaky_account_less_traffic_not_none():
    r = Rotator(Clock())
    p = _provider()
    good, bad = p.accounts
    for _ in range(5):
        r.success(p.id, good.id, 400)
        r.success(p.id, bad.id, 1600)
    for _ in range(2):   # failures without a rest (status 0 rests only after two in a row)
        r.failure(p.id, bad.id, status=500)
        r.success(p.id, bad.id, 1600)
    labels = _picks(r, p, 40)
    assert labels.count(good.label) > labels.count(bad.label) * 2
    assert labels.count(bad.label) >= 1


def test_fill_first_uses_the_first_healthy_account():
    r = Rotator(Clock())
    p = _provider(strategy="fill_first")
    assert set(_picks(r, p, 5)) == {"akun 1"}
    r.failure(p.id, p.accounts[0].id, status=429)
    assert set(_picks(r, p, 3)) == {"akun 2"}


def test_concurrency_limit_is_respected():
    r = Rotator(Clock())
    p = _provider()
    for a in p.accounts:
        get_store().update_account(p.id, a.id, {"max_concurrency": 1})
    p = get_store().get(p.id)
    r.pick(p), r.pick(p)
    with pytest.raises(NoAccountError):
        r.pick(p)


# ---------------------------------------------------------------- Claude translation


def test_openai_payload_becomes_a_claude_request():
    payload = {
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": [{"type": "text", "text": "Look"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "print(1)"},
            {"role": "user", "content": "and now?"},
        ],
        "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read", "parameters": {"type": "object"}}}],
        "max_tokens": 500, "temperature": 0.3, "stop": "END",
    }
    body = A.to_request(payload, "claude-x")
    assert body["system"][0]["text"] == "Be brief." and body["system"][0]["cache_control"] == {"type": "ephemeral"}
    roles = [t["role"] for t in body["messages"]]
    assert roles == ["user", "assistant", "user"], "tool result and the next user text merge into one turn"
    assert body["messages"][0]["content"][1]["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}
    assert body["messages"][1]["content"] == [{"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "a.py"}}]
    assert body["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "call_1", "content": "print(1)"}
    assert body["messages"][2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0]["input_schema"] == {"type": "object"} and body["tool_choice"] == {"type": "auto"}
    assert body["stop_sequences"] == ["END"] and body["max_tokens"] == 500 and body["temperature"] == 0.3


def test_claude_reply_becomes_a_chat_completion():
    data = {"id": "msg_1", "stop_reason": "tool_use",
            "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "Reading."},
                        {"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {"path": "x"}}],
            "usage": {"input_tokens": 100, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 50, "output_tokens": 40}}
    out = A.to_response(data, "c/claude-x")
    msg = out["choices"][0]["message"]
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert msg["content"] == "Reading." and msg["reasoning_content"] == "hmm"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"path": "x"}
    assert out["usage"]["prompt_tokens"] == 1050 and out["usage"]["prompt_tokens_details"]["cached_tokens"] == 900


def test_claude_stream_events_become_openai_chunks():
    t = A.StreamTranslator("c/claude-x")
    events = [
        {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 10, "cache_read_input_tokens": 90}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tu", "name": "f", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"a\":"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "1}"}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]
    chunks = [c for e in events for c in t.feed(e)]
    deltas = [c["choices"][0]["delta"] for c in chunks if c["choices"]]
    assert "".join(d.get("content", "") for d in deltas) == "Hi"
    args = "".join(tc["function"].get("arguments", "") for d in deltas for tc in d.get("tool_calls", []))
    assert args == "{\"a\":1}"
    assert any(c["choices"] and c["choices"][0]["finish_reason"] == "tool_calls" for c in chunks)
    assert chunks[-1]["usage"]["prompt_tokens"] == 100 and chunks[-1]["usage"]["completion_tokens"] == 7

    from model.stream.stream_handler import StreamAggregator

    async def replay():
        for c in chunks:
            yield c

    parsed = asyncio.run(StreamAggregator().aggregate(replay()))
    assert parsed.content == "Hi" and parsed.tool_calls[0]["name"] == "f" and parsed.tool_calls[0]["arguments"] == {"a": 1}


# ---------------------------------------------------------------- usage log


def test_usage_normalizes_both_formats():
    assert U.normalize({"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 60}}) == \
        {"input": 100, "output": 20, "cached": 60, "cache_write": 0, "total": 120}
    assert U.normalize({"input_tokens": 10, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 5, "output_tokens": 3}) == \
        {"input": 95, "output": 3, "cached": 80, "cache_write": 5, "total": 98}
    assert U.normalize(None) is None


def test_usage_periods_groups_and_series(tmp_path):
    log = U.UsageLog(tmp_path / "u.db")
    now = time.time()
    log.record(provider="p1", account="a1", account_label="satu", model="t/m1", ts=now - 60,
               usage={"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 50}})
    log.record(provider="p1", account="a2", model="t/m2", ts=now - 7200, usage={"prompt_tokens": 1000, "completion_tokens": 100})
    log.record(provider="p2", model="x/m3", ts=now - 3 * 86400, usage={"prompt_tokens": 5, "completion_tokens": 5})
    log.record(provider="p2", model="x/m3", ts=now - 40 * 86400, usage={"prompt_tokens": 7, "completion_tokens": 7})
    log.record(provider="p1", model="t/m1", ts=now - 30, usage=None, stream=True)
    log.record(provider="p1", model="t/m1", ts=now - 20, usage=None, ok=False, error="boom")
    hour = log.summary("1h", "provider", now=now)
    assert hour["totals"]["requests"] == 3 and hour["totals"]["total"] == 110 and hour["totals"]["errors"] == 1
    assert hour["totals"]["unreported"] == 1 and hour["totals"]["cache_hit_pct"] == 50.0
    assert len(hour["series"]) in (12, 13) and sum(b["total"] for b in hour["series"]) == 110
    assert log.summary("24h", "account", now=now)["totals"]["total"] == 1210
    assert {r["model"] for r in log.summary("7d", "model", now=now)["rows"]} == {"t/m1", "t/m2", "x/m3"}
    assert log.summary("30d", now=now)["totals"]["total"] == 1220
    assert log.summary("all", now=now)["totals"]["total"] == 1234
    with pytest.raises(ValueError):
        log.summary("2y")


# ---------------------------------------------------------------- dispatch


def _mock(monkeypatch, handler):
    real = D._client

    def fake(provider, account):
        key = (provider.id, account.id, provider.base_url, provider.timeout_s)
        if key not in D._clients:
            D._clients[key] = httpx.AsyncClient(base_url=provider.base_url, headers=D._headers(provider, account.api_key),
                                                transport=httpx.MockTransport(handler))
        return D._clients[key]

    monkeypatch.setattr(D, "_client", fake)
    return real


def test_dispatch_fails_over_from_a_rate_limited_key_and_logs_tokens(monkeypatch):
    p = _provider()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"].split()[-1]
        seen.append(key)
        body = json.loads(request.content)
        assert body["model"] == "gpt-x", "the prefix is stripped on the wire"
        if key == "key-aaaa-1111":
            return httpx.Response(429, headers={"retry-after": "20"}, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

    _mock(monkeypatch, handler)
    for _ in range(3):
        out = asyncio.run(D.chat(p, "t/gpt-x", [{"role": "user", "content": "hi"}]))
        assert out["choices"][0]["message"]["content"] == "ok"
    assert seen.count("key-aaaa-1111") == 1, "the resting key is not tried again"
    totals = U.get_usage_log().summary("1h", "account")
    assert totals["totals"]["total"] == 45 and totals["totals"]["errors"] == 1


def test_dispatch_does_not_punish_a_key_for_a_bad_request(monkeypatch):
    p = _provider()
    _mock(monkeypatch, lambda request: httpx.Response(400, json={"error": {"message": "bad param"}}))
    with pytest.raises(ModelError) as info:
        asyncio.run(D.chat(p, "t/gpt-x", [{"role": "user", "content": "hi"}]))
    assert info.value.status_code == 400
    assert all(not s["cooling_s"] for s in get_rotator().snapshot(p).values())


def test_dispatch_raises_rate_limit_when_every_key_is_out(monkeypatch):
    p = _provider()
    _mock(monkeypatch, lambda request: httpx.Response(429, headers={"retry-after": "120"}, json={"error": "limit"}))
    with pytest.raises(RateLimitError):
        asyncio.run(D.chat(p, "t/gpt-x", [{"role": "user", "content": "hi"}]))


def test_dispatch_speaks_claude_with_its_headers(monkeypatch):
    p = _provider(kind="anthropic", keys=("sk-ant-xxxxxx",))
    get_store().update_provider(p.id, {"base_url": "https://api.anthropic.example"})
    p = get_store().get(p.id)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "sk-ant-xxxxxx" and request.headers["anthropic-version"]
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["model"] == "claude-x" and body["messages"][0]["role"] == "user"
        return httpx.Response(200, json={"id": "m", "stop_reason": "end_turn", "content": [{"type": "text", "text": "halo"}],
                                         "usage": {"input_tokens": 5, "cache_read_input_tokens": 20, "output_tokens": 2}})

    _mock(monkeypatch, handler)
    out = asyncio.run(D.chat(p, "c/claude-x", [{"role": "user", "content": "hi"}]))
    assert out["choices"][0]["message"]["content"] == "halo"
    assert U.get_usage_log().summary("1h")["totals"]["cached"] == 20


def test_dispatch_streams_claude_and_records_usage(monkeypatch):
    p = _provider(kind="anthropic", keys=("sk-ant-yyyyyy",))
    sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in [
        {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 4}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "yo"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ])
    _mock(monkeypatch, lambda request: httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"}))

    async def run():
        return [c async for c in D.chat_stream(p, "c/claude-x", [{"role": "user", "content": "hi"}])]

    chunks = asyncio.run(run())
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"]) == "yo"
    assert U.get_usage_log().summary("1h")["totals"]["total"] == 5


def test_openai_stream_asks_for_usage(monkeypatch):
    p = _provider()
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"a"}}]}\n\ndata: [DONE]\n\n')

    _mock(monkeypatch, handler)

    async def run():
        return [c async for c in D.chat_stream(p, "t/gpt-x", [{"role": "user", "content": "hi"}])]

    asyncio.run(run())
    assert bodies[0]["stream_options"] == {"include_usage": True}


def test_api_client_routes_router_models_through_the_dispatcher(monkeypatch):
    from model.connection.api_client import APIClient

    _provider()
    calls = {}

    async def fake_chat(provider, model, messages, **kw):
        calls["model"] = model
        return {"choices": [{"message": {"content": "via router"}}]}

    monkeypatch.setattr(D, "chat", fake_chat)
    client = APIClient("https://main.example/v1", "main-key")
    out = asyncio.run(client.chat_completion("t/gpt-x", [{"role": "user", "content": "hi"}]))
    assert out["choices"][0]["message"]["content"] == "via router" and calls["model"] == "t/gpt-x"
    assert client.provider_of("t/gpt-x") == get_store().providers()[0].id
    assert get_store().providers()[0].id in client.provider_ids


def test_list_models_tags_the_prefix_and_adds_manual_ids(monkeypatch):
    p = _provider(models="extra-1")
    _mock(monkeypatch, lambda request: httpx.Response(200, json={"data": [{"id": "gpt-x"}, {"id": "extra-1"}]}))
    ids = [m["id"] for m in asyncio.run(D.list_models(p))]
    assert ids == ["t/gpt-x", "t/extra-1"]


# ---------------------------------------------------------------- HTTP API


def _no_fetch(monkeypatch, models=("gpt-x", "gpt-y")):
    async def fake(provider, account=None):
        return [{"id": provider.prefix + m, "owned_by": provider.id} for m in models]

    monkeypatch.setattr(D, "list_models", fake)


def test_tunnel_can_edit_like_the_local_page_until_a_pin_is_set(monkeypatch):
    from web.server import app

    _no_fetch(monkeypatch)
    far = TestClient(app, client=("203.0.113.9", 5000))
    r = far.post("/api/router/providers", json={"name": "Nine", "kind": "openai", "base_url": "http://127.0.0.1:20128/v1",
                                                 "api_keys": ["sk-local-123456"]})
    assert r.status_code == 200, r.text
    pid = r.json()["provider"]["id"]
    assert r.json()["models"]["count"] == 2, "models are fetched as soon as the provider is added"
    listing = far.get("/api/router/providers").json()
    assert listing["can_edit"] is True and listing["has_pin"] is False
    assert "sk-local-123456" not in json.dumps(listing)

    assert far.post("/api/router/pin", json={"pin": "4321"}).json()["has_pin"] is True
    assert far.get("/api/router/providers").json()["can_edit"] is False
    assert far.post(f"/api/router/providers/{pid}/accounts", json={"api_key": "sk-evil-000000"}).status_code == 403
    assert far.post(f"/api/router/providers/{pid}/accounts", json={"api_key": "sk-evil-000000"},
                    headers={"X-Router-Pin": "0000"}).status_code == 403
    ok = far.post(f"/api/router/providers/{pid}/accounts", json={"api_key": "sk-mine-999999"}, headers={"X-Router-Pin": "4321"})
    assert ok.status_code == 200 and ok.json()["models"]["ok"] is True
    local = TestClient(app, client=("127.0.0.1", 5000))
    assert local.get("/api/router/providers").json()["can_edit"] is True, "this computer never needs the PIN"
    assert local.post("/api/router/pin", json={"pin": ""}).json()["has_pin"] is False
    assert far.get("/api/router/usage?period=7d&group=model").status_code == 200
    assert far.get("/api/router/usage?period=nope").status_code == 400
    assert get_store().meta("pin_hash", "") == "" and "4321" not in get_store().path.read_text()


def test_env_endpoints_move_to_the_page_once():
    env = {"API_BASE_URL": "https://main.example/v1", "API_KEY": "sk-main-key-1",
           "BANSOS_API_BASE_URL": "https://bansos.example/v1", "BANSOS_API_KEY": "sk-bansos-1", "BANSOS_MODEL": "free-1"}
    added = get_store().import_env(env)
    assert [(p.name, p.prefix, p.default) for p in added] == [("Default", "", True), ("Bansos", "bansos/", False)]
    assert added[1].models == ["free-1"]
    assert get_store().route("gpt-anything").id == added[0].id, "the default provider serves unprefixed ids"
    assert get_store().route("bansos/free-1").id == added[1].id
    assert get_store().import_env(env) == [], "only once"
    assert get_store().import_env(env, force=True) == [], "an endpoint already there is skipped"


def test_only_one_default_and_the_old_one_keeps_its_models_reachable():
    a = get_store().add_provider({"name": "First", "base_url": "https://a.example/v1", "default": True, "api_keys": ["k-aaaaaaaa"]})
    b = get_store().add_provider({"name": "Second", "base_url": "https://b.example/v1", "default": True, "api_keys": ["k-bbbbbbbb"]})
    first = get_store().get(a.id)
    assert b.default and not first.default and first.prefix == "first/"
    assert get_store().route("whatever").id == b.id


def test_new_provider_models_reach_the_registry_and_leave_with_it(monkeypatch):
    from web.server import app, get_orchestrator

    _no_fetch(monkeypatch, models=("m1", "m2"))
    local = TestClient(app, client=("127.0.0.1", 5000))
    pid = local.post("/api/router/providers", json={"name": "Reg", "base_url": "https://r.example/v1", "prefix": "rg/",
                                                     "api_keys": ["k-regggggg"]}).json()["provider"]["id"]
    registry = get_orchestrator().registry
    assert registry.get("rg/m1") is not None and registry.get("rg/m2") is not None
    _no_fetch(monkeypatch, models=("m1",))
    local.post(f"/api/router/providers/{pid}/refresh")
    assert registry.get("rg/m2") is None, "a model the provider stopped listing is dropped"
    groups = local.get("/api/router/models").json()["groups"]
    assert any(g["provider"] == pid and g["models"] == ["rg/m1"] for g in groups)
    assert local.post("/api/router/default-model", json={"model": "rg/m1"}).status_code == 200
    assert local.get("/api/router/models").json()["default_model"] == "rg/m1"
    local.delete(f"/api/router/providers/{pid}")
    assert registry.get("rg/m1") is None


def test_no_key_anywhere_points_to_the_router_page(monkeypatch):
    from model.connection.api_client import APIClient

    client = APIClient("https://main.example/v1", "")
    with pytest.raises(ModelError, match="/router"):
        asyncio.run(client.chat_completion("gpt-x", [{"role": "user", "content": "hi"}]))


def test_router_page_is_served():
    from web.server import app

    r = TestClient(app).get("/router")
    assert r.status_code == 200 and "Router model" in r.text and "/static/router/router.js" in r.text
