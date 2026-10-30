"""Guard the web UI against CSP violations.

The server ships a strict `script-src 'self'` policy (see
`security/headers.py`). Any CDN `<script>`, or any inline `<script>` block,
breaks the UI without breaking the Python code, so it needs a test.

Keep every reference in these files local. If a vendor library is needed,
download it into `web/static/js/` and reference it with a `/static/...` path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from security.headers import DEFAULT_CSP

STATIC = Path(__file__).resolve().parent.parent / "web" / "static"
INDEX = STATIC / "index.html"

_SCRIPT_SRC = re.compile(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
_SCAN_SUFFIXES = {".html", ".css", ".js"}


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_index_has_no_external_script(index_html: str) -> None:
    external = [s for s in _SCRIPT_SRC.findall(index_html) if "://" in s or s.startswith("//")]
    assert external == [], f"external scripts are blocked by CSP script-src 'self': {external}"


def test_all_scripts_are_same_origin(index_html: str) -> None:
    for src in _SCRIPT_SRC.findall(index_html):
        assert src.startswith("/static/"), f"unexpected script path: {src}"


def test_no_inline_script_blocks(index_html: str) -> None:
    # `<script>` with neither src nor type="application/json" needs 'unsafe-inline'.
    inline = [
        m.group(0)
        for m in re.finditer(r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>", index_html, re.IGNORECASE | re.DOTALL)
        if m.group(1).strip()
    ]
    assert inline == [], "inline <script> needs a nonce/hash or 'unsafe-inline'; move it to /static/js"


def test_vendored_scripts_exist(index_html: str) -> None:
    for src in _SCRIPT_SRC.findall(index_html):
        path = STATIC / src[len("/static/"):]
        assert path.is_file(), f"missing vendored asset: {path}"


def test_no_remote_stylesheet_or_import() -> None:
    for path in STATIC.rglob("*"):
        if path.suffix.lower() not in _SCAN_SUFFIXES or path.name.endswith(".min.js"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "@import url(http" not in text, f"remote @import in {path.name}"
        for href in re.findall(r"<link\b[^>]*\bhref=[\"']([^\"']+)[\"']", text, re.IGNORECASE):
            if href.endswith(".css"):
                assert not ("://" in href or href.startswith("//")), f"remote stylesheet in {path.name}: {href}"


def test_csp_allows_only_self_scripts() -> None:
    directive = [d.strip() for d in DEFAULT_CSP.split(";") if d.strip().startswith("script-src")]
    assert directive, "CSP is missing script-src"
    assert "'unsafe-inline'" not in directive[0]
    assert "'unsafe-eval'" not in directive[0]
    assert "'self'" in directive[0]
