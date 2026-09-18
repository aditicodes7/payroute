"""R0: the transaction generator produces the context distribution we configured.

These are the cheap tests that catch a whole class of generator bugs before any
policy is allowed near the simulator.
"""

from __future__ import annotations

import math
from datetime import timedelta

import pytest

from tests.conftest import Z, assert_within_band


def _sim(cfg, seed=7):
    from payroute.sim import Simulator

    return Simulator(cfg, seed=seed)


def test_same_seed_reproduces_the_same_transaction_stream(world):
    a = [t.as_dict() for t in _sim(world, seed=11).transactions(500)]
    b = [t.as_dict() for t in _sim(world, seed=11).transactions(500)]
    assert a == b


def test_different_seeds_produce_different_streams(world):
    a = [t.as_dict() for t in _sim(world, seed=11).transactions(500)]
    b = [t.as_dict() for t in _sim(world, seed=12).transactions(500)]
    assert a != b


def test_transaction_stream_is_independent_of_the_routing_decisions(world):
    """The load-bearing reproducibility test.

    If context generation and outcome draws share one RNG stream, then two
    policies that choose differently consume different numbers of draws, the
    streams desynchronise, and from that point the policies are running on
    DIFFERENT TRAFFIC. The comparison silently stops being a comparison.

    Same seed, same transactions, regardless of what we route where.
    """
    sim_a, sim_b = _sim(world, seed=3), _sim(world, seed=3)

    stream_a, stream_b = [], []
    for _ in range(400):
        ta = sim_a.next_transaction()
        sim_a.resolve(ta, "atlas")
        stream_a.append(ta.as_dict())

        tb = sim_b.next_transaction()
        # different routing decision, and a different number of them
        sim_b.resolve(tb, "beacon")
        sim_b.resolve(tb, "cirrus")
        stream_b.append(tb.as_dict())

    assert stream_a == stream_b


def test_method_mix_matches_config(world):
    n = 40_000
    txns = list(_sim(world).transactions(n))
    for method, expected in world.method_mix.items():
        observed = sum(1 for t in txns if t.method == method) / n
        assert_within_band(observed, expected, n, label=f"method={method}")


def test_issuer_mix_matches_config(world):
    n = 40_000
    txns = list(_sim(world).transactions(n))
    for issuer, expected in world.issuer_mix.items():
        observed = sum(1 for t in txns if t.issuer == issuer) / n
        assert_within_band(observed, expected, n, label=f"issuer={issuer}")


def test_merchant_category_mix_matches_config(world):
    n = 40_000
    txns = list(_sim(world).transactions(n))
    for cat, expected in world.merchant_category_mix.items():
        observed = sum(1 for t in txns if t.merchant_category == cat) / n
        assert_within_band(observed, expected, n, label=f"category={cat}")


def test_amount_is_conditioned_on_method_not_drawn_independently(world):
    """Features must be CORRELATED.

    Independent draws hand R2's linear model an unrealistically clean problem,
    so we never learn whether it copes with the collinearity that real
    transaction data always has.
    """
    txns = list(_sim(world).transactions(30_000))

    def median_amount(method):
        xs = sorted(t.amount_paise for t in txns if t.method == method)
        return xs[len(xs) // 2]

    upi, card, nb = median_amount("upi"), median_amount("card"), median_amount("netbanking")
    assert upi < card < nb, f"expected upi < card < netbanking, got {upi}, {card}, {nb}"

    # and each should be near its configured median
    for method, observed in (("upi", upi), ("card", card), ("netbanking", nb)):
        expected = world.amount.median_paise_by_method[method]
        assert 0.85 * expected <= observed <= 1.15 * expected, (
            f"{method}: median {observed} far from configured {expected}"
        )


def test_amount_distribution_has_a_heavy_right_tail(world):
    """A symmetric amount distribution would make the revenue-weighted
    objective in R5 nearly identical to the success-rate objective, and the
    rung would have nothing to show."""
    amounts = sorted(t.amount_paise for t in _sim(world).transactions(30_000))
    p50 = amounts[len(amounts) // 2]
    p99 = amounts[int(len(amounts) * 0.99)]
    assert p99 / p50 > 8.0, f"p99/p50 = {p99 / p50:.1f}, tail is too thin to matter"


def test_amounts_respect_configured_bounds(world):
    for t in _sim(world).transactions(20_000):
        assert world.amount.min_paise <= t.amount_paise <= world.amount.max_paise
        assert isinstance(t.amount_paise, int), "money must be integer paise, never float"


def test_timestamps_are_monotonic_and_timezone_aware(world):
    txns = list(_sim(world).transactions(2_000))
    assert txns[0].ts.tzinfo is not None, "naive datetimes will bite us at R4 and R6"
    for earlier, later in zip(txns, txns[1:]):
        assert later.ts >= earlier.ts


def test_arrival_rate_varies_by_hour_of_day(world):
    """Diurnal pattern. Without it, an outage at 04:00 costs the same as one at
    21:00, which would make R4's and R5's revenue numbers meaningless."""
    cfg = world
    cfg.arrival.base_rate_per_minute = 2.0  # keep the test cheap: ~2,900 txns/day
    txns = list(_sim(cfg).transactions(6_000))

    counts = {}
    for t in txns:
        counts[t.ts.hour] = counts.get(t.ts.hour, 0) + 1

    peak = counts[cfg.arrival.peak_hour_utc]
    trough = counts[(cfg.arrival.peak_hour_utc + 12) % 24]
    assert peak > 2.0 * trough, (
        f"peak hour {peak} vs trough hour {trough}: arrival rate is too flat to be diurnal"
    )


def test_transaction_exposes_no_outcome_or_probability_information(world):
    """Leak test.

    The single most dangerous object in this codebase is a Transaction that
    quietly carries the answer. A policy is handed this object; if it can see a
    success probability, R2's results are fiction and no amount of careful
    feature engineering will save them.
    """
    t = next(iter(_sim(world).transactions(1)))
    visible = set(t.as_dict()) | {a for a in dir(t) if not a.startswith("_")}

    forbidden_substrings = ("success", "prob", "outcome", "latency", "fee", "revenue", "reward", "severity")
    leaked = [
        name
        for name in visible
        if any(s in name.lower() for s in forbidden_substrings)
    ]
    assert not leaked, f"Transaction leaks post-decision information: {leaked}"
