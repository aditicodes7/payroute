"""R5: maximise expected net revenue subject to a p95 latency budget.

Two things make this more than an argmax with a filter.

THE FEASIBLE SET IS A GUARANTEE, NOT A HOPE. Keeping only gateways whose recent
p95 is within budget means the fleet p95 is within budget by construction: a
mixture of distributions each with P(latency > B) <= 0.05 has the same property.
It is conservative — a portfolio constraint could spend 5% of traffic on a slow
but excellent gateway — and that conservatism is stated rather than hidden.

INFEASIBILITY IS RECORDED, NEVER RELAXED. When nothing meets the budget the
policy keeps routing, because a payment router that stops is worse than a slow
one, but it switches its objective to minimising latency and marks the decision
`infeasible`. The violation rate is then a number reported next to the p95, and
the claim is "the constraint held on 99.x% of decisions" rather than an
unqualified "the constraint holds".

A consequence worth knowing: an excluded gateway has propensity exactly zero,
which is R3's no-overlap condition. The more aggressively this policy
constrains, the less its log can say about the alternatives it refused to try.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Mapping

from payroute.decisionlog import Decision
from payroute.features import Featuriser
from payroute.features.health import HealthConfig, TerminalHealth
from payroute.features.latency import LatencyConfig, LatencyWindow
from payroute.policies.adaptive import AdaptiveLinUCB
from payroute.policies.objective import expected_net_revenue
from payroute.sim import Transaction

SATISFIED = "satisfied"
INFEASIBLE = "infeasible"


class ConstrainedLinUCB(AdaptiveLinUCB):
    policy_id = "constrained_linucb"
    policy_version = "1"

    def __init__(
        self,
        gateways,
        featuriser: Featuriser,
        health: TerminalHealth,
        latency: LatencyWindow,
        p95_budget_ms: float,
        take_rate_bps: int,
        fees: Mapping[str, tuple[int, int]],
        **kwargs,
    ):
        super().__init__(gateways, featuriser, health, **kwargs)
        self.latency = latency
        self.p95_budget_ms = float(p95_budget_ms)
        self.take_rate_bps = int(take_rate_bps)
        self.fees = dict(fees)

    @classmethod
    def for_config(
        cls,
        config,
        p95_budget_ms: float = 420.0,
        half_life_minutes: float = 5.0,
        # 60 minutes, not 10. A gateway that has been excluded receives only
        # exploration traffic — about 1.7% at epsilon 0.05 — so at night rates a
        # ten-minute window holds roughly eight of its samples, never enough to
        # trust a quantile. It then sits at the optimistic prior permanently.
        # Measured: 2.31% of decisions routed to the slow gateway at a
        # ten-minute window against 0.33% at sixty (docs/bugs.md).
        latency_window_minutes: float = 60.0,
        epsilon: float = 0.05,
        alpha: float = 1.0,
        ridge: float = 1.0,
        seed: int = 0,
        prior_successes: float = 9.0,
        prior_trials: float = 10.0,
        feedback_delay: timedelta = timedelta(0),
        prior_p95_ms: float = 250.0,
    ) -> "ConstrainedLinUCB":
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
        latency = LatencyWindow(
            gateways, LatencyConfig(window_minutes=latency_window_minutes, prior_p95_ms=prior_p95_ms)
        )
        return cls(
            gateways,
            Featuriser.from_config(config, health_gateways=gateways),
            health,
            latency,
            p95_budget_ms=p95_budget_ms,
            take_rate_bps=config.take_rate_bps,
            fees={g.name: (g.fee.flat_paise, g.fee.bps) for g in config.gateways},
            alpha=alpha,
            epsilon=epsilon,
            ridge=ridge,
            seed=seed,
        )

    # -- context -----------------------------------------------------------

    def context_for(self, txn: Transaction) -> dict:
        context = super().context_for(txn)
        # The p95s go in the context rather than being read from the store at
        # selection time, so that action_probs stays a pure function of context
        # — which is what makes this policy evaluable as an R3 target at all.
        for gateway, value in self.latency.p95_at(txn.ts).items():
            context[f"p95_{gateway}"] = value
        context["amount_paise_for_objective"] = context["amount_paise"]
        return context

    # -- selection ---------------------------------------------------------

    def expected_net(self, context: Mapping, gateway: str) -> float:
        """Optimism on the revenue scale: the upper confidence bound on the
        success probability, converted to money. Using the mean instead would
        throw away LinUCB's directed exploration; using the UCB score directly
        would be optimistic in the wrong units."""
        x = self.featuriser.transform(context)
        import numpy as np

        upper = float(self.theta(gateway) @ x) + self.alpha * float(
            np.sqrt(max(0.0, x @ self._A_inv[gateway] @ x))
        )
        flat, bps = self.fees[gateway]
        return expected_net_revenue(
            amount_paise=context["amount_paise"],
            take_rate_bps=self.take_rate_bps,
            flat_paise=flat,
            bps=bps,
            p_success=min(1.0, max(0.0, upper)),
        )

    def feasible_gateways(self, context: Mapping) -> list[str]:
        return [g for g in self.gateways if context[f"p95_{g}"] <= self.p95_budget_ms]

    def constraint_status(self, context: Mapping) -> str:
        return SATISFIED if self.feasible_gateways(context) else INFEASIBLE

    def action_probs(self, context: Mapping) -> dict[str, float]:
        feasible = self.feasible_gateways(context)
        if feasible:
            greedy = max(
                feasible, key=lambda g: (self.expected_net(context, g), self._tiebreak[g])
            )
            candidates = feasible
        else:
            # Nothing meets the budget. Keep routing, but optimise the thing the
            # constraint was protecting, and keep every gateway in play so the
            # log stays able to see a recovery.
            greedy = min(
                self.gateways, key=lambda g: (context[f"p95_{g}"], self._tiebreak[g])
            )
            candidates = self.gateways

        share = self.epsilon / len(candidates)
        probs = {g: 0.0 for g in self.gateways}
        for gateway in candidates:
            probs[gateway] = share + (1.0 - self.epsilon if gateway == greedy else 0.0)
        return probs

    def decide_from_context(self, context: Mapping) -> Decision:
        probs = self.action_probs(context)
        candidates = [g for g, p in probs.items() if p > 0.0]

        if self.epsilon > 0.0 and self._rng.random() < self.epsilon:
            chosen = candidates[int(self._rng.integers(len(candidates)))]
        else:
            chosen = max(candidates, key=lambda g: probs[g])

        return Decision(
            chosen,
            probs,
            probs[chosen],
            constraint_status=self.constraint_status(context),
            predicted_p95_ms=context[f"p95_{chosen}"],
        )

    def decide(self, txn: Transaction) -> Decision:
        return self.decide_from_context(self.context_for(txn))

    # -- feedback ----------------------------------------------------------

    def observe(self, txn: Transaction, gateway: str, outcome) -> None:
        super().observe(txn, gateway, outcome)
        self.latency.observe(gateway, outcome.latency_ms, at=txn.ts)
