"""R7: failure reasons, and the proof that adding them changed nothing.

The retry MDP's state is (attempt, method, last_failure_reason), and R0 had no
failure reasons at all. They are not decoration — they are the substance,
because reasons differ in whether retrying is worth anything. `issuer_down` and
`gateway_timeout` both look like "it timed out" and call for OPPOSITE
behaviour: a different gateway reaches the same dead bank, but routes around a
dead gateway.

Adding them must perturb nothing. The reason is drawn from its own RNG stream,
conditional on a failure that has already been decided, so success is still
`u < p` and the existing streams do not shift. The fingerprint test below is
the proof rather than the assertion: the hashes were captured from the
simulator BEFORE failure reasons existed.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import timedelta

import pytest

from config.default_world import BUSY_HOUR_OFFSET, START

# Captured from the simulator before failure reasons were added. If adding them
# shifted any RNG stream, these change and every number in R0-R6 is invalidated.
SUCCESS_FINGERPRINTS = {
    5: "452b9cc5b39cdf688177c756ad1386fa",
    909: "c148d78f1620c9117859c9a02c175dcf",
}


@pytest.mark.parametrize("seed", sorted(SUCCESS_FINGERPRINTS))
def test_adding_failure_reasons_did_not_perturb_any_outcome(world, seed):
    from payroute.sim import Simulator

    sim = Simulator(world, seed=seed)
    digest = hashlib.sha256()
    for txn in sim.transactions(20_000):
        for gateway in world.gateway_names:
            digest.update(b"1" if sim.resolve(txn, gateway).success else b"0")

    assert digest.hexdigest()[:32] == SUCCESS_FINGERPRINTS[seed], (
        "success outcomes changed — every measurement in R0 through R6 is now invalid"
    )


def test_failures_carry_a_reason_and_successes_do_not(clean_world):
    from payroute.sim import Simulator

    sim = Simulator(clean_world, seed=5)
    seen_failure = seen_success = False
    for txn in sim.transactions(3_000):
        for gateway in clean_world.gateway_names:
            outcome = sim.resolve(txn, gateway)
            if outcome.success:
                assert outcome.failure_reason is None
                seen_success = True
            else:
                assert outcome.failure_reason is not None
                seen_failure = True
    assert seen_failure and seen_success


def test_every_reason_is_from_the_declared_set(clean_world):
    from payroute.sim import Simulator
    from payroute.sim.failures import FAILURE_REASONS

    sim = Simulator(clean_world, seed=5)
    seen = {
        sim.resolve(txn, gateway).failure_reason
        for txn in sim.transactions(5_000)
        for gateway in clean_world.gateway_names
    }
    assert seen - {None} <= set(FAILURE_REASONS)


def test_the_clean_mix_is_not_dominated_by_one_reason(clean_world):
    """A retry policy learns nothing from a world where every failure has the
    same cause."""
    from payroute.sim import Simulator

    sim = Simulator(clean_world, seed=5)
    counts = Counter(
        sim.resolve(txn, gateway).failure_reason
        for txn in sim.transactions(5_000)
        for gateway in clean_world.gateway_names
    )
    del counts[None]
    assert len(counts) >= 3
    assert max(counts.values()) / sum(counts.values()) < 0.7


def test_a_gateway_incident_produces_gateway_timeouts(clean_world):
    """The signal the retry policy is meant to read: this failure means route
    around the gateway."""
    from payroute.sim import GatewayIncident, Simulator

    start = START + BUSY_HOUR_OFFSET
    sim = Simulator(clean_world, seed=5)
    sim.inject(GatewayIncident(gateway="atlas", start=start,
                               duration=timedelta(minutes=45), peak_severity=0.8))

    counts = Counter()
    for txn in sim.transactions(150_000):
        if start + timedelta(minutes=10) <= txn.ts < start + timedelta(minutes=35):
            outcome = sim.resolve(txn, "atlas")
            if not outcome.success:
                counts[outcome.failure_reason] += 1

    assert sum(counts.values()) > 500
    assert counts["gateway_timeout"] / sum(counts.values()) > 0.6


def test_an_issuer_incident_produces_issuer_down(clean_world):
    """And this one means do not bother retrying anywhere — every gateway
    reaches the same bank."""
    from payroute.sim import IssuerIncident, Simulator

    start = START + BUSY_HOUR_OFFSET
    sim = Simulator(clean_world, seed=5)
    sim.inject(IssuerIncident(issuer="hdfc", start=start,
                              duration=timedelta(minutes=45), peak_severity=0.8))

    counts = Counter()
    for txn in sim.transactions(150_000):
        if txn.issuer == "hdfc" and start + timedelta(minutes=10) <= txn.ts < start + timedelta(minutes=35):
            outcome = sim.resolve(txn, "atlas")
            if not outcome.success:
                counts[outcome.failure_reason] += 1

    assert sum(counts.values()) > 200
    assert counts["issuer_down"] / sum(counts.values()) > 0.6


def test_the_reason_is_deterministic_for_a_transaction(clean_world):
    from payroute.sim import Simulator

    sim = Simulator(clean_world, seed=5)
    for txn in sim.transactions(500):
        first = sim.resolve(txn, "atlas")
        assert first.failure_reason == sim.resolve(txn, "atlas").failure_reason
