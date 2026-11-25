"""The trading brain: memory + neural networks + "what happened in situations like this one", per symbol and timeframe.

``Brain.train`` builds the memory (``memory.build``) from every M5 bar ever recorded for the
symbol, trains the ensemble (``neural``), sits its exam on the newest days, and saves:

* ``<symbol>-<tf>.npz``: the networks, and the memory rows (standardised) with their outcomes,
  for the look-alike search;
* ``<symbol>-<tf>.json``: the exam, the condition tables, and the history of every training,
  so the panel shows whether the brain gets better as the memory grows.

``Brain.analyse`` answers for the bar that just closed:

* every plan's chances and expected result, ranked, and how much the five networks disagree;
* the 40 most similar past moments (nearest rows in the standardised memory, each measurement
  weighted by how much the networks rely on it, ``importance``) and what the best plans did after
  them, overall, in the same session, and weighted by closeness (a Gaussian kernel on the
  distance, so the nearest moments say most);
* the condition tables: how the reference plan (SL 1.5 / TP 2 ATR) went by session, by hour,
  by weekday, and near high-impact news against away from it.

Four groups of measurements are optional: price levels (``lv_``), the market's clocks (``tm_``),
seasonality with the cycle (``cy_``), and the regime with the quant model's math (``rg_``: ADX, Kalman,
the HMM state, the M5 rule). More measurements on a short memory mostly add ways to be
wrong, so ``choose_groups`` trains the ensemble with and without them and keeps the set that
predicts the validation rows best; a richer set has to beat the plain one by ``GROUP_MARGIN`` to be
kept. The exam is taken afterwards on rows that took no part in that choice. The slow calendars
(place in the month, the moon) are left out until the memory spans ``SLOW_SPAN_DAYS``.

A live run (``papersim`` in the intraday style) records each bar's spread and news mood
(``memory.record_context``) and calls ``maybe_train``. The brain then trains again in the
background when its last training is older than ``RETRAIN_S`` and new rows have outcomes, so a
run left going for days keeps teaching it.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from addons.trading import news as market_news
from addons.trading.quant import cycles, memory, neural, regime, sessions
from addons.trading.quant.memory import PLANS, SL, TIMEOUT, TP
from brain.utils.logger import get_logger

logger = get_logger(__name__)

RETRAIN_S = 3 * 3600
NEIGHBOURS = 40
HISTORY_KEEP = 60
MIN_NEW_ROWS = 6
COVERAGE_MIN = 0.3        # news and live measurements join the networks once they cover this share of the memory
NEWS_COLUMNS = ("news_known", "news_next_h", "news_prev_h", "news_high_4h", "news_high_past2h", "news_medium_4h")
LIVE_COLUMNS = ("live_context", "mood", "fear_greed")
SLOW_SPAN_DAYS = 90.0     # the month and moon calendars join once the memory spans this many days (three of each)
GROUPS = {"level": "lv_", "waktu": "tm_", "siklus": "cy_", "rezim": "rg_", "harian": "dl_"}
GROUP_LABELS = {"level": "Level harga (pivot, Fibonacci, zona)", "waktu": "Jam pasar & kalender", "siklus": "Musiman & siklus",
                "rezim": "Rezim & model kuant (ADX, Kalman, HMM, aturan M5)",
                "harian": "Konteks harian D1 (tren, volatilitas, rezim HMM harian)"}
# the sets tried, as the groups left out; the first is the plain brain the others must beat, the second the full set
# ("harian" is dropped from the ones that predate it, so they still mean what they did; a symbol without a daily
# file has no dl_ columns and ``choose_groups`` folds the variants that then say the same thing into one)
GROUP_VARIANTS: tuple[tuple[str, ...], ...] = (
    ("level", "waktu", "siklus", "rezim", "harian"), (), ("harian",), ("level", "waktu", "siklus", "rezim"),
    ("level", "waktu", "siklus", "harian"), ("level", "waktu", "siklus"), ("siklus", "harian"),
    ("waktu", "siklus", "harian"), ("level", "harian"), ("rezim", "harian"),
)
GROUP_MARGIN = 0.002      # validation log loss a richer set must win by
GROUP_MAX_ROWS = 20_000   # above this only the plain and the full set are compared: each try is a full training


def coverage_mask(names: list[str], x: np.ndarray, times: np.ndarray | None = None) -> tuple[np.ndarray, dict[str, float]]:
    """Columns left out because they are known for too few rows: a row where they are known would look
    unlike anything the networks learned from, and they would guess wildly on exactly the rows that matter.

    With ``times`` the slow calendars are judged too: until the rows span ``SLOW_SPAN_DAYS`` the place in
    the month and the moon's phase only ever went one way, so they would stand in for "later" and teach
    the networks the trend of those weeks.
    """
    mask = np.zeros(len(names), dtype=bool)
    share = {"news": float(np.nanmean(x[:, names.index("news_known")])) if len(x) else 0.0,
             "live": float(np.nanmean(x[:, names.index("live_context")])) if len(x) else 0.0}
    for group, cols in (("news", NEWS_COLUMNS), ("live", LIVE_COLUMNS)):
        if share[group] < COVERAGE_MIN:
            for col in cols:
                mask[names.index(col)] = True
    if times is not None:
        share["span_days"] = float(times[-1] - times[0]) / 86400.0 if len(times) else 0.0
        if share["span_days"] < SLOW_SPAN_DAYS:
            for col in cycles.SLOW_COLUMNS:
                if col in names:
                    mask[names.index(col)] = True
    return mask, {k: round(v, 3) for k, v in share.items()}


def group_mask(names: list[str], left_out: tuple[str, ...]) -> np.ndarray:
    """True for the columns of the optional groups named in ``left_out``."""
    prefixes = tuple(GROUPS[g] for g in left_out)
    return np.array([bool(prefixes) and n.startswith(prefixes) for n in names], dtype=bool)


def choose_groups(x: np.ndarray, y: np.ndarray, res: np.ndarray, train: slice, val: slice, names: list[str],
                  base_mask: np.ndarray, *, members: int, progress: Any = None
                  ) -> tuple[neural.Brainwave, tuple[str, ...], list[dict[str, Any]]]:
    """Train with each set of optional groups and keep the one that predicts the validation rows best.

    Returns the trained ensemble, the groups it leaves out, and every try with its validation loss.
    """
    present = [g for g in GROUPS if any(n.startswith(GROUPS[g]) for n in names)]
    variants: list[tuple[str, ...]] = []
    for option in (GROUP_VARIANTS if len(x) <= GROUP_MAX_ROWS else GROUP_VARIANTS[:2]):
        left = tuple(g for g in option if g in present)
        if left not in variants:
            variants.append(left)
    tries: list[tuple[float, tuple[str, ...], neural.Brainwave]] = []
    for k, left_out in enumerate(variants):
        if progress:
            progress(f"mencoba ukuran {k + 1}/{len(variants)}: "
                     + ("dasar saja" if len(left_out) == len(present) else "dasar + " + ", ".join(g for g in present if g not in left_out)))
        wave = neural.fit(x[train], y[train], res[train], x_val=x[val], y_val=y[val], result_val=res[val], names=names,
                          members=members, mask=base_mask | group_mask(names, left_out))
        tries.append((neural._ensemble_loss(wave, x[val], y[val]), left_out, wave))
    plain_loss = tries[0][0]
    best = min(tries, key=lambda t: t[0])
    chosen = best if best[0] < plain_loss - GROUP_MARGIN else tries[0]
    report = [{"used": [g for g in present if g not in left_out], "val_loss": round(loss, 4),
               "gain": round(plain_loss - loss, 4), "chosen": left_out == chosen[1]} for loss, left_out, _ in tries]
    return chosen[2], chosen[1], report


def importance(wave: neural.Brainwave) -> np.ndarray:
    """How much each measurement moves the networks: the first layer's weight norms, averaged over the
    ensemble and scaled to mean 1. Masked measurements get 0."""
    norms = np.stack([np.sqrt((net.weights[0] ** 2).sum(axis=1)) for net in wave.nets]).mean(axis=0)
    if wave.mask is not None:
        norms = np.where(wave.mask, 0.0, norms)
    total = norms.sum()
    out: np.ndarray = norms * (len(norms) / total) if total > 0 else np.ones(len(norms))
    return out


def _safe(symbol: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in symbol)[:32]


def _plan_index(side: int, sl: float, tp: float) -> int:
    return PLANS.index((side, sl, tp))


def _rate(values: np.ndarray, cls: int) -> float | None:
    return round(float((values == cls).mean()), 3) if len(values) else None


def _table(keys: list[str], cls: np.ndarray, res: np.ndarray, min_rows: int = 8) -> list[dict[str, Any]]:
    """For each key: rows, and the buy and sell reference plans' target rate and average result."""
    buy, sell = _plan_index(1, *memory.REFERENCE), _plan_index(-1, *memory.REFERENCE)
    groups: dict[str, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        groups[k].append(i)
    out = []
    for k, rows in sorted(groups.items()):
        if len(rows) < min_rows:
            continue
        idx = np.array(rows)
        out.append({"key": k, "rows": len(rows),
                    "buy_tp": _rate(cls[idx, buy], TP), "buy_avg_r": round(float(res[idx, buy].mean()), 3),
                    "sell_tp": _rate(cls[idx, sell], TP), "sell_avg_r": round(float(res[idx, sell].mean()), 3)})
    return out


class Brain:
    def __init__(self, symbol: str, timeframe: str, folder: Path | None = None) -> None:
        if timeframe not in memory.TIMEFRAMES:
            raise ValueError(f"timeframe: {', '.join(memory.TIMEFRAMES)}")
        self.symbol, self.timeframe = symbol, timeframe
        self.folder = folder or memory.FOLDER
        self.state = "idle"
        self.progress = ""
        self.error = ""
        self._lock = threading.Lock()
        self._train_lock = threading.Lock()
        self._wave: neural.Brainwave | None = None
        self._memory: dict[str, np.ndarray] = {}
        self._meta: dict[str, Any] = {}
        self._loaded = False
        self._dataset_key: tuple[Any, ...] | None = None
        self._dataset: memory.Dataset | None = None
        self._failed_at = 0.0
        self._failed_rows = -1

    # ── files ────────────────────────────────────────────────────────────────
    @property
    def stem(self) -> Path:
        return self.folder / f"{_safe(self.symbol)}-{self.timeframe}"

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        with contextlib.suppress(OSError, ValueError, KeyError):
            meta = json.loads(self.stem.with_suffix(".json").read_text(encoding="utf-8"))
            with np.load(self.stem.with_suffix(".npz")) as data:
                arrays = {k: data[k] for k in data.files}
            self._wave = neural.from_arrays(arrays, meta["names"], meta.get("exam", {}))
            self._memory = {k[4:]: arrays[k] for k in arrays if k.startswith("mem_")}
            self._meta = meta
            return
        with contextlib.suppress(OSError, ValueError):         # a failed training still leaves its history
            self._meta = json.loads(self.stem.with_suffix(".json").read_text(encoding="utf-8"))

    def _save(self, wave: neural.Brainwave | None, mem: dict[str, np.ndarray], meta: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        if wave is not None:
            arrays = neural.to_arrays(wave) | {f"mem_{k}": v for k, v in mem.items()}
            tmp = self.stem.with_name(self.stem.name + ".tmp.npz")
            save: Any = np.savez_compressed
            save(tmp, **arrays)
            tmp.replace(self.stem.with_suffix(".npz"))
        tmp_json = self.stem.with_suffix(".json.tmp")
        tmp_json.write_text(json.dumps(meta, ensure_ascii=False, default=float), encoding="utf-8")
        tmp_json.replace(self.stem.with_suffix(".json"))

    # ── the memory ───────────────────────────────────────────────────────────
    def dataset(self, backend: Any = None, *, now: float | None = None, spread: float | None = None,
                base: list[dict[str, float]] | None = None) -> memory.Dataset:
        """The memory as of now, rebuilt only when a new M5 bar, news week or live context arrived."""
        now = time.time() if now is None else now
        base = base if base is not None else memory.history_m5(self.symbol, backend)
        if spread is None:
            spread = 0.0
            with contextlib.suppress(Exception):
                tick = backend.tick(self.symbol) if backend is not None else None
                spread = max(0.0, float(tick["ask"]) - float(tick["bid"])) if tick else 0.0
        archive = market_news.load_calendar_archive()
        context = memory.load_context(self.symbol, self.timeframe, self.folder)
        seconds = memory.TIMEFRAMES[self.timeframe]
        key = (len(base), base[-1]["t"] if base else 0, int(now // seconds), len(archive["events"]), len(context),
               round(spread, 8))
        if key != self._dataset_key or self._dataset is None:
            self._dataset = memory.build(self.symbol, self.timeframe, base, now=now, spread=spread, archive=archive,
                                         currencies=market_news.currencies_of(self.symbol), context=context)
            self._dataset_key = key
        return self._dataset

    # ── training ─────────────────────────────────────────────────────────────
    def train(self, backend: Any = None, *, now: float | None = None, base: list[dict[str, float]] | None = None,
              spread: float | None = None, members: int = neural.MEMBERS) -> dict[str, Any]:
        """Build the memory, train, sit the exam, retrain on everything, save. Returns the status."""
        if not self._train_lock.acquire(blocking=False):
            return self.status()
        try:
            self.state, self.error, self.progress = "training", "", "membangun memori"
            started = time.time()
            ds = self.dataset(backend, now=now, spread=spread, base=base)
            rows = np.flatnonzero(ds.labelled)
            if len(rows) < neural.MIN_ROWS:
                raise ValueError(f"memori baru {len(rows)} bar {self.timeframe} dengan hasil, butuh {neural.MIN_ROWS}. "
                                 f"Biarkan EA merekam lebih lama, atau pilih timeframe lebih kecil.")
            x, y, res = ds.x[rows], ds.cls[rows].astype(int), ds.result[rows]
            n, gap = len(rows), ds.hold
            cut1, cut2 = int(n * 0.70), int(n * 0.85)
            train, val, test = slice(0, cut1 - gap), slice(cut1, cut2 - gap), slice(cut2, n)
            times = ds.times[rows]
            mask, coverage = coverage_mask(ds.names, x[train], times[train])
            self.progress = f"melatih {members} jaringan di {cut1 - gap} bar, ujian di {n - cut2} bar terbaru"

            def say(text: str) -> None:
                self.progress = text

            graded, left_out, group_tries = choose_groups(x, y, res, train, val, ds.names, mask, members=members, progress=say)
            exam = neural.exam(graded, x[test], y[test], res[test], res[train])
            epochs = max(10, int(np.median([net.epochs for net in graded.nets])))
            self.progress = "melatih ulang di seluruh memori"
            mask_all, coverage_all = coverage_mask(ds.names, x, times)
            mask_all = mask_all | group_mask(ds.names, left_out)
            wave = neural.fit(x, y, res, names=ds.names, members=members, epochs=epochs, seed=11, mask=mask_all)
            wave.temperature = graded.temperature              # calibrated where it could be checked
            wave.threshold, wave.threshold_profitable = graded.threshold, graded.threshold_profitable
            wave.exam = exam
            z = wave.standardise(x).astype(np.float32)
            imp = importance(wave)
            keys = [sessions.key(t) for t in ds.times[rows]]
            hours = [f"{int(t % 86400 // 3600):02d}" for t in ds.times[rows]]
            days = [("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min")[int(((t // 86400) + 3) % 7)] for t in ds.times[rows]]
            near = ds.names.index("news_next_h"), ds.names.index("news_prev_h"), ds.names.index("news_known")
            news_keys = ["tidak diketahui" if not x[i, near[2]] else
                         ("dekat berita besar (±2 jam)" if min(x[i, near[0]], x[i, near[1]]) * 24 <= 2 else "jauh dari berita besar")
                         for i in range(n)]
            history = list(self._meta.get("history", []))[-(HISTORY_KEEP - 1):]
            history.append({"at": time.time(), "rows": n, "avg_r_atr": exam["avg_r_atr"], "trades": exam["trades"],
                            "skill": exam["skill"], "passed": exam["passed"]})
            meta = {
                "symbol": self.symbol, "timeframe": self.timeframe, "trained_at": time.time(),
                "seconds": round(time.time() - started, 1), "names": ds.names, "features": len(ds.names),
                "rows": n, "train_rows": cut1 - gap, "exam_rows": n - cut2, "first": float(ds.times[rows[0]]),
                "last": float(ds.times[rows[-1]]), "base_bars": ds.base_bars, "hold_bars": ds.hold,
                "members": members, "hidden": list(neural.hidden_for(n)), "capacity": neural.capacity(),
                "parameters": int(sum(w.size + b.size for net in wave.nets for w, b in zip(net.weights, net.biases, strict=True))),
                "epochs": epochs, "spread": ds.spread,
                "temperature": graded.temperature,
                "exam": exam, "info": ds.info, "history": history,
                "coverage": coverage_all, "masked": [ds.names[i] for i in np.flatnonzero(mask_all)],
                "groups": [{"name": g, "label": GROUP_LABELS[g], "used": g not in left_out,
                            "columns": sum(1 for nm in ds.names if nm.startswith(GROUPS[g]))} for g in GROUPS
                           if g != "harian" or any(nm.startswith(GROUPS[g]) for nm in ds.names)],
                "group_tries": group_tries,
                "top_features": [{"name": ds.names[i], "importance": round(float(imp[i]), 3)} for i in np.argsort(-imp)[:12]],
                "conditions": {"session": _table(keys, y, res), "hour": _table(hours, y, res, min_rows=5),
                               "weekday": _table(days, y, res), "news": _table(news_keys, y, res)},
            }
            mem: dict[str, np.ndarray] = {"z": z, "cls": y.astype(np.int8), "res": res.astype(np.float32), "t": ds.times[rows],
                                          "imp": imp.astype(np.float32)}
            self._save(wave, mem, meta)
            with self._lock:
                self._wave, self._memory, self._meta, self._loaded = wave, mem, meta, True
            self.state, self.progress = "ready", ""
            logger.info(f"[brain] {self.symbol} {self.timeframe}: {n} rows, exam {exam['verdict']}")
        except Exception as exc:  # noqa: BLE001 - a failed training is reported on the panel, the old brain stays
            self.state, self.progress, self.error = "error", "", str(exc)[:300]
            self._failed_at = time.time()
            self._failed_rows = int(self._dataset.labelled.sum()) if self._dataset is not None else -1
            logger.warning(f"[brain] {self.symbol} {self.timeframe}: {exc}")
        finally:
            self._train_lock.release()
        return self.status()

    def train_in_background(self, backend: Any = None) -> bool:
        if self._train_lock.locked():
            return False
        self.state, self.progress = "training", "antre"
        threading.Thread(target=self.train, args=(backend,), name=f"brain-{self.symbol}-{self.timeframe}", daemon=True).start()
        return True

    def needs_training(self, labelled_rows: int | None = None) -> bool:
        with self._lock:
            self._load()
            if self._wave is None:      # after a failure, try again once the memory grew or an hour passed
                if self.state != "error":
                    return True
                grew = labelled_rows is not None and labelled_rows >= self._failed_rows + MIN_NEW_ROWS
                return grew or time.time() - self._failed_at > 3600
            if self._dataset is not None and self._wave.names != self._dataset.names:
                # the memory measures more (or other) things than this brain was trained on: it cannot answer
                return self.state != "error" or time.time() - self._failed_at > 3600
            old = time.time() - float(self._meta.get("trained_at", 0)) >= RETRAIN_S
            grown = labelled_rows is None or labelled_rows - int(self._meta.get("rows", 0)) >= MIN_NEW_ROWS
            return old and grown

    def maybe_train(self, backend: Any = None) -> bool:
        """Train again in the background when it is due. A live run calls this once per bar."""
        rows = int(self._dataset.labelled.sum()) if self._dataset is not None else None
        if self.state == "training" or not self.needs_training(rows):
            return False
        return self.train_in_background(backend)

    # ── answering ────────────────────────────────────────────────────────────
    @property
    def ready(self) -> bool:
        with self._lock:
            self._load()
            return self._wave is not None

    def analyse(self, backend: Any = None, *, now: float | None = None, base: list[dict[str, float]] | None = None,
                spread: float | None = None, top: int = 6) -> dict[str, Any]:
        """The brain's view of the bar that just closed. Without a trained brain: what it is waiting for."""
        ds = self.dataset(backend, now=now, spread=spread, base=base)
        with self._lock:
            self._load()
            wave, mem, meta = self._wave, self._memory, dict(self._meta)
        if wave is None or not ds.rows:
            return {"ok": False, "reason": self.error or "otak belum dilatih", "status": self.status()}
        if wave.names != ds.names:
            started = self.state != "training" and self.needs_training() and self.train_in_background(backend)
            return {"ok": False, "status": self.status(),
                    "reason": "ukuran memori berubah sejak pelatihan terakhir: " +
                              ("sedang dilatih ulang, coba lagi sebentar lagi" if started or self.state == "training" else "latih ulang")}
        row = ds.x[-1:]
        t_now = float(ds.times[-1])
        members = wave.proba_members(row)[:, 0]                 # (members, plans, 3)
        proba = members.mean(axis=0)
        ev = wave.ev(proba)
        spread_ev = wave.ev(members).std(axis=0)
        edge = ev - neural.CAUTION * spread_ev                 # what decides: the EV the networks agree on
        order = list(np.argsort(-edge))
        plans = [{"plan": int(p), "name": neural.plan_name(int(p)), "side": "buy" if PLANS[p][0] > 0 else "sell",
                  "stop_atr": PLANS[p][1], "target_atr": PLANS[p][2], "p_target": round(float(proba[p, TP]), 3),
                  "p_stop": round(float(proba[p, SL]), 3), "p_neither": round(float(proba[p, TIMEOUT]), 3),
                  "ev_atr": round(float(ev[p]), 3), "edge_atr": round(float(edge[p]), 3),
                  "disagreement": round(float(spread_ev[p]), 3)} for p in order]
        best = plans[0]
        best_side = {side: next(pl for pl in plans if pl["side"] == side) for side in ("buy", "sell")}
        out: dict[str, Any] = {
            "ok": True, "symbol": self.symbol, "timeframe": self.timeframe, "decided_at": t_now,
            "session": sessions.label(t_now), "session_key": sessions.key(t_now),
            "close": float(ds.close[-1]), "atr": float(ds.atr[-1]), "spread": ds.spread,
            "plans": plans[:top], "best": best, "best_buy": best_side["buy"], "best_sell": best_side["sell"],
            "take": best["edge_atr"] > wave.threshold, "threshold_atr": wave.threshold,
            "threshold_profitable": wave.threshold_profitable,
            "exam": meta.get("exam", {}), "trained_at": meta.get("trained_at"), "rows": meta.get("rows"),
            "hold_bars": ds.hold, "features": len(ds.names),
            "drivers": self._drivers(ds, wave),
        }
        if mem:
            out["similar"] = self._similar(wave, row, t_now, mem, [best_side["buy"]["plan"], best_side["sell"]["plan"]])
        out["conditions_now"] = self._conditions_now(meta, ds, t_now)
        out["regime_now"] = regime.snapshot(ds.names, ds.x[-1])
        return out

    def _drivers(self, ds: memory.Dataset, wave: neural.Brainwave, k: int = 8) -> list[dict[str, Any]]:
        """The measurements furthest from normal right now: what makes this bar unusual."""
        z = wave.standardise(ds.x[-1:])[0]
        idx = [i for i in np.argsort(-np.abs(z)) if wave.mask is None or not wave.mask[i]][:k]
        return [{"name": ds.names[i], "z": round(float(z[i]), 2), "value": round(float(np.nan_to_num(ds.x[-1, i])), 3)} for i in idx]

    def _similar(self, wave: neural.Brainwave, row: np.ndarray, t_now: float, mem: dict[str, np.ndarray],
                 plans: list[int]) -> dict[str, Any]:
        z = wave.standardise(row)[0].astype(np.float32)
        mz, mt = mem["z"], mem["t"]
        usable = mt < t_now - 1                                 # only moments whose outcome was known before now
        if not usable.any():
            return {}
        imp = mem["imp"] if "imp" in mem and len(mem["imp"]) == mz.shape[1] else importance(wave).astype(np.float32)
        d = np.sqrt((imp * (mz[usable] - z) ** 2).mean(axis=1))   # measurements the networks rely on count more
        k = min(NEIGHBOURS, int(usable.sum()))
        near = np.argsort(d)[:k]
        cls, res, times = mem["cls"][usable][near], mem["res"][usable][near], mt[usable][near]
        same = np.array([sessions.key(t) == sessions.key(t_now) for t in times])
        bandwidth = max(float(np.median(d[near])), 1e-6)
        kernel = np.exp(-0.5 * (d[near] / bandwidth) ** 2)       # the nearest moments say most
        kernel /= kernel.sum()
        out: dict[str, Any] = {"count": k, "distance": round(float(d[near].mean()), 3), "same_session": int(same.sum()),
                               "plans": []}
        for p in plans:
            col_c, col_r = cls[:, p], res[:, p]
            item = {"name": neural.plan_name(p), "target": int((col_c == TP).sum()), "stop": int((col_c == SL).sum()),
                    "neither": int((col_c == TIMEOUT).sum()), "avg_r_atr": round(float(col_r.mean()), 3),
                    "weighted_p_target": round(float((kernel * (col_c == TP)).sum()), 3),
                    "weighted_avg_r_atr": round(float((kernel * col_r).sum()), 3)}
            if same.any():
                item["same_session_target"] = int((col_c[same] == TP).sum())
                item["same_session_stop"] = int((col_c[same] == SL).sum())
            out["plans"].append(item)
        out["closest"] = [{"time": float(times[i]), "session": sessions.label(float(times[i])),
                           "buy": neural.plan_name(plans[0]) + ": " + ("target" if cls[i, plans[0]] == TP else
                                                                        "stop" if cls[i, plans[0]] == SL else "habis waktu"),
                           "sell": neural.plan_name(plans[1]) + ": " + ("target" if cls[i, plans[1]] == TP else
                                                                         "stop" if cls[i, plans[1]] == SL else "habis waktu")}
                          for i in range(min(5, k))]
        return out

    def _conditions_now(self, meta: dict[str, Any], ds: memory.Dataset, t_now: float) -> dict[str, Any]:
        cond = meta.get("conditions", {})
        names = ds.names
        x = ds.x[-1]
        known = bool(x[names.index("news_known")])
        hours = min(x[names.index("news_next_h")], x[names.index("news_prev_h")]) * 24
        news_key = "tidak diketahui" if not known else ("dekat berita besar (±2 jam)" if hours <= 2 else "jauh dari berita besar")
        wanted = {"session": sessions.key(t_now), "hour": f"{int(t_now % 86400 // 3600):02d}",
                  "weekday": ("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min")[int(((t_now // 86400) + 3) % 7)], "news": news_key}
        out: dict[str, Any] = {"reference_plan": f"SL{memory.REFERENCE[0]:g}/TP{memory.REFERENCE[1]:g} ATR"}
        for table, key in wanted.items():
            out[table] = next((row for row in cond.get(table, []) if row["key"] == key), {"key": key, "rows": 0})
        return out

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._load()
            meta = dict(self._meta)
            ready = self._wave is not None
        return {"symbol": self.symbol, "timeframe": self.timeframe, "state": self.state, "progress": self.progress,
                "error": self.error, "ready": ready,
                **{k: meta.get(k) for k in ("trained_at", "seconds", "features", "rows", "train_rows", "exam_rows", "first",
                                            "last", "base_bars", "hold_bars", "members", "hidden", "capacity", "parameters", "epochs", "temperature", "exam", "info",
                                            "coverage", "masked", "groups", "group_tries", "top_features")},
                "history": meta.get("history", [])[-20:], "conditions": meta.get("conditions", {}),
                "retrain_hours": RETRAIN_S / 3600, "min_rows": neural.MIN_ROWS}


_brains: dict[tuple[str, str, str], Brain] = {}
_brains_lock = threading.Lock()


def get_brain(symbol: str, timeframe: str, folder: Path | None = None) -> Brain:
    key = (symbol, timeframe, str(folder or memory.FOLDER))
    with _brains_lock:
        if key not in _brains:
            _brains[key] = Brain(symbol, timeframe, folder)
        return _brains[key]


def compact(view: dict[str, Any]) -> dict[str, Any]:
    """The brain's view in fewer tokens, for the LLM."""
    if not view.get("ok"):
        return {"ok": False, "reason": view.get("reason", "")}
    exam = view.get("exam") or {}

    def pick(plan: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
        return {k: plan.get(k) for k in keys if k in plan} if plan else {}

    keys = ("plan", "name", "p_target", "p_stop", "ev_atr", "edge_atr", "disagreement")
    return {
        "ok": True, "timeframe": view.get("timeframe"), "atr": round(float(view.get("atr") or 0.0), 6),
        "hold_bars": view.get("hold_bars"),
        "exam": {k: exam.get(k) for k in ("passed", "verdict", "trades", "avg_r_atr", "avg_r_lower", "t_stat", "static_avg_r_atr",
                                          "skill", "tp_rate", "brier", "ece")},
        "memory_rows": view.get("rows"), "brain_would_trade": view.get("take"),
        "entry_threshold_atr": view.get("threshold_atr"),
        "plans_ranked": [pick(p, keys) for p in view.get("plans", [])],
        "best_buy": pick(view.get("best_buy"), keys[:5]), "best_sell": pick(view.get("best_sell"), keys[:5]),
        "similar_past": view.get("similar", {}).get("plans", []),
        "similar_count": view.get("similar", {}).get("count"),
        "conditions_now": view.get("conditions_now", {}),
        "regime_now": view.get("regime_now", {}),
        "unusual_now": view.get("drivers", [])[:6],
        "p_target_calibration": ("baik" if (exam.get("ece") or 1) < 0.05 else "sedang" if (exam.get("ece") or 1) < 0.1
                                 else "lemah: p_target cenderung meleset"),
    }


__all__ = ["Brain", "get_brain", "compact", "importance", "RETRAIN_S"]
