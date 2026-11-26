"""The candle model inside the trading system: its forecast on the live bars, the price scenarios it implies, the block
the trading LLM reads, and the training process the panel starts and stops.

The forecast (``candle_lm.predict``) is the next 12 M5 candles (one hour) as a middle guess with a 10-90% band. From
it come scenarios, plans tied to a price, the way a trader writes them:

* ``reversal_buy``: if the price falls to the bottom of the model's 80% band for the hour (level A, where only one move
  in ten ends), buy; stop half an ATR below, target the model's middle guess for the end of the hour.
* ``reversal_sell``: the same at the top of the band (level B).
* ``trend``: when the model leans one way (its chance of a higher close, averaged over candles 3, 6 and 12, is at least
  ``LEAN`` away from even), enter now; stop at the far edge of the band over the next half hour, target the
  middle guess of the hour's high (buy) or low (sell).

The paper simulation trades these scenarios as its "Model candle" rule (limit orders at A and B, one cancelling the other,
or the trend entry at market), so their record builds up next to the other rules before anything real uses them. The
trading LLM reads the forecast, the scenarios and the model's exam (``compact``), and decides with everything else.

A forecast is worked out once per new M5 bar and model file, and costs a few milliseconds.
"""

from __future__ import annotations

import contextlib
import math
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from addons.trading.quant import m5store
from brain.config.paths import log_file
from brain.utils.platform_compat import low_priority_kwargs

ROOT = Path(__file__).resolve().parents[3]
LOG = log_file("candle_lm.log")
LEAN = 0.06               # a trend scenario needs the averaged chance of a higher close this far from 0.5
REVERSAL_STOP_ATR = 0.5   # beyond the band's edge
SIZES_ORDER = ("base", "fast", "large", "xl")
_lock = threading.Lock()
_model: dict[str, Any] = {}
_views: dict[tuple[str, float, float], dict[str, Any]] = {}
_grades: dict[tuple[str, float], tuple[int | None, float]] = {}


# ── the model ───────────────────────────────────────────────────────────────
def pick_size(instrument: str) -> str | None:
    """CANDLE_LM_SIZE when set, else the size with the best exam among models of the current design (old designs never run)."""
    from addons.trading.quant import candle_lm

    wanted = os.getenv("CANDLE_LM_SIZE", "").strip()
    if wanted:
        return wanted if (candle_lm.run_dir(instrument, wanted) / "best.pt").exists() else None
    found = []
    for size in SIZES_ORDER:
        path = candle_lm.run_dir(instrument, size) / "best.pt"
        if path.exists():
            arch, score = _grade(path)
            if arch == candle_lm.ARCH:
                found.append((score, size))
    return min(found)[1] if found else None


def _grade(path: Path) -> tuple[int | None, float]:
    """(design, exam score) of a best.pt, read once per file version."""
    key = (str(path), path.stat().st_mtime)
    if key not in _grades:
        import torch

        try:
            state = torch.load(path, map_location="cpu", mmap=True, weights_only=False)
            _grades[key] = (state["config"].get("arch"), float((state.get("exam") or {}).get("score", float("inf"))))
        except Exception:  # noqa: BLE001 - a broken file is simply not picked
            _grades[key] = (None, float("inf"))
    return _grades[key]


def _load(instrument: str) -> tuple[Any, dict[str, Any], float] | None:
    """The best model of the instrument, loaded again only when its file changed."""
    from addons.trading.quant import candle_lm

    size = pick_size(instrument)
    if size is None:
        return None
    path = candle_lm.run_dir(instrument, size) / "best.pt"
    mtime = path.stat().st_mtime
    key = (instrument, size, mtime)
    with _lock:
        if _model.get("key") != key:
            import torch

            torch.set_num_threads(2)                         # the trainer may be using the other cores
            model, state = candle_lm.load_model(instrument, size)
            _model.update(key=key, model=model, state=state)
        return _model["model"], _model["state"], mtime


