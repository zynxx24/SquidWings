"""A GPT-style model for M5 candles that learns straight from how far its guesses land from the real prices.

What it guesses: for each of the next ``horizon`` candles (k = 1..12) the close, the high and the low, measured from
the current close in ATR, as three numbers each: the middle guess (median) and a 10%-90% band.

How it learns: the loss is the error of the guess against the real value, divided by the error of the guess without a
brain (the training data's own median for that candle). So the training number reads like the exam: 1.00 is "no better
than not thinking", 0.95 is "5% closer". The middle guess uses the absolute error, the band the pinball loss of its
quantile. The network starts exactly at the brainless guess and only learns the correction.

The exam per candle (the newest 10% of the bars, never trained on): ``closeness = 100% - error in ATR`` (100% exact, 0%
off by one ATR, about $6 on gold M5, or more) next to the same score of the brainless guess, the error in dollars, how
much of the real high-low range the guessed range covers, the direction against always guessing the more common side,
and how often the real close fell inside the 10-90% band (80% when the band is honest).

Fast epochs: an epoch is one pass over every training bar (each bar scored once), with a context of 128 bars (the bigger
timeframes come in as features), so an epoch takes seconds and a run can do thousands. Against memorising 60k bars:
mirrored samples (up becomes down, high becomes low: twice the data), noise on the inputs, dropout, an averaged (EMA)
copy of the weights for the exam, the best kept apart; ``patience`` epochs without a better exam send it back to the
best at half the learning rate, and when that stops helping it waits for a day of new bars instead of memorising.

    .venv/bin/python -m addons.trading.quant.candle_lm train --fresh       # from zero; until stopped (Ctrl+C or kill saves)
    .venv/bin/python -m addons.trading.quant.candle_lm train               # carry on from best.pt (the most accurate version)
    .venv/bin/python -m addons.trading.quant.candle_lm status              # the exam per candle
    .venv/bin/python -m addons.trading.quant.candle_lm forecast            # the next 12 candles from the newest bars

Files: ``addons/trading/data/candle_lm/<INSTRUMENT>-<size>/`` (best.pt = only the most accurate version, log.jsonl, status.json).
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import math
import os
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F  # noqa: N812
from numpy.lib.stride_tricks import sliding_window_view
from torch import nn
from torch.optim.swa_utils import AveragedModel

from addons.trading.quant import m5store
from brain.utils.platform_compat import lower_own_priority

ARCH = 4                     # bump when a checkpoint of the previous design can no longer be loaded
RUNS = Path(__file__).resolve().parents[1] / "data" / "candle_lm"
ATR_N, H1, H4, DAY = 14, 12, 48, 288
WARMUP_BARS = DAY + ATR_N + 2
N_FEATURES = 19
CLIP = 10.0
QUANTILES = (0.1, 0.5, 0.9)
Q_WEIGHT = (0.5, 1.0, 0.5)
SKIP = 16                    # the first bars of a window have too little behind them to be scored
MIRROR_NEGATE = [0, 1, 2, 3, 11, 12, 13, 14, 15, 16, 18]   # features that flip sign when up becomes down
SYNC_EVERY_S = 900
WAIT_CHECK_S = 900
NEW_BARS_TO_RESUME = 288     # a day of new bars wakes a run that was waiting
# Benchmark 2026-11-22 (Core 5 120U, base): 2 threads 1.05 s/step, 3-4 = 1.3-1.6 (E-cores drag), 10 = 0.85.
# The user keeps all-but-two cores; --threads 2 or CANDLE_LM_THREADS=2 is the light choice.
DEFAULT_THREADS = max(1, (os.cpu_count() or 2) - 2)
SAVE_BEST_EVERY_S = 120      # only best.pt is written (no last.pt), and at most this often while it keeps improving


def _go_light(threads: int) -> int:
    """Run as a background guest: lowest CPU and disk priority (full speed while the laptop is idle), no compile swarm."""
    n = threads or int(os.environ.get("CANDLE_LM_THREADS") or 0) or DEFAULT_THREADS
    torch.set_num_threads(n)
    with contextlib.suppress(RuntimeError):                      # only allowed before any parallel work
        torch.set_num_interop_threads(1)
    os.environ.setdefault("TORCHINDUCTOR_COMPILE_THREADS", "1")
    lower_own_priority(19)                                       # CLI runs too, not only the panel's niced start
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())], check=False, capture_output=True, timeout=5)
    return n


@dataclass
class Config:
    size: str = "base"
    arch: int = ARCH
    ctx: int = 128
    d_model: int = 192
    layers: int = 4
    heads: int = 6
    dropout: float = 0.15
    horizon: int = 12
    batch: int = 64
    lr: float = 1e-3
    weight_decay: float = 0.05
    warmup_steps: int = 200
    ema_decay: float = 0.99
    input_noise: float = 0.05
    mirror: float = 0.5             # share of the training samples shown mirrored
    patience: int = 25              # epochs without a better exam before going back to the best at half the rate
    min_lr_scale: float = 1 / 32    # below this the run waits for new bars
    val_fraction: float = 0.10


SIZES = {
    "fast": dict(ctx=96, d_model=128, layers=3, heads=4, dropout=0.1, batch=64),
    "base": dict(ctx=128, d_model=192, layers=4, heads=6, dropout=0.15, batch=64),
    "large": dict(ctx=256, d_model=256, layers=6, heads=8, dropout=0.2, batch=32, lr=5e-4),
    "xl": dict(ctx=512, d_model=384, layers=8, heads=8, dropout=0.25, batch=16, lr=3e-4),
}


def make_config(size: str, **overrides: Any) -> Config:
    return Config(size=size, **{**SIZES[size], **overrides})


# ── settings the lab page edits ───────────────────────────────────────────────
# settings.json in the run folder holds the user's choices. When each one takes effect:
#   LIVE: read again after every epoch of a running trainer (the optimiser, the samples and the dropout follow at once);
#   RESTART: on the next start, weights kept (the exam split moves, so the best is scored again);
#   FRESH: the shape of the network, only when it starts from zero.
# RUN holds how the process runs (threads, time and epoch limits, compile); the panel's Start reads it.
LIVE_KEYS = ("lr", "weight_decay", "warmup_steps", "batch", "input_noise", "mirror", "dropout", "ema_decay", "patience",
             "min_lr_scale")
RESTART_KEYS = ("val_fraction",)
FRESH_KEYS = ("ctx", "d_model", "layers", "heads")
RUN_KEYS = ("threads", "hours", "epochs", "compile")
LIMITS: dict[str, tuple[float, float, type]] = {
    "lr": (1e-5, 1e-2, float), "weight_decay": (0.0, 0.5, float), "warmup_steps": (1, 5000, int), "batch": (8, 256, int),
    "input_noise": (0.0, 0.5, float), "mirror": (0.0, 1.0, float), "dropout": (0.0, 0.5, float),
    "ema_decay": (0.9, 0.9999, float), "patience": (1, 500, int), "min_lr_scale": (1 / 1024, 0.5, float),
    "val_fraction": (0.05, 0.3, float),
    "ctx": (32, 1024, int), "d_model": (64, 768, int), "layers": (1, 16, int), "heads": (1, 16, int),
    "threads": (0, 64, int), "hours": (0.0, 720.0, float), "epochs": (0, 1_000_000, int),
}


def clean_settings(values: dict[str, Any], size: str) -> dict[str, Any]:
    """Only known keys, inside their limits, with a network shape that can be built. Raises ValueError otherwise."""
    out: dict[str, Any] = {}
    for key, raw in values.items():
        if key == "compile":
            if raw is not None and not isinstance(raw, bool):
                raise ValueError("compile: true, false atau kosong (otomatis)")
            out[key] = raw
            continue
        if key not in LIMITS:
            raise ValueError(f"setelan tidak dikenal: {key}")
        if raw is None or raw == "":
            continue
        low, high, kind = LIMITS[key]
        try:
            value = kind(raw) if kind is float else int(float(raw))
        except (TypeError, ValueError):
            raise ValueError(f"{key}: bukan angka") from None
        if not (low <= value <= high) or (kind is float and not math.isfinite(value)):
            raise ValueError(f"{key}: harus {low:g} sampai {high:g}")
        out[key] = value
    shape = {**SIZES.get(size, SIZES["base"]), **{k: out[k] for k in FRESH_KEYS if k in out}}
    d, heads = int(shape["d_model"]), int(shape["heads"])
    if d % heads or (d // heads) % 2:
        raise ValueError(f"d_model {d} harus habis dibagi heads {heads}, dengan hasil genap")
    return out


def settings_path(folder: Path) -> Path:
    return folder / "settings.json"


def read_settings(folder: Path) -> tuple[dict[str, Any], float]:
    """(the saved values, the file's mtime) or ({}, 0) when nothing was saved."""
    path = settings_path(folder)
    with contextlib.suppress(OSError, ValueError):
        return dict(json.loads(path.read_text(encoding="utf-8")).get("values") or {}), path.stat().st_mtime
    return {}, 0.0


def write_settings(folder: Path, values: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    tmp = settings_path(folder).with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"values": values, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=1), encoding="utf-8")
    tmp.replace(settings_path(folder))


