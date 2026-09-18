"""R1: the propensity invariant, asserted rather than eyeballed.

Your brief calls for a test that every single decision has a propensity, not a
visual check. This is that test, and it runs over every policy — including the
deterministic ones, whose honest propensity is 1.0.

It is parametrised over the policy list deliberately: adding a sixth policy
without a propensity should turn this file red on the next run, without anyone
remembering to extend it.
"""

from __future__ import annotations

import pytest


def _sim(cfg, seed=7):
    from payroute.sim import Simulator

    return Simulator(cfg, seed=seed)


def _all_policies(cfg):
    from payroute.features import Featuriser
    from payroute.policies import (
        AlwaysCheapest,
        BestPerSegment,
        HistoricallyBest,
        LinUCB,
        RoundRobin,
        UniformRandom,
    )
    from payroute.policies.runner import run_policy

    training = run_policy(_sim(cfg, seed=101), UniformRandom(cfg.gateway_names, seed=101), 40_000)
    return [
        RoundRobin(cfg.gateway_names),
        UniformRandom(cfg.gateway_names, seed=5),
        AlwaysCheapest(cfg),
        HistoricallyBest.fit(training),
        BestPerSegment.fit(training),
        LinUCB(cfg.gateway_names, Featuriser.from_config(cfg), alpha=1.0, epsilon=0.05, seed=11),
    ]


def test_every_baseline_is_covered_by_this_file(clean_world):
    """Guards the guard: if someone adds a policy and forgets to register it
    here, the invariant silently stops being checked for it."""
    import payroute.policies as policies

    exported = {
        name
        for name in policies.__all__
        if name[0].isupper() and name not in {"Decision", "Policy"}
    }
    covered = {type(p).__name__ for p in _all_policies(clean_world)}
    assert exported == covered, f"policies not covered by the propensity test: {exported - covered}"


@pytest.mark.parametrize("index", range(6))
def test_every_decision_has_a_propensity(clean_world, index):
    from payroute.policies.runner import run_policy

    policy = _all_policies(clean_world)[index]
    log = run_policy(_sim(clean_world, seed=909), policy, 5_000)

    assert len(log) == 5_000
    for record in log:
        assert record.propensity is not None, f"{policy.policy_id} logged a decision with no propensity"
        assert 0.0 < record.propensity <= 1.0
        assert record.propensity == pytest.approx(record.action_probs[record.action])


@pytest.mark.parametrize("index", range(6))
def test_every_decision_logs_the_full_action_probability_vector(clean_world, index):
    """Not just the chosen action's propensity. Several estimators and most of
    the diagnostics want the logging policy's probability for actions we did
    NOT take; at three gateways the storage is free, and regenerating every
    experiment later is not."""
    from payroute.policies.runner import run_policy

    policy = _all_policies(clean_world)[index]
    log = run_policy(_sim(clean_world, seed=909), policy, 2_000)

    names = set(clean_world.gateway_names)
    for record in log:
        assert set(record.action_probs) == names
        assert sum(record.action_probs.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("index", range(6))
def test_every_decision_carries_context_action_and_reward(clean_world, index):
    """The R6 acceptance criterion, asserted three rungs early: amount, context,
    propensity, chosen action, and the outcome."""
    from payroute.policies.runner import run_policy

    policy = _all_policies(clean_world)[index]
    log = run_policy(_sim(clean_world, seed=909), policy, 1_000)

    assert log.validate() == []
    for record in log:
        assert record.context["amount_paise"] > 0
        assert record.context["method"] and record.context["issuer"]
        assert record.action in clean_world.gateway_names
        assert record.is_complete
        assert record.outcome.gateway == record.action
        assert record.policy_id and record.policy_version


def test_deterministic_policies_produce_degenerate_propensities(clean_world):
    """Stated as a test because it is a RESULT, not an implementation detail.

    A static rule's log cannot evaluate any policy that would have chosen
    differently: every importance weight is 0 or undefined. This is the real
    situation a payments company is in with its production rule table, and it
    is the argument for why exploration has to be deliberate. R3 quantifies how
    badly it degrades; R1 just establishes that it is true.
    """
    from payroute.policies import AlwaysCheapest, RoundRobin, UniformRandom
    from payroute.policies.runner import run_policy

    for policy in (RoundRobin(clean_world.gateway_names), AlwaysCheapest(clean_world)):
        log = run_policy(_sim(clean_world, seed=909), policy, 2_000)
        assert all(r.propensity == 1.0 for r in log)
        assert all(sorted(r.action_probs.values()) == [0.0, 0.0, 1.0] for r in log)

    log = run_policy(_sim(clean_world, seed=909), UniformRandom(clean_world.gateway_names, seed=5), 2_000)
    assert all(r.propensity == pytest.approx(1 / 3) for r in log)
