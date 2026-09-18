"""R1: the decision record.

This schema is the contract between the rung that writes decisions (R1, R2),
the rung that evaluates them (R3), and the service that persists them (R6). The
tests here are mostly about making it *hard to write a bad record* rather than
about behaviour — a decision logged without a propensity is not recoverable
after the fact, so the schema has to refuse it at construction time rather than
trust everyone to remember.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

START_TS = datetime(2026, 3, 2, 13, 0, 0, tzinfo=timezone.utc)


def _record(**overrides):
    from payroute.decisionlog import DecisionRecord

    kwargs = dict(
        txn_id="txn-000000042",
        ts=START_TS,
        context={"method": "upi", "issuer": "hdfc", "amount_paise": 35_000},
        action="atlas",
        propensity=1 / 3,
        action_probs={"atlas": 1 / 3, "beacon": 1 / 3, "cirrus": 1 / 3},
        policy_id="uniform_random",
        policy_version="1",
    )
    kwargs.update(overrides)
    return DecisionRecord(**kwargs)


def test_a_record_cannot_be_built_without_a_propensity():
    """The load-bearing invariant of the whole project, enforced by the type
    rather than by discipline."""
    from payroute.decisionlog import DecisionRecord

    with pytest.raises(TypeError):
        DecisionRecord(
            txn_id="txn-1",
            ts=START_TS,
            context={},
            action="atlas",
            action_probs={"atlas": 1.0},
            policy_id="p",
            policy_version="1",
        )


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_propensity_must_be_in_the_half_open_unit_interval(bad):
    """Zero is as invalid as negative: an action taken with probability zero is
    a contradiction, and it becomes a division by zero in every importance
    weight downstream."""
    with pytest.raises(ValueError):
        _record(propensity=bad, action_probs={"atlas": bad, "beacon": 1 - bad, "cirrus": 0.0})


def test_propensity_must_equal_the_chosen_actions_probability():
    """Two fields that must agree, so they get checked rather than trusted.
    They drift apart exactly when someone adds a new policy and updates one."""
    with pytest.raises(ValueError):
        _record(propensity=0.5, action_probs={"atlas": 1 / 3, "beacon": 1 / 3, "cirrus": 1 / 3})


def test_action_probs_must_sum_to_one():
    with pytest.raises(ValueError):
        _record(propensity=0.5, action_probs={"atlas": 0.5, "beacon": 0.2, "cirrus": 0.2})


def test_chosen_action_must_appear_in_action_probs():
    with pytest.raises(ValueError):
        _record(action="nimbus")


def test_context_is_snapshotted_not_referenced():
    """At R1 the context is static and this looks like pointless copying. At R4
    the features become time-decayed rolling aggregates, and a reference would
    resolve to DIFFERENT NUMBERS when R3 reads the log than the policy saw when
    it decided. That is train/serve skew wearing a disguise."""
    source = {"method": "upi", "issuer": "hdfc", "amount_paise": 35_000}
    record = _record(context=source)
    source["method"] = "card"
    source["amount_paise"] = 999
    assert record.context["method"] == "upi"
    assert record.context["amount_paise"] == 35_000


def test_context_cannot_be_mutated_through_the_record():
    record = _record()
    with pytest.raises(TypeError):
        record.context["method"] = "card"


def test_a_decision_without_an_outcome_is_incomplete():
    """Decisions and outcomes arrive at different times — immediately here,
    seconds apart over Kafka at R6. The schema models that from the start
    rather than pretending they are simultaneous."""
    record = _record()
    assert record.outcome is None
    assert not record.is_complete


def test_attaching_an_outcome_completes_the_record_without_mutating_it():
    from payroute.sim import Outcome

    record = _record()
    outcome = Outcome(gateway="atlas", success=True, latency_ms=180.0, fee_paise=315, net_revenue_paise=385)
    completed = record.with_outcome(outcome)

    assert completed.is_complete
    assert completed.outcome.success is True
    assert record.outcome is None, "with_outcome must not mutate the original record"
    assert completed.propensity == record.propensity


def test_outcome_gateway_must_match_the_logged_action():
    """Catches a whole class of join bugs: an outcome attached to the wrong
    decision is worse than a missing one, because nothing downstream can see it."""
    from payroute.sim import Outcome

    with pytest.raises(ValueError):
        _record().with_outcome(
            Outcome(gateway="beacon", success=True, latency_ms=1.0, fee_paise=0, net_revenue_paise=0)
        )


def test_jsonl_round_trip_preserves_every_field():
    from payroute.sim import Outcome

    record = _record().with_outcome(
        Outcome(gateway="atlas", success=False, latency_ms=214.5, fee_paise=0, net_revenue_paise=0)
    )
    restored = type(record).from_json(json.loads(record.to_json()))

    assert restored == record
    assert restored.ts == record.ts and restored.ts.tzinfo is not None
    assert restored.propensity == record.propensity
    assert dict(restored.action_probs) == dict(record.action_probs)
    assert dict(restored.context) == dict(record.context)


def test_log_round_trips_through_a_file(tmp_path):
    from payroute.decisionlog import DecisionLog

    log = DecisionLog(seed=11, policy_id="uniform_random")
    for i in range(50):
        log.append(_record(txn_id=f"txn-{i:09d}"))

    path = tmp_path / "decisions.jsonl"
    log.to_jsonl(path)
    restored = DecisionLog.from_jsonl(path)

    assert len(restored) == len(log)
    assert restored.seed == 11
    assert restored.policy_id == "uniform_random"
    assert list(restored) == list(log)


def test_log_reports_records_that_are_missing_outcomes():
    from payroute.decisionlog import DecisionLog

    log = DecisionLog(seed=1, policy_id="p")
    log.append(_record(txn_id="txn-1"))
    problems = log.validate()
    assert any("outcome" in p.lower() for p in problems)
