"""R0: background drift.

Even with no incident, gateway success rates should wobble slowly. This is the
omission most likely to hollow out a later rung: without noise, the world is
piecewise-constant, every change is a clean step, and R4's central tradeoff
(react fast vs overreact to noise) has no noise to overreact to.
"""

from __future__ import annotations

import statistics
from datetime import timedelta

import pytest

from tests.conftest import drift_adjusted_band, marginal_true_prob


def _sim(cfg, seed=7):
    from payroute.sim import Simulator

    return Simulator(cfg, seed=seed)


def _bin_rates(cfg, sim, n, gateway, bin_minutes=10):
    bins = {}
    for txn in sim.transactions(n):
        key = int(txn.ts.timestamp() // (bin_minutes * 60))
        ok, total = bins.get(key, (0, 0))
        bins[key] = (ok + int(sim.resolve(txn, gateway).success), total + 1)
    return [(ok / total, total) for ok, total in bins.values() if total > 200]


def test_drift_disabled_gives_binomial_variance_between_bins(clean_world):
    """Control case. With drift off, bin-to-bin variation should be pure
    sampling noise — this is what the overdispersion test below is measured
    against."""
    rates = _bin_rates(clean_world, _sim(clean_world, seed=31), 60_000, "atlas")
    assert len(rates) >= 15, f"only {len(rates)} usable bins"

    p = marginal_true_prob(clean_world, "atlas")
    observed_var = statistics.pvariance([r for r, _ in rates])
    mean_n = statistics.mean(n for _, n in rates)
    binomial_var = p * (1 - p) / mean_n

    assert observed_var < 2.5 * binomial_var, (
        f"drift is off but bin variance {observed_var:.2e} exceeds binomial {binomial_var:.2e}"
    )


def test_drift_enabled_is_overdispersed_relative_to_binomial(world):
    """With drift on, bins must vary MORE than sampling noise alone explains.
    That excess variance is the thing R4's decay constant has to see through."""
    rates = _bin_rates(world, _sim(world, seed=31), 60_000, "atlas")
    assert len(rates) >= 15

    p = marginal_true_prob(world, "atlas")
    observed_var = statistics.pvariance([r for r, _ in rates])
    mean_n = statistics.mean(n for _, n in rates)
    binomial_var = p * (1 - p) / mean_n

    assert observed_var > 2.0 * binomial_var, (
        f"bin variance {observed_var:.2e} is not meaningfully above binomial "
        f"{binomial_var:.2e} — drift is configured but not doing anything"
    )


def test_drift_is_mean_reverting_not_a_random_walk(world):
    """An unbounded random walk would eventually wander a gateway's success rate
    to 0 or 1 and quietly invalidate every long run."""
    sim = _sim(world, seed=44)
    ok = total = 0
    first = last = None
    for txn in sim.transactions(150_000):
        first = first or txn.ts
        last = txn.ts
        ok += int(sim.resolve(txn, "atlas").success)
        total += 1

    p = marginal_true_prob(world, "atlas")
    span = (last - first).total_seconds()
    lo, hi = drift_adjusted_band(p, total, world, span)
    observed = ok / total
    assert lo <= observed <= hi, (
        f"atlas long-run rate {observed:.4f} outside drift-adjusted band "
        f"[{lo:.4f}, {hi:.4f}] — the drift process is not mean-reverting"
    )


def test_drift_is_independent_across_gateways(world):
    """Correlated background drift would be a second, undeclared incident
    mechanism. Correlation between gateways belongs to the ISSUER incident
    model, where it is explicit and parameterised."""
    sim = _sim(world, seed=52)
    series = {"atlas": [], "beacon": []}
    bins = {}
    for txn in sim.transactions(80_000):
        key = int(txn.ts.timestamp() // 600)
        entry = bins.setdefault(key, {"atlas": [0, 0], "beacon": [0, 0]})
        for name in series:
            entry[name][0] += int(sim.resolve(txn, name).success)
            entry[name][1] += 1

    for key in sorted(bins):
        entry = bins[key]
        if entry["atlas"][1] < 200:
            continue
        for name in series:
            series[name].append(entry[name][0] / entry[name][1])

    assert len(series["atlas"]) >= 15
    r = statistics.correlation(series["atlas"], series["beacon"])
    assert abs(r) < 0.5, f"gateway drift is correlated (r={r:.2f}) with no incident injected"