def _as_array(bars: list[dict[str, float]]) -> np.ndarray:
    return np.array([[b["t"], b["o"], b["h"], b["l"], b["c"], b.get("v", 0.0)] for b in bars], dtype=np.float64)


def _m5_bars(symbol: str, bars: list[dict[str, float]] | None) -> np.ndarray:
    """The newest M5 bars: the ones given, else the EA's saved bars, topped up from the M5 store when they are too few."""
    from addons.trading.quant import memory

    rows = bars if bars is not None else memory.history_m5(symbol)
    arr = _as_array(rows) if rows else np.zeros((0, 6))
    if len(arr) < 1200:
        stored = np.array(m5store.load(symbol), dtype=np.float64)
        if len(stored):
            merged = {float(r[0]): r for r in stored}
            merged.update({float(r[0]): r for r in arr})
            arr = np.array([merged[t] for t in sorted(merged)])
    return arr


def view(symbol: str, bars: list[dict[str, float]] | None = None, *, price: float | None = None) -> dict[str, Any]:
    """The forecast and its scenarios for the newest bar, or {"ok": False, "reason": ...}."""
    instrument = m5store.instrument(symbol)
    try:
        loaded = _load(instrument)
        if loaded is None:
            return {"ok": False, "reason": "belum ada model candle: latih dulu (panel Model candle, Mulai latih)"}
        model, state, mtime = loaded
        arr = _m5_bars(symbol, bars)
        if not len(arr):
            return {"ok": False, "reason": "belum ada bar M5"}
        key = (symbol, float(arr[-1, 0]), mtime)
        with _lock:
            cached = _views.get(key)
        if cached is None:
            from addons.trading.quant import candle_lm

            with _lock:                                      # one forward pass at a time on the shared model
                cached = {"ok": True, "symbol": symbol, **candle_lm.predict(model, state, arr)}
                _views.clear()
                _views[key] = cached
        out = dict(cached)
        out["scenarios"] = scenarios(out, price)
        return out
    except Exception as exc:  # noqa: BLE001 - the model is extra evidence; everything else works without it
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:300]}


# ── scenarios ───────────────────────────────────────────────────────────────
def scenarios(fc: dict[str, Any], price: float | None = None) -> list[dict[str, Any]]:
    """Price-level plans from the forecast (see the module text). Prices are rounded to the forecast's precision."""
    q = fc.get("quantiles_atr") or []
    if not q:
        return []
    close, atr_price = float(fc["close"]), float(fc["atr_price"])
    rel = atr_price / close if close else 0.0
    to_price = lambda r: round(close * math.exp(r * rel), 3)   # noqa: E731
    now_price = float(price) if price else close
    k_all = range(len(q))
    band_low = min(q[k][2][0] for k in k_all)                  # the 10% low over the hour
    band_high = max(q[k][1][2] for k in k_all)                 # the 90% high over the hour
    mid_end = q[-1][0][1]
    p_ups = [c["p_up"] for c in fc["candles"]]
    picks = [p_ups[i] for i in (2, 5, len(p_ups) - 1) if i < len(p_ups)]
    lean = (sum(picks) / len(picks) - 0.5) if picks else 0.0
    edge = bool((fc.get("exam") or {}).get("edge"))
    strength = "teruji" if edge else "lemah (model belum lebih dekat dari tebakan tanpa otak)"
    valid_until = int(fc["last_bar_time"]) + 300 * (len(q) + 1)
    hold_s = 300 * len(q)
    out: list[dict[str, Any]] = []

    def add(kind: str, side: str, entry: float, stop: float, target: float, why: str, order: str) -> None:
        risk, reward = abs(entry - stop), abs(target - entry)
        if risk <= 0 or reward <= 0 or (target - entry) * (1 if side == "buy" else -1) <= 0:
            return
        out.append({"kind": kind, "side": side, "order": order, "entry": entry, "stop": stop, "target": target,
                    "rr": round(reward / risk, 2), "risk_atr": round(risk / atr_price, 2) if atr_price else None,
                    "valid_until": valid_until, "hold_s": hold_s, "why": why, "strength": strength})

    a, b = to_price(band_low), to_price(band_high)
    add("reversal_buy", "buy", a, to_price(band_low - REVERSAL_STOP_ATR), to_price(mid_end),
        f"kalau harga turun ke A {a} (bawah pita 80% sejam ke depan) → BUY, target kembali ke tengah", "limit")
    add("reversal_sell", "sell", b, to_price(band_high + REVERSAL_STOP_ATR), to_price(mid_end),
        f"kalau harga naik ke B {b} (atas pita 80% sejam ke depan) → SELL, target kembali ke tengah", "limit")
    if abs(lean) >= LEAN:
        half = range(min(6, len(q)))
        if lean > 0:
            add("trend", "buy", now_price, to_price(min(q[k][2][0] for k in half)), to_price(max(q[k][1][1] for k in k_all)),
                f"model condong naik (P naik rata-rata {0.5 + lean:.0%}) → BUY sekarang", "market")
        else:
            add("trend", "sell", now_price, to_price(max(q[k][1][2] for k in half)), to_price(min(q[k][2][1] for k in k_all)),
                f"model condong turun (P naik rata-rata {0.5 + lean:.0%}) → SELL sekarang", "market")
    return out


