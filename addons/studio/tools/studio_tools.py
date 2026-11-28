"""Studio tools: the agent's side of the video editor.

The agent makes and fixes motion videos as ``studio/<name>/motion.json`` (format in
``addons.studio.studio_doc``); the user watches and edits the same file in the Studio page and leaves
numbered notes on frames. These tools create, read, save (validated) and close notes.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from addons.studio import studio_doc
from addons.studio.studio_doc import ConflictError, StudioError
from brain.config.settings import get_settings
from brain.utils import shared_files
from brain.utils.logger import get_logger
from brain.utils.workspace_context import get_active_session
from tools.toolbox import _files
from tools.toolbox.catalog import group, param, spec

logger = get_logger(__name__)

group("studio", "Video Studio: motion videos as editable data, with keyframes, music and notes",
      "studio, video editor, editor video, keyframe, timeline, motion, animasi, motion graphic, catatan video, revisi video, "
      "edit video, motion.json, ekspor mp4, export video, musik video")


def _link(name: str) -> str:
    """The Studio for this project. The session is in it, so a team run's video opens from any chat."""
    session = get_active_session()
    return f"/studio?{urlencode({'session': session, 'project': name}) if session else 'project=' + quote(name)}"


def _button(name: str) -> dict[str, Any]:
    """What makes the answer end with a 'see your video' button (the loop appends shared Markdown)."""
    return {"shared": True, "markdown": f"[▶ Lihat & edit video \"{name}\" di Studio]({_link(name)})"}


def _issues(doc: dict[str, Any], root: Path, name: str) -> dict[str, Any]:
    found = studio_doc.check(doc, root, name)
    if not found:
        return {"check": "bersih: tidak ada yang keluar frame, bertumpuk, terlalu cepat, atau kurang kontras"}
    return {"issues": found[:20], "fix_first": [i["message"] for i in found if i["level"] == "error"][:8]}


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "size": f"{doc.get('width')}x{doc.get('height')} @ {doc.get('fps')} fps, {doc.get('duration')} s",
        "layers": [{"id": layer.get("id"), "type": layer.get("type"), "name": layer.get("name"),
                    "time": [layer.get("start"), layer.get("end")], "animated": sorted((layer.get("keys") or {}).keys())}
                   for layer in doc.get("layers") or []],
        "audio": [{"id": a.get("id"), "src": a.get("src"), "start": a.get("start")} for a in doc.get("audio") or []],
        "open_notes": len(studio_doc.open_notes(doc)),
    }


@spec("studio_create", "Start a new Studio video project (a motion.json with a background, a title and a shape) "
      "that the user can open, play, edit with keyframes and comment on. Then write the real video with studio_save.",
      "studio", [param("name", description="Short project name, e.g. 'promo kopi'."),
                 param("width", "int", required=False, default=1080), param("height", "int", required=False, default=1920),
                 param("fps", "int", required=False, default=30), param("duration", "num", "Seconds.", required=False, default=8),
                 param("title", description="Text for the starter title.", required=False, default="Judul video")],
      keywords="buat video studio, video baru, proyek video, motion video, video editor", lead=True, workspace=True,
      read_only=False)
def studio_create(name: str, width: int = 1080, height: int = 1920, fps: int = 30, duration: float = 8,
                  title: str = "Judul video", workspace_path: str | None = None) -> dict[str, Any]:
    root = _files.workspace(workspace_path)
    slug = studio_doc.slug(name)
    base, n = slug, 2
    while studio_doc.doc_path(root, slug).exists():
        slug, n = f"{base[:44]}-{n}", n + 1
    doc, stamp = studio_doc.save(root, slug, studio_doc.template(int(width), int(height), int(fps), float(duration), title))
    return {"ok": True, "name": slug, "file": f"studio/{slug}/motion.json", "stamp": stamp, "open": _link(slug),
            "next": "Write the real video with studio_save(name, doc). Put pictures and music in studio/<name>/assets/.",
            "summary": _summary(doc), **_button(slug)}


@spec("studio_get", "Read a Studio video project: the whole motion.json, a summary, and the stamp to pass to studio_save.",
      "studio", [param("name", description="Project name (folder under studio/).")],
      keywords="baca video studio, lihat proyek video, motion.json", workspace=True)
