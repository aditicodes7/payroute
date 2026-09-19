"""R2: the exploration wrapper and its propensities.

Textbook LinUCB takes the argmax, so its honest propensity is 1.0 and its log
can evaluate nothing — every importance weight is 0 or undefined. The
epsilon-greedy wrapper exists to fix that, and its propensity is exact:

    pi(a|x) = (1 - eps) + eps/K   if a is the argmax
              eps/K                otherwise

These tests care about one thing above all: that the logged propensity is what
the policy ACTUALLY does, not what it claims to do. An approximate or wishful
propensity is worse than none, because nothing downstream can detect it.
"""

from __future__ import annotations

import pytest

from tests.conftest import assert_within_band


def _policy(cfg, **kwargs):
    from payroute.features import Featuriser
    from payroute.policies import LinUCB

    params = dict(alpha=1.0, epsilon=0.1, ridge=1.0, seed=11)
    params.update(kwargs)
    return LinUCB(cfg.gateway_names, Featuriser.from_config(cfg), **params)


def _txn(cfg, seed=1):
    from payroute.sim import Simulator

    return next(iter(Simulator(cfg, seed=seed).transactions(1)))


def test_epsilon_zero_is_deterministic_with_propensity_one(clean_world):
    """Kept as a runnable configuration on purpose: it is the best-performing
    policy and the one whose log is provably unusable for OPE. R3 needs that
    end of the spectrum to exist."""
    policy = _policy(clean_world, epsilon=0.0)
    txn = _txn(clean_world)

    decisions = [policy.decide(txn) for _ in range(50)]
    assert len({d.action for d in decisions}) == 1
    for d in decisions:
        assert d.propensity == 1.0
        assert sorted(d.action_probs.values()) == [0.0, 0.0, 1.0]


def test_propensities_follow_the_epsilon_greedy_formula(clean_world):
    policy = _policy(clean_world, epsilon=0.3)
    txn = _txn(clean_world)
    k = len(clean_world.gateways)

    # A fresh policy has no unique greedy arm: theta = 0 and A = ridge*I for
    # every gateway, so all three scores are identical and "the argmax" is
    # whatever the tie-break says. Give it enough asymmetric evidence for one
    # arm to be genuinely ahead, so this test measures the probability formula
    # rather than a tie-break convention.
    from payroute.policies.runner import context_of

    context = context_of(txn)
    for _ in range(200):
        policy.update(context, "beacon", 1.0)
        policy.update(context, "atlas", 0.0)
    scores = policy.scores(context)
    assert len(set(scores.values())) == k, "warm-up failed to break the tie"

    decision = policy.decide(txn)
    greedy = max(policy.scores_for(txn), key=lambda n: policy.scores_for(txn)[n])

    probs = decision.action_probs
    assert probs[greedy] == pytest.approx(0.7 + 0.3 / k)
    for name in clean_world.gateway_names:
        if name != greedy:
            assert probs[name] == pytest.approx(0.3 / k)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert decision.propensity == pytest.approx(probs[decision.action])


def test_epsilon_one_is_uniform(clean_world):
    policy = _policy(clean_world, epsilon=1.0)
    decision = policy.decide(_txn(clean_world))
    assert all(p == pytest.approx(1 / 3) for p in decision.action_probs.values())


@pytest.mark.parametrize("epsilon", [0.05, 0.2, 0.5])
def test_logged_propensities_match_what_the_policy_actually_does(clean_world, epsilon):
    """The test that matters most in this file.

    Hold the context fixed and stop learning, so the action distribution is
    stationary; then draw many decisions and check the observed frequency of
    each action against the probability the policy claimed for it. If these
    disagree, every off-policy estimate built on this log is wrong in a way
    nothing downstream can see.
    """
    policy = _policy(clean_world, epsilon=epsilon)
    txn = _txn(clean_world)

    n = 30_000
    claimed = policy.decide(txn).action_probs
    counts = {name: 0 for name in clean_world.gateway_names}
    for _ in range(n):
        counts[policy.decide(txn).action] += 1

    for name, count in counts.items():
        assert_within_band(count / n, claimed[name], n, label=f"eps={epsilon} action={name}")


def test_cold_start_ties_are_broken_randomly_not_by_list_order(clean_world):
    """At t=0 every arm scores identically, so a naive argmax hands all early
    traffic to whichever gateway happens to be first in the list — and the
    bandit starts from a badly skewed sample it then has to dig out of."""
    from payroute.features import Featuriser
    from payroute.policies import LinUCB

    txn = _txn(clean_world)
    first_choices = []
    for seed in range(40):
        policy = LinUCB(
            clean_world.gateway_names,
            Featuriser.from_config(clean_world),
            alpha=1.0,
            epsilon=0.0,
            ridge=1.0,
            seed=seed,
        )
        first_choices.append(policy.decide(txn).action)

    assert len(set(first_choices)) > 1, (
        f"every cold-start decision went to {first_choices[0]} — ties are being broken by order"
    )


def test_exploration_reaches_every_arm_in_every_segment(clean_world):
    """Coverage is what R3 actually needs from this log. A policy that never
    tries an arm in a segment leaves the estimators nothing to reweight there,
    however good its propensities are elsewhere."""
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    log = run_policy(Simulator(clean_world, seed=5), _policy(clean_world, epsilon=0.2), 60_000)

    seen: dict[tuple[str, str], set[str]] = {}
    for record in log:
        key = (record.context["method"], record.context["issuer"])
        seen.setdefault(key, set()).add(record.action)

    missing = {k: set(clean_world.gateway_names) - v for k, v in seen.items() if len(v) < 3}
    assert not missing, f"segments with incomplete coverage: {missing}"


def test_smaller_epsilon_produces_smaller_propensities(clean_world):
    """The axis R3 sweeps: as the logging policy becomes more deterministic the
    non-greedy propensities shrink, importance weights grow, and the estimators
    degrade. Establishing the monotonicity here makes that chart meaningful."""
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    minima = []
    for epsilon in (0.5, 0.2, 0.05):
        log = run_policy(Simulator(clean_world, seed=5), _policy(clean_world, epsilon=epsilon), 5_000)
        minima.append(min(r.propensity for r in log))

    assert minima == sorted(minima, reverse=True), f"non-monotone: {minima}"
    assert minima[-1] < minima[0]
