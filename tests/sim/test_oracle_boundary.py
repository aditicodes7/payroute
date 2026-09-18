"""R0: the oracle boundary.

The simulator knows the true success probability of every (context, gateway,
time). R3 needs that — it is the ground truth the estimators are scored
against. Policies must never be able to reach it.

The defence is architectural, not disciplinary: "we agreed not to call it" is
not a defence, because the whole point of R2 is that a subtle feature leak is
easy to introduce and produces results that look excellent.
"""

from __future__ import annotations

import pytest

from tests.conftest import marginal_true_prob, true_prob


def _sim(cfg, seed=7):
    from payroute.sim import Simulator

    return Simulator(cfg, seed=seed)


def test_transaction_holds_no_reference_back_to_the_simulator(world):
    """A Transaction that carries a simulator reference is a leak with extra
    steps: a policy could reach the oracle through it."""
    from payroute.sim import Simulator

    txn = next(iter(_sim(world).transactions(1)))
    for name in dir(txn):
        if name.startswith("_"):
            continue
        value = getattr(txn, name, None)
        assert not isinstance(value, Simulator), f"Transaction.{name} exposes the Simulator"


def test_oracle_reports_the_configured_probability(clean_world):
    sim = _sim(clean_world)
    oracle = sim.oracle
    for txn in sim.transactions(200):
        for name in ("atlas", "beacon", "cirrus"):
            expected = true_prob(clean_world, name, txn.method, txn.issuer)
            assert oracle.true_success_prob(txn, name) == pytest.approx(expected, abs=1e-9)


def test_oracle_reflects_an_active_incident(clean_world):
    from datetime import timedelta

    from payroute.sim.incidents import GatewayIncident
    from tests.conftest import START

    start = START + timedelta(hours=2)
    sim = _sim(clean_world)
    sim.inject(GatewayIncident(gateway="atlas", start=start, duration=timedelta(minutes=30), peak_severity=0.6))

    inside = outside = None
    for txn in sim.transactions(60_000):
        if start + timedelta(minutes=10) <= txn.ts < start + timedelta(minutes=20):
            inside = inside or sim.oracle.true_success_prob(txn, "atlas") / true_prob(
                clean_world, "atlas", txn.method, txn.issuer
            )
        elif txn.ts > start + timedelta(hours=2):
            outside = outside or sim.oracle.true_success_prob(txn, "atlas") / true_prob(
                clean_world, "atlas", txn.method, txn.issuer
            )
        if inside and outside:
            break

    assert inside is not None and outside is not None
    assert inside < 0.7, f"oracle does not see the incident (ratio {inside:.3f})"
    assert outside == pytest.approx(1.0, abs=1e-9)


def test_oracle_computes_the_true_value_of_a_fixed_policy(clean_world):
    """This is R3's ground truth in embryo: the exact expected net revenue of a
    policy, computed from the true probabilities rather than estimated from
    sampled outcomes.

    It must agree with the empirical average over a large run — if it does not,
    every OPE error bar we report later is measured against the wrong number.
    """
    sim = _sim(clean_world, seed=77)
    txns = list(sim.transactions(60_000))

    def always_beacon(txn):
        return "beacon"

    truth = sim.oracle.true_policy_value(always_beacon, txns)
    empirical = sum(sim.resolve(t, "beacon").net_revenue_paise for t in txns) / len(txns)

    # net revenue is a heavy-tailed quantity; 3% agreement over 60k txns is a
    # meaningful check without being flaky
    assert truth == pytest.approx(empirical, rel=0.03), (
        f"oracle true value {truth:.1f} vs empirical {empirical:.1f} paise/txn"
    )
