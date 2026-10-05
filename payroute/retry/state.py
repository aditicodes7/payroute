"""The retry state, built by exactly one function.

The predicted failure for this rung is the state being defined slightly
differently in training than in the live service, so the learned policy never
fires. It is the worst kind of bug because it is silent: the table lookup
misses, every transaction with that reason gets the fallback action, and
nothing errors.

Hence: one builder, used by both sides; unknown inputs raise rather than
defaulting; and a space small enough to enumerate and check in full.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from payroute.sim.failures import FAILURE_REASONS

MAX_ATTEMPTS = 3
METHODS = ("upi", "card", "netbanking")


@dataclass(frozen=True)
class RetryState:
    attempt: int
    method: str
    last_failure_reason: str


def build_state(attempt: int, method: str, failure_reason: str) -> RetryState:
    """The only way to construct a state. Training and serving both come here.

    Every rejection below is deliberate. A defensive default for an unseen
    failure reason is precisely the mechanism that makes train/serve skew
    invisible — the policy silently stops being consulted.
    """
    if not 1 <= attempt <= MAX_ATTEMPTS:
        raise ValueError(f"attempt {attempt!r} outside 1..{MAX_ATTEMPTS}")
    if method not in METHODS:
        raise ValueError(f"unknown payment method {method!r}; known: {METHODS}")
    if failure_reason not in FAILURE_REASONS:
        raise ValueError(
            f"unknown failure reason {failure_reason!r}; known: {FAILURE_REASONS}. "
            "Mapping it to a default would silently retire the learned policy for every "
            "transaction that carries it."
        )
    return RetryState(attempt=attempt, method=method, last_failure_reason=failure_reason)


def state_from_outcome(attempt: int, method: str, outcome) -> RetryState:
    if outcome.failure_reason is None:
        raise ValueError("cannot build a retry state from a successful outcome")
    return build_state(attempt, method, outcome.failure_reason)


def all_states(methods: Iterable[str] = METHODS) -> list[RetryState]:
    """The entire space, 3 x methods x reasons. Enumerable by design: it is what
    lets `TabularQ.assert_covers` turn a missing state into a startup failure
    rather than a quiet degradation in production."""
    return [
        RetryState(attempt, method, reason)
        for attempt in range(1, MAX_ATTEMPTS + 1)
        for method in methods
        for reason in FAILURE_REASONS
    ]
