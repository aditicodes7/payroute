"""Incident models.

Two kinds, because there are two different real-world failures and they demand
different routing responses:

  GatewayIncident — the gateway's own stack degrades. Everything through it
                    suffers; other gateways are untouched. The response is
                    "route elsewhere", and it is the easy case.

  IssuerIncident  — a BANK degrades. Every gateway's success rate for that
                    issuer drops, scaled by each gateway's `issuer_sensitivity`.
                    There is still a best gateway during the outage, and which
                    one it is depends on the bank. A static rule table written
                    last quarter cannot express that, which is the premise of
                    this whole project.

Severity is a shaped profile in [0, 1], not a step. It ramps in, fluctuates
while it is happening, and ramps out. A rectangular step would be trivially
detectable by any change detector, which would hand R4 a free pass and make its
"react fast vs overreact to noise" tradeoff vacuous.

The fluctuation is deterministic given the incident (a fixed sum of sinusoids
with incident-specific phases) rather than freshly sampled per call, because
`severity_at(ts)` must return the same answer every time it is asked — the
simulator's replayability depends on it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

# Fractions of the window spent ramping in and out.
RAMP_IN = 0.15
RAMP_OUT = 0.25

# Amplitudes of the two wobble components, as fractions of peak severity.
WOBBLE = ((0.15, 7.0), (0.08, 3.3))  # (amplitude, cycles across the window)


def _smoothstep(x: float) -> float:
    """3x^2 - 2x^3 on [0, 1]: a ramp with zero slope at both ends, so the
    profile has no visible corner where the ramp meets the plateau."""
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


@dataclass
class _Incident:
    start: datetime
    duration: timedelta
    peak_severity: float
    phase: float = 0.0  # decorrelates the wobble between incidents

    def severity_at(self, ts: datetime) -> float:
        if ts < self.start:
            return 0.0
        elapsed = (ts - self.start).total_seconds()
        total = self.duration.total_seconds()
        if elapsed >= total:
            return 0.0

        frac = elapsed / total
        if frac < RAMP_IN:
            shape = _smoothstep(frac / RAMP_IN)
        elif frac > 1.0 - RAMP_OUT:
            shape = _smoothstep((1.0 - frac) / RAMP_OUT)
        else:
            shape = 1.0

        wobble = 1.0
        for amplitude, cycles in WOBBLE:
            wobble += amplitude * math.sin(2.0 * math.pi * cycles * frac + self.phase * cycles)

        return min(1.0, max(0.0, self.peak_severity * shape * wobble))


@dataclass
class GatewayIncident(_Incident):
    gateway: str = ""

    def __post_init__(self) -> None:
        if not self.gateway:
            raise ValueError("GatewayIncident requires a gateway name")


@dataclass
class IssuerIncident(_Incident):
    issuer: str = ""

    def __post_init__(self) -> None:
        if not self.issuer:
            raise ValueError("IssuerIncident requires an issuer name")


def _sample_window(rng, start, horizon, median_minutes, log_sigma):
    offset = timedelta(seconds=float(rng.random()) * horizon.total_seconds())
    minutes = median_minutes * math.exp(log_sigma * float(rng.standard_normal()))
    return start + offset, timedelta(minutes=minutes)


def _sample_severity(rng) -> float:
    # Beta(2, 2) rescaled to [0.25, 0.95]: most incidents are partial
    # degradations, total blackouts are rare.
    return 0.25 + 0.70 * float(rng.beta(2.0, 2.0))


def sample_gateway_incidents(
    cfg,
    seed: int,
    start: datetime,
    horizon: timedelta,
    per_gateway_per_day: float = 1.0,
    median_minutes: float = 25.0,
    log_sigma: float = 0.6,
) -> list[GatewayIncident]:
    """Draw a random schedule of gateway incidents over a horizon.

    Duration and severity are drawn, not fixed: a constant incident signature
    would let a policy overfit to one shape of outage.
    """
    rng = np.random.default_rng(seed)
    days = horizon.total_seconds() / 86400.0
    incidents: list[GatewayIncident] = []
    for g in cfg.gateways:
        count = int(rng.poisson(per_gateway_per_day * days))
        for _ in range(count):
            begins, duration = _sample_window(rng, start, horizon, median_minutes, log_sigma)
            incidents.append(
                GatewayIncident(
                    start=begins,
                    duration=duration,
                    peak_severity=_sample_severity(rng),
                    phase=float(rng.random()) * 2.0 * math.pi,
                    gateway=g.name,
                )
            )
    incidents.sort(key=lambda i: i.start)
    return incidents


def sample_issuer_incidents(
    cfg,
    seed: int,
    start: datetime,
    horizon: timedelta,
    per_issuer_per_day: float = 0.5,
    median_minutes: float = 40.0,
    log_sigma: float = 0.5,
) -> list[IssuerIncident]:
    """Bank-side incidents: rarer than gateway incidents but longer, which is
    roughly how bank downtime behaves."""
    rng = np.random.default_rng(seed)
    days = horizon.total_seconds() / 86400.0
    incidents: list[IssuerIncident] = []
    for issuer in cfg.issuer_mix:
        count = int(rng.poisson(per_issuer_per_day * days))
        for _ in range(count):
            begins, duration = _sample_window(rng, start, horizon, median_minutes, log_sigma)
            incidents.append(
                IssuerIncident(
                    start=begins,
                    duration=duration,
                    peak_severity=_sample_severity(rng),
                    phase=float(rng.random()) * 2.0 * math.pi,
                    issuer=issuer,
                )
            )
    incidents.sort(key=lambda i: i.start)
    return incidents