def studio_get(name: str, workspace_path: str | None = None) -> dict[str, Any]:
    doc, stamp = studio_doc.load(_files.workspace(workspace_path), name)
    return {"ok": True, "name": name, "stamp": stamp, "summary": _summary(doc), "doc": doc, "open": _link(name)}


@spec("studio_save", "Save a Studio video (the whole motion.json). It is checked first: every problem comes back with "
      "where it is, so fix and save again. Pass the stamp from studio_get or studio_create so a change the user made "
      "meanwhile in the editor is not overwritten (on a conflict, read again and redo your change).",
      "studio", [param("name", description="Project name."),
                 param("doc", "obj", "The whole document: width, height, fps, duration, background, fonts, layers, audio, notes."),
                 param("stamp", description="The stamp you read the project at.", required=False)],
      keywords="simpan video studio, ubah motion.json, edit video studio", workspace=True, read_only=False)
def studio_save(name: str, doc: Any, stamp: str | None = None, workspace_path: str | None = None) -> dict[str, Any]:
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError as exc:
            return {"ok": False, "error": f"doc is not valid JSON: {exc}"}
    root = _files.workspace(workspace_path)
    try:
        clean, new_stamp = studio_doc.save(root, name, doc, stamp or None)
    except ConflictError:
        return {"ok": False, "conflict": True,
                "error": "The user changed this video in the Studio since you read it. Call studio_get and apply your change again."}
    except StudioError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": name, "stamp": new_stamp, "summary": _summary(clean), "open": _link(name),
            **_issues(clean, root, name), **_button(name),
            "note": "Saved; the Studio reloads it by itself. Fix every item in fix_first and save again before you answer."}


@spec("studio_notes", "The open notes the user left on a Studio video: number, time, the box or point on the frame, "
      "the layer under it, and what to change.",
      "studio", [param("name", description="Project name."),
                 param("include_done", "bool", "Also list notes already marked done.", required=False, default=False)],
      keywords="catatan video, komentar video, revisi studio, feedback video", lead=True, workspace=True)
def studio_notes(name: str, include_done: bool = False, workspace_path: str | None = None) -> dict[str, Any]:
    doc, stamp = studio_doc.load(_files.workspace(workspace_path), name)
    notes = (doc.get("notes") or []) if include_done else studio_doc.open_notes(doc)
    layers = {layer.get("id"): layer for layer in doc.get("layers") or []}
    out = []
    for note in notes:
        item = dict(note)
        layer = layers.get(note.get("layer"))
        if layer:
            item["layer_summary"] = {k: layer.get(k) for k in ("type", "name", "text", "start", "end") if k in layer}
        out.append(item)
    return {"ok": True, "name": name, "stamp": stamp, "notes": out, "count": len(out),
            "how": "Change motion.json (studio_get, then studio_save with the stamp), then studio_note_done for each note."}


@spec("studio_note_done", "Mark a Studio note as done, with a one or two sentence reply that says what was changed. "
      "The user sees the reply on the note.",
      "studio", [param("name", description="Project name."), param("n", "int", "The note number."),
                 param("reply", description="What you changed, in the user's language.")],
      keywords="tandai catatan selesai, catatan video selesai", workspace=True, read_only=False)
def studio_note_done(name: str, n: int, reply: str, workspace_path: str | None = None) -> dict[str, Any]:
    root = _files.workspace(workspace_path)
    doc, stamp = studio_doc.load(root, name)
    if not studio_doc.resolve_note(doc, int(n), reply):
        return {"ok": False, "error": f"no note #{n}"}
    try:
        _, new_stamp = studio_doc.save(root, name, doc, stamp)
    except ConflictError:
        return {"ok": False, "conflict": True, "error": "The project changed while marking the note; call studio_note_done again."}
    return {"ok": True, "stamp": new_stamp, "open_notes_left": len(studio_doc.open_notes(doc))}


@spec("studio_check", "Check a Studio video for what a viewer would see wrong: layers outside the frame, text cut at "
      "the edge or too close to it, overlapping text, text with too little contrast against what is behind it, text "
      "gone before it can be read, keys after the end, missing files. Run it after every change.",
      "studio", [param("name", description="Project name.")],
      keywords="cek video studio, periksa video, validasi video", workspace=True)
