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

# The incident sits at 08:00 rather than the 13:00 peak: the arrival rate there
# (~109/min) is ample for 1-minute bins, and reaching it costs 30,000
# transactions instead of 100,000 — which keeps this file runnable.
OFFSET = timedelta(hours=8)
N = 50_000

# 1-minute bins, not 5. At 5 minutes both the adaptive and the static policy
# land in the first bin after onset and the measurement cannot tell them apart,
# which is not the same as them being equally fast (docs/bugs.md).
BINS = 1.0
MIN_COUNT = 20


def _sim_with_outage(cfg, seed=5):
    from payroute.sim import GatewayIncident, Simulator

    sim = Simulator(cfg, seed=seed)
    sim.inject(
        GatewayIncident(
            gateway="atlas",
            start=START + OFFSET,
            duration=WINDOW,
            peak_severity=0.75,
        )
    )
    return sim


def _incident_window():
    start = START + OFFSET
    return start, start + WINDOW


def _run(cfg, policy, n=N, seed=5):
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
    reaction = measure_reaction(log, "atlas", start, end, bin_minutes=BINS, min_count=MIN_COUNT)

    assert reaction.reacted_after is None
    assert reaction.share_before == pytest.approx(1 / 3, abs=0.01)


def test_the_adaptive_policy_reacts_at_all(clean_world):
    from payroute.decisionlog.reaction import measure_reaction

    start, end = _incident_window()
    reaction = measure_reaction(_run(clean_world, _adaptive(clean_world)), "atlas", start, end, bin_minutes=BINS, min_count=MIN_COUNT)

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
    adaptive = measure_reaction(_run(clean_world, _adaptive(clean_world)), "atlas", start, end, bin_minutes=BINS, min_count=MIN_COUNT)
    static = measure_reaction(_run(clean_world, _static(clean_world)), "atlas", start, end, bin_minutes=BINS, min_count=MIN_COUNT)

    assert adaptive.reacted_after is not None
    if static.reacted_after is None:
        return  # static never reacted at all, which is a stronger result
    assert adaptive.reacted_after < static.reacted_after


def test_the_static_policy_is_the_one_that_cannot_let_go(clean_world):
    """The larger half of the same effect, and the one the onset test misses.

    R2 measured this: LinUCB's A and b are unweighted sums, so a finished
    outage keeps weighing on the estimate long afterwards. Recovery, not onset,
    is where that costs the most.
    """
    from payroute.decisionlog.reaction import measure_reaction

    start, end = _incident_window()
    adaptive = measure_reaction(
        _run(clean_world, _adaptive(clean_world)), "atlas", start, end,
        bin_minutes=BINS, min_count=MIN_COUNT,
    )
    static = measure_reaction(
        _run(clean_world, _static(clean_world)), "atlas", start, end,
        bin_minutes=BINS, min_count=MIN_COUNT,
    )

    assert adaptive.recovered_after is not None
    if static.recovered_after is None:
        return  # never recovered within the run — the strongest form of the result
    assert adaptive.recovered_after < static.recovered_after


def test_routing_recovers_after_the_incident_ends(clean_world):
    """A policy that reacts and never comes back has not solved the problem, it
    has traded one permanent misrouting for another."""
    from payroute.decisionlog.reaction import measure_reaction

    start, end = _incident_window()
    reaction = measure_reaction(_run(clean_world, _adaptive(clean_world)), "atlas", start, end, bin_minutes=BINS, min_count=MIN_COUNT)

    assert reaction.recovered_after is not None, "traffic never returned after the outage ended"
    assert reaction.recovered_after < timedelta(hours=2)


def test_a_shorter_half_life_reacts_sooner(clean_world):
    from payroute.decisionlog.reaction import measure_reaction

    start, end = _incident_window()
    quick = measure_reaction(
        _run(clean_world, _adaptive(clean_world, half_life_minutes=2.0)), "atlas", start, end,
        bin_minutes=BINS, min_count=MIN_COUNT,
    )
    slow = measure_reaction(
        _run(clean_world, _adaptive(clean_world, half_life_minutes=60.0)), "atlas", start, end,
        bin_minutes=BINS, min_count=MIN_COUNT,
    )

    assert quick.reacted_after is not None
    if slow.reacted_after is None:
        return
    assert quick.reacted_after <= slow.reacted_after


def test_a_very_short_half_life_chases_noise_when_nothing_is_wrong(clean_world):
    """The other half of the tradeoff, which is the half people leave out.

    Two corrections from an earlier version (docs/bugs.md). The metric is now
    bin-to-bin JITTER rather than the standard deviation of levels, because a
    bandit's share drifts a long way as it stops exploring and that trend
    swamped the effect. And the comparison is against a quarter-minute
    half-life rather than one minute: at one minute the difference is about 17%
    and flips sign with epsilon = 0, because a prior worth ten observations
    plus a thousand transactions per bin keeps health steady almost regardless.
    That the cost is small at sane settings is a finding, not a failure — but
    the mechanism still has to be shown to exist.
    """
    from payroute.decisionlog.reaction import routing_jitter
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    twitchy = run_policy(Simulator(clean_world, seed=5), _adaptive(clean_world, half_life_minutes=0.25), 40_000)
    steady = run_policy(Simulator(clean_world, seed=5), _adaptive(clean_world, half_life_minutes=60.0), 40_000)

    assert routing_jitter(twitchy, bin_minutes=5) > routing_jitter(steady, bin_minutes=5)


def test_one_bad_transaction_barely_moves_health(clean_world):
    """The predicted failure for this rung, asserted against the right
    invariant.

    An earlier version asserted that the preferred gateway must not change.
    That is not a property any sane policy has: when two arms sit within 0.005
    of each other in score, a flip between them is indifference, not
    instability, and it costs essentially nothing. What the prior actually
    guarantees is that a single observation cannot move health far — measured
    at 0.019 on a store holding 36 observations of evidence (docs/bugs.md).
    """
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
    gateway = policy.greedy_action(policy.context_for(probe))

    evidence = policy.health.evidence_at(probe.ts)[gateway]
    before = policy.health.health_at(probe.ts)[gateway]
    policy.observe(probe, gateway, _failed_outcome(gateway))
    after = policy.health.health_at(probe.ts + timedelta(seconds=1))[gateway]

    assert evidence > 20, f"only {evidence:.1f} observations of evidence — warm-up was too short"
    assert abs(after - before) < 0.03, (
        f"one failed transaction moved health {before:.4f} -> {after:.4f} on {evidence:.1f} "
        "observations of evidence — the prior is not doing its job"
    )


def _failed_outcome(gateway):
    from payroute.sim import Outcome

    return Outcome(gateway=gateway, success=False, latency_ms=200.0, fee_paise=0, net_revenue_paise=0)
