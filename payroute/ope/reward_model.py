"""The reward model the Direct Method and Doubly Robust lean on.

One ridge regression per gateway, predicting success probability from the same
features the policy sees. Cross-fitted, because a model fitted on the rows it
then scores is optimistically accurate — which would make DM look better than
it is and DR inherit the flattery, biasing both toward the conclusion we most
want to be true.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

import numpy as np

from payroute.decisionlog import DecisionRecord
from payroute.features import Featuriser


class RidgeRewardModel:
    def __init__(self, thetas: dict[str, np.ndarray], featuriser: Featuriser):
        self.thetas = thetas
        self.featuriser = featuriser

    @classmethod
    def fit(
        cls,
        records: Iterable[DecisionRecord],
        featuriser: Featuriser,
        gateways: Sequence[str],
        ridge: float = 1.0,
    ) -> "RidgeRewardModel":
        d = featuriser.dim
        A = {g: ridge * np.eye(d) for g in gateways}
        b = {g: np.zeros(d) for g in gateways}

        for record in records:
            x = featuriser.transform(record.context)
            A[record.action] += np.outer(x, x)
            b[record.action] += float(record.outcome.success) * x

        return cls({g: np.linalg.solve(A[g], b[g]) for g in gateways}, featuriser)

    def predict(self, context: Mapping, action: str) -> float:
        x = self.featuriser.transform(context)
        # Clipped because the estimand is a probability; an unclipped linear
        # model will happily predict 1.03 and make DR's residuals worse.
        return float(min(1.0, max(0.0, self.thetas[action] @ x)))


class CrossFittedRewardModel:
    """k models, each fitted on the folds it will not be asked to score."""

    def __init__(self, models: list[RidgeRewardModel], folds: dict[str, int], n_folds: int):
        self.models = models
        self._folds = folds
        self.n_folds = n_folds

    @classmethod
    def fit(
        cls,
        log,
        featuriser: Featuriser,
        gateways: Sequence[str],
        folds: int = 2,
        ridge: float = 1.0,
        seed: int = 0,
    ) -> "CrossFittedRewardModel":
        records = list(log)
        rng = np.random.default_rng(seed)
        order = rng.permutation(len(records))

        assignment: dict[str, int] = {}
        for position, index in enumerate(order):
            assignment[records[index].txn_id] = position % folds

        models = []
        for fold in range(folds):
            training = [r for r in records if assignment[r.txn_id] != fold]
            models.append(RidgeRewardModel.fit(training, featuriser, gateways, ridge=ridge))
        return cls(models, assignment, folds)

    def fold_of(self, txn_id: str) -> int:
        return self._folds[txn_id]

    def training_folds_for(self, txn_id: str) -> frozenset[int]:
        """The folds the model that scores this record was fitted on — never
        including the record's own."""
        own = self.fold_of(txn_id)
        return frozenset(f for f in range(self.n_folds) if f != own)

    def fold_used_for(self, txn_id: str) -> int:
        return min(self.training_folds_for(txn_id))

    def predict(self, record: DecisionRecord, action: str) -> float:
        return self.models[self.fold_of(record.txn_id)].predict(record.context, action)
