"""Images in a session's files: the file endpoint, the read-file tool, and the path rules behind them.

Offline. Each test uses a temporary folder as the session workspace, so nothing touches real chats.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import web.server as server
from tool.execute.execute_upload_file import read_file_tool


def _png(width: int = 4, height: int = 3) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def session_root(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "session_workspace", lambda sid, create=True: tmp_path)
    (tmp_path / "plot.png").write_bytes(_png())
    (tmp_path / "notes.txt").write_text("secret text", encoding="utf-8")
    (tmp_path / "vector.svg").write_text("<svg></svg>", encoding="utf-8")
    return tmp_path


def test_session_image_is_served_with_safe_headers(session_root):
    client = TestClient(server.app)
    resp = client.get("/api/session/s1/file", params={"path": "plot.png"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in resp.headers["content-security-policy"]
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_path_traversal_is_refused(session_root, tmp_path):
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(_png())
    try:
        client = TestClient(server.app)
        resp = client.get("/api/session/s1/file", params={"path": "../outside.png"})
        assert resp.status_code == 404
    finally:
        outside.unlink(missing_ok=True)


def test_only_media_files_are_served(session_root):
    client = TestClient(server.app)
    assert client.get("/api/session/s1/file", params={"path": "notes.txt"}).status_code == 415
    assert client.get("/api/session/s1/file", params={"path": "vector.svg"}).status_code == 415
    assert client.get("/api/session/s1/file", params={"path": "missing.png"}).status_code == 404


def test_read_file_reports_image_size_and_a_markdown_embed(session_root, monkeypatch):
    monkeypatch.setattr("utils.workspace_context.session_workspace", lambda sid, create=True: session_root)
    monkeypatch.setattr("utils.workspace_context.get_active_session", lambda: "s1")
    out = read_file_tool.read_file(str(session_root / "plot.png"))
    assert out["success"] is True and out["is_image"] is True
    assert (out["width"], out["height"], out["format"]) == (4, 3, "PNG")
    assert out["markdown"] == "![plot](plot.png)"
    assert "![plot](plot.png)" in out["content"]


def test_read_file_still_treats_unknown_binaries_as_metadata_only(session_root):
    blob = session_root / "data.bin"
    blob.write_bytes(b"\x00\x01\x02")
    out = read_file_tool.read_file(str(blob))
    assert out["is_text"] is False and "is_image" not in out


def _mp4(size: int = 4096) -> bytes:
    """Enough of an MP4 to be recognised: a box size, 'ftyp', then padding."""
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * (size - 12)


def test_video_in_the_session_folder_plays_through_the_file_endpoint(session_root):
    """![video](renders/share.mp4) used to fail with 415, so the chat showed nothing."""
    (session_root / "renders").mkdir()
    (session_root / "renders" / "share.mp4").write_bytes(_mp4())
    client = TestClient(server.app)
    resp = client.get("/api/session/s1/file", params={"path": "renders/share.mp4"})
    assert resp.status_code == 200 and resp.headers["content-type"] == "video/mp4"
    part = client.get("/api/session/s1/file", params={"path": "renders/share.mp4"}, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100   # seeking in the player


def test_agent_memory_is_never_served_as_media(session_root):
    (session_root / ".agent_memory").mkdir()
    (session_root / ".agent_memory" / "clip.mp4").write_bytes(_mp4())
    client = TestClient(server.app)
    assert client.get("/api/session/s1/file", params={"path": ".agent_memory/clip.mp4"}).status_code == 404
