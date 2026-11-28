"""A layout probe for HyperFrames videos: what ``hyperframes check`` does not catch, measured in a real browser.

For every scene (each timed clip directly under the root composition) the probe shows only the clips that are
on screen at 70 % of that scene, seeks every registered GSAP timeline to that moment, then measures:

* ``text_overflows_box``: text sticking out of the box around it (a label wider than its icon or card)
* ``text_off_canvas``: text outside the frame
* ``frame_mostly_empty``: little of the frame painted besides the background
* ``same_layout_every_scene``: every scene's main box at the same place and size (a slideshow of one card)

It serves the project folder on 127.0.0.1 (a random port, only for the probe) to headless Chrome through the
DevTools protocol, and cleans up after itself. A composition built from sub-composition files is skipped with a
note, since only the HyperFrames runtime mounts those.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from brain.utils.logger import get_logger

logger = get_logger(__name__)

BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")
EMPTY_BELOW = 0.12            # painted share of the frame under which a scene reads as empty...
BIG_TEXT = 0.08               # ...unless a headline this tall (share of the frame height, ~86 px at 1080) holds it
OVERFLOW_PX = 4               # tolerance before a text counts as outside its box
SETTLE_SECONDS = 1.5          # fonts and scripts after load

PROBE_JS = r"""
(async (opts) => {
  const root = document.querySelector('[data-composition-id]');
  if (!root) return { skipped: 'no composition root' };
  if (document.querySelector('[data-composition-src]')) return { skipped: 'sub-compositions need the HyperFrames runtime' };
  const W = root.offsetWidth || 1920, H = root.offsetHeight || 1080;
  const num = (el, name) => { const v = parseFloat(el.getAttribute(name)); return Number.isFinite(v) ? v : null; };
  const tls = window.__timelines || {};
  const animations = document.getAnimations ? document.getAnimations().length : 0;
  const silenced = document.querySelectorAll('[data-layout-ignore], [data-layout-allow-overflow], [data-layout-allow-overlap]').length;
  const clips = [...document.querySelectorAll('.clip')].filter(el => num(el, 'data-start') !== null);
  const tops = clips.filter(el => num(el, 'data-duration') !== null && el.parentElement === root)
    .sort((a, b) => num(a, 'data-start') - num(b, 'data-start'));
  let times = tops.map(el => num(el, 'data-start') + 0.7 * num(el, 'data-duration'));
  if (times.length < 2) {
    // Scenes timed by the timeline alone: samples spread over the whole video.
    let total = num(root, 'data-duration');
    for (const key of Object.keys(tls)) { try { total = Math.max(total || 0, tls[key].duration()); } catch (e) { /* skip */ } }
    total = total || 10;
    const n = Math.max(3, Math.min(12, Math.round(total / 2)));
    times = [...Array(n)].map((_, i) => (i + 0.5) * total / n);
  }
  const name = el => el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.classList && el.classList.length ? '.' + [...el.classList].slice(0, 2).join('.') : '');
  const visible = el => {
    for (let p = el; p && p !== document.documentElement; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.1) return false;
    }
    return el.getClientRects().length > 0;
  };
  const painted = cs => {
    const bg = cs.backgroundColor;
    const hasBg = (bg && bg !== 'transparent' && !/rgba\([^)]*,\s*0\)$/.test(bg)) || cs.backgroundImage !== 'none';
    return hasBg || parseFloat(cs.borderTopWidth) > 0 || parseFloat(cs.borderLeftWidth) > 0;
  };
  const full = r => r.width * r.height > 0.85 * W * H;
  const boxOf = el => {
    for (let p = el.parentElement; p && p !== root && p !== document.body; p = p.parentElement) {
      if (p instanceof SVGElement) continue;
      const cs = getComputedStyle(p);
      if (full(p.getBoundingClientRect())) return null;     // a full-frame layer is the canvas, not a box
      if (painted(cs) || cs.overflow !== 'visible') return p;
    }
    return null;
  };
  const svgBoxOf = (el, r) => {
    // SVG text: the smallest shape of its drawing that holds the text's centre (the screen of a ticket machine).
    const svg = el.ownerSVGElement;
    if (!svg) return null;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    let best = null, area = Infinity;
    for (const shape of svg.querySelectorAll('rect, circle, ellipse')) {
      if (!visible(shape)) continue;
      const fill = shape.getAttribute('fill') || getComputedStyle(shape).fill;
      if (fill === 'none' || fill === 'transparent') continue;
      const b = shape.getBoundingClientRect();
      if (cx < b.left || cx > b.right || cy < b.top || cy > b.bottom) continue;
      const a = b.width * b.height;
      if (a < area && a > r.width * r.height * 0.3) { area = a; best = shape; }
    }
    return best;
  };
  const measure = async t => {
    for (const el of clips) {
      const s = num(el, 'data-start'), d = num(el, 'data-duration');
      el.style.display = (d === null || (t >= s && t < s + d)) ? '' : 'none';
    }
    for (const key of Object.keys(tls)) { try { tls[key].seek(t, false); } catch (e) { /* a broken timeline is lint's job */ } }
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    const R = root.getBoundingClientRect();
    const sample = { time: Math.round(t * 100) / 100, overflow: [], offCanvas: [], painted: 0, mainBox: null, bigText: 0 };
    const content = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent.replace(/\s+/g, ' ').trim();
      const el = node.parentElement;
      if (!text || !el || ['SCRIPT', 'STYLE', 'TITLE'].includes(el.tagName) || !visible(el)) continue;
      let r;
      if (el instanceof SVGElement) r = el.getBoundingClientRect();
      else { range.selectNodeContents(node); r = range.getBoundingClientRect(); }
      if (r.width < 1 || r.height < 1) continue;
      content.push(r);
      const size = el instanceof SVGElement ? r.height / 1.2 : parseFloat(getComputedStyle(el).fontSize);
      sample.bigText = Math.max(sample.bigText, Math.round(size / R.height * 1000) / 1000);
      const box = el instanceof SVGElement ? svgBoxOf(el, r) : boxOf(el);
      if (box) {
        const b = box.getBoundingClientRect();
        const over = Math.max(b.left - r.left, r.right - b.right, b.top - r.top, r.bottom - b.bottom);
        if (over > opts.overflowPx) sample.overflow.push({ text: text.slice(0, 60), px: Math.round(over), box: name(box), element: name(el) });
      }
      const off = Math.max(R.left - r.left, r.right - R.right, R.top - r.top, r.bottom - R.bottom);
      if (off > opts.overflowPx) sample.offCanvas.push({ text: text.slice(0, 60), px: Math.round(off), element: name(el) });
    }
    let best = null, area = 0;
    for (const el of root.querySelectorAll('*')) {
      if (el instanceof SVGElement && el.tagName.toLowerCase() !== 'svg') continue;
      const media = ['IMG', 'VIDEO', 'CANVAS'].includes(el.tagName) || el.tagName.toLowerCase() === 'svg';
      if (!(media || painted(getComputedStyle(el))) || !visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (full(r)) continue;
      content.push(r);
      const a = r.width * r.height;
      if (!media && a > area) { area = a; best = r; }
    }
    let hits = 0, total = 0;
    for (let gx = 0.5; gx < 48; gx += 1) for (let gy = 0.5; gy < 27; gy += 1) {
      total += 1;
      const x = R.left + gx / 48 * R.width, y = R.top + gy / 27 * R.height;
      if (content.some(r => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom)) hits += 1;
    }
    sample.painted = Math.round(hits / total * 100) / 100;
    if (best) sample.mainBox = [best.left - R.left, best.top - R.top, best.width, best.height].map(v => Math.round(v / R.width * 50));
    return sample;
  };
  const out = [];
  for (const t of times) {
    let sample = await measure(t);
    // A blank moment between two scenes is a cut, not an empty frame: look a little before and after.
    for (const shift of [-0.4, 0.4]) {
      if (sample.painted >= opts.emptyBelow || t + shift < 0) break;
      const other = await measure(t + shift);
      if (other.painted > sample.painted) sample = { ...other, time: sample.time };
    }
    out.push(sample);
  }
  return { width: W, height: H, samples: out, timelines: Object.keys(tls).length, animations, silenced };
})
"""


def browser() -> str | None:
    return next((p for n in BROWSERS if (p := shutil.which(n))), None)


def _size(folder: Path) -> tuple[int, int]:
    text = (folder / "index.html").read_text(encoding="utf-8", errors="replace")
    import re

    width = re.search(r"data-width\s*=\s*[\"']?(\d{3,4})", text)
    height = re.search(r"data-height\s*=\s*[\"']?(\d{3,4})", text)
    return (int(width.group(1)) if width else 1920, int(height.group(1)) if height else 1080)


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class names it so
        return


def findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The probe's report as check findings (section "probe"), warnings only."""
    out: list[dict[str, Any]] = []
    samples = report.get("samples") or []
    for sample in samples:
        for item in sample.get("overflow") or []:
            out.append({"section": "probe", "severity": "warning", "code": "text_overflows_box", "time": sample["time"],
                        "text": item["text"], "selector": item["element"],
                        "message": f"\"{item['text']}\" sticks out of its box {item['box']} by {item['px']} px at "
                                   f"{sample['time']} s. Make the text smaller or shorter, let it wrap, or make the box bigger."})
        for item in sample.get("offCanvas") or []:
            out.append({"section": "probe", "severity": "warning", "code": "text_off_canvas", "time": sample["time"],
                        "text": item["text"], "selector": item["element"],
                        "message": f"\"{item['text']}\" is {item['px']} px outside the frame at {sample['time']} s."})
        if sample.get("painted", 1) < EMPTY_BELOW and sample.get("bigText", 0) < BIG_TEXT:
            out.append({"section": "probe", "severity": "warning", "code": "frame_mostly_empty", "time": sample["time"],
                        "text": "", "selector": "",
                        "message": f"Only {round(sample['painted'] * 100)} % of the frame shows anything at {sample['time']} s. "
                                   "Use the frame: a bigger headline, an illustration, shapes or a chart that carry the idea."})
    if len(samples) >= 2 and not report.get("timelines") and not report.get("animations"):
        out.append({"section": "probe", "severity": "warning", "code": "no_motion", "text": "", "selector": "",
                    "message": "Nothing moves: no GSAP timeline is registered in window.__timelines and no CSS "
                               "animation runs, so every scene is a still with a hard cut. Add the entrances and "
                               "movement the script describes."})
    if report.get("silenced"):
        out.append({"section": "probe", "severity": "warning", "code": "check_silenced", "text": "", "selector": "",
                    "message": f"{report['silenced']} element(s) carry data-layout-ignore or data-layout-allow-*, which "
                               "hide layout problems from hyperframes check. Remove them and fix the layout instead; "
                               "keep one only where an overlap or bleed is on purpose, and say why."})
    boxes = [tuple(s["mainBox"]) for s in samples if s.get("mainBox")]
    # Small motion (a drift, a 2 % zoom) moves a box a unit or two: the same card still counts as the same.
    common = max((sum(all(abs(x - y) <= 2 for x, y in zip(a, b, strict=True)) for b in boxes) for a in boxes), default=0)
    if len(boxes) >= 3 and common >= 0.8 * len(boxes) and len(boxes) >= 0.6 * len(samples):
        out.append({"section": "probe", "severity": "warning", "code": "same_layout_every_scene", "text": "", "selector": "",
                    "message": f"{common} of {len(boxes)} moments put their main box at the same place and size, so the video "
                               "reads as one card changing its words. Give the scenes different layouts (split, big "
                               "number, full-bleed image or illustration, list, close-up) from the chosen direction."})
    return out


