"""LinUCB with an epsilon-greedy exploration wrapper.

Per arm, a ridge regression and a confidence bound:

    A_a = lambda*I + sum x x^T      b_a = sum r x      theta_a = A_a^-1 b_a
    score(a) = theta_a . x  +  alpha * sqrt(x^T A_a^-1 x)

The second term is the whole point. A_a^-1 is essentially the inverse
covariance of the data that arm has seen, so x^T A_a^-1 x is large exactly
where the arm has little data IN THAT DIRECTION. Uncertainty is directional:
the policy can be confident about UPI-HDFC and clueless about netbanking-Kotak
on the same arm, and it explores only where it is ignorant. That is what makes
it more data-efficient than a per-segment lookup table, which needs
observations in every cell before it knows anything.

Reward is success (0/1), not net revenue. UCB's bounds assume a bounded reward
with well-behaved noise; net revenue is heavy-tailed and would swamp the signal
with amount variance. R5 computes expected revenue analytically from the
learned probability instead — there is no reason to learn what can be computed.

EXPLORATION AND PROPENSITY. Textbook LinUCB takes the argmax, so its honest
propensity is 1.0 and its log can evaluate nothing. The epsilon-greedy wrapper
gives an exactly computable propensity:

    pi(a|x) = (1 - eps) + eps/K   for the greedy arm,   eps/K otherwise

Thompson sampling was considered and rejected: its propensity has no closed
form, and an approximate propensity in a denominator is a bias nothing
downstream can detect.
"""

from __future__ import annotations

from typing import Iterable, Mapping

import numpy as np

from payroute.decisionlog import Decision
from payroute.features import Featuriser
from payroute.policies.runner import context_of
from payroute.sim import Transaction


class LinUCB:
    policy_id = "linucb"
    policy_version = "1"

    def __init__(
        self,
        gateways: Iterable[str],
        featuriser: Featuriser,
        alpha: float = 1.0,
        epsilon: float = 0.05,
        ridge: float = 1.0,
        seed: int = 0,
    ):
        self.gateways = list(gateways)
        self.featuriser = featuriser
        self.alpha = float(alpha)
        self.epsilon = float(epsilon)
        self.ridge = float(ridge)
        self.dim = featuriser.dim

        d = self.dim
        self._A = {name: self.ridge * np.eye(d) for name in self.gateways}
        self._A_inv = {name: np.eye(d) / self.ridge for name in self.gateways}
        self._b = {name: np.zeros(d) for name in self.gateways}

        self._rng = np.random.default_rng(seed)
        # A fixed, seed-derived priority used only to break exact ties. At t=0
        # every arm scores identically; an argmax that breaks ties by list order
        # would hand all early traffic to one gateway and start the bandit from
        # a skewed sample. Drawn once rather than per decision so that repeated
        # decisions on the same context stay reproducible.
        self._tiebreak = dict(zip(self.gateways, self._rng.permutation(len(self.gateways))))

    # -- inspection --------------------------------------------------------

    def A(self, gateway: str) -> np.ndarray:
        return self._A[gateway]

    def A_inv(self, gateway: str) -> np.ndarray:
        return self._A_inv[gateway]

    def b(self, gateway: str) -> np.ndarray:
        return self._b[gateway]

    def theta(self, gateway: str) -> np.ndarray:
        return self._A_inv[gateway] @ self._b[gateway]

    def confidence_width(self, context: Mapping, gateway: str) -> float:
        x = self.featuriser.transform(context)
        return float(np.sqrt(max(0.0, x @ self._A_inv[gateway] @ x)))

    def scores(self, context: Mapping) -> dict[str, float]:
        x = self.featuriser.transform(context)
        return {
            name: float(self.theta(name) @ x)
            + self.alpha * float(np.sqrt(max(0.0, x @ self._A_inv[name] @ x)))
            for name in self.gateways
        }

    def context_for(self, txn: Transaction) -> dict:
        """The one place that decides what this policy sees. R4's adaptive
        variant overrides it to add decayed health, and nothing else changes."""
        return context_of(txn)

    def scores_for(self, txn: Transaction) -> dict[str, float]:
        return self.scores(self.context_for(txn))

    def greedy_action(self, context: Mapping) -> str:
        scores = self.scores(context)
        return max(self.gateways, key=lambda n: (scores[n], self._tiebreak[n]))

    # -- acting ------------------------------------------------------------

    def action_probs(self, context: Mapping) -> dict[str, float]:
        k = len(self.gateways)
        share = self.epsilon / k
        greedy = self.greedy_action(context)
        return {name: share + (1.0 - self.epsilon if name == greedy else 0.0) for name in self.gateways}

    def decide(self, txn: Transaction) -> Decision:
        context = self.context_for(txn)
        probs = self.action_probs(context)

        if self.epsilon > 0.0 and self._rng.random() < self.epsilon:
            chosen = self.gateways[int(self._rng.integers(len(self.gateways)))]
        else:
            chosen = max(probs, key=lambda n: probs[n])

        return Decision(chosen, probs, probs[chosen])

    # -- learning ----------------------------------------------------------

    def update(self, context: Mapping, gateway: str, reward: float) -> None:
        """Bandit feedback: only the arm we actually pulled is updated."""
        x = self.featuriser.transform(context)
        self._A[gateway] = self._A[gateway] + np.outer(x, x)
        self._b[gateway] = self._b[gateway] + reward * x

        # Sherman-Morrison rank-one update, so scoring is a matrix-vector
        # product rather than a 16x16 inversion on every decision. The drift
        # this can accumulate is checked by a test rather than assumed away.
        inv = self._A_inv[gateway]
        Ax = inv @ x
        self._A_inv[gateway] = inv - np.outer(Ax, Ax) / (1.0 + float(x @ Ax))