def compact(v: dict[str, Any]) -> dict[str, Any] | None:
    """What the trading LLM reads: the exam first, then the path, the band and the scenarios."""
    if not v.get("ok"):
        return None
    per = {c["candle"]: c for c in v.get("exam_per_candle") or []}
    exam = v.get("exam") or {}

    def scored(k: int) -> dict[str, Any] | None:
        c = per.get(k)
        return {k2: c[k2] for k2 in ("closeness", "closeness_no_brain", "direction_hit", "direction_majority", "band_80_hit")} if c else None

    return {
        "exam": {"error_vs_no_brain": exam.get("score"), "edge": exam.get("edge"), "candle_1": scored(1), "candle_12": scored(12)},
        "close": v["close"], "atr": v["atr_price"],
        "path": [{"candle": c["candle"], "close_mid": c["close"], "close_10_90": c["close_p10_p90"], "p_up": c["p_up"]}
                 for c in v["candles"] if c["candle"] in (1, 3, 6, 12)],
        "band_hour": {"low_10": min(c["low_p10"] for c in v["candles"]), "high_90": max(c["high_p90"] for c in v["candles"])},
        "scenarios": [{k: s[k] for k in ("kind", "side", "order", "entry", "stop", "target", "rr")} for s in v.get("scenarios", [])],
    }


def for_llm(symbol: str, price: float | None = None) -> dict[str, Any] | None:
    """The compact block for the analyst, or None when there is no model (the analyst works without it)."""
    return compact(view(symbol, price=price))


# ── the training process ────────────────────────────────────────────────────
def trainer_pids() -> list[int]:
    """Every running ``candle_lm train`` process, however it was started (panel, terminal or systemd)."""
    found = []
    for entry in Path("/proc").iterdir() if Path("/proc").is_dir() else []:
        if not entry.name.isdigit():
            continue
        with contextlib.suppress(OSError):
            args = (entry / "cmdline").read_bytes().split(b"\0")
            if b"addons.trading.quant.candle_lm" in args and b"train" in args:
                found.append(int(entry.name))
    return sorted(found)