def _run(folder: Path, chrome: str, timeout: float) -> dict[str, Any]:
    width, height = _size(folder)
    handler = functools.partial(_Quiet, directory=str(folder))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    profile = tempfile.mkdtemp(prefix="sq-probe-")
    process = subprocess.Popen([chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                "--hide-scrollbars", "--mute-audio", "--remote-debugging-port=0", f"--user-data-dir={profile}",
                                f"--window-size={width},{height}", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        port_file = Path(profile) / "DevToolsActivePort"
        while not port_file.is_file() or not port_file.read_text().strip():
            if time.time() > deadline or process.poll() is not None:
                return {"skipped": "Chrome did not start"}
            time.sleep(0.1)
        port = port_file.read_text().splitlines()[0].strip()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10) as answer:
            target = next(t["webSocketDebuggerUrl"] for t in json.load(answer) if t.get("type") == "page")
        page = f"http://127.0.0.1:{server.server_address[1]}/index.html"
        return asyncio.run(_drive(target, page, width, height, timeout))
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)
        if process.poll() is None:
            process.kill()
        server.shutdown()
        server.server_close()
        shutil.rmtree(profile, ignore_errors=True)


async def _drive(target: str, page: str, width: int, height: int, timeout: float) -> dict[str, Any]:
    import websockets

    async with websockets.connect(target, max_size=20_000_000) as socket:
        counter = 0

        async def call(method: str, **params: Any) -> dict[str, Any]:
            nonlocal counter
            counter += 1
            mine = counter
            await socket.send(json.dumps({"id": mine, "method": method, "params": params}))
            while True:
                message = json.loads(await asyncio.wait_for(socket.recv(), timeout))
                if message.get("id") == mine:
                    if "error" in message:
                        raise RuntimeError(str(message["error"].get("message")))
                    result: dict[str, Any] = message.get("result") or {}
                    return result

        await call("Page.enable")
        await call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
        await call("Page.navigate", url=page)
        end = time.time() + timeout
        while time.time() < end:
            state = await call("Runtime.evaluate", expression="document.readyState", returnByValue=True)
            if (state.get("result") or {}).get("value") == "complete":
                break
            await asyncio.sleep(0.2)
        await asyncio.sleep(SETTLE_SECONDS)
        result = await call("Runtime.evaluate", expression=f"{PROBE_JS}({json.dumps({'overflowPx': OVERFLOW_PX, 'emptyBelow': EMPTY_BELOW})})",
                            awaitPromise=True, returnByValue=True)
        if result.get("exceptionDetails"):
            return {"skipped": f"probe script failed: {str(result['exceptionDetails'].get('text'))[:200]}"}
        value = (result.get("result") or {}).get("value")
        return value if isinstance(value, dict) else {"skipped": "probe returned nothing"}


async def probe(folder: Path, timeout: float = 60.0) -> dict[str, Any]:
    """Measure a HyperFrames project's layout. Returns {"findings": [...], "samples": n} or {"skipped": reason}."""
    if not (folder / "index.html").is_file():
        return {"skipped": "no index.html"}
    chrome = browser()
    if chrome is None:
        return {"skipped": "no Chrome or Chromium on this machine"}
    try:
        report = await asyncio.to_thread(_run, folder, chrome, timeout)
    except (OSError, RuntimeError, ValueError, StopIteration, TimeoutError) as exc:
        logger.warning(f"[video_probe] {folder.name}: {exc}")
        return {"skipped": f"probe failed: {type(exc).__name__}"}
    if report.get("skipped"):
        return {"skipped": str(report["skipped"])}
    return {"findings": findings(report), "samples": len(report.get("samples") or []), "timelines": report.get("timelines", 0),
            "animations": report.get("animations", 0)}
