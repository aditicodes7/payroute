"""R1: the numbers every later rung has to beat, and the acceptance criterion
that round robin does not react to an outage.

Rule 10 applies from here on: no comparison is reported without a confidence
interval and a sample size.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from config.default_world import BUSY_HOUR_OFFSET, START


def _sim(cfg, seed=7):
    from payroute.sim import Simulator

    return Simulator(cfg, seed=seed)


def _outage_sim(cfg, seed=7):
    from payroute.sim import GatewayIncident, Simulator

    sim = Simulator(cfg, seed=seed)
    sim.inject(
        GatewayIncident(
            gateway="atlas",
            start=START + BUSY_HOUR_OFFSET,
            duration=timedelta(minutes=40),
            peak_severity=0.65,
        )
    )
    return sim


def test_round_robin_does_not_react_to_an_outage(clean_world):
    """The acceptance criterion for this rung, as a measured fact.

    Round robin's share of traffic into the degraded gateway during the outage
    must be indistinguishable from its share before it. That failure is the
    entire motivation for the project, so it is asserted rather than asserted-in-prose.
    """
    from payroute.policies import RoundRobin
    from payroute.policies.runner import run_policy

    window_start = START + BUSY_HOUR_OFFSET
    window_end = window_start + timedelta(minutes=40)

    log = run_policy(_outage_sim(clean_world), RoundRobin(clean_world.gateway_names), 150_000)

    before = [r for r in log if r.ts < window_start]
    during = [r for r in log if window_start <= r.ts < window_end]
    assert len(during) > 2_000, f"only {len(during)} transactions inside the window"

    share_before = sum(r.action == "atlas" for r in before) / len(before)
    share_during = sum(r.action == "atlas" for r in during) / len(during)

    assert abs(share_during - share_before) < 0.01, (
        f"round robin shifted traffic {share_before:.3f} -> {share_during:.3f}; it is not "
        "supposed to be capable of reacting at all"
    )
    assert abs(share_during - 1 / 3) < 0.01


def test_round_robin_success_rate_falls_during_an_outage(clean_world):
    """The consequence of not reacting: it keeps routing into a gateway it
    could have avoided, and pays for it in the only currency that matters."""
    from payroute.policies import RoundRobin
    from payroute.policies.runner import run_policy
    from payroute.decisionlog.metrics import summarize

    window_start = START + BUSY_HOUR_OFFSET
    window_end = window_start + timedelta(minutes=40)

    log = run_policy(_outage_sim(clean_world), RoundRobin(clean_world.gateway_names), 150_000)
    before = summarize([r for r in log if r.ts < window_start])
    during = summarize([r for r in log if window_start <= r.ts < window_end])

    assert during.success_rate < before.success_rate - 0.10
    assert during.n > 2_000


def test_summary_reports_an_interval_and_a_sample_size(clean_world):
    """Rule 10, enforced by the shape of the return type: a point estimate with
    no uncertainty is not a result in this project."""
    from payroute.policies import RoundRobin
    from payroute.policies.runner import run_policy
    from payroute.decisionlog.metrics import summarize

    log = run_policy(_sim(clean_world), RoundRobin(clean_world.gateway_names), 20_000)
    summary = summarize(log)

    assert summary.n == 20_000
    lo, hi = summary.success_ci
    assert lo < summary.success_rate < hi
    lo, hi = summary.net_revenue_ci
    assert lo < summary.net_revenue_per_1k < hi
    assert summary.net_revenue_per_1k > 0


def test_net_revenue_per_1k_matches_the_raw_log(clean_world):
    from payroute.policies import RoundRobin
    from payroute.policies.runner import run_policy
    from payroute.decisionlog.metrics import summarize

    log = run_policy(_sim(clean_world), RoundRobin(clean_world.gateway_names), 10_000)
    summary = summarize(log)
    expected = sum(r.outcome.net_revenue_paise for r in log) / len(log) * 1_000
    assert summary.net_revenue_per_1k == pytest.approx(expected)


def test_policies_are_compared_on_identical_transactions(clean_world):
    """Common random numbers again: two policies run on the same seed must see
    the same transaction stream, whatever they choose. Without this, a
    comparison between baselines is partly a comparison between two different
    days of traffic."""
    from payroute.policies import AlwaysCheapest, RoundRobin
    from payroute.policies.runner import run_policy

    a = run_policy(_sim(clean_world, seed=55), RoundRobin(clean_world.gateway_names), 3_000)
    b = run_policy(_sim(clean_world, seed=55), AlwaysCheapest(clean_world), 3_000)

    assert [r.txn_id for r in a] == [r.txn_id for r in b]
    assert [dict(r.context) for r in a] == [dict(r.context) for r in b]
    assert [r.action for r in a] != [r.action for r in b], "the two policies never disagreed"


def test_paired_comparison_is_tighter_than_comparing_two_means(clean_world):
    """Why the common-random-number design pays off in the measurement, not
    just in the debugging: the variance of the DIFFERENCE is much smaller than
    the variance of either mean, so a real gap is detectable with far fewer
    transactions."""
    from payroute.policies import HistoricallyBest, RoundRobin, UniformRandom
    from payroute.policies.runner import run_policy
    from payroute.decisionlog.metrics import paired_difference, summarize

    training = run_policy(_sim(clean_world, seed=101), UniformRandom(clean_world.gateway_names, seed=101), 40_000)
    best = HistoricallyBest.fit(training)

    a = run_policy(_sim(clean_world, seed=55), RoundRobin(clean_world.gateway_names), 40_000)
    b = run_policy(_sim(clean_world, seed=55), best, 40_000)

    paired = paired_difference(a, b)
    sa, sb = summarize(a), summarize(b)

    unpaired_width = (sa.net_revenue_ci[1] - sa.net_revenue_ci[0]) + (sb.net_revenue_ci[1] - sb.net_revenue_ci[0])
    paired_width = paired.net_revenue_ci[1] - paired.net_revenue_ci[0]

    assert paired_width < unpaired_width
    assert paired.n == 40_000


def test_paired_difference_refuses_mismatched_logs(clean_world):
    """Pairing two logs that saw different transactions produces a number that
    looks fine and means nothing."""
    from payroute.policies import RoundRobin
    from payroute.policies.runner import run_policy
    from payroute.decisionlog.metrics import paired_difference

    a = run_policy(_sim(clean_world, seed=55), RoundRobin(clean_world.gateway_names), 2_000)
    b = run_policy(_sim(clean_world, seed=56), RoundRobin(clean_world.gateway_names), 2_000)

    with pytest.raises(ValueError):
        paired_difference(a, b)
