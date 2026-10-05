"""R7: deciding whether and how to retry a failed transaction.

The one rung in this project that is not a bandit. A failed attempt changes the
state — the attempt number, the known failure reason, the remaining sensible
options — so the action at attempt 1 determines the state at attempt 2, and a
greedy per-attempt choice is not optimal.
"""

from payroute.retry.policies import AlwaysRetry, FixedRetryOnce, NeverRetry
from payroute.retry.qlearner import ACTIONS, TabularQ
from payroute.retry.runner import RetryResult, run_with_retries
from payroute.retry.state import MAX_ATTEMPTS, RetryState, all_states, build_state, state_from_outcome

__all__ = [
    "ACTIONS",
    "TabularQ",
    "AlwaysRetry",
    "FixedRetryOnce",
    "NeverRetry",
    "RetryResult",
    "run_with_retries",
    "MAX_ATTEMPTS",
    "RetryState",
    "all_states",
    "build_state",
    "state_from_outcome",
]
