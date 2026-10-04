"""R4: LinUCB with time-decayed terminal health in its context.

R2 measured the problem this fixes. LinUCB's A and b are unweighted sums over
all history, so after 75,000 observations a 40-minute outage is a vanishing
fraction of the evidence: its regret accelerated at the incident and was still
climbing 75,000 transactions later, while a frozen lookup table absorbed the
hit and returned to flat.

The decay lives in the FEATURES rather than in the sufficient statistics,
because the world has two timescales. The (method, issuer, gateway) quality
matrix is stationary; gateway health moves in minutes. Discounting A and b
decays both at the same rate, buying adaptivity by permanently degrading the
estimate of the thing that never changes. `DiscountedLinUCB` below implements
that alternative so the choice can be measured rather than argued.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable

import numpy as np

from payroute.features import Featuriser
from payroute.features.health import HealthConfig, TerminalHealth
from payroute.policies.linucb import LinUCB
from payroute.policies.runner import context_of
from payroute.sim import Transaction


class AdaptiveLinUCB(LinUCB):
    policy_id = "adaptive_linucb"
    policy_version = "1"

    def __init__(self, gateways, featuriser, health: TerminalHealth, **kwargs):
        super().__init__(gateways, featuriser, **kwargs)
        self.health = health

    @classmethod
    def for_config(
        cls,
        config,
        half_life_minutes: float = 5.0,
        epsilon: float = 0.05,
        alpha: float = 1.0,
        ridge: float = 1.0,
        seed: int = 0,
        prior_successes: float = 9.0,
        prior_trials: float = 10.0,
        feedback_delay: timedelta = timedelta(0),
    ) -> "AdaptiveLinUCB":
        gateways = config.gateway_names
        health = TerminalHealth(
            gateways,
            HealthConfig(
                half_life_minutes=half_life_minutes,
                prior_successes=prior_successes,
                prior_trials=prior_trials,
                feedback_delay=feedback_delay,
            ),
        )
        featuriser = Featuriser.from_config(config, health_gateways=gateways)
        return cls(gateways, featuriser, health, alpha=alpha, epsilon=epsilon, ridge=ridge, seed=seed)

    def context_for(self, txn: Transaction) -> dict:
        context = context_of(txn)
        # health_at only folds in outcomes already observed by this instant, so
        # a decision can never see its own result.
        for gateway, value in self.health.health_at(txn.ts).items():
            context[f"health_{gateway}"] = value
        return context

    def observe(self, txn: Transaction, gateway: str, outcome) -> None:
        self.health.observe(gateway, outcome.success, at=txn.ts)


class DiscountedLinUCB(LinUCB):
    """The alternative design, kept as a control.

    Forgets by shrinking the sufficient statistics each update. One line, and it
    does adapt — but it discounts the stationary structure at the same rate as
    the health signal, which is the cost R4 exists to measure.
    """

    policy_id = "discounted_linucb"
    policy_version = "1"

    def __init__(self, gateways, featuriser, gamma: float = 0.9995, **kwargs):
        super().__init__(gateways, featuriser, **kwargs)
        self.gamma = float(gamma)

    def update(self, context, gateway: str, reward: float) -> None:
        self._A[gateway] = self.gamma * self._A[gateway] + (1.0 - self.gamma) * self.ridge * np.eye(self.dim)
        self._b[gateway] = self.gamma * self._b[gateway]
        # The incremental inverse is not valid under a global rescale, so it is
        # rebuilt here. Slower per update, and correct — which is the right way
        # round for a control.
        self._A_inv[gateway] = np.linalg.inv(self._A[gateway])
        super().update(context, gateway, reward)
