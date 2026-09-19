"""R3: the reward model must never score a row it was fitted on.

Same leakage lesson as R1's fitted baselines, one rung later and with more at
stake: a reward model fitted on the rows it then scores is optimistically
accurate, so the Direct Method looks better than it is and Doubly Robust
inherits the flattery. Since DR is the estimator we intend to recommend, that
bias points directly at the conclusion we most want to be true.
"""

from __future__ import annotations

import pytest


def _log(clean_world, n=20_000, epsilon=0.2, seed=5):
    from payroute.features import Featuriser
    from payroute.policies import LinUCB
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    policy = LinUCB(clean_world.gateway_names, Featuriser.from_config(clean_world),
                    alpha=1.0, epsilon=epsilon, seed=11)
    return run_policy(Simulator(clean_world, seed=seed), policy, n)


def test_folds_partition_the_log_exactly(clean_world):
    from payroute.features import Featuriser
    from payroute.ope import CrossFittedRewardModel

    log = _log(clean_world, n=5_000)
    model = CrossFittedRewardModel.fit(log, Featuriser.from_config(clean_world),
                                       clean_world.gateway_names, folds=2)

    folds = [model.fold_of(r.txn_id) for r in log]
    assert set(folds) == {0, 1}
    assert len(folds) == len(log)
    assert abs(folds.count(0) - folds.count(1)) <= 1


def test_a_record_is_never_scored_by_a_model_that_saw_it(clean_world):
    """The guard, checked structurally rather than by inspection."""
    from payroute.features import Featuriser
    from payroute.ope import CrossFittedRewardModel

    log = _log(clean_world, n=5_000)
    model = CrossFittedRewardModel.fit(log, Featuriser.from_config(clean_world),
                                       clean_world.gateway_names, folds=2)

    for record in list(log)[:500]:
        assert model.fold_used_for(record.txn_id) != model.fold_of(record.txn_id)


def test_in_sample_fitting_looks_better_than_it_is(clean_world):
    """The bias made visible: the same model scores its own training rows more
    accurately than rows it has never seen. If these came out equal, the
    cross-fitting machinery would not be doing anything."""
    from payroute.features import Featuriser
    from payroute.ope import CrossFittedRewardModel, RidgeRewardModel

    log = _log(clean_world, n=20_000)
    featuriser = Featuriser.from_config(clean_world)

    in_sample = RidgeRewardModel.fit(list(log), featuriser, clean_world.gateway_names)
    cross = CrossFittedRewardModel.fit(log, featuriser, clean_world.gateway_names, folds=2)

    def mse(predict):
        errors = [(predict(r) - float(r.outcome.success)) ** 2 for r in log]
        return sum(errors) / len(errors)

    in_sample_mse = mse(lambda r: in_sample.predict(r.context, r.action))
    cross_mse = mse(lambda r: cross.predict(r, r.action))
    assert in_sample_mse < cross_mse


def test_the_reward_model_predicts_success_probability_reasonably(clean_world):
    """DR only helps if the model is worth something: the point of the residual
    correction is that the residuals are small."""
    from payroute.features import Featuriser
    from payroute.ope import CrossFittedRewardModel
    from tests.conftest import true_prob

    log = _log(clean_world, n=40_000)
    model = CrossFittedRewardModel.fit(log, Featuriser.from_config(clean_world),
                                       clean_world.gateway_names, folds=2)

    errors = []
    for record in list(log)[:2_000]:
        truth = true_prob(clean_world, record.action, record.context["method"], record.context["issuer"])
        errors.append(abs(model.predict(record, record.action) - truth))
    assert sum(errors) / len(errors) < 0.03, "reward model is too weak for DR to lean on"
