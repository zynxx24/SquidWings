"""Reading web pages: content extraction, safety, and research across sources."""

from __future__ import annotations

from typing import Any

import pytest

from tool.execute.execute_web_search import web_reader
from tool.execute.execute_web_search.web_reader import Page, PageUnavailableError

ARTICLE = """
<html><head><title>Python 3.14 dirilis</title>
<meta property="article:published_time" content="2026-10-07T10:00:00Z"></head>
<body>
<nav class="menu"><a href="/">Beranda</a><a href="/x">MENU_NAVIGASI</a></nav>
<div class="cookie-banner">TEKS_COOKIE</div>
<article><h1>Python 3.14 dirilis</h1><p>Versi baru membawa JIT. %s</p>
<table><tr><th>Fitur</th></tr><tr><td>Free threading</td></tr></table></article>
<aside>TEKS_SAMPING</aside><footer>TEKS_FOOTER</footer>
<script>var JS_KODE = 1;</script>
</body></html>
""" % ("Isi panjang. " * 60)


def test_extraction_keeps_the_article_and_drops_the_rest() -> None:
    page = web_reader.extract_page(ARTICLE, "https://x.test/a")
    assert page.title == "Python 3.14 dirilis"
    assert page.published.startswith("2026-10-07")
    assert "Versi baru membawa JIT" in page.text
    assert "Free threading" in page.text
    for noise in ("MENU_NAVIGASI", "TEKS_COOKIE", "TEKS_SAMPING", "TEKS_FOOTER", "JS_KODE"):
        assert noise not in page.text


def test_page_without_article_tag_still_gives_its_text() -> None:
    page = web_reader.extract_page("<html><body><div><p>Harga kopi naik 5 persen.</p></div></body></html>", "https://x.test")
    assert "Harga kopi naik" in page.text


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:20128/v1/models",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ],
)
def test_private_addresses_cannot_be_read(url: str) -> None:
    with pytest.raises(PageUnavailableError):
        web_reader._check_url(url)


def test_public_addresses_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_reader.socket, "getaddrinfo", lambda *_a, **_k: [(0, 0, 0, "", ("93.184.216.34", 443))])
    web_reader._check_url("https://example.com/page")


def test_a_public_name_pointing_at_a_private_address_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_reader.socket, "getaddrinfo", lambda *_a, **_k: [(0, 0, 0, "", ("10.1.2.3", 443))])
    with pytest.raises(PageUnavailableError, match="not a public"):
        web_reader._check_url("https://rebind.example/")


@pytest.mark.parametrize("title", ["Just a moment...", "Attention Required! | Cloudflare", "Verify you are human"])
def test_bot_checks_are_recognised(title: str) -> None:
    assert web_reader.looks_blocked(Page(url="u", title=title, text="pendek"))


def test_a_long_article_mentioning_captcha_is_not_a_block() -> None:
    assert not web_reader.looks_blocked(Page(url="u", title="Sejarah captcha", text="captcha " * 400))


def test_passages_keep_what_the_query_asks_about() -> None:
    text = "\n\n".join(["Pembuka halaman."] + [f"Paragraf umum {i} " + "isi " * 40 for i in range(30)] + ["Harga kopi arabika naik."])
    kept, cut = web_reader.passages(text, "harga kopi arabika", 800)
    assert cut and len(kept) <= 800
    assert kept.startswith("Pembuka halaman.")
    assert "Harga kopi arabika naik." in kept


def test_short_pages_are_not_cut() -> None:
    assert web_reader.passages("pendek", "apa", 100) == ("pendek", False)


def test_results_prefer_different_sites() -> None:
    results = [{"url": f"https://a.test/{i}"} for i in range(5)] + [{"url": "https://b.test/1"}, {"url": "https://www.c.test/1"}]
    urls = [r["url"] for r in web_reader._pick_results(results, 4)]
    assert urls == ["https://a.test/0", "https://a.test/1", "https://b.test/1", "https://www.c.test/1"]


async def test_research_reads_pages_and_labels_what_it_could_not(monkeypatch: pytest.MonkeyPatch) -> None:
    async def search(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": True, "results": [
            {"title": "Baik", "url": "https://a.test/1", "snippet": "cuplikan a"},
            {"title": "Diblokir", "url": "https://b.test/1", "snippet": "cuplikan b"},
        ]}

    async def fetch(url: str) -> Page:
        if "b.test" in url:
            raise PageUnavailableError("b.test refused access")
        return Page(url=url, title="Judul A", text="ISI_HALAMAN_A " * 20, published="2026-10-01")

    monkeypatch.setattr(web_reader, "web_search", search)
    monkeypatch.setattr(web_reader, "fetch_page", fetch)
    result = await web_reader.web_research("kopi")
    assert result["ok"] and result["pages_read"] == 1
    good, bad = result["sources"]
    assert good["read"] and "ISI_HALAMAN_A" in good["content"] and good["published"] == "2026-10-01"
    assert not bad["read"] and bad["snippet"] == "cuplikan b" and "refused" in bad["reason"]


