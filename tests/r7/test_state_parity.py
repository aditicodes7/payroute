"""R7: the state representation, and the train/serve skew it is built to avoid.

The predicted failure for this rung: the retry state defined slightly
differently in training than in the live service, so the learned policy never
fires online. It is the nastiest kind because it fails SILENTLY — the lookup
misses, every transaction with that reason gets the fallback action, and
nothing errors.

Three defences, and the third is only available because the model is tabular:
one function builds the state everywhere; an unknown reason raises rather than
defaulting; and the state space is small enough to enumerate and assert
coverage over.
"""

from __future__ import annotations

import pytest


def test_the_state_is_built_by_one_function(clean_world):
    from payroute.retry.state import build_state

    state = build_state(attempt=2, method="upi", failure_reason="gateway_timeout")
    assert state.attempt == 2
    assert state.method == "upi"
    assert state.last_failure_reason == "gateway_timeout"
    assert state == build_state(attempt=2, method="upi", failure_reason="gateway_timeout")


def test_an_unknown_failure_reason_raises_rather_than_defaulting(clean_world):
    """A silent default is how the whole Q-table stops being consulted. If the
    gateway starts returning a reason the trainer never saw, that has to be
    loud."""
    from payroute.retry.state import build_state

    with pytest.raises(ValueError) as excinfo:
        build_state(attempt=1, method="upi", failure_reason="SOFT_DECLINE_07")
    assert "SOFT_DECLINE_07" in str(excinfo.value)


def test_an_unknown_method_raises_too(clean_world):
    from payroute.retry.state import build_state

    with pytest.raises(ValueError):
        build_state(attempt=1, method="crypto", failure_reason="gateway_timeout")


def test_an_out_of_range_attempt_raises(clean_world):
    from payroute.retry.state import MAX_ATTEMPTS, build_state

    with pytest.raises(ValueError):
        build_state(attempt=MAX_ATTEMPTS + 1, method="upi", failure_reason="gateway_timeout")


def test_the_state_space_is_small_and_fully_enumerable(clean_world):
    from payroute.retry.state import MAX_ATTEMPTS, all_states
    from payroute.sim.failures import FAILURE_REASONS

    states = all_states(clean_world.method_mix)
    assert len(states) == MAX_ATTEMPTS * len(clean_world.method_mix) * len(FAILURE_REASONS)
    assert len(set(states)) == len(states)


def test_a_state_from_the_service_matches_one_from_the_simulator(clean_world):
    """Train/serve parity, asserted directly: the simulator's Outcome and a
    gateway's JSON response must produce the same state object."""
    from payroute.retry.state import build_state, state_from_outcome
    from payroute.sim import Outcome

    outcome = Outcome(gateway="atlas", success=False, latency_ms=900.0, fee_paise=0,
                      net_revenue_paise=0, failure_reason="gateway_timeout")
    from_simulator = state_from_outcome(attempt=2, method="card", outcome=outcome)

    service_payload = {"status": "failed", "reason": "gateway_timeout"}
    from_service = build_state(attempt=2, method="card",
                               failure_reason=service_payload["reason"])

    assert from_simulator == from_service


def test_the_q_table_covers_every_state_the_server_can_build(clean_world):
    """The defence tabular buys you. With 54 states you can check all of them at
    load time, so a state the server can construct but the trainer never
    visited is a startup failure rather than a silent degradation."""
    from payroute.retry.qlearner import TabularQ
    from payroute.retry.state import all_states

    states = all_states(clean_world.method_mix)
    learner = TabularQ(states, seed=1)
    learner.assert_covers(states)


def test_a_missing_state_is_caught_loudly(clean_world):
    from payroute.retry.qlearner import TabularQ
    from payroute.retry.state import all_states, build_state

    states = all_states(clean_world.method_mix)
    partial = [s for s in states if s.last_failure_reason != "risk_declined"]
    learner = TabularQ(partial, seed=1)

    with pytest.raises(KeyError) as excinfo:
        learner.assert_covers(states)
    assert "risk_declined" in str(excinfo.value)
