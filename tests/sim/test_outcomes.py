"""R0: gateway outcomes — success, latency, fees, and the counterfactual
machinery that R3 will depend on."""

from __future__ import annotations

import pytest

from tests.conftest import (
    Z,
    assert_within_band,
    marginal_true_prob,
    true_prob,
)


def _sim(cfg, seed=7):
    from payroute.sim import Simulator

    return Simulator(cfg, seed=seed)


def test_clean_run_success_rates_match_configured_probabilities(clean_world):
    """No incidents, no drift: each gateway's observed rate must sit inside the
    binomial band around its configured marginal probability."""
    n = 60_000
    sim = _sim(clean_world)
    hits = {g.name: 0 for g in clean_world.gateways}

    for txn in sim.transactions(n):
        for name in hits:
            if sim.resolve(txn, name).success:
                hits[name] += 1

    for name, count in hits.items():
        expected = marginal_true_prob(clean_world, name)
        assert_within_band(count / n, expected, n, label=f"gateway={name}")


def test_success_depends_on_context_not_just_on_gateway(clean_world):
    """Per-segment rates must track the configured (method, issuer) matrix.

    If success depended on the gateway alone there would be nothing contextual
    to learn, and R2 could not beat R1 no matter how well LinUCB works.
    """
    sim = _sim(clean_world, seed=21)
    segments = [("upi", "hdfc"), ("card", "sbi"), ("netbanking", "axis")]
    buckets = {(m, i, g.name): [0, 0] for m, i in segments for g in clean_world.gateways}

    for txn in sim.transactions(120_000):
        key = (txn.method, txn.issuer)
        if key not in segments:
            continue
        for g in clean_world.gateways:
            ok, total = buckets[(key[0], key[1], g.name)]
            buckets[(key[0], key[1], g.name)] = [ok + int(sim.resolve(txn, g.name).success), total + 1]

    for (method, issuer, name), (ok, total) in buckets.items():
        assert total > 800, f"segment {method}/{issuer} too small to test ({total})"
        expected = true_prob(clean_world, name, method, issuer)
        assert_within_band(ok / total, expected, total, label=f"{name} on {method}/{issuer}")


def test_no_gateway_dominates_every_segment(clean_world):
    """There must exist at least two segments whose best gateway differs.

    This is the property that makes the entire project non-trivial. If it ever
    fails, the simulator has been configured into a world where a static rule
    is optimal and every later rung is measuring noise.
    """
    winners = set()
    for method in clean_world.method_mix:
        for issuer in clean_world.issuer_mix:
            best = max(
                clean_world.gateways,
                key=lambda g: true_prob(clean_world, g.name, method, issuer),
            )
            winners.add(best.name)
    assert len(winners) >= 2, f"a single gateway wins every segment: {winners}"


def test_outcomes_are_deterministic_given_the_transaction(clean_world):
    """Resolving the same transaction twice must give the same answer.

    Not a pedantic property: it is what makes a logged decision replayable, and
    R3's ground-truth computation depends on it.
    """
    sim = _sim(clean_world, seed=5)
    for txn in sim.transactions(300):
        first = sim.resolve(txn, "atlas")
        second = sim.resolve(txn, "atlas")
        assert first.success == second.success
        assert first.latency_ms == second.latency_ms
        assert first.net_revenue_paise == second.net_revenue_paise


def test_common_random_numbers_make_counterfactuals_coherent(clean_world):
    """Each transaction carries one shared uniform draw, so `success` is
    `u < p(gateway)`. That means: if a transaction succeeded on the gateway with
    the LOWER success probability, it must also succeed on the higher one.

    This is what lets the simulator answer "what would have happened on gateway
    C?" — the question real logged data can never answer, and the question R3
    needs answered in order to have a ground truth at all.
    """
    sim = _sim(clean_world, seed=9)
    for txn in sim.transactions(4_000):
        ranked = sorted(
            (g.name for g in clean_world.gateways),
            key=lambda n: true_prob(clean_world, n, txn.method, txn.issuer),
        )
        results = [sim.resolve(txn, n).success for n in ranked]
        # once True, must stay True as probability increases
        assert results == sorted(results), (
            f"txn {txn.txn_id}: outcomes {dict(zip(ranked, results))} are not "
            "monotone in success probability — the shared uniform draw is not shared"
        )