def send_control(folder: Path, cmd: str) -> None:
    """Ask a running trainer to do something after its current epoch: reset_lr (full rate again, wakes a waiting run)
    or save (write best.pt now)."""
    if cmd not in ("reset_lr", "save"):
        raise ValueError("perintah: reset_lr atau save")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "control.json").write_text(json.dumps({"cmd": cmd, "at": time.time()}), encoding="utf-8")


def _take_control(folder: Path) -> str | None:
    path = folder / "control.json"
    with contextlib.suppress(OSError, ValueError):
        cmd = json.loads(path.read_text(encoding="utf-8")).get("cmd")
        path.unlink(missing_ok=True)
        return cmd if isinstance(cmd, str) else None
    return None


def _apply_live(cfg: Config, model: nn.Module, ema: Any, values: dict[str, Any]) -> list[str]:
    """Put the LIVE settings into the running config, the dropout layers and the EMA. Returns what changed."""
    changed = []
    for key in LIVE_KEYS:
        if key in values and getattr(cfg, key) != values[key]:
            changed.append(f"{key} {getattr(cfg, key):g}→{values[key]:g}")
            setattr(cfg, key, values[key])
    if any(c.startswith("dropout") for c in changed):
        for module in model.modules():
            if isinstance(module, nn.Dropout):
                module.p = cfg.dropout
            if isinstance(module, Block):
                module.p = cfg.dropout
    if any(c.startswith("ema_decay") for c in changed):
        ema.avg_fn = _ema_fn(cfg.ema_decay)
    return changed


# ── data ──────────────────────────────────────────────────────────────────────
def _prev_mean(x: np.ndarray, w: int) -> np.ndarray:
    """mean of x[i-w:i] at every i (nan before there are w values)."""
    c = np.concatenate([[0.0], np.cumsum(x)])
    out = np.full(len(x), np.nan)
    out[w:] = (c[w:-1] - c[:-w - 1]) / w
    return out


def _rolling(x: np.ndarray, w: int, fn: Any) -> np.ndarray:
    """fn over x[i-w+1:i+1] (this bar included), nan before there are w values."""
    out = np.full(len(x), np.nan)
    if len(x) >= w:
        out[w - 1:] = fn(sliding_window_view(x, w), axis=1)
    return out


