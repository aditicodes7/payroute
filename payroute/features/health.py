"""Time-decayed per-terminal health.

Two design points carry this module.

DECAY IS IN WALL-CLOCK TIME. Arrivals are diurnal by a factor of seven, so a
rolling window of the last N transactions would react in 33 minutes at the
03:00 trough and 5 minutes at the 15:00 peak. "Time to react" would not be a
single quantity.

THE PRIOR DOES NOT DECAY. An exponentially weighted count shrinks toward zero
during quiet periods; if the prior decayed with it, the store would hold no
evidence at all and the next single observation would dominate completely — one
failure swinging health from 0.95 to near zero, and the policy abandoning a
working gateway. The prior is added at read time and is a permanent floor on how
much evidence it takes to move the number.

Outcomes become visible at `at + feedback_delay`, never before. In the simulator
feedback is instantaneous; over Kafka at R6 it is not, and a policy tuned on
zero-latency feedback would behave differently in the service with nothing in
the output to say so.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable


@dataclass
class HealthConfig:
    half_life_minutes: float
    prior_successes: float = 9.0
    prior_trials: float = 10.0
    feedback_delay: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if self.half_life_minutes <= 0:
            raise ValueError("half_life_minutes must be positive")
        if self.prior_trials < self.prior_successes:
            raise ValueError("prior_trials must be at least prior_successes")
        if self.prior_trials <= 0:
            raise ValueError("prior_trials must be positive, or a quiet period leaves 0/0")


class TerminalHealth:
    def __init__(self, gateways: Iterable[str], config: HealthConfig):
        self.gateways = list(gateways)
        self.config = config
        self._half_life_seconds = config.half_life_minutes * 60.0

        self._events: dict[str, list[tuple[float, float]]] = {g: [] for g in self.gateways}
        self._times: dict[str, list[float]] = {g: [] for g in self.gateways}
        self._cursor: dict[str, int] = {g: 0 for g in self.gateways}
        self._successes: dict[str, float] = {g: 0.0 for g in self.gateways}
        self._trials: dict[str, float] = {g: 0.0 for g in self.gateways}
        self._anchor: dict[str, float | None] = {g: None for g in self.gateways}

    def observe(self, gateway: str, success: bool, at: datetime, observed_at: datetime | None = None) -> None:
        if gateway not in self._events:
            raise KeyError(f"unknown gateway {gateway!r}")
        visible_at = observed_at or (at + self.config.feedback_delay)
        stamp = visible_at.timestamp()
        self._events[gateway].append((stamp, float(bool(success))))
        self._times[gateway].append(stamp)

    def _decay(self, dt_seconds: float) -> float:
        return 0.5 ** (dt_seconds / self._half_life_seconds)

    def _advance(self, gateway: str, t: float) -> None:
        """Bring the decayed sums up to time t, folding in every outcome visible
        by then and no others."""
        anchor = self._anchor[gateway]
        if anchor is not None and t < anchor:
            # A read in the past: rewind and recompute. Only happens on replay,
            # and it has to give the same answer it gave the first time, or a
            # log could not be re-read consistently.
            self._cursor[gateway] = 0
            self._successes[gateway] = 0.0
            self._trials[gateway] = 0.0
            self._anchor[gateway] = None
            anchor = None

        times = self._times[gateway]
        limit = bisect.bisect_right(times, t)
        index = self._cursor[gateway]
        while index < limit:
            stamp, success = self._events[gateway][index]
            if anchor is not None:
                factor = self._decay(stamp - anchor)
                self._successes[gateway] *= factor
                self._trials[gateway] *= factor
            self._successes[gateway] += success
            self._trials[gateway] += 1.0
            anchor = stamp
            index += 1

        if anchor is not None and t > anchor:
            factor = self._decay(t - anchor)
            self._successes[gateway] *= factor
            self._trials[gateway] *= factor
            anchor = t

        self._cursor[gateway] = index
        self._anchor[gateway] = anchor

    def health_at(self, t: datetime) -> dict[str, float]:
        stamp = t.timestamp()
        out = {}
        for gateway in self.gateways:
            self._advance(gateway, stamp)
            out[gateway] = (self._successes[gateway] + self.config.prior_successes) / (
                self._trials[gateway] + self.config.prior_trials
            )
        return out

    def evidence_at(self, t: datetime) -> dict[str, float]:
        """How many observations' worth of (decayed) evidence stands behind each
        gateway's number. Useful for diagnosing an overreaction: a health value
        resting on 0.3 observations is not a measurement."""
        stamp = t.timestamp()
        out = {}
        for gateway in self.gateways:
            self._advance(gateway, stamp)
            out[gateway] = self._trials[gateway]
        return out
