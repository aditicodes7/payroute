"""R2: does it actually learn, and does it beat R1?

The rung's acceptance criteria: the regret curve flattens, and a propensity
exists for every decision. Both are asserted here rather than eyeballed.
"""

from __future__ import annotations

import pytest

from config.default_world import build_default_world


def _policy(cfg, **kwargs):
    from payroute.features import Featuriser
    from payroute.policies import LinUCB

    params = dict(alpha=1.0, epsilon=0.05, ridge=1.0, seed=11)
    params.update(kwargs)
    return LinUCB(cfg.gateway_names, Featuriser.from_config(cfg), **params)


def _sim(cfg, seed=909):
    from payroute.sim import Simulator

    return Simulator(cfg, seed=seed)


def test_the_runner_feeds_rewards_back(clean_world):
    """Without this the bandit is just an expensive random policy. Asserted
    because a silently-not-learning policy still produces plausible output."""
    import numpy as np

    from payroute.policies.runner import run_policy

    policy = _policy(clean_world)
    before = {n: policy.A(n).copy() for n in clean_world.gateway_names}
    run_policy(_sim(clean_world), policy, 3_000)

    changed = [n for n in clean_world.gateway_names if not np.allclose(policy.A(n), before[n])]
    assert len(changed) == len(clean_world.gateway_names), f"only {changed} were updated"


def test_regret_curve_flattens(clean_world):
    """The learning criterion, made testable.

    Regret is computed exactly from the oracle — we know the true best arm for
    every context, so this is not an estimate. Average regret over the last
    fifth of the run must be well below the first fifth: that is what sublinear
    cumulative regret looks like when you only have one run.
    """
    from payroute.policies.runner import run_policy
    from payroute.ope.regret import instantaneous_regret

    sim = _sim(clean_world)
    log = run_policy(sim, _policy(clean_world), 60_000)
    regret = instantaneous_regret(sim, log)

    fifth = len(regret) // 5
    early = sum(regret[:fifth]) / fifth
    late = sum(regret[-fifth:]) / fifth

    assert late < 0.5 * early, f"average regret {early:.4f} -> {late:.4f}: not learning"
    assert late < 0.02, f"average regret settled at {late:.4f}, too high to call it converged"


def test_cumulative_regret_is_sublinear(clean_world):
    """Stronger than 'the chart looks flat': doubling the horizon must less
    than double the accumulated regret."""
    from payroute.policies.runner import run_policy
    from payroute.ope.regret import instantaneous_regret

    sim = _sim(clean_world)
    log = run_policy(sim, _policy(clean_world), 60_000)
    regret = instantaneous_regret(sim, log)

    half = sum(regret[: len(regret) // 2])
    full = sum(regret)
    assert full < 1.6 * half, f"regret grew linearly: {half:.1f} -> {full:.1f}"


def test_linucb_beats_round_robin_on_success(clean_world):
    """Paired on identical transactions, and the interval must exclude zero —
    rule 10 applies to this rung's headline claim too."""
    from payroute.decisionlog.metrics import paired_difference
    from payroute.policies import RoundRobin
    from payroute.policies.runner import run_policy

    baseline = run_policy(_sim(clean_world), RoundRobin(clean_world.gateway_names), 60_000)
    learned = run_policy(_sim(clean_world), _policy(clean_world), 60_000)

    diff = paired_difference(baseline, learned)
    assert diff.success_ci[0] > 0, (
        f"success delta {diff.success_rate_delta:+.3%} with interval "
        f"[{diff.success_ci[0]:+.3%}, {diff.success_ci[1]:+.3%}] — not a result"
    )


def test_linucb_is_competitive_with_the_frozen_segment_table(clean_world):
    """best_per_segment already knows the contextual structure, so this is the
    comparison that says whether LinUCB's machinery earns its keep rather than
    merely beating a policy that ignores context."""
    from payroute.decisionlog.metrics import paired_difference
    from payroute.policies import BestPerSegment, UniformRandom
    from payroute.policies.runner import run_policy

    training = run_policy(
        _sim(clean_world, seed=101), UniformRandom(clean_world.gateway_names, seed=101), 60_000
    )
    frozen = BestPerSegment.fit(training)

    a = run_policy(_sim(clean_world), frozen, 60_000)
    b = run_policy(_sim(clean_world), _policy(clean_world), 60_000)

    diff = paired_difference(a, b)
    assert diff.success_ci[1] > -0.01, (
        f"LinUCB is materially worse than a frozen lookup table: "
        f"{diff.success_rate_delta:+.3%} [{diff.success_ci[0]:+.3%}, {diff.success_ci[1]:+.3%}]"
    )


def test_every_linucb_decision_logs_a_propensity(clean_world):
    """The rung's second acceptance criterion. The parametrised invariant test
    in tests/r1 covers this too, via payroute.policies.__all__ — this is the
    explicit version for the policy the criterion was written about."""
    from payroute.policies.runner import run_policy

    log = run_policy(_sim(clean_world), _policy(clean_world), 20_000)
    assert len(log) == 20_000
    for record in log:
        assert 0.0 < record.propensity <= 1.0
        assert record.propensity == pytest.approx(record.action_probs[record.action])
    assert log.validate() == []


def test_a_deterministic_run_produces_a_log_that_cannot_be_reweighted(clean_world):
    """Stated as a test because it is the argument, not a detail: at epsilon=0
    every propensity is 1.0 and every alternative action has probability zero,
    so no other policy can be evaluated from this log at all."""
    from payroute.policies.runner import run_policy

    log = run_policy(_sim(clean_world), _policy(clean_world, epsilon=0.0), 5_000)
    assert all(r.propensity == 1.0 for r in log)
    assert all(sorted(r.action_probs.values()) == [0.0, 0.0, 1.0] for r in log)
