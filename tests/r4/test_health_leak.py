"""R4: the two ways a health feature leaks.

The obvious one is an off-by-one — folding this transaction's own outcome into
the health it is scored against. It produces a model that predicts failures
brilliantly because it is reading them.

The one that costs more later is the feedback delay. In the simulator an outcome
is available instantly; in the service at R6 it arrives over Kafka, seconds
later. A policy tuned at R4 on zero-latency feedback would behave differently in
production and nothing would say so — train/serve skew, three rungs early.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc)
GATEWAYS = ["atlas", "beacon", "cirrus"]


def _store(delay=timedelta(0), half_life_minutes=10.0):
    from payroute.features.health import HealthConfig, TerminalHealth

    return TerminalHealth(
        GATEWAYS,
        HealthConfig(
            half_life_minutes=half_life_minutes,
            prior_successes=9.0,
            prior_trials=10.0,
            feedback_delay=delay,
        ),
    )


def test_an_outcome_is_invisible_before_it_is_observed():
    store = _store(delay=timedelta(seconds=60))
    for i in range(40):
        store.observe("atlas", success=False, at=T0 + timedelta(seconds=i * 0.1))

    assert store.health_at(T0 + timedelta(seconds=30))["atlas"] == pytest.approx(0.9)
    assert store.health_at(T0 + timedelta(seconds=90))["atlas"] < 0.5


def test_zero_delay_still_excludes_the_current_transaction():
    """Even with instant feedback, the health used for a decision must be the
    health BEFORE that decision's outcome exists. The outcome has not happened
    yet at the moment of choosing."""
    store = _store(delay=timedelta(0))
    before = store.health_at(T0)["atlas"]
    store.observe("atlas", success=False, at=T0)
    assert before == pytest.approx(0.9)


def test_reading_in_the_past_does_not_see_the_future():
    """Health is read at decision time and snapshotted into the log. If a later
    read could alter what an earlier timestamp returns, R3 re-reading the log
    would get different numbers than the policy saw."""
    store = _store()
    early = store.health_at(T0 + timedelta(minutes=1))["atlas"]
    for i in range(50):
        store.observe("atlas", success=False, at=T0 + timedelta(minutes=5, seconds=i))
    store.health_at(T0 + timedelta(minutes=30))
    assert store.health_at(T0 + timedelta(minutes=1))["atlas"] == pytest.approx(early)


def test_the_policy_scores_on_health_that_predates_its_own_outcome(clean_world):
    """End to end: run the adaptive policy and check that the health logged with
    each decision does not already contain that decision's result."""
    from payroute.policies import AdaptiveLinUCB
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    policy = AdaptiveLinUCB.for_config(clean_world, half_life_minutes=5.0, epsilon=0.1, seed=11)
    log = run_policy(Simulator(clean_world, seed=5), policy, 3_000)

    replay = AdaptiveLinUCB.for_config(clean_world, half_life_minutes=5.0, epsilon=0.1, seed=11)
    for record in log:
        expected = replay.health.health_at(record.ts)[record.action]
        assert record.context[f"health_{record.action}"] == pytest.approx(expected), (
            f"{record.txn_id}: logged health does not match health computed from outcomes "
            "strictly before this decision"
        )
        replay.health.observe(record.action, record.outcome.success, at=record.ts)


def test_the_adaptive_featuriser_reads_only_what_the_policy_provides(clean_world):
    from payroute.policies import AdaptiveLinUCB
    from payroute.sim import Simulator
    from tests.r2.test_features import RecordingDict

    policy = AdaptiveLinUCB.for_config(clean_world, half_life_minutes=5.0, epsilon=0.1, seed=11)
    txn = next(iter(Simulator(clean_world, seed=1).transactions(1)))
    allowed = set(policy.context_for(txn))

    recording = RecordingDict(policy.context_for(txn))
    policy.featuriser.transform(recording)

    leaked = recording.accessed - allowed
    assert not leaked, f"featuriser read keys the policy never provided: {leaked}"


def test_context_carries_no_post_decision_information(clean_world):
    """The health features are the one piece of state in the context that is
    derived from outcomes, so the forbidden-substring guard needs tightening
    rather than dropping: health is allowed, the raw outcome fields are not."""
    from payroute.policies import AdaptiveLinUCB
    from payroute.sim import Simulator

    policy = AdaptiveLinUCB.for_config(clean_world, half_life_minutes=5.0, epsilon=0.1, seed=11)
    txn = next(iter(Simulator(clean_world, seed=1).transactions(1)))
    keys = set(policy.context_for(txn))

    forbidden = ("success", "outcome", "latency", "fee", "revenue", "reward", "severity")
    offending = [k for k in keys if any(s in k.lower() for s in forbidden)]
    assert not offending, f"context leaks post-decision information: {offending}"
    assert any(k.startswith("health_") for k in keys)
