"""Shared fixtures and statistical helpers for the R0 simulator tests.

Two ideas carry most of the weight here:

1. `binomial_band` — the range an *observed* success rate should fall in if the
   underlying probability really is `p`. Asserting against this band is the
   difference between "the chart looks flat" and "the chart is flat within
   sampling noise". Only the second one is a claim.

2. The world itself lives in `config/default_world.py`, not here, so the
   experiment scripts and the test suite build the same one. A second copy
   would drift, and then the tests would be asserting properties of a world the
   experiments never run.
"""

from __future__ import annotations

import math
import pytest

from config.default_world import BUSY_HOUR_OFFSET, START, build_default_world

# z=4 gives a ~1-in-15,000 false alarm rate per assertion. Deliberately wide:
# a flaky test suite teaches you to ignore failures, which is worse than a
# slightly insensitive one.
Z = 4.0

# The world under test and where incident windows are placed both live in
# config/default_world.py, so the experiments and the tests cannot drift apart.
build_world = build_default_world


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


def drift_adjusted_band(observed_p: float, n: int, cfg, span_seconds: float, z: float = Z):
    """Band for a success rate measured over a run with background drift.

    A binomial band is the wrong yardstick here at ANY z: the observed rate
    carries two independent sources of variance, sampling noise AND the
    time-average of the AR(1) drift process. For an AR(1) with stationary
    variance sigma^2 and correlation time tau, the variance of its average over
    a span T >> tau is approximately sigma^2 * 2*tau/T.

    Widening z until the test stops failing would have hidden the fact that we
    were comparing against the wrong distribution.
    """
    tau_seconds = cfg.drift.reversion_half_life_minutes * 60.0 / math.log(2.0)
    binomial_var = observed_p * (1.0 - observed_p) / n
    drift_var = (cfg.drift.sigma ** 2) * (2.0 * tau_seconds / span_seconds) if cfg.drift.enabled else 0.0
    se = math.sqrt(binomial_var + drift_var)
    return (observed_p - z * se, observed_p + z * se)


def assert_outside_band(observed: float, p: float, n: int, z: float = Z, label: str = ""):
    lo, hi = binomial_band(p, n, z)
    assert not (lo <= observed <= hi), (
        f"{label}: observed rate {observed:.4f} is INSIDE {z}-sigma band "
        f"[{lo:.4f}, {hi:.4f}] for p={p:.4f}, n={n} — expected a visible change"
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
