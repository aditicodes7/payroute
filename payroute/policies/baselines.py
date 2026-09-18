"""R1 baselines.

Five policies with three distinct jobs:

  round_robin        the illustrative baseline — what people actually build,
                     and the one whose inability to react is the point
  uniform_random     the LOGGING policy: genuinely randomised, so its
                     propensities are honestly 1/K and R3 gets a dataset with
                     complete coverage
  always_cheapest    lowest fee for this transaction, success ignored
  historically_best  one fixed gateway forever — the strongest naive baseline
  best_per_segment   a frozen (method, issuer) -> gateway table

Every one of them returns a propensity. For the deterministic four that
propensity is 1.0, which is the honest answer and also precisely why their logs
are nearly useless for off-policy evaluation: every importance weight against
them is 0 or undefined. That is the real situation a payments company is in
with its production rule table, and it is the argument for why exploration has
to be deliberate rather than incidental.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

import numpy as np

from payroute.decisionlog import Decision
from payroute.sim import Transaction
from payroute.sim.config import SimulatorConfig


@runtime_checkable
class Policy(Protocol):
    policy_id: str
    policy_version: str

    def decide(self, txn: Transaction) -> Decision: ...


def _one_hot(gateways: list[str], chosen: str) -> dict[str, float]:
    return {name: (1.0 if name == chosen else 0.0) for name in gateways}


class RoundRobin:
    """Cycle through gateways in order. Ignores context, ignores health.

    Its propensity is 1.0 because it is deterministic. Logging 1/K here would
    produce correct-looking arithmetic, but only by coincidence — it works
    because round robin's assignment happens to be independent of context, and
    that is a property of this policy rather than a rule.
    """

    policy_id = "round_robin"
    policy_version = "1"

    def __init__(self, gateways: Iterable[str]):
        self.gateways = list(gateways)
        self._next = 0

    def decide(self, txn: Transaction) -> Decision:
        chosen = self.gateways[self._next % len(self.gateways)]
        self._next += 1
        return Decision(chosen, _one_hot(self.gateways, chosen), 1.0)


class UniformRandom:
    """Draws uniformly at random, so 1/K is simply true rather than asserted.

    This is the policy whose logs R3 evaluates against. Its value is coverage:
    every segment contains every gateway, so there is something to reweight.
    """

    policy_id = "uniform_random"
    policy_version = "1"

    def __init__(self, gateways: Iterable[str], seed: int = 0):
        self.gateways = list(gateways)
        self._rng = np.random.default_rng(seed)
        self._p = 1.0 / len(self.gateways)

    def decide(self, txn: Transaction) -> Decision:
        chosen = self.gateways[int(self._rng.integers(len(self.gateways)))]
        return Decision(chosen, {name: self._p for name in self.gateways}, self._p)


class AlwaysCheapest:
    """Lowest fee for THIS transaction — which is not a fixed gateway.

    A flat fee component makes "cheapest" depend on the amount: cirrus charges
    100 paise + 70bps, atlas charges 90bps and no flat fee, so they cross at
    50,000 paise. An implementation that picked one gateway forever would be
    wrong on most UPI traffic while calling itself cheapest.
    """

    policy_id = "always_cheapest"
    policy_version = "1"

    def __init__(self, config: SimulatorConfig):
        self.gateways = config.gateway_names
        self._fees = [(g.name, g.fee.flat_paise, g.fee.bps) for g in config.gateways]

    def decide(self, txn: Transaction) -> Decision:
        chosen = min(
            self._fees, key=lambda f: (f[1] + (f[2] * txn.amount_paise) // 10_000, f[0])
        )[0]
        return Decision(chosen, _one_hot(self.gateways, chosen), 1.0)


class _Fitted:
    """Shared machinery for baselines learned from a log.

    `train_seed` is carried so the runner can refuse to evaluate the policy on
    the run it was fitted from. Fitting on the transactions you are then scored
    against lets the baseline peek at the noise in its own test set: it looks
    better than it is, and whatever it is compared against looks worse by
    exactly the same amount.
    """

    def __init__(self, gateways: list[str], train_seed: int | None):
        self.gateways = gateways
        self.train_seed = train_seed


class HistoricallyBest(_Fitted):
    """One gateway, chosen once from history, used forever."""

    policy_id = "historically_best"
    policy_version = "1"

    def __init__(self, gateway: str, gateways: list[str], train_seed: int | None = None):
        super().__init__(gateways, train_seed)
        self.gateway = gateway

    @classmethod
    def fit(cls, log) -> "HistoricallyBest":
        stats: dict[str, list[int]] = {}
        for record in log:
            entry = stats.setdefault(record.action, [0, 0])
            entry[0] += int(record.outcome.success)
            entry[1] += 1
        if not stats:
            raise ValueError("cannot fit on an empty log")
        best = max(sorted(stats), key=lambda name: stats[name][0] / stats[name][1])
        return cls(best, sorted(stats), train_seed=log.seed)

    def decide(self, txn: Transaction) -> Decision:
        return Decision(self.gateway, _one_hot(self.gateways, self.gateway), 1.0)


class BestPerSegment(_Fitted):
    """A frozen (method, issuer) -> gateway lookup table.

    This is the baseline that makes R2's eventual result interpretable. It
    already captures the contextual structure LinUCB claims to learn, but it
    cannot move once fitted — so beating it isolates the adaptivity claim,
    while beating round robin on a clean run isolates the contextual one.
    """

    policy_id = "best_per_segment"
    policy_version = "1"

    def __init__(
        self,
        table: dict[tuple[str, str], str],
        fallback: str,
        gateways: list[str],
        train_seed: int | None = None,
    ):
        super().__init__(gateways, train_seed)
        self.table = dict(table)
        self.fallback = fallback

    @classmethod
    def fit(cls, log, min_samples: int = 30) -> "BestPerSegment":
        stats: dict[tuple[str, str, str], list[int]] = {}
        overall: dict[str, list[int]] = {}
        for record in log:
            key = (record.context["method"], record.context["issuer"], record.action)
            entry = stats.setdefault(key, [0, 0])
            entry[0] += int(record.outcome.success)
            entry[1] += 1
            entry = overall.setdefault(record.action, [0, 0])
            entry[0] += int(record.outcome.success)
            entry[1] += 1

        if not overall:
            raise ValueError("cannot fit on an empty log")
        gateways = sorted(overall)
        fallback = max(gateways, key=lambda name: overall[name][0] / overall[name][1])

        segments = {(m, i) for m, i, _ in stats}
        table: dict[tuple[str, str], str] = {}
        for segment in segments:
            # A gateway with too few observations in this segment is not
            # evidence, it is noise — leaving it out is what the fallback is for.
            candidates = [
                (name, stats[(segment[0], segment[1], name)])
                for name in gateways
                if stats.get((segment[0], segment[1], name), [0, 0])[1] >= min_samples
            ]
            if candidates:
                table[segment] = max(candidates, key=lambda c: c[1][0] / c[1][1])[0]

        return cls(table, fallback, gateways, train_seed=log.seed)

    def decide(self, txn: Transaction) -> Decision:
        chosen = self.table.get((txn.method, txn.issuer), self.fallback)
        return Decision(chosen, _one_hot(self.gateways, chosen), 1.0)
