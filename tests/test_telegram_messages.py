"""Telegram messages that read well, buttons that open a page in Telegram, and the live channel of a flow."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from brain.agent import team_orchestrator, team_outputs
from brain.agent.orchestrator import AgentResponse
from brain.agent.team_flow import normalize_node_config
from brain.agent.team_orchestrator import TeamOrchestrator, TeamStore
from brain.agent.team_views import ViewStore
from brain.agent.telegram_format import compose, json_to_html, markdown_to_html, page_html
from brain.config.constants import ResponseStatus


class FakeOrchestrator:
    def __init__(self, reply: str = "jawaban") -> None:
        self.reply = reply
        self.registry = SimpleNamespace(get=lambda _id: object())

    async def process(self, **kwargs: Any) -> AgentResponse:
        return AgentResponse(content=self.reply, status=ResponseStatus.SUCCESS)


def node(id_: str, kind: str, config: dict[str, Any], *after: str) -> dict[str, Any]:
    return {"id": id_, "name": id_, "kind": kind, "config": config, "depends_on": list(after)}


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def fake_rich(parts: list[str], chat_id: str = "", **options: Any) -> int:
        sent.append({"parts": parts, "chat_id": chat_id, **options})
        return len(parts)

    monkeypatch.setattr(team_orchestrator, "send_rich", fake_rich)
    return sent


def make(tmp_path: Path, reply: str = "jawaban") -> TeamOrchestrator:
    return TeamOrchestrator(FakeOrchestrator(reply), TeamStore(tmp_path / "teams"))  # type: ignore[arg-type]


async def finish(manager: TeamOrchestrator, team_id: str, task: str = "tugas") -> Any:
    run = await manager.start_run(team_id, task, {"source": "telegram", "chat_id": "42"})
    await asyncio.wait_for(manager.run_tasks[run.id], timeout=5)
    return manager.store.get_run(run.id)


# --- formatting --------------------------------------------------------------------


def test_markdown_becomes_telegram_html() -> None:
    text = "# Cuaca\n\nHari ini **cerah** dan _hangat_.\n\n- pagi 24°\n- siang 31°\n\n`kode` <script>\n\n[Sumber](https://bmkg.go.id)"
    out = markdown_to_html(text)
    assert out.startswith("<b>Cuaca</b>")
    assert "<b>cerah</b>" in out and "<i>hangat</i>" in out
    assert "• pagi 24°" in out
    assert "<code>kode</code> &lt;script&gt;" in out
    assert '<a href="https://bmkg.go.id">Sumber</a>' in out
    assert "<a" not in markdown_to_html("[x](javascript:alert(1))")


def test_tables_fit_a_phone() -> None:
    narrow = markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert narrow.startswith("<pre>") and "─" in narrow
    wide = markdown_to_html("| produk | keterangan panjang sekali | harga |\n|---|---|---|\n| Kaos polos premium | katun combed 30s | 75000 |")
    assert "<b>Kaos polos premium</b>" in wide and "harga: 75000" in wide


def test_rows_become_a_short_list_with_readable_numbers() -> None:
    rows = [{"kode": "KS-1", "nama": f"Kaos {i}", "harga": 75000, "hp": "0812345"} for i in range(12)]
    html, cut = json_to_html(rows)
    assert cut and "dan 4 lainnya" in html
    assert "• <b>Kaos 0</b>" in html and "harga: 75.000" in html and "hp: 0812345" in html


def test_long_text_is_summarised_only_when_a_page_can_hold_the_rest() -> None:
    long = "\n\n".join(f"Paragraf {i} " + "isi " * 60 for i in range(20))
    assert compose(long, summarize=True).cut
    whole = compose(long, summarize=False)
    assert not whole.cut and len(whole.parts) > 1 and all(len(p) <= 4096 for p in whole.parts)
    plain = compose("**tebal**", "plain")
    assert plain.parts == ["**tebal**"] and not plain.html


def test_page_shows_rows_as_a_table_and_escapes_text() -> None:
    page = page_html("Pesanan <b>", "Toko", json.dumps([{"nama": "<i>Kaos</i>", "harga": 75000}]))
    assert "<th>nama</th>" in page and "&lt;i&gt;Kaos&lt;/i&gt;" in page and "75.000" in page
    assert "<title>Pesanan &lt;b&gt;</title>" in page
    assert "<script>alert" not in page_html("t", "", "<script>alert(1)</script>")


def test_telegram_node_settings_keep_only_what_changed() -> None:
    assert normalize_node_config("telegram", {"chat_id": "42"}, ()) == {"chat_id": "42"}
    kept = normalize_node_config("telegram", {"style": "plain", "text": "Hai", "silent": True,
                                              "buttons": [{"label": "Buka", "kind": "web"}, {"label": "", "url": ""}]}, ())
    assert kept == {"chat_id": "", "style": "plain", "text": "Hai", "buttons": [{"label": "Buka", "kind": "web"}], "silent": True}
    with pytest.raises(ValueError, match="tautan"):
        normalize_node_config("telegram", {"buttons": [{"label": "x", "kind": "link", "url": "ftp://a"}]}, ())


def test_web_buttons_open_inside_telegram_only_in_a_private_chat() -> None:
    buttons = [{"label": "Lihat", "kind": "web", "url": "https://a.trycloudflare.com/v/abc"},
               {"label": "Toko", "kind": "link", "url": "https://toko.id"}]
    private = team_outputs.keyboard(buttons, private=True)
    assert private == {"inline_keyboard": [[{"text": "Lihat", "web_app": {"url": "https://a.trycloudflare.com/v/abc"}},
                                            {"text": "Toko", "url": "https://toko.id"}]]}
    group = team_outputs.keyboard(buttons, private=False)
    assert group is not None and group["inline_keyboard"][0][0] == {"text": "Lihat", "url": "https://a.trycloudflare.com/v/abc"}


# --- in a flow ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_message_with_a_web_page_button(tmp_path: Path, outbox: list[dict[str, Any]],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    manager = make(tmp_path, reply=json.dumps([{"nama": "Kaos", "harga": 75000}, {"nama": "Tote", "harga": 55000}]))

    async def base_url() -> str:
        return "https://contoh.trycloudflare.com"

    monkeypatch.setattr(manager.pages, "base_url", base_url)
    team = manager.create_team("Toko", "", [
        {"id": "a", "name": "a", "role": "r", "model_id": "m"},
        node("kirim", "telegram", {"text": "Ada **{{json.count}}** produk, termurah {{json.1.nama}}",
                                   "buttons": [{"label": "Lihat semua", "kind": "web"}], "page_title": "Katalog"}, "a"),
    ], None, style="flow", starts=["a"])
    run = await finish(manager, team.id)
    assert run.status == "completed", run.error
    message = outbox[0]
    assert message["chat_id"] == "42"
    assert message["parts"] == ["Ada <b>2</b> produk, termurah Tote"]
    assert message["fallback"] == ["Ada **2** produk, termurah Tote"]
    button = message["buttons"][0]
    assert button["label"] == "Lihat semua" and button["kind"] == "web"
    token = button["url"].rsplit("/", 1)[1]
    assert button["url"].startswith("https://contoh.trycloudflare.com/v/")
    page = manager.pages.store.render(token)
    assert page is not None and "<title>Katalog</title>" in page and "Tote" in page
    assert "jendela web" in run.agents["kirim"]["note"]


@pytest.mark.asyncio
async def test_long_answer_gets_a_summary_and_an_open_button(tmp_path: Path, outbox: list[dict[str, Any]],
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    manager = make(tmp_path, reply="\n\n".join("Bagian " + "kata " * 80 for _ in range(10)))

    async def base_url() -> str:
        return "https://contoh.trycloudflare.com"

    monkeypatch.setattr(manager.pages, "base_url", base_url)
    team = manager.create_team("Riset", "", [{"id": "a", "name": "a", "role": "r", "model_id": "m"},
                                             node("kirim", "telegram", {}, "a")], None, style="flow", starts=["a"])
    await finish(manager, team.id)
    assert len(outbox[0]["parts"]) == 1 and "bersambung" in outbox[0]["parts"][0]
    assert outbox[0]["buttons"][0]["label"] == "Buka lengkap"


@pytest.mark.asyncio
async def test_without_a_public_address_a_long_answer_is_split_instead(tmp_path: Path, outbox: list[dict[str, Any]],
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    manager = make(tmp_path, reply="\n\n".join("Bagian " + "kata " * 80 for _ in range(14)))

    async def no_url() -> str:
        raise RuntimeError("tidak ada cloudflared")

    monkeypatch.setattr(manager.pages, "base_url", no_url)
    team = manager.create_team("Riset", "", [{"id": "a", "name": "a", "role": "r", "model_id": "m"},
                                             node("kirim", "telegram", {}, "a")], None, style="flow", starts=["a"])
    run = await finish(manager, team.id)
    assert len(outbox[0]["parts"]) > 1 and not outbox[0]["buttons"]
    assert "tidak dibuat" in run.agents["kirim"]["note"]


@pytest.mark.asyncio
async def test_the_flow_channel_carries_runs_started_elsewhere(tmp_path: Path, outbox: list[dict[str, Any]]) -> None:
    manager = make(tmp_path)
    team = manager.create_team("f", "", [{"id": "a", "name": "a", "role": "r", "model_id": "m"}], None,
                               style="flow", starts=["a"])
    seen: list[str] = []

    async def listen() -> None:
        async for event in manager.stream_team(team.id, ping_seconds=0.05):
            seen.append(event["type"])
            if event["type"] == "run_finished":
                return

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0.01)
    await finish(manager, team.id)
    await asyncio.wait_for(listener, timeout=5)
    assert seen[0] in ("ping", "run_started") and "run_finished" in seen
    assert "agent_finished" in seen


def test_pages_are_found_only_by_their_token(tmp_path: Path) -> None:
    store = ViewStore(tmp_path / "views")
    token = store.save("Judul", "Alur", "isi")
    assert store.render(token) is not None
    assert store.get("../teams") is None and store.get("pendek") is None


@pytest.mark.asyncio
async def test_preview_fills_the_same_names_a_run_does() -> None:
    from web.team_api import TelegramPreview, telegram_preview

    out = await telegram_preview(TelegramPreview(config={"text": "**{{json.count}}** pesanan, {{date}}"},
                                                 input='[{"nama": "A"}, {"nama": "B"}]'))
    assert out["html"] and out["parts"][0].startswith("<b>2</b> pesanan, 20")
    assert "{{" not in out["parts"][0]