def test_resolve_matches_the_oracle_counterfactual(clean_world):
    sim = _sim(clean_world, seed=4)
    oracle = sim.oracle
    for txn in sim.transactions(500):
        for name in ("atlas", "beacon", "cirrus"):
            assert sim.resolve(txn, name).success == oracle.counterfactual(txn, name).success


def test_latency_is_positive_and_has_the_configured_ordering(clean_world):
    sim = _sim(clean_world, seed=6)
    samples = {g.name: [] for g in clean_world.gateways}
    for txn in sim.transactions(20_000):
        for name in samples:
            out = sim.resolve(txn, name)
            assert out.latency_ms > 0
            samples[name].append(out.latency_ms)

    def p(name, q):
        xs = sorted(samples[name])
        return xs[int(len(xs) * q)]

    # medians should land near the configured values
    for g in clean_world.gateways:
        observed = p(g.name, 0.50)
        assert 0.85 * g.latency.median_ms <= observed <= 1.15 * g.latency.median_ms, (
            f"{g.name}: median latency {observed:.0f}ms vs configured {g.latency.median_ms}ms"
        )

    # and there must be a real tail — R5's p95 constraint is meaningless without one
    for g in clean_world.gateways:
        assert p(g.name, 0.95) / p(g.name, 0.50) > 1.5, f"{g.name} has no latency tail"

    assert p("atlas", 0.95) < p("cirrus", 0.95) < p("beacon", 0.95)


def test_fee_and_net_revenue_are_computed_from_config(clean_world):
    """R5 maximises net revenue, so the arithmetic has to be nailed down at R0.

    net revenue on success = take_rate_bps * amount / 10_000 - fee
    net revenue on failure = 0 (fee_on_failure is False in this world)
    """
    sim = _sim(clean_world, seed=8)
    gw = {g.name: g for g in clean_world.gateways}
    checked_success = checked_failure = 0

    for txn in sim.transactions(3_000):
        for name, g in gw.items():
            out = sim.resolve(txn, name)
            expected_fee = g.fee.flat_paise + (g.fee.bps * txn.amount_paise) // 10_000
            if out.success:
                expected_value = (clean_world.take_rate_bps * txn.amount_paise) // 10_000
                assert out.fee_paise == expected_fee
                assert out.net_revenue_paise == expected_value - expected_fee
                checked_success += 1
            else:
                assert out.fee_paise == 0
                assert out.net_revenue_paise == 0
                checked_failure += 1

    assert checked_success > 0 and checked_failure > 0, "need both outcomes covered"


def test_net_revenue_is_integer_paise(clean_world):
    sim = _sim(clean_world, seed=2)
    for txn in sim.transactions(200):
        out = sim.resolve(txn, "atlas")
        assert isinstance(out.net_revenue_paise, int)
        assert isinstance(out.fee_paise, int)


def test_cheapest_gateway_is_not_the_most_reliable_one(clean_world):
    """R5 needs a genuine tension between cost and success rate. If the cheapest
    gateway were also the best, the constrained policy would be identical to the
    success-rate-only policy and the rung would have nothing to demonstrate."""
    cheapest = min(clean_world.gateways, key=lambda g: g.fee.bps)
    most_reliable = max(clean_world.gateways, key=lambda g: marginal_true_prob(clean_world, g.name))
    fastest = min(clean_world.gateways, key=lambda g: g.latency.median_ms)
    assert cheapest.name != most_reliable.name, (
        "the cheapest gateway is also the most reliable — R5's cost/success "
        "tradeoff would not exist in this world"
    )
    assert fastest.name != most_reliable.name, (
        "the fastest gateway is also the most reliable — R5's latency "
        "constraint would never bind"
    )
