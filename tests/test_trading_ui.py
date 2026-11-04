"""The trading page's own code. Offline. The chart logic runs in Node when Node is installed."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "web" / "static" / "trading"
SCRIPTS = ["chart.js", "panel-core.js", "panel-tables.js", "panel-ticket.js", "trading.js"]


def _source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_every_script_is_loaded_in_dependency_order():
    html = _source("index.html")
    found = re.findall(r'src="/static/trading/([\w-]+\.js)"', html)
    assert found == SCRIPTS


def test_every_element_the_scripts_use_exists_on_the_page():
    ids = set(re.findall(r'id="([\w-]+)"', _source("index.html")))
    used = {name for script in SCRIPTS for name in re.findall(r'\$\("([\w-]+)"\)', _source(script))}
    assert used - ids == set()


def test_every_data_tab_and_timeframe_has_a_handler_target():
    html = _source("index.html")
    assert set(re.findall(r'data-tab="(\w+)"', html)) == {"positions", "orders", "history"}
    assert set(re.findall(r'data-tf="(\w+)"', html)) == {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}


def test_the_page_uses_the_same_design_tokens_and_theme_setting_as_the_chat():
    chat = (STATIC.parent / "index.html").read_text(encoding="utf-8")
    assert "/static/css/tokens.css" in chat and "/static/css/tokens.css" in _source("index.html")
    assert 'THEME_KEY = "theme"' in _source("trading.js")
    assert "#" not in _source("trading.css").split("* { box-sizing", 1)[0].replace("/*", "").split("*/")[-1]


def test_the_redraw_queue_really_draws():
    """Regression: a search-and-replace once made the queue call itself, so no chart ever appeared."""
    body = _source("chart.js").split("schedule() {", 1)[1].split("\n    }", 1)[0]
    assert "this.draw()" in body and "this.schedule()" not in body


def test_server_text_never_reaches_the_page_as_html():
    for script in SCRIPTS:
        assert "innerHTML" not in _source(script), script


def test_rates_endpoint_allows_the_bars_the_panel_asks_for():
    wanted = int(re.search(r"MT4_BARS = (\d+)", _source("trading.js")).group(1))
    allowed = int(re.search(r"count: int = Query\(200, ge=10, le=(\d+)\)", (STATIC.parent.parent / "mt5_api.py").read_text(encoding="utf-8")).group(1))
    assert wanted <= allowed


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_chart_math_in_node():
    chart_path = json.dumps(str(STATIC / "chart.js"))
    script = f"const C = require({chart_path});" + """
    const m5 = [0, 300, 600, 900].map((t, i) => ({ t, o: i + 1, h: i + 2, l: i, c: i + 1.5, v: 10 }));
    const ema = C.ema([{ c: 10 }, { c: 20 }], 3);
    console.log(JSON.stringify({
      m15: C.aggregate(m5, 900),
      ema,
      step: C.niceStep(1, 5),
      latest: C.visibleRange(1000, 800, 8, 8),
      past: C.visibleRange(1000, 800, 8, -200),
      few: C.visibleRange(3, 800, 8, 8),
    }));
    """
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20, check=True)
    out = json.loads(done.stdout)
    assert out["m15"] == [{"t": 0, "o": 1, "h": 4, "l": 0, "c": 3.5, "v": 30}, {"t": 900, "o": 4, "h": 5, "l": 3, "c": 4.5, "v": 10}]
    assert out["ema"] == [10, 15]
    assert out["step"] == pytest.approx(0.2)
    assert out["latest"]["last"] == 999, "the newest bar must be on screen at the default view"
    assert out["past"]["last"] < 999 < 1000 and out["past"]["first"] >= 0
    assert out["few"] == {"first": 0, "last": 2}