def _back(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[n:] = x[:-n]
    return out


def features(bars: np.ndarray) -> dict[str, np.ndarray]:
    """bars: [N, 6] t,o,h,l,c,v. Per-bar features (bar i uses bars <= i only), and what the targets are made from."""
    t, o, h, low, c, v = (bars[:, k] for k in range(6))
    prev = np.concatenate([[o[0]], c[:-1]])
    tr = np.maximum(h, prev) - np.minimum(low, prev)
    rel_atr = _prev_mean(tr, ATR_N) / prev                       # known before bar i opens
    rel_atr = np.where(rel_atr > 0, rel_atr, np.nan)
    move = lambda x: np.log(x / prev) / rel_atr                  # noqa: E731
    r_o, r_h, r_l, r_c = move(o), move(h), move(low), move(c)
    lv = np.log1p(v)
    lv_mean = _prev_mean(lv, DAY)
    lv_std = np.sqrt(np.maximum(_prev_mean(lv * lv, DAY) - lv_mean ** 2, 1e-6))
    la = np.log(rel_atr)
    la_mean = _prev_mean(np.nan_to_num(la, nan=0.0), DAY)
    minute = (t % 86400) / 86400 * 2 * math.pi
    dow = ((t // 86400 + 3) % 7) / 7 * 2 * math.pi               # 1970-01-01 was a Thursday
    gap = np.log1p(np.maximum(0.0, np.diff(t, prepend=t[0] - 300) / 300 - 1))
    atr_c = c * rel_atr                                          # one ATR in price, at this bar
    ret = lambda n: np.log(c / _back(c, n)) / rel_atr / math.sqrt(n)   # noqa: E731  - a random walk's move is ~sqrt(n)

    def place(n: int) -> np.ndarray:                             # -1 at the low of the last n bars, +1 at the high
        hi, lo = _rolling(h, n, np.max), _rolling(low, n, np.min)
        return 2 * (c - lo) / np.maximum(hi - lo, 1e-9) - 1

    day_key = t // 86400
    first = np.r_[True, day_key[1:] != day_key[:-1]]
    day_open = o[np.maximum.accumulate(np.where(first, np.arange(len(t)), 0))]
    width_h4 = np.log(np.maximum(_rolling(h, H4, np.max) - _rolling(low, H4, np.min), 1e-9) / atr_c) - math.log(math.sqrt(H4))
    x = np.stack([r_o, r_h, r_l, r_c, (lv - lv_mean) / lv_std, la - la_mean,
                  np.sin(minute), np.cos(minute), np.sin(dow), np.cos(dow), gap,
                  ret(3), ret(H1), ret(H4), ret(DAY), place(H4), place(DAY), width_h4,
                  (c - day_open) / atr_c / 10], axis=1)
    x = np.clip(np.nan_to_num(x, nan=0.0, posinf=CLIP, neginf=-CLIP), -CLIP, CLIP).astype(np.float32)
    return {"x": x, "r_c": r_c, "rel_atr": rel_atr, "close": c, "high": h, "low": low, "t": t}


def future_moves(f: dict[str, np.ndarray], horizon: int) -> tuple[np.ndarray, np.ndarray]:
    """[N, horizon*3]: candle k's close, high and low from bar i's close, in the ATR known at bar i; and that ATR in price."""
    n = len(f["close"])
    atr = np.r_[f["rel_atr"][1:], np.nan]                        # the ATR of bar i+1, made of bars <= i
    raw = np.full((n, horizon, 3), np.nan)
    c = f["close"]
    for k in range(1, horizon + 1):
        for j, series in enumerate((c, f["high"], f["low"])):
            raw[:-k, k - 1, j] = np.log(series[k:] / c[:-k]) / atr[:-k]
    return raw.reshape(n, horizon * 3), atr * c


def mirror(x: torch.Tensor, y: torch.Tensor, horizon: int) -> tuple[torch.Tensor, torch.Tensor]:
    """Up becomes down: price moves flip sign and high and low swap places, in the features and in the targets."""
    xm = x.clone()
    xm[..., MIRROR_NEGATE] *= -1
    xm[..., [1, 2]] = xm[..., [2, 1]]
    ym = -y.view(*y.shape[:-1], horizon, 3)[..., [0, 2, 1]]
    return xm, ym.reshape(y.shape)


class Dataset:
    """Features, targets and the train/exam split of one instrument's M5 store."""

    def __init__(self, bars: np.ndarray, cfg: Config, stats: dict[str, list[float]] | None = None) -> None:
        f = features(bars)
        self.cfg, self.n = cfg, len(bars)
        self.x = f["x"]
        raw, self.unit = future_moves(f, cfg.horizon)
        self.valid = np.isfinite(raw) & np.isfinite(self.unit)[:, None]
        self.y = np.nan_to_num(raw).astype(np.float32)
        self.n_targets = cfg.horizon * 3
        self.offset = np.repeat(np.arange(1, cfg.horizon + 1), 3)      # target j looks this many bars ahead
        self.start = WARMUP_BARS
        self.val_start = self.n - max(cfg.ctx, int((self.n - self.start) * cfg.val_fraction))
        self.train_end = self.val_start - cfg.ctx                      # a gap of one window between train and exam
        if self.train_end - self.start < cfg.ctx * 4:
            raise ValueError(f"terlalu sedikit bar ({self.n}) untuk ctx {cfg.ctx}")
        if stats is None:                                              # the brainless guess and its error, from training only
            tr = raw[self.start:self.train_end]
            median = np.nanmedian(tr, axis=0)
            stats = {"median": median.tolist(), "scale": np.nanmean(np.abs(tr - median), axis=0).tolist()}
        self.stats = stats

    def gather(self, starts: np.ndarray, masked_from: int | None = None, skip: int = 0
               ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, np.ndarray]:
        idx = starts[:, None] + np.arange(self.cfg.ctx)[None, :]
        ok = self.valid[idx].copy()                                     # [B, ctx, targets]
        if masked_from is not None:                                     # no target may look into the exam
            ok &= idx[..., None] + self.offset[None, None, :] < masked_from
        ok[:, :skip] = False
        return torch.from_numpy(self.x[idx]), torch.from_numpy(self.y[idx]), torch.from_numpy(ok), idx

    def epoch(self, rng: np.random.Generator) -> list[np.ndarray]:
        """Batches of window starts that score every training bar once, tiled from a random offset, shuffled."""
        stride = self.cfg.ctx - SKIP
        starts = np.arange(self.start + int(rng.integers(0, stride)), self.train_end - self.cfg.ctx + 1, stride)
        rng.shuffle(starts)
        return [starts[i:i + self.cfg.batch] for i in range(0, len(starts), self.cfg.batch)]

    def val_windows(self) -> list[np.ndarray]:
        """Windows whose newest half covers the exam once; the older half is context only."""
        half = self.cfg.ctx // 2
        starts = np.arange(self.val_start - half, self.n - self.cfg.ctx + 1, half)
        return [starts[i:i + 64] for i in range(0, len(starts), 64)]


# ── model ─────────────────────────────────────────────────────────────────────
class RMSNorm(nn.Module):
    def __init__(self, d: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.eps, self.weight = eps, nn.Parameter(torch.ones(d))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + self.eps) * self.weight


def _rope(n: int, dim: int, base: float = 10000.0) -> tuple[torch.Tensor, torch.Tensor]:
    inv = 1.0 / base ** (torch.arange(0, dim, 2).float() / dim)
    ang = torch.outer(torch.arange(n).float(), inv)
    return ang.cos(), ang.sin()


def _apply_rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    a, b = x[..., 0::2], x[..., 1::2]
    return torch.stack([a * cos - b * sin, a * sin + b * cos], -1).flatten(-2)


class Block(nn.Module):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        d, self.h = cfg.d_model, cfg.heads
        hidden = int(math.ceil(8 * d / 3 / 64) * 64)
        self.n1, self.n2 = RMSNorm(d), RMSNorm(d)
        self.qkv, self.out = nn.Linear(d, 3 * d, bias=False), nn.Linear(d, d, bias=False)
        self.w1, self.w3, self.w2 = nn.Linear(d, hidden, bias=False), nn.Linear(d, hidden, bias=False), nn.Linear(hidden, d, bias=False)
        self.p = cfg.dropout
        self.drop = nn.Dropout(cfg.dropout)

    def forward(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
        b, n, d = x.shape
        q, k, v = self.qkv(self.n1(x)).view(b, n, 3, self.h, d // self.h).permute(2, 0, 3, 1, 4)
        q, k = _apply_rope(q, cos, sin), _apply_rope(k, cos, sin)
        a = F.scaled_dot_product_attention(q, k, v, is_causal=True, dropout_p=self.p if self.training else 0.0)
        x = x + self.drop(self.out(a.transpose(1, 2).reshape(b, n, d)))
        y = self.n2(x)
        return x + self.drop(self.w2(F.silu(self.w1(y)) * self.w3(y)))


class CandleGPT(nn.Module):
    """Gives [B, n, targets, 3]: the 10%, 50% and 90% guesses of each target, in ATR from the current close."""

    def __init__(self, cfg: Config, stats: dict[str, list[float]]) -> None:
        super().__init__()
        self.cfg = cfg
        d = cfg.d_model
        self.targets = cfg.horizon * 3
        self.inp = nn.Linear(N_FEATURES, d)
        self.drop = nn.Dropout(cfg.dropout)
        self.blocks = nn.ModuleList(Block(cfg) for _ in range(cfg.layers))
        self.norm = RMSNorm(d)
        self.head = nn.Linear(d, self.targets * 3)
        cos, sin = _rope(cfg.ctx, d // cfg.heads)
        self.register_buffer("cos", cos, persistent=False)
        self.register_buffer("sin", sin, persistent=False)
        self.register_buffer("center", torch.tensor(stats["median"], dtype=torch.float32))
        self.register_buffer("scale", torch.tensor(stats["scale"], dtype=torch.float32))
        for name, p in self.named_parameters():
            if p.dim() == 2:
                std = 0.02 / math.sqrt(2 * cfg.layers) if name.endswith(("out.weight", "w2.weight")) else 0.02
                nn.init.normal_(p, 0.0, std)
            elif name.endswith("bias"):
                nn.init.zeros_(p)
        nn.init.zeros_(self.head.weight)                    # start exactly at the brainless guess
        with torch.no_grad():                               # with a band of about +-1.3 brainless errors
            self.head.bias.view(self.targets, 3)[:, 0] = 0.5
            self.head.bias.view(self.targets, 3)[:, 2] = 0.5

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, n, _ = x.shape
        h = self.drop(self.inp(x))
        cos, sin = self.cos[:n], self.sin[:n]
        for block in self.blocks:
            h = block(h, cos, sin)
        out = self.head(self.norm(h)).view(b, n, self.targets, 3)
        mid = out[..., 1]
        q = torch.stack([mid - F.softplus(out[..., 0]), mid, mid + F.softplus(out[..., 2])], -1)
        return self.center[:, None] + self.scale[:, None] * q


def n_params(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


def ratio_loss(q: torch.Tensor, y: torch.Tensor, ok: torch.Tensor, scale: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """The pinball loss of each quantile over the brainless error, and the middle guess's error ratio (for the log)."""
    e = (y.unsqueeze(-1) - q) / scale[:, None]                      # [B, n, T, 3]
    tau = torch.tensor(QUANTILES, dtype=q.dtype)
    pin = torch.maximum(tau * e, (tau - 1) * e) * torch.tensor(Q_WEIGHT, dtype=q.dtype)
    m = ok.to(q.dtype)
    count = m.sum().clamp(min=1)
    loss = (pin.sum(-1) * m).sum() / count / (0.5 * sum(Q_WEIGHT))
    ratio = (e[..., 1].abs() * m).sum() / count
    return loss, ratio


# ── exam ──────────────────────────────────────────────────────────────────────
@torch.no_grad()
def evaluate(model: nn.Module, data: Dataset) -> dict[str, Any]:
    """Closeness per candle ahead, next to the same score of the guess without a brain."""
    was = model.training
    model.eval()
    hor, half, T = data.cfg.horizon, data.cfg.ctx // 2, data.n_targets  # noqa: N806
    base = torch.tensor(data.stats["median"], dtype=torch.float64)
    sums = {k: torch.zeros(T, dtype=torch.float64) for k in ("err", "err_b", "close", "close_b", "usd", "usd_b", "inband", "n")}
    per_k = {k: torch.zeros(hor, dtype=torch.float64) for k in ("iou", "iou_b", "hit", "dir_n", "ups", "n")}
    for starts in data.val_windows():
        x, y, ok, idx = data.gather(starts, skip=half)
        ok &= torch.from_numpy(idx >= data.val_start)[..., None]
        q = model(x).double()
        y = y.double()
        unit = torch.nan_to_num(torch.from_numpy(data.unit[idx]))[..., None]
        m = ok.double()
        err, err_b = (q[..., 1] - y).abs(), (base - y).abs()
        for key, value in (("err", err), ("err_b", err_b), ("close", (1 - err).clamp(min=0)), ("close_b", (1 - err_b).clamp(min=0)),
                           ("usd", err * unit), ("usd_b", err_b * unit), ("inband", ((q[..., 0] <= y) & (y <= q[..., 2])).double())):
            sums[key] += (value * m).sum((0, 1))
        sums["n"] += m.sum((0, 1))
        mid, y3, b3 = q[..., 1].view(*q.shape[:2], hor, 3), y.view(*y.shape[:2], hor, 3), base.view(hor, 3)
        ok3 = ok.view(*ok.shape[:2], hor, 3).all(-1)
        lo_a, hi_a = y3[..., 2], y3[..., 1]
        for key, (lo_p, hi_p) in (("iou", (torch.minimum(mid[..., 2], mid[..., 0]), torch.maximum(mid[..., 1], mid[..., 0]))),
                                  ("iou_b", (b3[:, 2].expand_as(lo_a), b3[:, 1].expand_as(hi_a)))):
            inter = (torch.minimum(hi_p, hi_a) - torch.maximum(lo_p, lo_a)).clamp(min=0)
            union = (torch.maximum(hi_p, hi_a) - torch.minimum(lo_p, lo_a)).clamp(min=1e-9)
            per_k[key] += torch.where(ok3, inter / union, 0.0).sum((0, 1))
        moved = ok3 & (y3[..., 0] != 0)
        per_k["hit"] += (((mid[..., 0] > 0) == (y3[..., 0] > 0)) & moved).double().sum((0, 1))
        per_k["dir_n"] += moved.double().sum((0, 1))
        per_k["ups"] += ((y3[..., 0] > 0) & moved).double().sum((0, 1))
        per_k["n"] += ok3.double().sum((0, 1))
    model.train(was)
    n = sums["n"].clamp(min=1)
    mean = {k: (sums[k] / n).view(hor, 3) for k in sums if k != "n"}
    per_candle = []
    for k in range(hor):
        dn, cnt = max(1.0, float(per_k["dir_n"][k])), max(1.0, float(per_k["n"][k]))
        share = float(per_k["ups"][k]) / dn
        per_candle.append({
            "candle": k + 1,
            "closeness": round(100 * float(mean["close"][k].mean()), 2),
            "closeness_no_brain": round(100 * float(mean["close_b"][k].mean()), 2),
            "gain_points": round(100 * float(mean["close"][k].mean() - mean["close_b"][k].mean()), 3),
            "error_usd_close": round(float(mean["usd"][k, 0]), 3),
            "error_usd_close_no_brain": round(float(mean["usd_b"][k, 0]), 3),
            "range_overlap": round(100 * float(per_k["iou"][k]) / cnt, 2),
            "range_overlap_no_brain": round(100 * float(per_k["iou_b"][k]) / cnt, 2),
            "direction_hit": round(100 * float(per_k["hit"][k]) / dn, 2),
            "direction_majority": round(100 * max(share, 1 - share), 2),
            "band_80_hit": round(100 * float(mean["inband"][k, 0]), 1),
        })
    rel = float(sums["err"].sum() / sums["err_b"].sum())        # the model's error as a share of the brainless error
    edge = rel < 0.99 and per_candle[0]["gain_points"] > 0
    lines = ", ".join(f"+{c['candle']}: {c['closeness']:.1f}% ({c['gain_points']:+.2f})" for c in per_candle[:3])
    return {"score": round(rel, 5), "edge": edge,
            "closeness_avg": round(float(np.mean([c["closeness"] for c in per_candle])), 3),
            "gain_avg_points": round(float(np.mean([c["gain_points"] for c in per_candle])), 3),
            "per_candle": per_candle, "exam_bars": int(sums["n"][0]),
            "verdict": (f"{'LEBIH DEKAT' if edge else 'belum lebih dekat'} dari tebakan tanpa otak: error {rel:.2%} "
                        f"dari error tanpa otak; kedekatan {lines} poin vs tanpa otak")}


# ── training ──────────────────────────────────────────────────────────────────
def run_dir(instrument: str, size: str) -> Path:
    return RUNS / f"{instrument}-{size}"


def _lr(cfg: Config, step: int, scale: float) -> float:
    return cfg.lr * scale * min(1.0, (step + 1) / cfg.warmup_steps)


def _save(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    torch.save(payload, tmp)
    tmp.replace(path)


def _load_bars(symbol: str, sync: bool) -> tuple[np.ndarray, dict[str, Any]]:
    report = m5store.sync(symbol) if sync else {}
    bars = np.array(m5store.load(symbol), dtype=np.float64)
    if len(bars) == 0:
        raise SystemExit("gudang M5 kosong: jalankan  python -m addons.trading.quant.m5store XAUUSD")
    return bars, report


def _optimizer(model: nn.Module, cfg: Config) -> torch.optim.Optimizer:
    decay = [p for p in model.parameters() if p.dim() >= 2]
    rest = [p for p in model.parameters() if p.dim() < 2]
    return torch.optim.AdamW([{"params": decay, "weight_decay": cfg.weight_decay}, {"params": rest, "weight_decay": 0.0}],
                             lr=cfg.lr, betas=(0.9, 0.95))


def _ema_fn(decay: float) -> Any:
    """An EMA whose decay grows from 0.1 to ``decay``, so the average follows the model from the first steps."""
    def avg(averaged: torch.Tensor, current: torch.Tensor, n: torch.Tensor) -> torch.Tensor:
        d = min(decay, (1 + float(n)) / (10 + float(n)))
        return d * averaged + (1 - d) * current
    return avg


def _print_exam(exam: dict[str, Any], every: int = 1) -> None:
    print("   candle | dekat   tanpa otak  selisih | error $ tanpa otak | range  tanpa otak | arah  mayoritas | pita80", flush=True)
    for c in exam["per_candle"][::every]:
        print(f"   +{c['candle']:<6} | {c['closeness']:5.1f}%  {c['closeness_no_brain']:5.1f}%  {c['gain_points']:+6.2f} | "
              f"{c['error_usd_close']:6.2f}  {c['error_usd_close_no_brain']:6.2f}   | {c['range_overlap']:5.1f}% {c['range_overlap_no_brain']:5.1f}%"
              f"      | {c['direction_hit']:5.1f}% {c['direction_majority']:5.1f}% | {c['band_80_hit']:5.1f}%", flush=True)


def train(symbol: str = "XAUUSD", size: str = "base", hours: float = 0.0, epochs: int = 0, threads: int = 0,
          fresh: bool = False, seed: int = 0, compile_model: bool | None = None, overrides: dict[str, Any] | None = None) -> None:
    name = m5store.instrument(symbol)
    folder = run_dir(name, size)
    folder.mkdir(parents=True, exist_ok=True)
    threads = _go_light(threads)
    torch.manual_seed(seed)
    best_path = folder / "best.pt"
    for leftover in folder.glob("*.tmp"):                            # a save cut off half way
        leftover.unlink(missing_ok=True)
    if fresh:                                                        # from zero: the old run leaves nothing behind
        for old in folder.iterdir():
            if old.is_dir() and old.name.startswith("old-"):
                shutil.rmtree(old, ignore_errors=True)
            elif old.name in ("last.pt", "best.pt", "log.jsonl", "status.json"):
                old.unlink(missing_ok=True)
    state = None if fresh or not best_path.exists() else torch.load(best_path, weights_only=False)
    if state and state["config"].get("arch") != ARCH:
        raise SystemExit(f"checkpoint di {folder} dari desain model lama: tambahkan --fresh untuk mulai ulang")
    saved, settings_mtime = read_settings(folder)
    if state:                                                        # weights kept: the shape stays, the rest may change
        cfg = Config(**{**state["config"], **{k: saved[k] for k in (*LIVE_KEYS, *RESTART_KEYS) if k in saved},
                        **{k: v for k, v in (overrides or {}).items() if k not in FRESH_KEYS}})
    else:
        cfg = make_config(size, **{k: saved[k] for k in (*LIVE_KEYS, *RESTART_KEYS, *FRESH_KEYS) if k in saved},
                          **(overrides or {}))
    _take_control(folder)                                            # a request left over from an earlier run
    bars, report = _load_bars(name, sync=True)
    data = Dataset(bars, cfg, state["stats"] if state else None)
    model = CandleGPT(cfg, data.stats)
    ema = AveragedModel(model, avg_fn=_ema_fn(cfg.ema_decay), use_buffers=True)
    opt = _optimizer(model, cfg)
    step, epoch, best, lr_scale, bad, waiting = 0, 0, float("inf"), 1.0, 0, False
    best_state: dict[str, torch.Tensor] | None = None
    best_at = (0, 0)                                                 # epoch and step of the best version
    if state:                                                        # resume from the most accurate version
        model.load_state_dict(state["model"])
        ema.module.load_state_dict(state["model"])
        best_state = {k: v.detach().clone() for k, v in state["model"].items()}
        best = evaluate(ema.module, data)["score"]                   # scored again: the exam window moves with new bars
        run: dict[str, Any] = {}
        with contextlib.suppress(OSError, ValueError):
            run = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        epoch = int(run.get("epoch") or state.get("epoch") or 0)
        step = int(run.get("step") or state.get("step") or 0)
        lr_scale, waiting = float(run.get("lr_scale") or 1.0), bool(run.get("waiting"))
        best_at = (int(state.get("epoch") or 0), int(state.get("step") or 0))
    rng = np.random.default_rng(seed + epoch)
    if compile_model is None:
        compile_model = cfg.size in ("large", "xl")
    forward = model
    if compile_model:
        try:
            forward = torch.compile(model)
        except Exception as exc:  # noqa: BLE001 - compiling is only for speed
            print(f"[candle_lm] torch.compile gagal, jalan tanpa compile: {exc}", flush=True)
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    steps_per_epoch = len(data.epoch(np.random.default_rng(0)))
    applied: list[str] = []
    print(f"[candle_lm] {name} {size}: {n_params(model) / 1e6:.2f}M parameter, ctx {cfg.ctx}, {data.n} bar M5 "
          f"({report.get('first')} .. {report.get('last')}), latih {data.train_end - data.start} bar, ujian {data.n - data.val_start} bar, "
          f"{steps_per_epoch} langkah/epoch, {threads} thread; {'lanjut dari epoch ' + str(epoch) if state else 'mulai dari nol'}",
          flush=True)

    stop = {"now": False}

    def _stop(*_: Any) -> None:
        stop["now"] = True
        print("[candle_lm] berhenti, menyimpan...", flush=True)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    deadline = time.time() + hours * 3600 if hours > 0 else float("inf")
    start_epoch = epoch
    synced_at = best_saved_at = time.time()
    best_dirty = False
    last_table = -10**9

    def save_best(exam: dict[str, Any]) -> None:
        _save(best_path, {"config": asdict(cfg), "stats": data.stats, "model": best_state, "epoch": best_at[0], "step": best_at[1],
                          "exam": exam, "instrument": name, "bars": data.n})

    def rebase(new_bars: np.ndarray) -> None:
        """New bars move the exam to the newest 10%: score the best weights again so 'better' still means better."""
        nonlocal data, best
        data = Dataset(new_bars, cfg, data.stats)
        if best_state is not None:
            probe = copy.deepcopy(ema.module)
            probe.load_state_dict(best_state)
            best = evaluate(probe, data)["score"]

    def wait_for_bars() -> bool:
        """Sleep until a day of new bars arrives (True) or the run is stopped (False)."""
        print(f"[candle_lm] sudah mentok di data ini: menunggu minimal {NEW_BARS_TO_RESUME} bar baru "
              f"(cek tiap {WAIT_CHECK_S // 60} menit)", flush=True)
        while not stop["now"] and time.time() < deadline:
            for _ in range(max(1, WAIT_CHECK_S // 5)):
                if stop["now"] or time.time() >= deadline:
                    return False
                if (folder / "control.json").exists():
                    break
                time.sleep(5)
            if (folder / "control.json").exists():                   # the lab page asked it to go on now
                return True
            with contextlib.suppress(Exception):
                new_bars, _ = _load_bars(name, sync=True)
                if len(new_bars) - data.n >= NEW_BARS_TO_RESUME:
                    print(f"[candle_lm] {len(new_bars) - data.n} bar baru: belajar lagi", flush=True)
                    rebase(new_bars)
                    return True
        return False

    best_exam: dict[str, Any] | None = None
    model.train()
    while not stop["now"] and time.time() < deadline and (epochs <= 0 or epoch - start_epoch < epochs):
        if waiting:
            if not wait_for_bars():
                break
            waiting, lr_scale, bad = False, 0.5, 0
            if best_state is not None:
                model.load_state_dict(best_state)
                ema.module.load_state_dict(best_state)
            opt = _optimizer(model, cfg)
        elif time.time() - synced_at > SYNC_EVERY_S:
            synced_at = time.time()
            with contextlib.suppress(Exception):
                new_bars, rep = _load_bars(name, sync=True)
                if len(new_bars) != data.n:
                    rebase(new_bars)
                    print(f"[candle_lm] data baru: {rep.get('added', 0)} bar ditambah, total {data.n}", flush=True)
        began, losses, ratios = time.time(), [], []
        for starts in data.epoch(rng):
            if stop["now"] or time.time() >= deadline:
                break
            for group in opt.param_groups:
                group["lr"] = _lr(cfg, step, lr_scale)
            x, y, ok, _ = data.gather(starts, data.train_end, skip=SKIP)
            if cfg.mirror > 0:
                flip = torch.rand(x.shape[0]) < cfg.mirror
                if flip.any():
                    xm, ym = mirror(x[flip], y[flip], cfg.horizon)
                    x, y = x.clone(), y.clone()
                    x[flip], y[flip] = xm, ym
            x = x + cfg.input_noise * torch.randn_like(x)
            loss, ratio = ratio_loss(forward(x), y, ok, model.scale)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            ema.update_parameters(model)
            losses.append(loss.item())
            ratios.append(ratio.item())
            step += 1
        if not losses:
            break
        epoch += 1
        exam = evaluate(ema.module, data)
        improved = exam["score"] < best
        if improved:
            best, bad, best_exam, best_dirty = exam["score"], 0, exam, True
            best_at = (epoch, step)
            best_state = {k: v.detach().clone() for k, v in ema.module.state_dict().items()}
        else:
            bad += 1
        note = ""
        if bad >= cfg.patience and best_state is not None:          # going nowhere: back to the best, slower
            lr_scale, bad = lr_scale / 2, 0
            model.load_state_dict(best_state)
            ema.module.load_state_dict(best_state)
            opt = _optimizer(model, cfg)
            note = f" | kembali ke versi terbaik, LR x{lr_scale:g}"
            if lr_scale < cfg.min_lr_scale:
                waiting, note = True, " | mentok: tunggu data baru"
        cmd = _take_control(folder)
        if cmd == "reset_lr":
            lr_scale, bad, waiting = 1.0, 0, False
            note += " | LR kembali penuh (dari panel)"
        elif cmd == "save" and best_exam is not None:
            best_dirty, best_saved_at = True, 0.0
        saved, mtime = read_settings(folder)
        if mtime and mtime != settings_mtime:
            settings_mtime = mtime
            changed = _apply_live(cfg, model, ema, saved)
            if changed:
                for group in opt.param_groups[:1]:
                    group["weight_decay"] = cfg.weight_decay
                applied = changed
                note += " | setelan baru: " + ", ".join(changed)
        train_ratio = float(np.mean(ratios))
        row = {"epoch": epoch, "step": step, "lr": round(_lr(cfg, step, lr_scale), 8), "train_ratio": round(train_ratio, 5),
               "train_loss": round(float(np.mean(losses)), 5), "seconds": round(time.time() - began, 2), "bars": data.n,
               **{k: v for k, v in exam.items() if k != "per_candle"}, "best": improved, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        with open(folder / "log.jsonl", "a", encoding="utf-8") as out:
            out.write(json.dumps({**row, **({"per_candle": exam["per_candle"]} if improved else {})}) + "\n")
        if best_dirty and (time.time() - best_saved_at > SAVE_BEST_EVERY_S or waiting or note):
            save_best(best_exam)
            best_dirty, best_saved_at = False, time.time()
        (folder / "status.json").write_text(json.dumps({**row, "per_candle": exam["per_candle"], "best_score": round(best, 5),
                                                        "best_exam": best_exam, "params": n_params(model), "size": size,
                                                        "lr_scale": lr_scale, "waiting": waiting, "running": True,
                                                        "config": asdict(cfg), "threads": threads, "pid": os.getpid(),
                                                        "started_at": started_at, "steps_per_epoch": len(data.epoch(np.random.default_rng(0))),
                                                        "best_at": {"epoch": best_at[0], "step": best_at[1]},
                                                        "applied": applied, "settings_mtime": settings_mtime,
                                                        "limit": {"hours": hours, "epochs": epochs,
                                                                  "deadline": None if deadline == float("inf") else deadline,
                                                                  "start_epoch": start_epoch}}, indent=1),
                                            encoding="utf-8")
        print(f"[candle_lm] epoch {epoch} | latih: error {train_ratio:.2%} dari tanpa otak | ujian: {exam['score']:.2%}, "
              f"kedekatan {exam['closeness_avg']:.2f}% ({exam['gain_avg_points']:+.3f} poin)"
              f"{' ★ terbaik' if improved else ''}{note} [{row['seconds']:.1f}s]", flush=True)
        if (improved and epoch - last_table >= 20) or epoch % 100 == 0:
            _print_exam(exam, every=1 if improved else 3)
            last_table = epoch
    if best_dirty and best_exam is not None:
        save_best(best_exam)
    with contextlib.suppress(OSError, ValueError):
        status_now = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        (folder / "status.json").write_text(json.dumps({**status_now, "running": False}, indent=1), encoding="utf-8")
    print("[candle_lm] tersimpan:", folder, flush=True)


# ── forecast ──────────────────────────────────────────────────────────────────
def load_model(symbol: str = "XAUUSD", size: str = "base") -> tuple[CandleGPT, dict[str, Any]]:
    folder = run_dir(m5store.instrument(symbol), size)
    path = folder / "best.pt"
    if not path.exists():
        raise FileNotFoundError(f"belum ada model di {folder}: latih dulu")
    state = torch.load(path, weights_only=False)
    if state["config"].get("arch") != ARCH:
        raise FileNotFoundError(f"model di {folder} dari desain lama: latih ulang dengan --fresh")
    model = CandleGPT(Config(**state["config"]), state["stats"])
    model.load_state_dict(state["model"])
    model.eval()
    return model, state


def _p_up(q10: float, q50: float, q90: float) -> float:
    """P(move > 0) from three quantiles, the distribution taken as straight lines between them."""
    pts = [(q10, 0.1), (q50, 0.5), (q90, 0.9)]
    if q10 >= 0:
        return 0.9 + 0.1 * min(1.0, q10 / max(q90 - q10, 1e-9))
    if q90 <= 0:
        return 0.1 * max(0.0, 1 + q90 / max(q90 - q10, 1e-9))
    (a, pa), (b, pb) = (pts[0], pts[1]) if q50 > 0 else (pts[1], pts[2])
    return 1 - (pa + (pb - pa) * (0 - a) / max(b - a, 1e-9))


@torch.no_grad()
def forecast(symbol: str = "XAUUSD", size: str = "base", sync: bool = True) -> dict[str, Any]:
    """The next ``horizon`` candles from the store's newest bars."""
    model, state = load_model(symbol, size)
    bars, _ = _load_bars(symbol, sync=sync)
    return predict(model, state, bars)


@torch.no_grad()
def predict(model: CandleGPT, state: dict[str, Any], bars: np.ndarray) -> dict[str, Any]:
    """The next ``horizon`` candles after ``bars`` ([N, 6] t,o,h,l,c,v M5, oldest first): the middle guess and the 10-90%
    band of each price, the same in ATR (``quantiles_atr``: per candle [close, high, low] x [10%, 50%, 90%]), and the exam."""
    cfg = model.cfg
    if len(bars) < cfg.ctx + WARMUP_BARS:
        raise ValueError(f"butuh {cfg.ctx + WARMUP_BARS} bar M5, baru ada {len(bars)}")
    tail = bars[-(cfg.ctx + WARMUP_BARS):]
    f = features(tail)
    q = model(torch.from_numpy(f["x"][-cfg.ctx:]).unsqueeze(0))[0, -1].double()   # [targets, 3]
    close = float(tail[-1, 4])
    prev = tail[-ATR_N - 1:-1, 4]
    atr = float((np.maximum(tail[-ATR_N:, 2], prev) - np.minimum(tail[-ATR_N:, 3], prev)).mean() / close)
    price = lambda r: round(close * math.exp(float(r) * atr), 3)   # noqa: E731
    exam = state.get("exam") or {}
    scored = {c["candle"]: c for c in exam.get("per_candle", [])}
    candles, open_ = [], close
    for k in range(cfg.horizon):
        c_q, h_q, l_q = q[k * 3], q[k * 3 + 1], q[k * 3 + 2]
        c_ = price(c_q[1])
        candles.append({"candle": k + 1, "time": int(tail[-1, 0]) + 300 * (k + 1), "open": open_,
                        "high": max(price(h_q[1]), open_, c_), "low": min(price(l_q[1]), open_, c_), "close": c_,
                        "close_p10_p90": [price(c_q[0]), price(c_q[2])], "high_p90": price(h_q[2]), "low_p10": price(l_q[0]),
                        "p_up": round(_p_up(*(float(v) for v in c_q)), 3),
                        "exam_closeness": scored.get(k + 1, {}).get("closeness"),
                        "exam_no_brain": scored.get(k + 1, {}).get("closeness_no_brain")})
        open_ = c_
    return {"instrument": state.get("instrument"), "size": cfg.size, "epoch": state.get("epoch"),
            "last_bar_time": int(tail[-1, 0]), "close": close, "atr_price": round(atr * close, 3), "candles": candles,
            "quantiles_atr": [[[round(float(v), 4) for v in q[k * 3 + j]] for j in range(3)] for k in range(cfg.horizon)],
            "exam": {k: exam.get(k) for k in ("score", "closeness_avg", "gain_avg_points", "edge", "verdict")},
            "exam_per_candle": exam.get("per_candle", []),
            "note": "Peluang, bukan kepastian. exam_closeness = seberapa dekat model ini di ujian untuk candle itu."}


def status(symbol: str = "XAUUSD", size: str = "base") -> dict[str, Any]:
    folder = run_dir(m5store.instrument(symbol), size)
    with contextlib.suppress(OSError, ValueError):
        return json.loads((folder / "status.json").read_text(encoding="utf-8"))
    return {"running": False, "note": f"belum ada run di {folder}"}


def main() -> None:
    ap = argparse.ArgumentParser(prog="candle_lm", description="Model transformer candle M5")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--symbol", default="XAUUSD")
    t.add_argument("--size", default="base", choices=list(SIZES))
    t.add_argument("--hours", type=float, default=0.0, help="0 = sampai dihentikan")
    t.add_argument("--epochs", type=int, default=0, help="0 = tanpa batas")
    t.add_argument("--threads", type=int, default=0, help=f"0 = {DEFAULT_THREADS} thread (atau CANDLE_LM_THREADS); 2 = ringan")
    t.add_argument("--no-mirror", action="store_true", help="tanpa sampel cermin (naik jadi turun)")
    t.add_argument("--compile", dest="compile", action="store_true", default=None, help="paksa torch.compile (default: hanya large/xl)")
    t.add_argument("--no-compile", dest="compile", action="store_false", help="jangan pakai torch.compile")
    t.add_argument("--fresh", action="store_true", help="mulai dari nol (file lama dihapus)")
    for cmd in ("forecast", "status"):
        p = sub.add_parser(cmd)
        p.add_argument("--symbol", default="XAUUSD")
        p.add_argument("--size", default="base", choices=list(SIZES))
        p.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "train":
        train(a.symbol, a.size, a.hours, a.epochs, a.threads, a.fresh, compile_model=a.compile,
              overrides={"mirror": 0.0} if a.no_mirror else None)
        return
    if a.cmd == "forecast":
        out = forecast(a.symbol, a.size)
        if a.json:
            print(json.dumps(out, indent=1, ensure_ascii=False))
            return
        print(f"{out['instrument']} {out['size']} epoch {out['epoch']}  close {out['close']}  ATR ${out['atr_price']}")
        print(f"ujian: {out['exam'].get('verdict')}")
        print(" candle | open      high      low       close     (10%-90%)            naik | dekat di ujian (tanpa otak)")
        for c in out["candles"]:
            print(f"  +{c['candle']:<4} | {c['open']:<9} {c['high']:<9} {c['low']:<9} {c['close']:<9} "
                  f"({c['close_p10_p90'][0]}-{c['close_p10_p90'][1]})  {c['p_up']:.0%} | "
                  f"{c['exam_closeness']}% ({c['exam_no_brain']}%)")
        return
    st = status(a.symbol, a.size)
    if a.json or "per_candle" not in st:
        print(json.dumps(st, indent=1, ensure_ascii=False))
        return
    state = "menunggu data baru" if st.get("waiting") else "jalan" if st.get("running") else "berhenti"
    print(f"epoch {st['epoch']} ({state}) | ujian terbaik: error {st.get('best_score', 0):.2%} dari tanpa otak")
    best = st.get("best_exam") or st
    print(f"versi terbaik: {best.get('verdict')}")
    _print_exam(best)


if __name__ == "__main__":
    main()
