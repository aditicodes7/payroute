"""R7: the Q-learning arithmetic and the propensity on a retry decision.

    Q(s,a) <- Q(s,a) + alpha * [ r + gamma * max_a' Q(s',a') - Q(s,a) ]

The bootstrap term is the whole difference from a bandit: the value of an
action includes the best value available from where it lands you. Episodes cap
at three attempts, so gamma is 1.0 — undiscounted episodic return is the thing
we actually care about, which is total revenue from this transaction.
"""

from __future__ import annotations

import pytest


def build_state(*args, **kwargs):
    from payroute.retry.state import build_state as _build

    return _build(*args, **kwargs)


def _learner(clean_world, **kwargs):
    from payroute.retry.qlearner import TabularQ
    from payroute.retry.state import all_states

    params = dict(alpha=0.5, gamma=1.0, epsilon=0.1, seed=3)
    params.update(kwargs)
    return TabularQ(all_states(clean_world.method_mix), **params)


def test_q_starts_at_zero(clean_world):
    learner = _learner(clean_world)
    state = build_state(1, "upi", "gateway_timeout")
    assert all(learner.q(state, a) == 0.0 for a in learner.actions)


def test_a_terminal_update_has_no_bootstrap(clean_world):
    """Q <- 0 + 0.5 * (10 + 0 - 0) = 5.0"""
    learner = _learner(clean_world, alpha=0.5)
    state = build_state(1, "upi", "gateway_timeout")

    learner.update(state, "retry_different_gateway", reward=10.0, next_state=None)
    assert learner.q(state, "retry_different_gateway") == pytest.approx(5.0)


def test_the_bootstrap_uses_the_best_action_in_the_next_state(clean_world):
    """Next state is worth 8 via its best action, so with alpha 0.5 and
    gamma 1.0:  Q <- 0 + 0.5 * (2 + 8 - 0) = 5.0"""
    learner = _learner(clean_world, alpha=0.5, gamma=1.0)
    first = build_state(1, "upi", "gateway_timeout")
    second = build_state(2, "upi", "gateway_timeout")

    learner.update(second, "give_up", reward=3.0, next_state=None)       # -> 1.5
    learner.update(second, "retry_same_gateway", reward=16.0, next_state=None)  # -> 8.0
    assert learner.q(second, "retry_same_gateway") == pytest.approx(8.0)

    learner.update(first, "retry_different_gateway", reward=2.0, next_state=second)
    assert learner.q(first, "retry_different_gateway") == pytest.approx(5.0)


def test_gamma_discounts_the_bootstrap(clean_world):
    learner = _learner(clean_world, alpha=0.5, gamma=0.5)
    first = build_state(1, "card", "do_not_honour")
    second = build_state(2, "card", "do_not_honour")

    learner.update(second, "retry_same_gateway", reward=16.0, next_state=None)  # -> 8.0
    learner.update(first, "retry_different_gateway", reward=2.0, next_state=second)
    assert learner.q(first, "retry_different_gateway") == pytest.approx(0.5 * (2.0 + 0.5 * 8.0))


def test_repeated_updates_converge_on_the_expected_value(clean_world):
    learner = _learner(clean_world, alpha=0.1)
    state = build_state(1, "upi", "insufficient_funds")
    for _ in range(300):
        learner.update(state, "give_up", reward=7.0, next_state=None)
    assert learner.q(state, "give_up") == pytest.approx(7.0, abs=0.05)


def test_every_retry_decision_logs_a_propensity(clean_world):
    """A retry decision is a decision. CLAUDE.md does not carve out an
    exception, so the invariant applies here too."""
    learner = _learner(clean_world, epsilon=0.2)
    state = build_state(2, "upi", "gateway_timeout")

    for _ in range(200):
        decision = learner.decide(state)
        assert decision.action in learner.actions
        assert 0.0 < decision.propensity <= 1.0
        assert decision.propensity == pytest.approx(decision.action_probs[decision.action])
        assert sum(decision.action_probs.values()) == pytest.approx(1.0)


def test_propensities_match_the_observed_action_frequencies(clean_world):
    from tests.conftest import assert_within_band

    learner = _learner(clean_world, epsilon=0.3)
    state = build_state(2, "upi", "gateway_timeout")
    learner.update(state, "retry_different_gateway", reward=5.0, next_state=None)

    claimed = learner.decide(state).action_probs
    counts = {a: 0 for a in learner.actions}
    n = 20_000
    for _ in range(n):
        counts[learner.decide(state).action] += 1
    for action, count in counts.items():
        assert_within_band(count / n, claimed[action], n, label=f"action={action}")


def test_epsilon_zero_is_greedy(clean_world):
    learner = _learner(clean_world, epsilon=0.0)
    state = build_state(1, "upi", "gateway_timeout")
    learner.update(state, "give_up", reward=9.0, next_state=None)

    assert {learner.decide(state).action for _ in range(50)} == {"give_up"}
    assert learner.decide(state).propensity == 1.0
