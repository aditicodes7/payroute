"""Fixed retry policies, as baselines for the learned one."""

from __future__ import annotations

from payroute.decisionlog import Decision
from payroute.retry.qlearner import ACTIONS
from payroute.retry.state import RetryState


def _fixed(action: str) -> Decision:
    return Decision(action, {a: (1.0 if a == action else 0.0) for a in ACTIONS}, 1.0)


class FixedRetryOnce:
    """The incumbent: retry exactly once, on a different gateway, whatever the
    reason. This is the number R7 has to be measured against."""

    policy_id = "fixed_retry_once"
    policy_version = "1"

    def decide(self, state: RetryState) -> Decision:
        return _fixed("retry_different_gateway" if state.attempt == 1 else "give_up")


class AlwaysRetry:
    policy_id = "always_retry"
    policy_version = "1"

    def decide(self, state: RetryState) -> Decision:
        return _fixed("retry_different_gateway")


class NeverRetry:
    policy_id = "never_retry"
    policy_version = "1"

    def decide(self, state: RetryState) -> Decision:
        return _fixed("give_up")
