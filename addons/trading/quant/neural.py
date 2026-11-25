"""The trading brain's neurons: an ensemble of small neural networks, in numpy, graded on days it never saw.

Each network reads one memory row (``memory.feature_matrix``, about 90 numbers) and answers, for
each of the 24 plans (buy or sell, three stops, four targets), three chances that add up to one:
the target first, the stop first, or neither within the hold. From those the expected result of
each plan follows, in ATR and after the spread:

    EV = P(target) * target - P(stop) * stop + P(neither) * (what "neither" averaged in training)

How it learns
-------------
* Hidden layers (SiLU) that grow with the memory and with this machine (``capacity``: RAM, cores and a
  measured matrix speed pick ``kecil``, ``sedang``, ``besar`` or ``masif``). On ``besar``: 192-96 neurons
  below 3,000 rows, 384-192-96 below 20,000, then 768-384-192; ``kecil`` keeps the old 64-32 / 128-64 /
  256-128. Training runs in float32 with a wall-time budget per network. Dropout, weight decay, the Adam optimiser, and
  a cross-entropy loss over all 24 plans at once, so what one plan teaches helps the others.
* Five networks, each on its own bootstrap sample and its own seed. Their average is the
  answer, and how far they disagree is shown as uncertainty.
* Time order is never broken. The oldest 70% trains, the next 15% stops the training before
  it memorises (early stopping), and the newest 15% is the exam. The exam rows are never used
  to choose anything, and a gap of one hold separates each part, because a row's outcome
  reaches into the following bars.
* The exam is a trading test, not only a loss. On every exam bar the plan with the best EV is
  taken when its edge (EV less half the networks' disagreement, ``CAUTION``) clears the threshold
  chosen on the validation rows (``choose_threshold``: where picks there really made money,
  against the winner's curse of always finding one of 24 plans that looks good), and its real outcome from the memory is counted. That
  is compared with two yardsticks: always the plan that did best in training (which catches a
  trend that simply continued), and the average of all plans (luck). The brain passes only
  when it makes money on the exam, beats both yardsticks, and its probabilities beat the base
  rates (``skill`` above 0).
* The chances are calibrated on the validation rows (temperature scaling), because a network's
  raw chances are usually too sure of themselves, and an over-sure EV trades every bar.
* After the exam, the networks are trained again on everything for the same number of epochs,
  so the newest market counts in what is used live.
* Newer rows weigh more (``recency_weights``: a half-life of ``HALF_LIFE_SHARE`` of the memory),
  so the networks follow the market as it is now without forgetting how it was.
* Each network's weights are averaged over its training (Polyak/EMA averaging) and read with that
  average: a flatter, steadier answer than the last noisy step. The learning rate warms up and then
  decays along a cosine; gradients are clipped; the labels are smoothed a little so a network
  cannot become certain of a single outcome.
* The ensemble's temperature is found by a continuous search (golden section) on the validation
  rows, and the entry threshold is set where the lower confidence bound of the realised result
  of the picks is highest, not where the mean happened to be: a bar that is lucky on few rows
  does not win.
* The exam also reports the calibration of P(target) (Brier score, expected calibration error) and
  the t-statistic of the average result, so the panel and the LLM can see how far to trust it.

More rows make it better only if there is something to learn. The exam says which.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from addons.trading.quant.memory import FOLDER, PLANS, SL, TIMEOUT, TP

# The networks grow with the memory, and how big they may grow depends on this machine (``capacity``).
SIZES: dict[str, tuple[tuple[int | None, tuple[int, ...]], ...]] = {
    "kecil": ((3_000, (64, 32)), (20_000, (128, 64)), (None, (256, 128))),
    "sedang": ((3_000, (128, 64)), (20_000, (256, 128, 64)), (None, (512, 256, 128))),
    "besar": ((3_000, (192, 96)), (20_000, (384, 192, 96)), (None, (768, 384, 192))),
    "masif": ((3_000, (256, 128)), (20_000, (512, 256, 128, 64)), (None, (1024, 512, 256, 128))),
}
SIZE_ORDER = ("kecil", "sedang", "besar", "masif")
GROWTH = SIZES["kecil"]          # the original sizes, kept for a machine that cannot do more
MEMBERS = 5
EPOCHS = 160
PATIENCE = 18
BATCH = 128
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 1e-3
DROPOUT = 0.2
MIN_EV = 0.08              # in ATR: below this the brain does not take a plan
CAUTION = 0.5              # the EV used to decide is lowered by this many times the networks' disagreement
MIN_ROWS = 300             # labelled rows needed to train and grade
MIN_EXAM_TRADES = 20
CLASSES = 3
HALF_LIFE_SHARE = 0.35     # the row this share of the memory back from the newest weighs half as much
MIN_WEIGHT = 0.15          # the oldest row still weighs this much: the past is not forgotten
LABEL_SMOOTHING = 0.02
CLIP_NORM = 5.0            # gradient norm clip per step
WARMUP_EPOCHS = 3
EMA_DECAY = 0.995          # Polyak averaging of the weights read at prediction time


def recency_weights(n: int, half_life_share: float = HALF_LIFE_SHARE, floor: float = MIN_WEIGHT) -> np.ndarray:
    """Sample weights that decay going back in time, mean 1: the newest row weighs most."""
    if n <= 0:
        return np.ones(0)
    age = np.arange(n, dtype=float)[::-1]
    half_life = max(1.0, half_life_share * n)
    w = np.maximum(0.5 ** (age / half_life), floor)
    out: np.ndarray = w / w.mean()
    return out


def _lr_at(epoch: int, epochs: int, base: float = LEARNING_RATE) -> float:
    """Linear warm-up for a few epochs, then cosine decay to a tenth of the base rate."""
    if epoch <= WARMUP_EPOCHS:
        return base * epoch / WARMUP_EPOCHS
    progress = (epoch - WARMUP_EPOCHS) / max(1, epochs - WARMUP_EPOCHS)
    return base * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * min(1.0, progress))))


# ── how big this machine lets the brain be ─────────────────────────────────
EPOCH_TARGET_S = 2.5        # the biggest network on 20,000 rows should run an epoch in about this long
SIZE_MIN_RAM_GB = {"kecil": 0.0, "sedang": 4.0, "besar": 8.0, "masif": 16.0}
BUDGET_S = {"kecil": 60.0, "sedang": 90.0, "besar": 150.0, "masif": 240.0}   # wall time per network, then it stops
_capacity: dict[str, Any] | None = None


def _ram_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError, AttributeError):
        return 4.0


def _gflops(seconds: float = 0.25) -> float:
    """Measured float32 matrix speed of this machine, with batch-sized products like training uses."""
    rng = np.random.default_rng(0)
    a = rng.standard_normal((BATCH, 512), dtype=np.float32)
    b = rng.standard_normal((512, 512), dtype=np.float32)
    a @ b                                   # warm up the BLAS threads
    done, started = 0, time.perf_counter()
    while time.perf_counter() - started < seconds:
        for _ in range(20):
            a @ b
        done += 20
    elapsed = max(time.perf_counter() - started, 1e-6)
    return 2 * BATCH * 512 * 512 * done / elapsed / 1e9


def _flops_per_epoch(hidden: tuple[int, ...], rows: int = 20_000, inputs: int = 150) -> float:
    sizes = (inputs, *hidden, len(PLANS) * CLASSES)
    return 6.0 * rows * sum(a * b for a, b in zip(sizes[:-1], sizes[1:], strict=True))


DEVICE_FILE = "device.json"
DEVICE_KEEP_S = 30 * 86400      # a remembered speed is trusted this long, then measured afresh


def _best_speed(folder: Path | None = None) -> float:
    """This machine's matrix speed: the best of now and what was measured before (``device.json`` in the
    brain folder). A test that ran while the laptop was busy must not shrink the brain for good."""
    path = (folder or FOLDER) / DEVICE_FILE
    ram, cpus = round(_ram_gb(), 1), os.cpu_count() or 1
    now = max(_gflops() for _ in range(2))
    saved: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        saved = json.loads(path.read_text(encoding="utf-8"))
    same = saved.get("cpus") == cpus and saved.get("ram_gb") == ram and time.time() - float(saved.get("at", 0)) < DEVICE_KEEP_S
    best = max(now, float(saved.get("gflops", 0.0))) if same else now
    if not same or best > float(saved.get("gflops", 0.0)):
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"gflops": round(best, 1), "cpus": cpus, "ram_gb": ram,
                                        "at": time.time() if not same else saved.get("at", time.time())}), encoding="utf-8")
    return best


def capacity(refresh: bool = False) -> dict[str, Any]:
    """The size of brain this machine runs well: from its RAM, its cores and its matrix speed.

    ``TRADING_BRAIN_SIZE`` (kecil, sedang, besar, masif) overrides the choice; ``auto`` or nothing measures.
    The speed is the best measured on this machine (``_best_speed``), checked once per process.
    """
    global _capacity
    if _capacity is not None and not refresh:
        return dict(_capacity)
    ram, cpus = _ram_gb(), os.cpu_count() or 1
    wanted = os.getenv("TRADING_BRAIN_SIZE", "auto").strip().lower()
    speed = _best_speed()
    if wanted in SIZES:
        tier, why = wanted, "dipilih lewat TRADING_BRAIN_SIZE"
    else:
        tier = "kecil"
        for name in SIZE_ORDER:
            biggest = SIZES[name][-1][1]
            if ram >= SIZE_MIN_RAM_GB[name] and _flops_per_epoch(biggest) / (speed * 1e9) <= EPOCH_TARGET_S:
                tier = name
        why = f"{ram:.0f} GB RAM, {cpus} core, {speed:.0f} GFLOPS terukur"
    _capacity = {"tier": tier, "why": why, "ram_gb": round(ram, 1), "cpus": cpus, "gflops": round(speed, 1),
                 "budget_s": BUDGET_S[tier], "largest": list(SIZES[tier][-1][1])}
    return dict(_capacity)


def hidden_for(rows: int, tier: str | None = None) -> tuple[int, ...]:
    """Hidden layers sized for the memory and this machine: small while the memory is small, so the
    networks cannot just memorise it, and as big as ``capacity`` allows once there is enough to learn."""
    growth = SIZES.get(tier or capacity()["tier"], GROWTH)
    for limit, size in growth:
        if limit is None or rows < limit:
            return size
    return growth[-1][1]


def _silu(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    s = (1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))).astype(x.dtype, copy=False)
    return x * s, s


def _softmax3(logits: np.ndarray) -> np.ndarray:
    z = logits.reshape(len(logits), -1, CLASSES)
    z = z - z.max(axis=2, keepdims=True)
    e = np.exp(z)
    out: np.ndarray = e / e.sum(axis=2, keepdims=True)
    return out


@dataclass
class Net:
    weights: list[np.ndarray]
    biases: list[np.ndarray]
    epochs: int = 0
    val_loss: float = math.nan

    @classmethod
    def new(cls, inputs: int, outputs: int, rng: np.random.Generator, hidden: tuple[int, ...] = (64, 32)) -> Net:
        sizes = (inputs, *hidden, outputs)
        weights = [rng.normal(0, math.sqrt(2.0 / a), size=(a, b)).astype(np.float32)
                   for a, b in zip(sizes[:-1], sizes[1:], strict=True)]
        weights[-1] *= 0.1
        return cls(weights, [np.zeros(b, dtype=np.float32) for b in sizes[1:]])

    def forward(self, x: np.ndarray, rng: np.random.Generator | None = None) -> tuple[np.ndarray, list[Any]]:
        caches: list[Any] = []
        a = x
        for k, (w, b) in enumerate(zip(self.weights, self.biases, strict=True)):
            z = a @ w + b
            if k == len(self.weights) - 1:
                caches.append((a, None, None, None))
                return z, caches
            out, s = _silu(z)
            mask = None
            if rng is not None and DROPOUT > 0:
                mask = ((rng.random(out.shape, dtype=np.float32) >= DROPOUT) / (1 - DROPOUT)).astype(np.float32)
                out = out * mask
            caches.append((a, z, s, mask))
            a = out
        raise AssertionError("unreachable")

    def proba(self, x: np.ndarray, temperature: float = 1.0) -> np.ndarray:
        logits, _ = self.forward(x)
        return _softmax3(logits / temperature)


def _targets(y: np.ndarray) -> np.ndarray:
    """One-hot classes softened by LABEL_SMOOTHING: (rows, plans, 3)."""
    n, plans = y.shape
    hot = np.full((n, plans, CLASSES), LABEL_SMOOTHING / CLASSES)
    np.put_along_axis(hot, y[:, :, None].astype(int), 1 - LABEL_SMOOTHING + LABEL_SMOOTHING / CLASSES, axis=2)
    return hot


def _loss_and_grads(net: Net, x: np.ndarray, y: np.ndarray, rng: np.random.Generator | None,
                    weights: np.ndarray | None = None) -> tuple[float, list[np.ndarray], list[np.ndarray]]:
    logits, caches = net.forward(x, rng)
    p = _softmax3(logits)
    n, plans = y.shape
    w = np.ones(n) if weights is None else weights
    target = _targets(y)
    loss = float(-np.sum(w[:, None, None] * target * np.log(np.maximum(p, 1e-9))) / (w.sum() * plans))
    grad = (p - target) * w[:, None, None]
    delta = (grad.reshape(n, plans * CLASSES) / (w.sum() * plans)).astype(np.float32)
    gw: list[np.ndarray] = [np.empty(0)] * len(net.weights)
    gb: list[np.ndarray] = [np.empty(0)] * len(net.biases)
    for k in range(len(net.weights) - 1, -1, -1):
        a, _, _, _ = caches[k]
        gw[k] = a.T @ delta + WEIGHT_DECAY * net.weights[k]
        gb[k] = delta.sum(axis=0)
        if k:
            back = delta @ net.weights[k].T
            _, z, s, mask = caches[k - 1]
            if mask is not None:
                back = back * mask
            delta = back * (s + z * s * (1 - s))
    return loss, gw, gb


def _loss(net: Net, x: np.ndarray, y: np.ndarray) -> float:
    p = net.proba(x)
    picked = np.take_along_axis(p, y[:, :, None].astype(int), axis=2)[:, :, 0]
    return float(-np.mean(np.log(np.maximum(picked, 1e-9))))


def train_net(x: np.ndarray, y: np.ndarray, x_val: np.ndarray | None, y_val: np.ndarray | None, *, seed: int,
              epochs: int = EPOCHS, bootstrap: bool = True, hidden: tuple[int, ...] | None = None,
              weights: np.ndarray | None = None, budget_s: float | None = None) -> Net:
    """One network. With validation rows it stops when they stop improving and keeps its best weights.

    ``weights`` are per-row sample weights (recency). The network returned carries the Polyak
    average of its weights over training, which is what is read at prediction time. Training is in
    float32 (twice the speed of float64 on a CPU, plenty of precision for this) and stops after
    ``budget_s`` seconds (default: this machine's ``capacity``), keeping the best weights so far.
    """
    rng = np.random.default_rng(seed)
    budget = capacity()["budget_s"] if budget_s is None else budget_s
    started = time.perf_counter()
    x = np.asarray(x, dtype=np.float32)
    x_val = None if x_val is None else np.asarray(x_val, dtype=np.float32)
    w_rows = np.ones(len(x)) if weights is None else np.asarray(weights, dtype=float)
    if bootstrap:
        # weighted bootstrap: a newer row is drawn more often, so the ensemble's diversity follows the weights
        pick = rng.choice(len(x), size=len(x), replace=True, p=w_rows / w_rows.sum())
        x, y, w_rows = x[pick], y[pick], np.ones(len(x))
    net = Net.new(x.shape[1], y.shape[1] * CLASSES, rng, hidden or hidden_for(len(x)))
    params = [*net.weights, *net.biases]
    m = [np.zeros_like(q) for q in params]
    v = [np.zeros_like(q) for q in params]
    avg = [q.copy() for q in params]             # Polyak/EMA average, what is read live
    step = 0
    best = (math.inf, [q.copy() for q in avg], 0)
    waited = 0
    for epoch in range(1, epochs + 1):
        lr = _lr_at(epoch, epochs)
        order = rng.permutation(len(x))
        for start in range(0, len(x), BATCH):
            batch = order[start:start + BATCH]
            _, gw, gb = _loss_and_grads(net, x[batch], y[batch], rng, w_rows[batch])
            grads = [*gw, *gb]
            norm = math.sqrt(sum(float((g * g).sum()) for g in grads))
            if norm > CLIP_NORM:
                grads = [g * (CLIP_NORM / norm) for g in grads]
            step += 1
            for q, g, mq, vq, aq in zip(params, grads, m, v, avg, strict=True):
                mq *= 0.9
                mq += 0.1 * g
                vq *= 0.999
                vq += 0.001 * g * g
                q -= lr * (mq / (1 - 0.9 ** step)) / (np.sqrt(vq / (1 - 0.999 ** step)) + 1e-8)
                decay = min(EMA_DECAY, (1 + step) / (10 + step))     # a short average early, a long one later
                aq *= decay
                aq += (1 - decay) * q
        out_of_time = time.perf_counter() - started > budget
        if x_val is None or y_val is None:
            if out_of_time:
                break
            continue
        val = _loss(Net(avg[:len(net.weights)], avg[len(net.weights):]), x_val, y_val)
        if val < best[0] - 1e-5:
            best, waited = (val, [q.copy() for q in avg], epoch), 0
        else:
            waited += 1
            if waited >= PATIENCE:
                break
        if out_of_time:
            break
    chosen = best[1] if (x_val is not None and best[2]) else avg
    for q, saved in zip(params, chosen, strict=True):
        q[...] = saved
    if x_val is not None and best[2]:
        net.epochs, net.val_loss = best[2], best[0]
    else:
        net.epochs = epochs
    return net


# ── the ensemble ────────────────────────────────────────────────────────────
@dataclass
class Brainwave:
    """Trained networks plus everything needed to read a new row the same way."""

    nets: list[Net]
    mean: np.ndarray
    scale: np.ndarray
    names: list[str]
    timeout_mean: np.ndarray            # (plans,) what "neither" averaged, in ATR
    base_rates: np.ndarray              # (plans, 3) class frequencies in training
    exam: dict[str, Any] = field(default_factory=dict)
    mask: np.ndarray | None = None      # (features,) True: left out, such as news before the archive covers enough rows
    temperature: float = 1.0            # above 1 softens over-confident chances; set on the validation rows
    threshold: float = MIN_EV           # the edge (ATR) a plan needs before it is taken; set on the validation rows
    threshold_profitable: bool = True   # False: no threshold made money on the validation rows

    def standardise(self, x: np.ndarray) -> np.ndarray:
        z = (np.nan_to_num(x, nan=0.0) - self.mean) / self.scale
        if self.mask is not None:
            z[:, self.mask] = 0.0
        out: np.ndarray = np.clip(z, -6, 6).astype(np.float32)
        return out

    def proba_members(self, x: np.ndarray) -> np.ndarray:
        """(members, rows, plans, 3)."""
        z = self.standardise(x)
        return np.stack([net.proba(z, self.temperature) for net in self.nets])

    def ev(self, proba: np.ndarray) -> np.ndarray:
        """Expected result of each plan in ATR, for probabilities shaped (..., plans, 3)."""
        sl = np.array([p[1] for p in PLANS])
        tp = np.array([p[2] for p in PLANS])
        out: np.ndarray = proba[..., TP] * tp - proba[..., SL] * sl + proba[..., TIMEOUT] * self.timeout_mean
        return out


def _scaling(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    filled = np.nan_to_num(x, nan=0.0)
    mean = filled.mean(axis=0)
    scale = filled.std(axis=0)
    return mean, np.where(scale > 1e-9, scale, 1.0)


def fit(x: np.ndarray, y: np.ndarray, result: np.ndarray, *, x_val: np.ndarray | None = None, y_val: np.ndarray | None = None,
        result_val: np.ndarray | None = None,
        names: list[str], members: int = MEMBERS, seed: int = 7, epochs: int | None = None,
        mask: np.ndarray | None = None, weights: np.ndarray | None = None) -> Brainwave:
    """Rows are in time order, oldest first. ``weights`` default to equal, or to ``recency_weights`` when
    ``TRADING_BRAIN_RECENCY=on`` (every row then counts, but the newest more)."""
    if weights is not None:
        w = np.asarray(weights, dtype=float)
    elif os.getenv("TRADING_BRAIN_RECENCY", "off").strip().lower() in ("on", "1", "true", "ya"):
        w = recency_weights(len(x))
    else:
        w = np.ones(len(x))
    mean, scale = _scaling(x)
    timeout_mean = np.array([float(np.average(result[y[:, p] == TIMEOUT, p], weights=w[y[:, p] == TIMEOUT]))
                             if (y[:, p] == TIMEOUT).any() else 0.0 for p in range(len(PLANS))])
    base = np.stack([np.bincount(y[:, p].astype(int), weights=w, minlength=CLASSES) / w.sum() for p in range(len(PLANS))])
    wave = Brainwave([], mean, scale, list(names), timeout_mean, base, mask=mask)
    z = wave.standardise(x)
    zv = wave.standardise(x_val) if x_val is not None else None
    for k in range(members):
        wave.nets.append(train_net(z, y, zv, y_val, seed=seed + k * 101, epochs=epochs or EPOCHS, hidden=hidden_for(len(x)),
                                   weights=w))
    if x_val is not None and y_val is not None:
        wave.temperature = calibrate(wave, x_val, y_val)
        if result_val is not None:
            wave.threshold, wave.threshold_profitable = choose_threshold(wave, x_val, result_val)
    return wave


THRESHOLDS = (0.08, 0.12, 0.15, 0.2, 0.25, 0.32, 0.4, 0.5, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2)
LCB_Z = 1.0                # the threshold is chosen on mean - LCB_Z * standard error


def edges(wave: Brainwave, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each row's best plan and its edge: the EV the networks agree on (EV less CAUTION times their spread)."""
    members = wave.proba_members(x)
    edge = wave.ev(members.mean(axis=0)) - CAUTION * wave.ev(members).std(axis=0)
    choice = edge.argmax(axis=1)
    return choice, edge[np.arange(len(edge)), choice]


def lower_bound(values: np.ndarray, z: float = LCB_Z) -> float:
    """mean - z * standard error: what the average result is at least, with some confidence."""
    n = len(values)
    if n == 0:
        return -math.inf
    mean = float(values.mean())
    se = float(values.std(ddof=1)) / math.sqrt(n) if n > 1 else abs(mean)
    return mean - z * se


def lowest_paying_threshold(best: np.ndarray, realised: np.ndarray, min_trades: int = 10) -> float:
    """How far the bar may come down: lowering it adds the trades whose edge lies in the band between
    the two bars, and that is allowed only while those added trades paid on their own (mean above 0).

    A total can stay positive while the trades added at the bottom lose: the strong picks above carry
    them. Judging each band by itself stops the brain trading every bar because a few bars are good.
    Bands with fewer than ``min_trades`` trades are joined to the next one down before they are judged.
    """
    floor = THRESHOLDS[-1]
    above = np.zeros(len(best), dtype=bool)
    pending = np.zeros(len(best), dtype=bool)
    for t in sorted(THRESHOLDS, reverse=True):
        take = best > t
        pending |= take & ~above
        above = take
        if pending.sum() < min_trades:
            floor = t if not pending.any() else floor
            continue
        if float(realised[pending].mean()) <= 0:
            return floor
        floor, pending = t, np.zeros(len(best), dtype=bool)
    return floor


def choose_threshold(wave: Brainwave, x_val: np.ndarray, result_val: np.ndarray, min_trades: int = 10) -> tuple[float, bool]:
    """The edge a plan must clear, picked on the validation rows: the best of picking the top of 24 plans
    always looks good (the winner's curse), so the bar is set where the lower confidence bound of the
    picks' total result (n times the bound of their mean) is highest: more trades count only while
    each adds value the rows can vouch for, and a bar with few lucky rows has a wide bound and does not win."""
    choice, best = edges(wave, x_val)
    realised = result_val[np.arange(len(result_val)), choice]
    floor = lowest_paying_threshold(best, realised, min_trades)
    scored = []
    for t in THRESHOLDS:
        take = best > t
        n = int(take.sum())
        if n >= min_trades and t >= floor:
            bound = lower_bound(realised[take])
            scored.append((n * bound, bound, float(realised[take].mean()), t))
    if not scored:
        return THRESHOLDS[-1], False
    _, bound, mean, t = max(scored)
    if mean <= 0:                      # nothing paid: keep the most selective bar and say so
        return THRESHOLDS[-1], False
    return t, bound > 0


def _ensemble_loss(wave: Brainwave, x: np.ndarray, y: np.ndarray) -> float:
    proba = wave.proba_members(x).mean(axis=0)
    picked = np.take_along_axis(proba, y[:, :, None].astype(int), axis=2)[:, :, 0]
    return float(-np.mean(np.log(np.maximum(picked, 1e-9))))


def calibrate(wave: Brainwave, x_val: np.ndarray, y_val: np.ndarray) -> float:
    """The temperature that makes the ensemble's chances fit the validation rows best (temperature scaling),
    found by a golden-section search on log-temperature between 0.5 and 8."""
    def loss_at(log_t: float) -> float:
        wave.temperature = math.exp(log_t)
        return _ensemble_loss(wave, x_val, y_val)

    lo, hi = math.log(0.5), math.log(8.0)
    phi = (math.sqrt(5) - 1) / 2
    a, b = hi - phi * (hi - lo), lo + phi * (hi - lo)
    fa, fb = loss_at(a), loss_at(b)
    for _ in range(24):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - phi * (hi - lo)
            fa = loss_at(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + phi * (hi - lo)
            fb = loss_at(b)
    wave.temperature = 1.0
    best = math.exp((lo + hi) / 2)
    return round(max(0.5, min(8.0, best)), 3)


def calibration(p: np.ndarray, hit: np.ndarray, bins: int = 10) -> dict[str, float]:
    """How honest a set of chances is: Brier score and expected calibration error of P(event) against what happened."""
    p = np.clip(np.asarray(p, dtype=float).ravel(), 0, 1)
    hit = np.asarray(hit, dtype=float).ravel()
    if not len(p):
        return {"brier": math.nan, "ece": math.nan}
    brier = float(np.mean((p - hit) ** 2))
    edges_ = np.linspace(0, 1, bins + 1)
    which = np.clip(np.digitize(p, edges_[1:-1]), 0, bins - 1)
    ece = 0.0
    for k in range(bins):
        inside = which == k
        if inside.any():
            ece += inside.mean() * abs(float(p[inside].mean()) - float(hit[inside].mean()))
    return {"brier": round(brier, 4), "ece": round(float(ece), 4)}


def exam(wave: Brainwave, x: np.ndarray, y: np.ndarray, result: np.ndarray, train_result: np.ndarray) -> dict[str, Any]:
    """The trading test on rows the networks never saw."""
    proba = wave.proba_members(x).mean(axis=0)
    picked = np.take_along_axis(proba, y[:, :, None].astype(int), axis=2)[:, :, 0]
    loss = float(-np.mean(np.log(np.maximum(picked, 1e-9))))
    base = wave.base_rates[None, :, :].repeat(len(y), axis=0)
    base_picked = np.take_along_axis(base, y[:, :, None].astype(int), axis=2)[:, :, 0]
    base_loss = float(-np.mean(np.log(np.maximum(base_picked, 1e-9))))
    choice, best_edge = edges(wave, x)
    take = best_edge > wave.threshold
    realised = result[np.arange(len(result)), choice][take]
    outcome = y[np.arange(len(y)), choice][take]
    static_plan = int(train_result.mean(axis=0).argmax())
    static = result[:, static_plan]
    wins = float(realised[realised > 0].sum())
    losses = float(-realised[realised < 0].sum())
    avg = float(realised.mean()) if len(realised) else 0.0
    buys = int(sum(1 for c in choice[take] if PLANS[c][0] > 0))
    se = float(realised.std(ddof=1)) / math.sqrt(len(realised)) if len(realised) > 1 else 0.0
    cal = calibration(proba[..., TP], y == TP)
    out: dict[str, Any] = {
        "rows": len(y), "trades": int(take.sum()), "trade_rate": round(float(take.mean()), 3) if len(take) else 0.0,
        "buys": buys, "sells": int(take.sum()) - buys,
        "tp_rate": round(float((outcome == TP).mean()), 3) if len(outcome) else None,
        "avg_r_atr": round(avg, 4), "sum_r_atr": round(float(realised.sum()), 3),
        "avg_r_lower": round(lower_bound(realised), 4) if len(realised) > 1 else None,
        "t_stat": round(avg / se, 3) if se > 0 else None,
        "profit_factor": round(wins / losses, 3) if losses else (None if not wins else math.inf),
        "static_plan": _plan_name(static_plan), "static_avg_r_atr": round(float(static.mean()), 4),
        "random_avg_r_atr": round(float(result.mean()), 4),
        "loss": round(loss, 4), "base_loss": round(base_loss, 4), "skill": round(1 - loss / base_loss, 4) if base_loss else 0.0,
        "brier": cal["brier"], "ece": cal["ece"], "temperature": round(float(wave.temperature), 3),
        "threshold_atr": wave.threshold, "threshold_profitable": wave.threshold_profitable,
    }
    out["passed"] = bool(out["trades"] >= MIN_EXAM_TRADES and avg > 0 and avg > out["static_avg_r_atr"]
                         and avg > out["random_avg_r_atr"] and out["skill"] > 0)
    out["verdict"] = _verdict(out)
    if out["profit_factor"] == math.inf:
        out["profit_factor"] = None
    return out


def _verdict(e: dict[str, Any]) -> str:
    if e["trades"] < MIN_EXAM_TRADES:
        return f"belum lulus: baru {e['trades']} trade di ujian (butuh {MIN_EXAM_TRADES})"
    if e["passed"]:
        return (f"lulus: rata-rata {e['avg_r_atr']:+.3f} ATR per trade di {e['trades']} trade ujian, "
                f"di atas selalu-{e['static_plan']} ({e['static_avg_r_atr']:+.3f}) dan acak ({e['random_avg_r_atr']:+.3f})")
    parts = []
    if e["avg_r_atr"] <= 0:
        parts.append(f"rugi rata-rata {e['avg_r_atr']:+.3f} ATR per trade")
    if e["avg_r_atr"] <= e["static_avg_r_atr"]:
        parts.append(f"tidak lebih baik dari selalu-{e['static_plan']} ({e['static_avg_r_atr']:+.3f})")
    if e["skill"] <= 0:
        parts.append("peluangnya tidak lebih tepat dari frekuensi dasar")
    return "belum lulus: " + "; ".join(parts or ["kalah dari pembanding acak"])


def _plan_name(p: int) -> str:
    side, sl, tp = PLANS[p]
    return f"{'buy' if side > 0 else 'sell'} SL{sl:g}/TP{tp:g}"


def plan_name(p: int) -> str:
    return _plan_name(p)


# ── saving ──────────────────────────────────────────────────────────────────
def to_arrays(wave: Brainwave) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {"mean": wave.mean, "scale": wave.scale, "timeout_mean": wave.timeout_mean,
                                  "base_rates": wave.base_rates, "members": np.array(len(wave.nets)),
                                  "mask": wave.mask if wave.mask is not None else np.zeros(len(wave.mean), dtype=bool),
                                  "temperature": np.array(wave.temperature), "threshold": np.array(wave.threshold),
                                  "threshold_profitable": np.array(wave.threshold_profitable)}
    for k, net in enumerate(wave.nets):
        for layer, (w, b) in enumerate(zip(net.weights, net.biases, strict=True)):
            out[f"m{k}_w{layer}"] = w.astype(np.float32)
            out[f"m{k}_b{layer}"] = b.astype(np.float32)
    return out


def from_arrays(data: Any, names: list[str], exam_info: dict[str, Any]) -> Brainwave:
    nets = []
    for k in range(int(data["members"])):
        weights, biases = [], []
        layer = 0
        while f"m{k}_w{layer}" in data:
            weights.append(np.asarray(data[f"m{k}_w{layer}"], dtype=np.float32))
            biases.append(np.asarray(data[f"m{k}_b{layer}"], dtype=np.float32))
            layer += 1
        nets.append(Net(weights, biases))
    mask = np.asarray(data["mask"], dtype=bool) if "mask" in data else None
    return Brainwave(nets, np.asarray(data["mean"]), np.asarray(data["scale"]), names, np.asarray(data["timeout_mean"]),
                     np.asarray(data["base_rates"]), exam_info, mask=mask,
                     temperature=float(data["temperature"]) if "temperature" in data else 1.0,
                     threshold=float(data["threshold"]) if "threshold" in data else MIN_EV,
                     threshold_profitable=bool(data["threshold_profitable"]) if "threshold_profitable" in data else True)


__all__ = ["Brainwave", "Net", "fit", "exam", "train_net", "plan_name", "to_arrays", "from_arrays", "hidden_for", "capacity", "MIN_EV",
           "MIN_ROWS", "recency_weights", "calibration", "lower_bound", "edges", "choose_threshold", "lowest_paying_threshold", "calibrate"]
