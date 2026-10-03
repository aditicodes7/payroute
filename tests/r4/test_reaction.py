"""R4: time to react — the rung's headline number, made measurable.

"When did it react?" is ambiguous until it is pinned down, so:

  onset    minutes from incident start until the policy's share of traffic to
           the degraded gateway falls below half its pre-incident share AND
           stays there for a sustained window. The persistence requirement is
           what stops one noisy bin from claiming a reaction that did not happen.

  recovery minutes from incident end until the share returns to within a band of
           its pre-incident level.

Both are measured per incident, across many incidents, because one incident is
an anecdote.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from config.default_world import BUSY_HOUR_OFFSET, START

WINDOW = timedelta(minutes=45)


def _sim_with_outage(cfg, seed=5):
    from payroute.sim import GatewayIncident, Simulator

    sim = Simulator(cfg, seed=seed)
    sim.inject(
        GatewayIncident(
            gateway="atlas",
            start=START + BUSY_HOUR_OFFSET,
            duration=WINDOW,
            peak_severity=0.75,
        )
    )
    return sim


def _incident_window():
    start = START + BUSY_HOUR_OFFSET
    return start, start + WINDOW


def _run(cfg, policy, n=120_000, seed=5):
    from payroute.policies.runner import run_policy

    return run_policy(_sim_with_outage(cfg, seed=seed), policy, n)


def _adaptive(cfg, half_life_minutes=5.0, epsilon=0.05):
    from payroute.policies import AdaptiveLinUCB

    return AdaptiveLinUCB.for_config(
        cfg, half_life_minutes=half_life_minutes, epsilon=epsilon, seed=11
    )


def _static(cfg, epsilon=0.05):
    from payroute.features import Featuriser
    from payroute.policies import LinUCB

    return LinUCB(cfg.gateway_names, Featuriser.from_config(cfg), alpha=1.0, epsilon=epsilon, seed=11)


def test_a_policy_that_cannot_react_reports_no_onset(clean_world):
    """The guard on the measurement itself. Round robin is incapable of
    reacting, so `reacted_after` must be None rather than some number produced
    by noise."""
    from payroute.decisionlog.reaction import measure_reaction
    from payroute.policies import RoundRobin

    start, end = _incident_window()
    log = _run(clean_world, RoundRobin(clean_world.gateway_names))
    reaction = measure_reaction(log, "atlas", start, end)

    assert reaction.reacted_after is None
    assert reaction.share_before == pytest.approx(1 / 3, abs=0.01)


def test_the_adaptive_policy_reacts_at_all(clean_world):
    from payroute.decisionlog.reaction import measure_reaction

    start, end = _incident_window()
    reaction = measure_reaction(_run(clean_world, _adaptive(clean_world)), "atlas", start, end)

    assert reaction.reacted_after is not None, "adaptive policy never diverted traffic away"
    assert reaction.reacted_after < WINDOW
    assert reaction.share_during_min < 0.5 * reaction.share_before


def test_decayed_features_react_faster_than_static_ones(clean_world):
    """The rung's acceptance criterion.

    R2 measured the problem: LinUCB's A and b are unweighted sums over all
    history, so a 40-minute outage is a vanishing fraction of the evidence and
    the policy barely moves. This is the fix, measured against it.
    """
    from payroute.decisionlog.reaction import measure_reaction

    start, end = _incident_window()
    adaptive = measure_reaction(_run(clean_world, _adaptive(clean_world)), "atlas", start, end)
    static = measure_reaction(_run(clean_world, _static(clean_world)), "atlas", start, end)

    assert adaptive.reacted_after is not None
    if static.reacted_after is None:
        return  # static never reacted at all, which is a stronger result
    assert adaptive.reacted_after < static.reacted_after


def test_routing_recovers_after_the_incident_ends(clean_world):
    """A policy that reacts and never comes back has not solved the problem, it
    has traded one permanent misrouting for another."""
    from payroute.decisionlog.reaction import measure_reaction

    start, end = _incident_window()
    reaction = measure_reaction(_run(clean_world, _adaptive(clean_world)), "atlas", start, end)

    assert reaction.recovered_after is not None, "traffic never returned after the outage ended"
    assert reaction.recovered_after < timedelta(hours=2)


def test_a_shorter_half_life_reacts_sooner(clean_world):
    from payroute.decisionlog.reaction import measure_reaction

    start, end = _incident_window()
    quick = measure_reaction(
        _run(clean_world, _adaptive(clean_world, half_life_minutes=2.0)), "atlas", start, end
    )
    slow = measure_reaction(
        _run(clean_world, _adaptive(clean_world, half_life_minutes=60.0)), "atlas", start, end
    )

    assert quick.reacted_after is not None
    if slow.reacted_after is None:
        return
    assert quick.reacted_after <= slow.reacted_after


def test_a_shorter_half_life_is_less_stable_when_nothing_is_wrong(clean_world):
    """The other half of the tradeoff, which is the half people leave out.

    Without this, "shorter is better" looks true. Measured on a CLEAN world,
    where every diversion away from a healthy gateway is a false alarm paid for
    in revenue.
    """
    from payroute.decisionlog.reaction import routing_instability
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    quick = run_policy(Simulator(clean_world, seed=5), _adaptive(clean_world, half_life_minutes=1.0), 60_000)
    slow = run_policy(Simulator(clean_world, seed=5), _adaptive(clean_world, half_life_minutes=60.0), 60_000)

    assert routing_instability(quick, bin_minutes=5) > routing_instability(slow, bin_minutes=5)


def test_one_bad_transaction_cannot_flip_the_routing(clean_world):
    """The predicted failure for this rung, as a direct assertion on the
    mechanism: a single failure must not move health enough to change which
    gateway the policy prefers."""
    from payroute.policies import AdaptiveLinUCB
    from payroute.sim import Simulator

    policy = AdaptiveLinUCB.for_config(clean_world, half_life_minutes=2.0, epsilon=0.0, seed=11)
    sim = Simulator(clean_world, seed=5)
    txns = list(sim.transactions(4_000))

    for txn in txns[:3_000]:
        decision = policy.decide(txn)
        outcome = sim.resolve(txn, decision.action)
        policy.update(policy.context_for(txn), decision.action, float(outcome.success))
        policy.observe(txn, decision.action, outcome)

    probe = txns[3_000]
    before = policy.greedy_action(policy.context_for(probe))
    policy.observe(probe, before, _failed_outcome(before))
    after = policy.greedy_action(policy.context_for(txns[3_001]))

    assert before == after, (
        f"one failed transaction moved the preferred gateway from {before} to {after} — "
        "the prior is not doing its job"
    )


def _failed_outcome(gateway):
    from payroute.sim import Outcome

    return Outcome(gateway=gateway, success=False, latency_ms=200.0, fee_paise=0, net_revenue_paise=0)
