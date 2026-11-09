"""The main page hosts the trading and router pages, and the model pickers group by upstream. Offline."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model.router.groups import group_of, grouped
from security.headers import (
    DEFAULT_CSP,
    LOOPBACK_FRAMES,
    csp_with,
    embeddable_headers,
    shell_headers,
)

STATIC = Path(__file__).resolve().parent.parent / "web" / "static"


# ---------------------------------------------------------------- grouping

NAMES = {"default": "Default", "bansos": "Bansos", "9r": "9router"}
PREFIXES = {"default": "", "bansos": "bansos/", "9r": "9r/"}


@pytest.mark.parametrize(("model", "provider", "key", "label"), [
    ("kg/claude-haiku-5-5", "default", "default/kg", "Default · kg"),
    ("vk/anthropic/claude-sonnet-4.6", "default", "default/vk", "Default · vk"),
    ("gemini/gemma-4-31b-it", "default", "default/gemini", "Default · gemini"),
    ("bansos/glm-5-3-flash", "bansos", "bansos", "Bansos"),            # the prefix is the provider, not an upstream
    ("9r/oc/qwen-4", "9r", "9r/oc", "9router · oc"),                    # a new router, other names
    ("9r/gpt-4.1", "9r", "9r", "9router"),
    ("gpt-4.1-mini", "", "lain", "lain"),
    ("Mixed/Case", "x", "x/mixed", "x · mixed"),                        # unknown provider keeps its id
])
def test_group_of(model, provider, key, label):
    group = group_of(model, provider, NAMES, PREFIXES)
    assert (group.key, group.label) == (key, label)


def test_grouped_sorts_groups_and_ids():
    out = grouped([("vk/b", "default"), ("kg/z", "default"), ("kg/a", "default"), ("bansos/m", "bansos")], NAMES, PREFIXES)
    assert [(g.label, ids) for g, ids in out] == [
        ("Bansos", ["bansos/m"]), ("Default · kg", ["kg/a", "kg/z"]), ("Default · vk", ["vk/b"])]


def test_family_matches_the_router_rest_scope():
    # The picker group and the quota rest of the rotator must name the same upstream.
    from model.router.rotation import family_of

    assert group_of("kg/x", "default", NAMES, PREFIXES).family == family_of("kg/x")


# ---------------------------------------------------------------- frame headers

def test_default_csp_still_refuses_frames():
    assert "frame-ancestors 'none'" in DEFAULT_CSP


def test_shell_may_frame_only_itself_and_loopback():
    csp = shell_headers()["Content-Security-Policy"]
    assert f"frame-src {LOOPBACK_FRAMES}" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self';" in csp


def test_embeddable_page_rules():
    headers = embeddable_headers()
    assert headers["Content-Security-Policy"].endswith("frame-ancestors 'self'")
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert csp_with().count("frame-ancestors") == 1


@pytest.fixture(scope="module")
def client():
    from web.server import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.mark.parametrize("path", ["/", "/trading", "/router"])
def test_each_path_opens_the_main_page(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "/static/js/app.js" in r.text and "/static/js/page.js" in r.text
    assert "frame-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(("path", "script"), [("/trading?embed=1", "trading.js"), ("/router?embed=1", "router.js")])
def test_embedded_pages(client, path, script):
    r = client.get(path)
    assert r.status_code == 200 and script in r.text and "/static/js/page.js" in r.text
    assert r.headers["x-frame-options"] == "SAMEORIGIN"


def test_models_carry_their_group(client, monkeypatch):
    from model.registry.model_info import ModelInfo
    from web import server

    orch = server.get_orchestrator()
    for model_id in ("kg/test-a", "vk/test-b"):
        orch.registry.register(ModelInfo(id=model_id, display_name=model_id, provider="default"))
    try:
        data = client.get("/api/models").json()
        flat = {m["id"]: m for ms in data["models"].values() for m in ms}
        assert flat["kg/test-a"]["group"] == "default/kg"
        assert flat["vk/test-b"]["group_label"].endswith("· vk")
        assert any(g["key"] == "default/kg" for g in data["groups"])
        groups = client.get("/api/router/models").json()["groups"]
        assert any(g["key"] == "default/vk" and "vk/test-b" in g["models"] for g in groups)
    finally:
        for model_id in ("kg/test-a", "vk/test-b"):
            orch.registry.unregister(model_id)


# ---------------------------------------------------------------- static wiring

def test_pages_load_the_shared_loader_before_their_scripts():
    for page in ("index.html", "router/index.html", "trading/index.html"):
        html = (STATIC / page).read_text(encoding="utf-8")
        scripts = re.findall(r'<script src="([^"]+)"', html)
        assert scripts[0] == "/static/js/page.js", page
        assert "/static/css/page.css" in html


def test_standalone_controls_hide_inside_the_chat_page():
    router = (STATIC / "router/index.html").read_text(encoding="utf-8")
    trading = (STATIC / "trading/index.html").read_text(encoding="utf-8")
    assert 'data-sq-view="chat" data-sq-standalone' in router
    assert 'id="themebtn"' in trading and 'data-sq-standalone>Tema' in trading


def test_rail_links_switch_views_in_place():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for view in ("chat", "trading", "router"):
        assert f'data-view="{view}"' in html
    assert 'target="_blank"' not in html.split('id="nav-trading"')[1].split("</a>")[0]


def test_chat_model_picker_follows_router_changes():
    # The chat page used to fetch models once; providers added on the router page never showed.
    router = (STATIC / "router/router.js").read_text(encoding="utf-8")
    page = (STATIC / "js/page.js").read_text(encoding="utf-8")
    app = (STATIC / "js/app.js").read_text(encoding="utf-8")
    assert "notify('sq-models')" in router
    assert "BroadcastChannel('squidwings')" in page and "BroadcastChannel('squidwings')" in app
    assert "e.data.type === 'sq-models') refreshModels(true)" in app
    assert app.count("refreshModels(true)") >= 4   # frame, other tab, tab focus, back to chat