def test_results_matching_the_question_are_opened_first() -> None:
    results = [
        {"title": "Python", "url": "https://python.org/", "snippet": "Welcome"},
        {"title": "What's new", "url": "https://docs.python.org/whatsnew/3.14.html", "snippet": "Python 3.14 features"},
    ]
    assert web_reader._rank_results(results, "Python 3.14 features")[0]["url"].endswith("3.14.html")


def test_short_pages_leave_their_budget_to_long_ones() -> None:
    assert web_reader._share_budget([100, 50_000, 50_000], 12_000) == [100, 5950, 5950]
    assert sum(web_reader._share_budget([100, 200], 12_000)) == 300


async def test_empty_pages_do_not_count_as_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def search(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": True, "results": [{"title": "Kosong", "url": "https://a.test/1", "snippet": "cuplikan"}]}

    async def fetch(url: str) -> Page:
        return Page(url=url, text="{}")

    monkeypatch.setattr(web_reader, "web_search", search)
    monkeypatch.setattr(web_reader, "fetch_page", fetch)
    result = await web_reader.web_research("kopi")
    assert result["pages_read"] == 0 and not result["sources"][0]["read"]


async def test_a_failed_search_is_passed_on(monkeypatch: pytest.MonkeyPatch) -> None:
    async def search(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": False, "error": "provider refused", "results": []}

    monkeypatch.setattr(web_reader, "web_search", search)
    assert (await web_reader.web_research("kopi"))["ok"] is False


async def test_web_read_reports_refusals_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch(_url: str) -> Page:
        raise PageUnavailableError("x.test refused access")

    monkeypatch.setattr(web_reader, "fetch_page", fetch)
    result = await web_reader.web_read("https://x.test")
    assert result["ok"] is False and "refused" in result["error"]


def test_tool_calls_are_reported_while_still_being_written() -> None:
    from model.stream.stream_handler import StreamAggregator, StreamState

    seen: list[tuple[int, str, str]] = []
    aggregator = StreamAggregator(on_tool_delta=lambda *call: seen.append(call))
    state = StreamState()
    for piece in ('{"path": "app', '.py", "content": "x'):
        aggregator._process_chunk(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "file_write", "arguments": piece}}]}}]},
            state,
        )
    assert seen[-1] == (0, "file_write", '{"path": "app.py", "content": "x')


def test_web_results_are_not_cut_to_the_small_tier_limit() -> None:
    from agent.agent_loop import AgentLoop
    from agent.harness import Harness, scaffold_for

    loop = AgentLoop(None, None, harness=Harness(scaffold=scaffold_for("x-flash")))  # type: ignore[arg-type]
    big = {"success": True, "result": "x" * 15_000}
    assert len(loop._serialize_tool_result({**big, "tool_name": "web_research"})) >= 15_000
    assert len(loop._serialize_tool_result({**big, "tool_name": "read_file"})) < 8_000


@pytest.mark.parametrize(
    ("provider", "body"),
    [
        ("searxng", '{"results": [{"title": "A", "url": "https://a.test", "content": "isi"}, {"title": "B", "url": "ftp://b"}]}'),
        ("brave-api", '{"web": {"results": [{"title": "A", "url": "https://a.test", "description": "isi"}]}}'),
    ],
)
def test_search_api_answers_are_understood(provider: str, body: str) -> None:
    from tool.execute.execute_web_search.web_search_tool import _parse_provider

    assert _parse_provider(provider, body) == [{"title": "A", "url": "https://a.test", "snippet": "isi"}]


async def test_a_configured_search_api_is_tried_first(monkeypatch: pytest.MonkeyPatch) -> None:
    from tool.execute.execute_web_search import web_search_tool as tool

    asked: list[str] = []

    async def fetch(provider: str, _query: str, _config: Any) -> list[dict[str, str]]:
        asked.append(provider)
        return [{"title": "A", "url": "https://a.test", "snippet": ""}]

    monkeypatch.setattr(tool, "_fetch_from_provider", fetch)
    monkeypatch.setattr(tool, "_get_config", lambda: tool._Config(
        True, 1.0, 5, 1, 0.0, 0, 10, False, "", "", False, "", 1.0, searxng_url="http://searx.test",
    ))
    provider, _ = await tool._fetch_results("kopi", tool._get_config())
    assert provider == "searxng" and asked == ["searxng"]
