"""R3: the whole point — scoring the estimators against an answer key.

Because the world is synthetic we know V(pi_e) exactly. A real deployment can
only compare one estimate against another; here we can ask which estimator is
actually right, and watch what happens to each as the logging policy becomes
more deterministic.

This is the load-bearing negative result of the project: OPE degrades as
exploration shrinks, and at zero exploration it does not degrade gracefully —
it stops being defined.
"""

from __future__ import annotations

import pytest


def _logging_policy(cfg, epsilon):
    from payroute.features import Featuriser
    from payroute.policies import LinUCB

    return LinUCB(cfg.gateway_names, Featuriser.from_config(cfg), alpha=1.0, epsilon=epsilon, seed=11)


def _run(cfg, epsilon, n=40_000, seed=5):
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    sim = Simulator(cfg, seed=seed)
    return sim, run_policy(sim, _logging_policy(cfg, epsilon), n)


def _target(cfg, training_log):
    from payroute.policies import BestPerSegment

    return BestPerSegment.fit(training_log)


def test_oracle_gives_an_exact_value_for_a_stochastic_target(clean_world):
    """The answer key itself, checked before anything is scored against it: the
    exact value must agree with what the policy actually earns when run."""
    from payroute.decisionlog.metrics import summarize
    from payroute.policies import UniformRandom
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    sim = Simulator(clean_world, seed=5)
    policy = UniformRandom(clean_world.gateway_names, seed=3)
    log = run_policy(sim, policy, 60_000)

    replay = sim.fresh()
    txns = list(replay.transactions(len(log)))
    truth = replay.oracle.true_success_value(lambda t: policy.action_probs(), txns)

    assert truth == pytest.approx(summarize(log).success_rate, abs=0.005)


def test_ips_is_unbiased_when_overlap_is_healthy(clean_world):
    """IPS's guarantee, verified rather than quoted: with plenty of exploration
    its interval should cover the true value."""
    from payroute.ope import ips
    from payroute.policies import UniformRandom
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    sim = Simulator(clean_world, seed=5)
    logger = UniformRandom(clean_world.gateway_names, seed=3)
    log = run_policy(sim, logger, 60_000)
    target = _target(clean_world, log)

    replay = sim.fresh()
    txns = list(replay.transactions(len(log)))
    truth = replay.oracle.true_success_value(
        lambda t: target.action_probs({"method": t.method, "issuer": t.issuer}), txns
    )

    result = ips(log, target)
    assert result.ci[0] <= truth <= result.ci[1], (
        f"truth {truth:.4f} outside IPS interval [{result.ci[0]:.4f}, {result.ci[1]:.4f}]"
    )


def test_ips_variance_explodes_as_exploration_shrinks(clean_world):
    """The headline negative result, as a monotonicity assertion."""
    from payroute.ope import ips

    widths, esss = [], []
    for epsilon in (0.30, 0.10, 0.02):
        sim, log = _run(clean_world, epsilon)
        target = _target(clean_world, _run(clean_world, 0.30, n=40_000, seed=101)[1])
        result = ips(log, target)
        widths.append(result.ci[1] - result.ci[0])
        esss.append(result.ess)

    assert widths == sorted(widths), f"IPS interval did not widen as epsilon fell: {widths}"
    assert esss == sorted(esss, reverse=True), f"ESS did not fall as epsilon fell: {esss}"


def test_doubly_robust_is_more_stable_than_ips_at_low_exploration(clean_world):
    """DR's residual trick, measured: the importance weight multiplies
    (r - r_hat) rather than r, so a weight of 50 multiplies something near zero.
    """
    from payroute.features import Featuriser
    from payroute.ope import CrossFittedRewardModel, doubly_robust, ips

    sim, log = _run(clean_world, 0.02, n=60_000)
    target = _target(clean_world, _run(clean_world, 0.30, n=40_000, seed=101)[1])
    model = CrossFittedRewardModel.fit(log, Featuriser.from_config(clean_world),
                                       clean_world.gateway_names, folds=2)

    replay = sim.fresh()
    txns = list(replay.transactions(len(log)))
    truth = replay.oracle.true_success_value(
        lambda t: target.action_probs({"method": t.method, "issuer": t.issuer}), txns
    )

    ips_result = ips(log, target)
    dr_result = doubly_robust(log, target, reward_model=model.predict)

    assert abs(dr_result.estimate - truth) < abs(ips_result.estimate - truth)
    assert (dr_result.ci[1] - dr_result.ci[0]) < (ips_result.ci[1] - ips_result.ci[0])


def test_the_direct_method_is_confidently_wrong_where_coverage_is_poor(clean_world):
    """DM never looks at a propensity, so it cannot know which of its
    predictions are extrapolation. Its interval stays narrow while its error
    grows — the most dangerous failure mode in the set, because it looks like
    the most trustworthy result."""
    from payroute.features import Featuriser
    from payroute.ope import CrossFittedRewardModel, direct_method

    sim, log = _run(clean_world, 0.02, n=60_000)
    target = _target(clean_world, _run(clean_world, 0.30, n=40_000, seed=101)[1])
    model = CrossFittedRewardModel.fit(log, Featuriser.from_config(clean_world),
                                       clean_world.gateway_names, folds=2)

    result = direct_method(log, target, reward_model=model.predict)
    width = result.ci[1] - result.ci[0]
    assert width < 0.01, f"DM interval {width:.4f} — expected it to look reassuringly tight"


def test_a_deterministic_log_cannot_evaluate_a_different_policy(clean_world):
    """The end of the spectrum. At epsilon = 0 this is not a variance problem
    that more data would fix — the estimand is not identified."""
    from payroute.ope import NoOverlapError, ips

    sim, log = _run(clean_world, 0.0, n=20_000)
    target = _target(clean_world, _run(clean_world, 0.30, n=40_000, seed=101)[1])

    assert all(r.propensity == 1.0 for r in log)
    with pytest.raises(NoOverlapError):
        ips(log, target)
