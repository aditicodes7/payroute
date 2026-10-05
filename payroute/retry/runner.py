"""Running transactions with retries, and measuring what that recovered.

A retry is not free. Each one costs a configurable amount, and the customer
abandons with some probability after every failure — because the real cost of
retrying is mostly that the person gives up and goes elsewhere. Without those,
the optimal policy is "retry forever" and the rung is vacuous.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from payroute.retry.state import MAX_ATTEMPTS, state_from_outcome


@dataclass(frozen=True)
class RetryResult:
    n: int
    success_rate: float
    recovered_rate: float
    attempts_per_transaction: float
    net_revenue_per_1k: float
    retry_cost_per_1k: float


def _different_gateway(decision, exclude: set[str]) -> str:
    """R7 decides WHETHER to retry; R5's ranking decides where."""
    candidates = [g for g, p in decision.action_probs.items() if g not in exclude and p > 0.0]
    if not candidates:
        candidates = [g for g in decision.action_probs if g not in exclude]
    return max(candidates, key=lambda g: decision.action_probs[g])


def run_with_retries(
    sim,
    routing_policy,
    retry_policy,
    n: int,
    max_attempts: int = MAX_ATTEMPTS,
    retry_cost_paise: int = 600,
    abandon_prob: float = 0.12,
    seed: int = 0,
) -> RetryResult:
    rng = np.random.default_rng(seed)
    successes = recovered = attempts_total = 0
    revenue = 0
    retry_cost = 0

    for txn in sim.transactions(n):
        routing = routing_policy.decide(txn)
        gateway = routing.action
        attempt = 1
        previous_reason = None
        pending = None  # (state, action) awaiting its reward

        while True:
            outcome = sim.resolve(txn, gateway, attempt=attempt, previous_reason=previous_reason)
            attempts_total += 1

            if hasattr(routing_policy, "update"):
                routing_policy.update(routing_policy.context_for(txn), gateway, float(outcome.success))
            if hasattr(routing_policy, "observe"):
                routing_policy.observe(txn, gateway, outcome)

            if pending is not None and hasattr(retry_policy, "update"):
                reward = (outcome.net_revenue_paise if outcome.success else 0) - retry_cost_paise
                terminal = outcome.success or attempt >= max_attempts
                next_state = (
                    None if terminal else state_from_outcome(attempt, txn.method, outcome)
                )
                retry_policy.update(pending[0], pending[1], float(reward), next_state)
                pending = None

            if outcome.success:
                successes += 1
                revenue += outcome.net_revenue_paise
                if attempt > 1:
                    recovered += 1
                break

            if attempt >= max_attempts:
                break

            state = state_from_outcome(attempt, txn.method, outcome)
            decision = retry_policy.decide(state)

            if decision.action == "give_up":
                if hasattr(retry_policy, "update"):
                    retry_policy.update(state, "give_up", 0.0, None)
                break

            # The customer may leave regardless of what we would have liked to do.
            if rng.random() < abandon_prob:
                break

            previous_reason = outcome.failure_reason
            if decision.action == "retry_different_gateway":
                gateway = _different_gateway(routing, {gateway})
            attempt += 1
            retry_cost += retry_cost_paise
            pending = (state, decision.action)

    return RetryResult(
        n=n,
        success_rate=successes / n,
        recovered_rate=recovered / n,
        attempts_per_transaction=attempts_total / n,
        net_revenue_per_1k=(revenue - retry_cost) / n * 1_000,
        retry_cost_per_1k=retry_cost / n * 1_000,
    )
