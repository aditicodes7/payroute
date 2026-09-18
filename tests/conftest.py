"""Shared fixtures and statistical helpers for the R0 simulator tests.

Two ideas carry most of the weight here:

1. `binomial_band` — the range an *observed* success rate should fall in if the
   underlying probability really is `p`. Asserting against this band is the
   difference between "the chart looks flat" and "the chart is flat within
   sampling noise". Only the second one is a claim.

2. `WORLD` — a small, fully explicit three-gateway world. The numbers are
   chosen so that no single gateway is best everywhere (see
   test_outcomes.py::test_no_gateway_dominates_every_segment). If one gateway
   dominated, R2 would have nothing contextual to learn and could not beat R1
   no matter how good LinUCB is.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

# z=4 gives a ~1-in-15,000 false alarm rate per assertion. Deliberately wide:
# a flaky test suite teaches you to ignore failures, which is worse than a
# slightly insensitive one.
Z = 4.0

START = datetime(2026, 3, 2, 0, 0, 0, tzinfo=timezone.utc)  # a Monday, midnight UTC


def binomial_band(p: float, n: int, z: float = Z) -> tuple[float, float]:
    """Expected range of an observed success RATE over n Bernoulli(p) draws."""
    se = math.sqrt(p * (1.0 - p) / n)
    return (p - z * se, p + z * se)


def assert_within_band(observed: float, p: float, n: int, z: float = Z, label: str = ""):
    lo, hi = binomial_band(p, n, z)
    assert lo <= observed <= hi, (
        f"{label}: observed rate {observed:.4f} outside {z}-sigma band "
        f"[{lo:.4f}, {hi:.4f}] for p={p:.4f}, n={n}"
    )


def assert_outside_band(observed: float, p: float, n: int, z: float = Z, label: str = ""):
    lo, hi = binomial_band(p, n, z)
    assert not (lo <= observed <= hi), (
        f"{label}: observed rate {observed:.4f} is INSIDE {z}-sigma band "
        f"[{lo:.4f}, {hi:.4f}] for p={p:.4f}, n={n} — expected a visible change"
    )


# --------------------------------------------------------------------------
# The test world.
# --------------------------------------------------------------------------
# Success probability model: p(gateway, method, issuer)
#     = clip(base_success_by_method[method] + issuer_adjust[issuer], 0, 1)
# "base" is the gateway's general competence at a payment method; "adjust" is
# its acquiring relationship with a particular bank. Additive because it is the
# simplest form that produces ranking inversions across segments, which is the
# property we actually need.
#
# Money is in PAISE (integers) throughout. Floats for money is a bug generator.


def build_world():
    """Construct the canonical test SimulatorConfig.

    Imported lazily inside the function so that a missing implementation
    surfaces as a clear failure in each test rather than a collection error
    that hides the whole suite.
    """
    from payroute.sim.config import (
        AmountConfig,
        ArrivalConfig,
        DriftConfig,
        FeeConfig,
        GatewayConfig,
        LatencyConfig,
        SimulatorConfig,
    )

    gateways = [
        GatewayConfig(
            name="atlas",
            base_success_by_method={"upi": 0.90, "card": 0.95, "netbanking": 0.88},
            issuer_adjust={"hdfc": +0.04, "sbi": -0.05, "icici": +0.01, "axis": 0.00, "kotak": +0.02},
            latency=LatencyConfig(median_ms=180.0, log_sigma=0.40),
            fee=FeeConfig(flat_paise=0, bps=90),
            issuer_sensitivity={"hdfc": 0.90, "sbi": 0.40, "icici": 0.50, "axis": 0.50, "kotak": 0.50},
        ),
        GatewayConfig(
            name="beacon",
            base_success_by_method={"upi": 0.94, "card": 0.89, "netbanking": 0.91},
            issuer_adjust={"hdfc": -0.06, "sbi": +0.04, "icici": 0.00, "axis": +0.02, "kotak": -0.01},
            latency=LatencyConfig(median_ms=320.0, log_sigma=0.55),
            fee=FeeConfig(flat_paise=0, bps=110),
            issuer_sensitivity={"hdfc": 0.30, "sbi": 0.70, "icici": 0.50, "axis": 0.50, "kotak": 0.50},
        ),
        GatewayConfig(
            name="cirrus",
            base_success_by_method={"upi": 0.92, "card": 0.92, "netbanking": 0.85},
            issuer_adjust={"hdfc": 0.00, "sbi": 0.00, "icici": -0.03, "axis": +0.03, "kotak": 0.00},
            latency=LatencyConfig(median_ms=240.0, log_sigma=0.35),
            fee=FeeConfig(flat_paise=100, bps=70),
            issuer_sensitivity={"hdfc": 0.60, "sbi": 0.55, "icici": 0.50, "axis": 0.50, "kotak": 0.50},
        ),
    ]

    return SimulatorConfig(
        gateways=gateways,
        method_mix={"upi": 0.62, "card": 0.28, "netbanking": 0.10},
        issuer_mix={"hdfc": 0.28, "sbi": 0.24, "icici": 0.20, "axis": 0.16, "kotak": 0.12},
        merchant_category_mix={"food_delivery": 0.35, "ecommerce": 0.40, "travel": 0.15, "utilities": 0.10},
        amount=AmountConfig(
            # Median amount in paise, per method: UPI skews small, netbanking
            # large. log_sigma controls the right tail.
            median_paise_by_method={"upi": 35_000, "card": 180_000, "netbanking": 450_000},
            log_sigma_by_method={"upi": 1.10, "card": 1.00, "netbanking": 0.90},
            min_paise=100,
            max_paise=50_000_000,
        ),
        arrival=ArrivalConfig(
            base_rate_per_minute=120.0,
            # Multiplier on the arrival rate by hour-of-day. Quiet at 4am,
            # busy at 9pm. Without this, "outage at 3am" and "outage at 9pm"
            # cost the same, which is not how payments work.
            diurnal_amplitude=0.75,
            peak_hour_utc=15,  # ~21:00 IST
        ),
        drift=DriftConfig(
            enabled=True,
            # Mean-reverting (AR(1) / Ornstein-Uhlenbeck) wobble on each
            # gateway's success probability, in probability units.
            sigma=0.015,
            reversion_half_life_minutes=45.0,
        ),
        take_rate_bps=200,  # our revenue on a successful transaction: 2% MDR
        fee_on_failure=False,
        start_time=START,
    )


@pytest.fixture
def world():
    return build_world()


@pytest.fixture
def clean_world():
    """Same world with background drift switched off.

    Needed for the exact binomial-band assertions: with drift on, the observed
    variance is legitimately larger than binomial, so a binomial band would
    fail for the right reason and tell us nothing.
    """
    cfg = build_world()
    cfg.drift.enabled = False
    return cfg


def true_prob(cfg, gateway_name: str, method: str, issuer: str) -> float:
    """Recompute the configured success probability, independent of the
    simulator's own code. If this and the simulator disagree, one of them is
    wrong — and the test should not be the one that trusts the other."""
    gw = next(g for g in cfg.gateways if g.name == gateway_name)
    return min(1.0, max(0.0, gw.base_success_by_method[method] + gw.issuer_adjust[issuer]))


def marginal_true_prob(cfg, gateway_name: str) -> float:
    """Configured success probability marginalised over the method/issuer mix."""
    total = 0.0
    for method, pm in cfg.method_mix.items():
        for issuer, pi in cfg.issuer_mix.items():
            total += pm * pi * true_prob(cfg, gateway_name, method, issuer)
    return total
