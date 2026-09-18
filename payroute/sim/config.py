"""Configuration objects for the R0 simulator.

Every number here is an assumption a skeptical reader is entitled to push on,
which is why they live in explicit dataclasses (and, in experiments, in a file
under `config/`) rather than scattered through the simulator as literals.

Money is integer PAISE throughout. Floats for money is a bug generator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime


def _check_mix(name: str, mix: dict[str, float]) -> None:
    total = sum(mix.values())
    if not math.isclose(total, 1.0, abs_tol=1e-6):
        raise ValueError(f"{name} must sum to 1.0, got {total!r}")
    if any(v < 0 for v in mix.values()):
        raise ValueError(f"{name} contains a negative weight")


@dataclass
class LatencyConfig:
    """Log-normal latency: median_ms * exp(log_sigma * z)."""

    median_ms: float
    log_sigma: float


@dataclass
class FeeConfig:
    """What the gateway charges us: a flat component plus basis points."""

    flat_paise: int
    bps: int


@dataclass
class GatewayConfig:
    name: str
    # The gateway's general competence at a payment method.
    base_success_by_method: dict[str, float]
    # Its acquiring relationship with a particular bank, as an additive
    # adjustment. Additive is the simplest form that produces the ranking
    # inversions across segments that make the routing problem non-trivial.
    issuer_adjust: dict[str, float]
    latency: LatencyConfig
    fee: FeeConfig
    # How badly this gateway suffers when a given issuer degrades. Different
    # gateways reach the same bank by different acquiring paths, so they do not
    # all fall over together — which is what leaves a correct route to find
    # during a bank outage.
    issuer_sensitivity: dict[str, float]


@dataclass
class AmountConfig:
    median_paise_by_method: dict[str, int]
    log_sigma_by_method: dict[str, float]
    min_paise: int
    max_paise: int


@dataclass
class ArrivalConfig:
    """Non-homogeneous Poisson arrivals with a daily cycle.

    rate(t) = base_rate_per_minute * (1 + diurnal_amplitude * cos(2*pi*(hour - peak_hour)/24))
    """

    base_rate_per_minute: float
    diurnal_amplitude: float
    peak_hour_utc: int


@dataclass
class DriftConfig:
    """Mean-reverting AR(1) wobble on each gateway's success probability.

    `sigma` is the stationary standard deviation, in probability units.
    Independent across gateways on purpose: correlated background drift would
    be a second, undeclared incident mechanism.
    """

    enabled: bool
    sigma: float
    reversion_half_life_minutes: float
    grid_seconds: float = 30.0


@dataclass
class SimulatorConfig:
    gateways: list[GatewayConfig]
    method_mix: dict[str, float]
    issuer_mix: dict[str, float]
    merchant_category_mix: dict[str, float]
    amount: AmountConfig
    arrival: ArrivalConfig
    drift: DriftConfig
    take_rate_bps: int
    fee_on_failure: bool
    start_time: datetime

    def __post_init__(self) -> None:
        _check_mix("method_mix", self.method_mix)
        _check_mix("issuer_mix", self.issuer_mix)
        _check_mix("merchant_category_mix", self.merchant_category_mix)
        if self.start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware")
        if not 0.0 <= self.arrival.diurnal_amplitude < 1.0:
            raise ValueError("diurnal_amplitude must be in [0, 1) so the arrival rate stays positive")
        names = [g.name for g in self.gateways]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate gateway names: {names}")
        for g in self.gateways:
            missing = set(self.method_mix) - set(g.base_success_by_method)
            if missing:
                raise ValueError(f"gateway {g.name} has no success rate for methods {sorted(missing)}")
            missing = set(self.issuer_mix) - set(g.issuer_adjust)
            if missing:
                raise ValueError(f"gateway {g.name} has no issuer adjustment for {sorted(missing)}")
            missing = set(self.issuer_mix) - set(g.issuer_sensitivity)
            if missing:
                raise ValueError(f"gateway {g.name} has no issuer sensitivity for {sorted(missing)}")

    def gateway(self, name: str) -> GatewayConfig:
        for g in self.gateways:
            if g.name == name:
                return g
        raise KeyError(f"unknown gateway {name!r}")

    @property
    def gateway_names(self) -> list[str]:
        return [g.name for g in self.gateways]