def studio_check(name: str, workspace_path: str | None = None) -> dict[str, Any]:
    root = _files.workspace(workspace_path)
    doc, stamp = studio_doc.load(root, name)
    return {"ok": True, "name": name, "stamp": stamp, **_issues(doc, root, name)}


def _browser() -> str | None:
    return next((p for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if (p := shutil.which(n))), None)


_capture_lock = asyncio.Lock()


async def _capture(browser: str, url: str, width: int, height: int, timeout: float = 45.0) -> bytes:
    """Open the frame page in headless Chrome, wait until it says it is ready, and screenshot it.

    Driven over the DevTools protocol: Chrome's own --screenshot does not wait for video clips to decode.
    One capture at a time: they share one browser profile.
    """
    async with _capture_lock:
        return await _capture_with(browser, url, width, height, timeout)


async def _capture_with(browser: str, url: str, width: int, height: int, timeout: float) -> bytes:
    import base64

    import httpx
    import websockets

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # One profile for every capture: a fresh one each time would look like a new visitor to the web app.
    profile = str(get_settings().project_root / ".agent_memory" / "studio-browser")
    Path(profile).mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        browser, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars", "--mute-audio",
        f"--remote-debugging-port={port}", f"--user-data-dir={profile}", f"--window-size={width},{height}", "about:blank",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        target = ""
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(60):
                try:
                    pages = (await client.get(f"http://127.0.0.1:{port}/json")).json()
                    target = next((p["webSocketDebuggerUrl"] for p in pages if p.get("type") == "page"), "")
                except (httpx.HTTPError, ValueError):
                    target = ""
                if target:
                    break
                await asyncio.sleep(0.25)
        if not target:
            raise RuntimeError("the browser did not start")
        async with websockets.connect(target, max_size=64 * 1024 * 1024) as ws:
            counter = 0

            async def call(method: str, **params: Any) -> dict[str, Any]:
                nonlocal counter
                counter += 1
                await ws.send(json.dumps({"id": counter, "method": method, "params": params}))
                while True:
                    message = json.loads(await ws.recv())
                    if message.get("id") == counter:
                        if "error" in message:
                            raise RuntimeError(str(message["error"].get("message")))
                        return dict(message.get("result") or {})

            await call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
            await call("Page.navigate", url=url)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                state = await call("Runtime.evaluate", expression="document.title", returnByValue=True)
                if (state.get("result") or {}).get("value") == "ready":
                    break
                await asyncio.sleep(0.3)
            else:
                raise TimeoutError("the frames were not ready in time")
            # The whole sheet, however long the captions made it.
            size = await call("Runtime.evaluate", returnByValue=True, expression=(
                "JSON.stringify([document.documentElement.scrollWidth, document.documentElement.scrollHeight])"))
            full_w, full_h = json.loads((size.get("result") or {}).get("value") or "[0, 0]")
            shot = await call("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                              clip={"x": 0, "y": 0, "width": max(width, full_w), "height": max(100, full_h), "scale": 1})
            return base64.b64decode(shot["data"])
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


def _server() -> str | None:
    port = int(getattr(get_settings(), "web_port", 8080) or 8080)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return f"http://127.0.0.1:{port}"
    except OSError:
        return None


@spec("studio_still", "Render frames of a Studio video at given times into one contact-sheet image (PNG), exactly as "
      "the editor and the export draw them, and share it in the chat. Use it to look at your own work, or to show the "
      "user the key moments. Needs the web app running.",
      "studio", [param("name", description="Project name."),
                 param("times", "list", "Seconds to show, e.g. [0.5, 2, 4.5]. Empty: 6 moments spread over the video.",
                       required=False, items="num"),
                 param("share", "bool", "Also show it in the chat.", required=False, default=True)],
      keywords="frame video, cuplikan video, preview video, lihat hasil video, screenshot video, still", workspace=True,
      read_only=False)
async def studio_still(name: str, times: list[float] | None = None, share: bool = True,
                       workspace_path: str | None = None) -> dict[str, Any]:
    root = _files.workspace(workspace_path)
    doc, _ = studio_doc.load(root, name)
    duration = float(doc.get("duration") or 1)
    moments = [max(0.0, min(duration - 1e-3, float(t))) for t in (times or [])][:12] or \
        [round(duration * (i + 0.5) / 6, 2) for i in range(6)]
    out = studio_doc.folder(root, name) / "stills" / f"frames-{time.strftime('%Y%m%d-%H%M%S')}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    # The C engine draws them on this machine, the same way the export does: no browser needed.
    try:
        from addons.studio import renderer

        scale = min(1.0, 360 / max(doc["width"], doc["height"]))
        await asyncio.to_thread(renderer.still, root, name, moments, scale=scale, cols=min(3, len(moments)), out=out)
        return _still_result(root, name, out, moments, share)
    except Exception as exc:  # noqa: BLE001 - fall back to the browser renderer below
        logger.info(f"[studio] engine still unavailable, trying the browser: {exc}")
    browser, server, session = _browser(), _server(), get_active_session()
    if not browser or not server or not session:
        return {"ok": False, "error": "studio_still needs the web app running and Chrome or Chromium installed",
                "instead": "use studio_check, and ask the user to look in the Studio"}
    cols = min(3, len(moments))
    rows = -(-len(moments) // cols)
    scale = min(360 / doc["width"], 360 / doc["height"])
    width = int(cols * (doc["width"] * scale + 16) + 16)
    height = int(rows * (doc["height"] * scale + 40) + 16)
    query = urlencode({"session": session, "project": name, "times": ",".join(f"{t:g}" for t in moments), "scale": f"{scale:.4f}"})
    try:
        png = await _capture(browser, f"{server}/studio/frame?{query}", width, height)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return {"ok": False, "error": f"could not draw the frames: {exc}"}
    out.write_bytes(png)
    return _still_result(root, name, out, moments, share)


def _still_result(root: Path, name: str, out: Path, moments: list[float], share: bool) -> dict[str, Any]:
    if not out.is_file():
        return {"ok": False, "error": "no image was made"}
    relative = out.relative_to(root).as_posix()
    result: dict[str, Any] = {"ok": True, "file": relative, "times": moments}
    if share:
        shared_files.add(root, relative)
        session_id = get_active_session()
        result.update(shared=True, markdown=shared_files.markdown(session_id, relative, f"Cuplikan {name}"))
    return result


@spec("studio_captions", "Add captions to a Studio video from its speech: Whisper transcribes the voice or the video "
      "clip with word timings, and each short line becomes a text layer (white, outlined, pop-in) timed to the words. "
      "Running it again replaces the captions it made before.",
      "studio", [param("name", description="Project name."),
                 param("source", description="Audio clip id/src or video layer id to transcribe (default: the first clip with sound).", required=False, default=""),
                 param("language", description="Language code, e.g. 'id' or 'en' (default: detect).", required=False, default=""),
                 param("max_words", "int", "Words per caption line.", required=False, default=4),
                 param("position", description="bottom, middle or top.", required=False, default="bottom"),
                 param("font", description="Font family (default: the video's first font).", required=False, default="")],
      keywords="caption, captions, subtitle, subtitel, teks otomatis, transkrip video, caption otomatis, auto caption",
      lead=True, workspace=True, read_only=False)
async def studio_captions(name: str, source: str = "", language: str = "", max_words: int = 4, position: str = "bottom",
                          font: str = "", workspace_path: str | None = None) -> dict[str, Any]:
    from addons.studio import captions, renderer

    root = _files.workspace(workspace_path)
    doc, stamp = studio_doc.load(root, name)
    try:
        new_doc, count, used = await asyncio.to_thread(
            captions.captions, doc, studio_doc.folder(root, name), source, language, max_words, position, font)
    except renderer.RenderError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - Whisper missing or the file unreadable: say so
        return {"ok": False, "error": f"Transkripsi gagal: {exc}"}
    try:
        clean, new_stamp = studio_doc.save(root, name, new_doc, stamp)
    except ConflictError:
        return {"ok": False, "conflict": True, "error": "The video changed while transcribing. Run studio_captions again."}
    except StudioError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": name, "stamp": new_stamp, "captions": count, "source": used, "open": _link(name),
            "summary": _summary(clean), **_issues(clean, root, name), **_button(name)}


@spec("studio_list", "List the Studio video projects in this chat.", "studio", [],
      keywords="daftar video studio, proyek video", workspace=True)
def studio_list(workspace_path: str | None = None) -> dict[str, Any]:
    projects = studio_doc.projects(_files.workspace(workspace_path))
    return {"ok": True, "projects": [{**p, "open": _link(p["name"])} for p in projects]}