def training_status(symbol: str = "XAUUSD") -> dict[str, Any]:
    from addons.trading.quant import candle_lm

    instrument = m5store.instrument(symbol)
    size = pick_size(instrument) or "base"
    st = candle_lm.status(instrument, size)
    pids = trainer_pids()
    store = {}
    with contextlib.suppress(OSError, ValueError):
        import json

        store = json.loads((m5store.STORE / f"{instrument}.json").read_text(encoding="utf-8"))
    return {"instrument": instrument, "size": size, "running": bool(pids), "pids": pids,
            "epoch": st.get("epoch"), "waiting": bool(st.get("waiting")) and bool(pids), "best_score": st.get("best_score"),
            "best_exam": st.get("best_exam"), "last": {k: st.get(k) for k in ("epoch", "score", "train_ratio", "seconds", "at")},
            "params": st.get("params"), "data": {k: store.get(k) for k in ("bars", "first", "last", "days")},
            "has_model": (candle_lm.run_dir(instrument, size) / "best.pt").exists()}


def start_training(size: str = "base", fresh: bool = False, symbol: str = "XAUUSD") -> dict[str, Any]:
    """Start ``candle_lm train`` in the background (niced, logging to candle_lm.log). Refused when one is running.

    The run settings saved from the lab page (threads, a time or epoch limit, compile) become its arguments; the
    model settings it reads itself from settings.json in the run folder."""
    from addons.trading.quant import candle_lm

    if size not in candle_lm.SIZES:
        raise ValueError(f"ukuran: {', '.join(candle_lm.SIZES)}")
    if trainer_pids():
        raise RuntimeError("training sudah jalan")
    instrument = m5store.instrument(symbol)
    saved, _ = candle_lm.read_settings(candle_lm.run_dir(instrument, size))
    args = [sys.executable, "-m", "addons.trading.quant.candle_lm", "train", "--symbol", instrument, "--size", size]
    for key, flag in (("threads", "--threads"), ("hours", "--hours"), ("epochs", "--epochs")):
        if saved.get(key):
            args += [flag, str(saved[key])]
    if saved.get("compile") is True:
        args.append("--compile")
    elif saved.get("compile") is False:
        args.append("--no-compile")
    if fresh:
        args.append("--fresh")
    with open(LOG, "ab") as log:
        proc = subprocess.Popen(args, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"},
                                **low_priority_kwargs(10))
    return {"started": True, "pid": proc.pid, "size": size, "fresh": fresh}


def stop_training(wait_s: float = 30.0) -> dict[str, Any]:
    """Ask every trainer to save and stop (SIGTERM), and wait a little for them to finish."""
    pids = trainer_pids()
    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
    deadline = time.time() + wait_s
    while time.time() < deadline and any(p in trainer_pids() for p in pids):
        time.sleep(0.5)
    left = [p for p in pids if p in trainer_pids()]
    return {"stopped": [p for p in pids if p not in left], "still_running": left}


# ── the lab page: everything about one run, its history, its log and its settings ──────────────
_cpu_seen: dict[int, tuple[float, float]] = {}


def trainer_runs() -> list[dict[str, Any]]:
    """Each running trainer with the instrument and size it trains (read from its command line)."""
    runs = []
    for pid in trainer_pids():
        with contextlib.suppress(OSError):
            args = [a.decode(errors="replace") for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
            opt = {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i].startswith("--")}
            runs.append({"pid": pid, "size": opt.get("--size", "base"), "symbol": opt.get("--symbol", "XAUUSD"),
                         "fresh": "--fresh" in args})
    return runs


