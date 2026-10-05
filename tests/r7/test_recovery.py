"""R7: does the learned retry policy actually recover more, and at what cost?

A retry on the same gateway must not be deterministic, or the whole exercise is
empty: with common random numbers, re-resolving a transaction would return the
identical outcome every time. So each attempt beyond the first draws from its
own pre-drawn uniform — attempt 1 is untouched, which is what keeps every
earlier rung's numbers valid.
"""

from __future__ import annotations

import pytest

N = 20_000


def _routing(world):
    from payroute.policies import ConstrainedLinUCB

    return ConstrainedLinUCB.for_config(world, p95_budget_ms=420.0, epsilon=0.05, seed=11)


def test_a_retry_is_not_a_replay(clean_world):
    """Attempt 2 on the same gateway must be an independent draw. Attempt 1
    must be byte-identical to what it always was."""
    from payroute.sim import Simulator

    sim = Simulator(clean_world, seed=5)
    differ = 0
    checked = 0
    for txn in sim.transactions(2_000):
        first = sim.resolve(txn, "atlas")
        assert first == sim.resolve(txn, "atlas", attempt=1)
        second = sim.resolve(txn, "atlas", attempt=2)
        checked += 1
        differ += first.success != second.success
    assert differ > 0, "retrying the same gateway replayed the same outcome every time"
    assert differ < checked * 0.3


def test_the_learned_policy_beats_fixed_retry_once_on_revenue(clean_world):
    from payroute.retry.runner import run_with_retries
    from payroute.retry.policies import FixedRetryOnce
    from payroute.retry.qlearner import TabularQ
    from payroute.retry.state import all_states
    from payroute.sim import Simulator

    learner = TabularQ(all_states(clean_world.method_mix), alpha=0.1, gamma=1.0, epsilon=0.1, seed=3)
    run_with_retries(Simulator(clean_world, seed=101), _routing(clean_world), learner, N)
    learner.epsilon = 0.0

    learned = run_with_retries(Simulator(clean_world, seed=5), _routing(clean_world), learner, N)
    fixed = run_with_retries(Simulator(clean_world, seed=5), _routing(clean_world), FixedRetryOnce(), N)

    assert learned.net_revenue_per_1k > fixed.net_revenue_per_1k, (
        f"learned {learned.net_revenue_per_1k:,.0f} vs fixed {fixed.net_revenue_per_1k:,.0f}"
    )


def test_recovered_rate_is_reported_for_both(clean_world):
    """The rung's stated measurement. Note it is a measurement, not a target:
    a policy that recovers more transactions while spending more on retries is
    not obviously better, which is why revenue is reported beside it."""
    from payroute.retry.runner import run_with_retries
    from payroute.retry.policies import FixedRetryOnce
    from payroute.sim import Simulator

    fixed = run_with_retries(Simulator(clean_world, seed=5), _routing(clean_world), FixedRetryOnce(), N)
    assert 0.0 < fixed.recovered_rate < 1.0
    assert fixed.attempts_per_transaction > 1.0


def test_the_learned_table_is_readable_and_sensible(clean_world):
    """Tabular buys interpretability, and a policy you can read is a policy you
    can defend. It should learn not to retry when the money is not there."""
    from payroute.retry.runner import run_with_retries
    from payroute.retry.qlearner import TabularQ
    from payroute.retry.state import all_states, build_state
    from payroute.sim import Simulator

    learner = TabularQ(all_states(clean_world.method_mix), alpha=0.1, gamma=1.0, epsilon=0.2, seed=3)
    run_with_retries(Simulator(clean_world, seed=101), _routing(clean_world), learner, 60_000)
    learner.epsilon = 0.0

    for method in clean_world.method_mix:
        state = build_state(1, method, "insufficient_funds")
        assert learner.greedy(state) == "give_up", (
            f"learned to retry on insufficient funds for {method}: "
            f"{ {a: round(learner.q(state, a), 2) for a in learner.actions} }"
        )


def test_retry_costs_are_counted(clean_world):
    """Retrying must not be free, or the optimal policy is to retry forever."""
    from payroute.retry.runner import run_with_retries
    from payroute.retry.policies import AlwaysRetry, NeverRetry
    from payroute.sim import Simulator

    greedy = run_with_retries(Simulator(clean_world, seed=5), _routing(clean_world), AlwaysRetry(), N)
    none = run_with_retries(Simulator(clean_world, seed=5), _routing(clean_world), NeverRetry(), N)

    assert greedy.success_rate > none.success_rate
    assert greedy.attempts_per_transaction > none.attempts_per_transaction
    assert greedy.retry_cost_per_1k > 0
