"""A small Gaussian HMM (diagonal covariance) with Baum-Welch training and a causal forward filter.

Training is offline on a window that the caller chooses, and the walk-forward runner only
ever passes past data. The filter at time t uses observations 0..t and returns
P(state_t | obs_0..t), which is the posterior the spec asks for.
"""

from __future__ import annotations

import math
import statistics

_MIN_VAR = 1e-6


class GaussianHMM:
    def __init__(self, n_states: int = 3, seed_order: int = 0) -> None:
        self.k = n_states
        self.pi: list[float] = [1.0 / n_states] * n_states
        self.A: list[list[float]] = [[1.0 / n_states] * n_states for _ in range(n_states)]
        self.mu: list[list[float]] = []
        self.var: list[list[float]] = []
        self.seed_order = seed_order

    # ------------------------------------------------------------ emissions
    def _logpdf(self, x: list[float], j: int) -> float:
        s = 0.0
        for d, v in enumerate(x):
            var = self.var[j][d]
            s += -0.5 * (math.log(2 * math.pi * var) + (v - self.mu[j][d]) ** 2 / var)
        return s

    # ------------------------------------------------------------ training
    def fit(self, data: list[list[float]], iters: int = 25) -> GaussianHMM:
        T, D = len(data), len(data[0])
        self._init_from_quantiles(data)
        prev_ll = -math.inf
        for _ in range(iters):
            logB = [[self._logpdf(data[t], j) for j in range(self.k)] for t in range(T)]
            alpha, c = self._forward(logB)
            beta = self._backward(logB, c)
            ll = -sum(math.log(ci) for ci in c)
            gamma = [[0.0] * self.k for _ in range(T)]
            for t in range(T):
                norm = sum(alpha[t][j] * beta[t][j] for j in range(self.k)) or 1.0
                for j in range(self.k):
                    gamma[t][j] = alpha[t][j] * beta[t][j] / norm
            xi_sum = [[0.0] * self.k for _ in range(self.k)]
            for t in range(T - 1):
                denom = 0.0
                tmp = [[0.0] * self.k for _ in range(self.k)]
                for i in range(self.k):
                    for j in range(self.k):
                        v = alpha[t][i] * self.A[i][j] * math.exp(logB[t + 1][j]) * beta[t + 1][j]
                        tmp[i][j] = v
                        denom += v
                denom = denom or 1.0
                for i in range(self.k):
                    for j in range(self.k):
                        xi_sum[i][j] += tmp[i][j] / denom
            # M-step
            self.pi = gamma[0][:]
            for i in range(self.k):
                row = sum(gamma[t][i] for t in range(T - 1)) or 1.0
                for j in range(self.k):
                    self.A[i][j] = max(xi_sum[i][j] / row, 1e-6)
                s = sum(self.A[i])
                self.A[i] = [a / s for a in self.A[i]]
            for j in range(self.k):
                w = sum(gamma[t][j] for t in range(T)) or 1e-9
                for d in range(D):
                    m = sum(gamma[t][j] * data[t][d] for t in range(T)) / w
                    v = sum(gamma[t][j] * (data[t][d] - m) ** 2 for t in range(T)) / w
                    self.mu[j][d] = m
                    self.var[j][d] = max(v, _MIN_VAR)
            if abs(ll - prev_ll) < 1e-4:
                break
            prev_ll = ll
        return self

    def _init_from_quantiles(self, data: list[list[float]]) -> None:
        """Start states from quantile bins of the first feature (the trend strength proxy)."""
        T, D = len(data), len(data[0])
        order = sorted(range(T), key=lambda t: data[t][0])
        chunk = max(1, T // self.k)
        self.mu, self.var = [], []
        for j in range(self.k):
            idx = order[j * chunk: (j + 1) * chunk if j < self.k - 1 else T]
            rows = [data[t] for t in idx] or [data[0]]
            mu = [statistics.fmean(r[d] for r in rows) for d in range(D)]
            var = [max(statistics.pvariance([r[d] for r in rows]) if len(rows) > 1 else 1.0, _MIN_VAR) for d in range(D)]
            self.mu.append(mu)
            self.var.append(var)

    def _forward(self, logB: list[list[float]]) -> tuple[list[list[float]], list[float]]:
        T = len(logB)
        alpha = [[0.0] * self.k for _ in range(T)]
        c = [0.0] * T
        for j in range(self.k):
            alpha[0][j] = self.pi[j] * math.exp(logB[0][j])
        c[0] = sum(alpha[0]) or 1e-300
        alpha[0] = [a / c[0] for a in alpha[0]]
        for t in range(1, T):
            for j in range(self.k):
                s = sum(alpha[t - 1][i] * self.A[i][j] for i in range(self.k))
                alpha[t][j] = s * math.exp(logB[t][j])
            c[t] = sum(alpha[t]) or 1e-300
            alpha[t] = [a / c[t] for a in alpha[t]]
        return alpha, c

    def _backward(self, logB: list[list[float]], c: list[float]) -> list[list[float]]:
        T = len(logB)
        beta = [[1.0] * self.k for _ in range(T)]
        for t in range(T - 2, -1, -1):
            for i in range(self.k):
                beta[t][i] = sum(self.A[i][j] * math.exp(logB[t + 1][j]) * beta[t + 1][j] for j in range(self.k)) / c[t + 1]
        return beta

    # ------------------------------------------------------------ filtering
    def filter(self, data: list[list[float]]) -> list[list[float]]:
        """Causal posterior P(state_t | obs_0..t) for every t. Uses no future data."""
        logB = [[self._logpdf(x, j) for j in range(self.k)] for x in data]
        alpha, _ = self._forward(logB)
        return alpha


def label_states(model: GaussianHMM, trend_idx: int = 0, adx_idx: int = 2, atr_idx: int = 3) -> dict[str, int]:
    """Name the three states from their means: highvol = highest ATR ratio, trend = highest ADX of the rest."""
    k = model.k
    highvol = max(range(k), key=lambda j: model.mu[j][atr_idx])
    rest = [j for j in range(k) if j != highvol]
    trend = max(rest, key=lambda j: model.mu[j][adx_idx])
    range_ = [j for j in rest if j != trend][0]
    return {"trend": trend, "range": range_, "highvol": highvol}