def process_stats(pid: int) -> dict[str, Any] | None:
    """CPU (share of the whole laptop since the last look), memory, threads, nice and uptime of one process."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return None
    hz = os.sysconf("SC_CLK_TCK")
    ticks = (int(fields[11]) + int(fields[12])) / hz                  # utime + stime, seconds
    with open("/proc/uptime", encoding="ascii") as f:
        boot_s = float(f.read().split()[0])
    age = max(1e-3, boot_s - int(fields[19]) / hz)
    now = time.monotonic()
    before = _cpu_seen.get(pid)
    _cpu_seen[pid] = (now, ticks)
    used = (ticks - before[1]) / max(1e-3, now - before[0]) if before and now - before[0] > 0.5 else ticks / age
    cores = os.cpu_count() or 1

    def field(name: str) -> str:
        for line in status.splitlines():
            if line.startswith(name + ":"):
                return line.split(":", 1)[1].strip()
        return ""

    return {"pid": pid, "cpu_pct": round(100 * used / cores, 1), "cores_busy": round(used, 2), "cores": cores,
            "rss_mb": round(int(field("VmRSS").split()[0] or 0) / 1024, 1) if field("VmRSS") else None,
            "threads": int(field("Threads") or 0), "nice": int(fields[16]), "uptime_s": round(age)}


def _machine() -> dict[str, Any]:
    info: dict[str, Any] = {"cores": os.cpu_count() or 1}
    with contextlib.suppress(OSError):
        info["load"] = [round(x, 2) for x in os.getloadavg()]
    with contextlib.suppress(OSError, ValueError):
        mem = {line.split(":")[0]: int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines() if line}
        info["mem_total_gb"] = round(mem["MemTotal"] / 1048576, 1)
        info["mem_free_gb"] = round(mem["MemAvailable"] / 1048576, 1)
    return info


def lab_status(symbol: str = "XAUUSD", size: str | None = None) -> dict[str, Any]:
    """The run the lab shows (the running one, else the asked size, else the best model) with its settings."""
    from dataclasses import asdict

    from addons.trading.quant import candle_lm

    instrument = m5store.instrument(symbol)
    runs = trainer_runs()
    running = next((r for r in runs if m5store.instrument(r["symbol"]) == instrument), None)
    if size not in candle_lm.SIZES:
        size = running["size"] if running else (pick_size(instrument) or "base")
    folder = candle_lm.run_dir(instrument, size)
    st = candle_lm.status(instrument, size)
    this_running = bool(running and running["size"] == size)
    saved, saved_mtime = candle_lm.read_settings(folder)
    default = asdict(candle_lm.make_config(size))
    effective = {**default, **(st.get("config") or {})}
    store: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        import json

        store = json.loads((m5store.STORE / f"{instrument}.json").read_text(encoding="utf-8"))
    sizes = []
    for name in candle_lm.SIZES:
        path = candle_lm.run_dir(instrument, name) / "best.pt"
        sub = candle_lm.status(instrument, name)
        arch, score = _grade(path) if path.exists() else (None, None)
        sizes.append({"size": name, "has_model": path.exists(), "current_design": arch == candle_lm.ARCH if path.exists() else None,
                      "best_score": sub.get("best_score"), "epoch": sub.get("epoch"), "exam_score": score,
                      "shape": {k: candle_lm.SIZES[name][k] for k in ("ctx", "d_model", "layers", "heads")},
                      "running": bool(running is not None and running["size"] == name)})
    return {
        "instrument": instrument, "size": size, "arch": candle_lm.ARCH,
        "running": this_running, "other_running": None if this_running or not running else running,
        "waiting": bool(st.get("waiting")) and this_running, "pid": running["pid"] if this_running and running else None,
        "process": process_stats(running["pid"]) if this_running and running else None, "machine": _machine(),
        "epoch": st.get("epoch"), "step": st.get("step"), "lr": st.get("lr"), "lr_scale": st.get("lr_scale"),
        "seconds": st.get("seconds"), "steps_per_epoch": st.get("steps_per_epoch"), "at": st.get("at"),
        "started_at": st.get("started_at") if this_running else None, "limit": st.get("limit") if this_running else None,
        "score": st.get("score"), "train_ratio": st.get("train_ratio"), "best_score": st.get("best_score"),
        "best_at": st.get("best_at"), "best_exam": st.get("best_exam"), "last_exam": st.get("per_candle"),
        "params": st.get("params"), "threads": st.get("threads"), "applied": st.get("applied") or [],
        "settings_live": bool(this_running and saved_mtime and saved_mtime == st.get("settings_mtime")),
        "has_model": (folder / "best.pt").exists(),
        "config": effective, "default": default, "saved": saved,
        "keys": {"live": list(candle_lm.LIVE_KEYS), "restart": list(candle_lm.RESTART_KEYS),
                 "fresh": list(candle_lm.FRESH_KEYS), "run": list(candle_lm.RUN_KEYS)},
        "limits": {k: [lo, hi] for k, (lo, hi, _) in candle_lm.LIMITS.items()},
        "default_threads": candle_lm.DEFAULT_THREADS,
        "data": {k: store.get(k) for k in ("bars", "first", "last", "days")},
        "sizes": sizes,
    }


def lab_history(symbol: str = "XAUUSD", size: str = "base", points: int = 600) -> dict[str, Any]:
    """The epochs of the run (log.jsonl) thinned to about ``points`` rows, best epochs always kept."""
    import json

    from addons.trading.quant import candle_lm

    path = candle_lm.run_dir(m5store.instrument(symbol), size) / "log.jsonl"
    keep = ("epoch", "step", "lr", "train_ratio", "train_loss", "score", "closeness_avg", "gain_avg_points", "seconds", "best", "at")
    rows: list[dict[str, Any]] = []
    with contextlib.suppress(OSError), open(path, encoding="utf-8") as f:
        for line in f:
            with contextlib.suppress(ValueError):
                row = json.loads(line)
                rows.append({k: row.get(k) for k in keep})
    total = len(rows)
    if total > points:
        every = math.ceil(total / points)
        rows = [r for i, r in enumerate(rows) if r.get("best") or i % every == 0 or i == total - 1]
    return {"size": size, "total": total, "rows": rows}


def log_tail(lines: int = 200, max_bytes: int = 96_000) -> dict[str, Any]:
    """The last lines of candle_lm.log (where the panel's trainer writes)."""
    try:
        size = LOG.stat().st_size
        with open(LOG, "rb") as f:
            f.seek(max(0, size - max_bytes))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return {"lines": [], "size": 0}
    out = text.splitlines()
    if size > max_bytes and out:
        out = out[1:]                                               # the first line was cut in half
    return {"lines": out[-lines:], "size": size}


def save_settings(symbol: str, size: str, values: dict[str, Any]) -> dict[str, Any]:
    """Check and save the lab's settings for one run; say which ones the running trainer takes at once."""
    from addons.trading.quant import candle_lm

    if size not in candle_lm.SIZES:
        raise ValueError(f"ukuran: {', '.join(candle_lm.SIZES)}")
    clean = candle_lm.clean_settings(values, size)
    folder = candle_lm.run_dir(m5store.instrument(symbol), size)
    candle_lm.write_settings(folder, clean)
    running = any(r["size"] == size and m5store.instrument(r["symbol"]) == m5store.instrument(symbol) for r in trainer_runs())
    return {"saved": clean, "running": running,
            "now": [k for k in clean if k in candle_lm.LIVE_KEYS] if running else [],
            "next_start": [k for k in clean if k in candle_lm.RESTART_KEYS or k in candle_lm.RUN_KEYS or
                           (not running and k in candle_lm.LIVE_KEYS)],
            "fresh_only": [k for k in clean if k in candle_lm.FRESH_KEYS]}


def control(symbol: str, size: str, cmd: str) -> dict[str, Any]:
    from addons.trading.quant import candle_lm

    if size not in candle_lm.SIZES:
        raise ValueError(f"ukuran: {', '.join(candle_lm.SIZES)}")
    if not any(r["size"] == size for r in trainer_runs()):
        raise RuntimeError("training ukuran ini tidak sedang jalan")
    candle_lm.send_control(candle_lm.run_dir(m5store.instrument(symbol), size), cmd)
    return {"sent": cmd}
