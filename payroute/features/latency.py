"""A time-bounded window of recent latencies, per gateway.

The constraint is a p95, which is a property of a distribution rather than of a
single decision. The feasible-set design turns it into something checkable per
decision: keep only gateways whose RECENT p95 is within budget. Then the fleet
p95 is within budget by construction, because a mixture of distributions each
with P(latency > B) <= 0.05 also has P(latency > B) <= 0.05.

Time-bounded rather than count-bounded, for R4's reason: arrivals are diurnal,
so a count-based window would span a different stretch of wall-clock at 03:00
than at 15:00.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass
class LatencyConfig:
    window_minutes: float
    quantile: float = 0.95
    max_samples: int = 2_000
    # What an unmeasured gateway is assumed to be. Optimistic on purpose: a
    # pessimistic default is a trap rather than a constraint, since an excluded
    # gateway is never routed to, never produces samples, and is never
    # readmitted.
    prior_p95_ms: float = 250.0
    # Below this many samples in the window, the measured quantile is not
    # trusted and the prior is used instead. The nearest-rank p95 of five
    # samples is just their maximum, which sits near the 83rd percentile in
    # expectation — so a sparsely sampled gateway gets a systematically
    # optimistic p95, and a feasibility filter built on it is biased toward
    # admitting slow gateways. Measured: 66 of 25,000 decisions routed to a
    # known-slow gateway on a measured-but-undersampled quantile (docs/bugs.md).
    min_samples: int = 15
    # How long a trusted measurement keeps counting once the window has emptied.
    # This is the recovery knob, made explicit: a gateway measured slow stays
    # excluded for this long and is then retried. Without it the store forgets
    # as soon as traffic moves away, the optimistic prior readmits the gateway,
    # and the readmission cycle is an accident of how fast samples age out
    # rather than a decision. See docs/bugs.md.
    trust_ttl_minutes: float = 30.0
    # The quantile is recomputed at most once per this many seconds of
    # simulated time per gateway. At a few hundred transactions a minute that
    # is a small staleness for a large saving, and it keeps repeated reads at
    # the same instant consistent.
    refresh_seconds: float = 10.0


class LatencyWindow:
    def __init__(self, gateways: Iterable[str], config: LatencyConfig):
        self.gateways = list(gateways)
        self.config = config
        self._window_seconds = config.window_minutes * 60.0
        self._times: dict[str, list[float]] = {g: [] for g in self.gateways}
        self._values: dict[str, list[float]] = {g: [] for g in self.gateways}
        self._cache: dict[str, tuple[int, float]] = {}
        self._trusted: dict[str, tuple[float, float]] = {}

    def observe(self, gateway: str, latency_ms: float, at: datetime) -> None:
        if gateway not in self._times:
            raise KeyError(f"unknown gateway {gateway!r}")
        stamp = at.timestamp()
        self._times[gateway].append(stamp)
        self._values[gateway].append(float(latency_ms))
        self._prune(gateway, stamp)

    def _prune(self, gateway: str, newest: float) -> None:
        times, values = self._times[gateway], self._values[gateway]
        cutoff = newest - self._window_seconds
        drop = bisect.bisect_left(times, cutoff)
        if drop:
            del times[:drop]
            del values[:drop]
        excess = len(times) - self.config.max_samples
        if excess > 0:
            del times[:excess]
            del values[:excess]

    def sample_count(self, gateway: str) -> int:
        return len(self._times[gateway])

    def _quantile(self, gateway: str, t: float) -> float:
        times, values = self._times[gateway], self._values[gateway]
        lo = bisect.bisect_left(times, t - self._window_seconds)
        hi = bisect.bisect_right(times, t)  # never a sample from the future
        window = sorted(values[lo:hi])
        if window and len(window) >= self.config.min_samples:
            rank = max(0, math.ceil(self.config.quantile * len(window)) - 1)
            value = window[rank]
            self._trusted[gateway] = (t, value)
            return value

        # Too few samples to trust a quantile. Fall back to the last
        # measurement that WAS trustworthy rather than to the optimistic prior:
        # a gateway stops producing samples precisely because it was excluded,
        # so reverting to the prior would erase the evidence that excluded it.
        trusted = self._trusted.get(gateway)
        if trusted is not None and 0.0 <= t - trusted[0] <= self.config.trust_ttl_minutes * 60.0:
            return trusted[1]
        return self.config.prior_p95_ms

    def p95_at(self, t: datetime) -> dict[str, float]:
        stamp = t.timestamp()
        bucket = int(stamp // self.config.refresh_seconds)
        out = {}
        for gateway in self.gateways:
            cached = self._cache.get(gateway)
            if cached is None or cached[0] != bucket:
                cached = (bucket, self._quantile(gateway, stamp))
                self._cache[gateway] = cached
            out[gateway] = cached[1]
        return out
