"""Tabular Q-learning over the retry state.

    Q(s,a) <- Q(s,a) + alpha * [ r + gamma * max_a' Q(s',a') - Q(s,a) ]
                                        ^^^^^^^^^^^^^^^^^^^
                              the term a contextual bandit does not have

That bootstrap is the whole reason this rung is not another bandit. LinUCB
estimates the reward of an action; this estimates the reward of an action plus
the best available from wherever it leaves you. Retrying somewhere with a 60%
chance can be the wrong move if it spends the attempt budget you would rather
have kept.

gamma defaults to 1.0: episodes cap at three attempts, so there is no infinite
horizon to tame and the undiscounted episodic return is the quantity we
actually care about — total revenue from this transaction.
"""

from __future__ import annotations

from typing import Iterable, Mapping

import numpy as np

from payroute.decisionlog import Decision
from payroute.retry.state import RetryState

ACTIONS = ("give_up", "retry_same_gateway", "retry_different_gateway")


class TabularQ:
    policy_id = "retry_qlearner"
    policy_version = "1"

    def __init__(
        self,
        states: Iterable[RetryState],
        alpha: float = 0.1,
        gamma: float = 1.0,
        epsilon: float = 0.1,
        seed: int = 0,
    ):
        self.actions = ACTIONS
        self.alpha = float(alpha)
        self.gamma = float(gamma)
        self.epsilon = float(epsilon)
        self._q: dict[RetryState, dict[str, float]] = {
            state: {action: 0.0 for action in ACTIONS} for state in states
        }
        self._rng = np.random.default_rng(seed)
        order = self._rng.permutation(len(ACTIONS))
        self._tiebreak = {action: int(order[i]) for i, action in enumerate(ACTIONS)}

    # -- inspection --------------------------------------------------------

    def q(self, state: RetryState, action: str) -> float:
        return self._q[state][action]

    def table(self) -> dict[RetryState, dict[str, float]]:
        return {state: dict(values) for state, values in self._q.items()}

    def assert_covers(self, states: Iterable[RetryState]) -> None:
        missing = [s for s in states if s not in self._q]
        if missing:
            raise KeyError(
                f"{len(missing)} state(s) the server can construct are absent from the Q-table, "
                f"first: {missing[0]}. Serving them would silently fall back to a default action."
            )

    # -- acting ------------------------------------------------------------

    def greedy(self, state: RetryState) -> str:
        values = self._q[state]
        return max(self.actions, key=lambda a: (values[a], self._tiebreak[a]))

    def action_probs(self, state: RetryState) -> dict[str, float]:
        share = self.epsilon / len(self.actions)
        greedy = self.greedy(state)
        return {a: share + (1.0 - self.epsilon if a == greedy else 0.0) for a in self.actions}

    def decide(self, state: RetryState) -> Decision:
        """A retry decision is a decision: it carries a propensity like every
        other one in this project."""
        probs = self.action_probs(state)
        if self.epsilon > 0.0 and self._rng.random() < self.epsilon:
            chosen = self.actions[int(self._rng.integers(len(self.actions)))]
        else:
            chosen = max(probs, key=lambda a: probs[a])
        return Decision(chosen, probs, probs[chosen])

    # -- learning ----------------------------------------------------------

    def update(
        self, state: RetryState, action: str, reward: float, next_state: RetryState | None
    ) -> None:
        bootstrap = 0.0 if next_state is None else max(self._q[next_state].values())
        current = self._q[state][action]
        self._q[state][action] = current + self.alpha * (reward + self.gamma * bootstrap - current)
