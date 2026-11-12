"""ElevenLabs voice tools: clear setup message without a key, real files with one. No network: the API is faked."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from config import credentials
from tool.execute.execute_toolbox import CATALOG, match, voice_tools

KEY = "sk_test_0123456789abcdefghijklmnop"
VOICES = {"voices": [
    {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "category": "premade",
     "labels": {"gender": "female", "age": "young", "accent": "american", "description": "calm"}},
    {"voice_id": "pNInz6obpgDQGcFmaJgB", "name": "Adam", "category": "premade",
     "labels": {"gender": "male", "age": "middle aged", "accent": "american", "description": "deep"}},
]}


def run(name: str, **kwargs: Any) -> dict[str, Any]:
    out = CATALOG[name].func(**kwargs)
    if asyncio.iscoroutine(out):
        out = asyncio.run(out)
    return out


@pytest.fixture()
def no_key(monkeypatch):
    monkeypatch.setattr(credentials, "get", lambda key: "")


@pytest.fixture()
def api(monkeypatch, tmp_path):
    """A connected account. Returns the list of requests the tools made."""
    values = {"elevenlabs_api_key": KEY, "elevenlabs_voice_id": ""}
    monkeypatch.setattr(credentials, "get", lambda key: values.get(key, ""))
    calls: list[dict[str, Any]] = []

    async def fake(method, path, *, json_body=None, params=None, timeout=30.0):
        calls.append({"method": method, "path": path, "json": json_body, "params": params})
        request = httpx.Request(method, f"{voice_tools.API}{path}")
        if path == "/v1/voices":
            return httpx.Response(200, json=VOICES, request=request)
        if path == "/v1/user/subscription":
            return httpx.Response(200, json={"tier": "free", "character_count": 1200, "character_limit": 10000},
                                  request=request)
        if path.startswith("/v1/text-to-speech/"):
            return httpx.Response(200, content=b"ID3fake-mp3", headers={"content-type": "audio/mpeg"}, request=request)
        raise AssertionError(path)

    monkeypatch.setattr(voice_tools, "_request", fake)
    return calls


@pytest.fixture()
def ws(tmp_path):
    from utils import workspace_context

    token = workspace_context._active_workspace.set(str(tmp_path))
    yield tmp_path
    workspace_context._active_workspace.reset(token)


def test_without_a_key_both_tools_say_how_to_connect(no_key, ws):
    for name, kwargs in (("voice_list", {}), ("voice_speak", {"text": "Halo", "output": "a.mp3"})):
        out = run(name, **kwargs)
        assert out["ok"] is False and out["connected"] is False
        assert "Kredensial" in out["setup"] and "ELEVENLABS_API_KEY" in out["setup"]
    assert not list(ws.iterdir())


def test_list_shows_voices_and_what_is_left(api):
    out = run("voice_list")
    assert out["ok"] and out["count"] == 2 and out["model"] == voice_tools.DEFAULT_MODEL
    assert out["quota"]["characters_left"] == 8800
    assert run("voice_list", search="male")["count"] == 2
    assert [v["name"] for v in run("voice_list", search="deep")["voices"]] == ["Adam"]


def test_speak_by_name_writes_an_mp3_in_the_workspace(api, ws):
    out = run("voice_speak", text="Model murah, kerja disiplin.", output="voice/open", voice="adam", share=False)
    assert out["ok"] and out["saved"] == "voice/open.mp3" and out["voice"] == "Adam"
    assert (ws / "voice" / "open.mp3").read_bytes() == b"ID3fake-mp3"
    tts = next(c for c in api if c["method"] == "POST")
    assert tts["path"] == "/v1/text-to-speech/pNInz6obpgDQGcFmaJgB"
    assert tts["json"]["model_id"] == "eleven_multilingual_v2" and tts["json"]["text"].startswith("Model murah")
    assert KEY not in str(out)


def test_an_unknown_voice_lists_the_choices(api, ws):
    out = run("voice_speak", text="Halo", output="a.mp3", voice="Budi", share=False)
    assert out["ok"] is False and "Rachel" in out["error"] and "Adam" in out["error"]


def test_too_long_text_is_refused_before_any_call(api, ws):
    out = run("voice_speak", text="a" * 5001, output="a.mp3", share=False)
    assert out["ok"] is False and "split" in out["error"]
    assert api == []


def test_a_rejected_key_is_reported_plainly(monkeypatch, ws):
    monkeypatch.setattr(credentials, "get", lambda key: KEY if key == "elevenlabs_api_key" else "")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["xi-api-key"] == KEY and request.url.host == "api.elevenlabs.io"
        return httpx.Response(401, json={"detail": {"status": "invalid_api_key"}})

    real = httpx.AsyncClient
    monkeypatch.setattr(voice_tools.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    out = run("voice_list")
    assert out["ok"] is False and "401" in out["error"] and KEY not in out["error"]


def test_voice_words_wake_the_tools_and_the_key_is_a_credential():
    assert "voice_speak" in match("tolong bikin voice over buat video ini")
    assert "voice_list" in match("daftar suara yang ada di elevenlabs")
    assert "elevenlabs_api_key" in credentials.BY_KEY and credentials.BY_KEY["elevenlabs_api_key"].secret
    assert any(g["key"] == "voice" for g in credentials.public()["groups"])


def test_the_credentials_test_button_checks_elevenlabs(api):
    from web.team_api import _test_voice

    assert asyncio.run(_test_voice()) == "ElevenLabs tersambung: 2 suara, sisa 8.800 karakter."


def test_the_credentials_test_button_says_how_to_connect(no_key):
    from agent.team_outputs import OutputError
    from web.team_api import _test_voice

    with pytest.raises(OutputError, match="belum tersambung"):
        asyncio.run(_test_voice())
