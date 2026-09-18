"""R0: incident injection — the acceptance criteria for the rung.

Two incident types:

  GatewayIncident — one gateway's own stack degrades. Everything routed through
                    it suffers; every other gateway is untouched.

  IssuerIncident  — a BANK degrades. Every gateway's success rate for that
                    issuer drops, scaled by each gateway's sensitivity. This is
                    the realistic and more interesting case: during the outage
                    there is still a best gateway, and finding it is the job.
                    A static rule table written last quarter cannot do this.
"""

from __future__ import annotations

import statistics
from datetime import timedelta

import pytest

from tests.conftest import (
    BUSY_HOUR_OFFSET,
    START,
    assert_outside_band,
    assert_within_band,
    marginal_true_prob,
    true_prob,
)


def _sim(cfg, seed=7):
    from payroute.sim import Simulator

    return Simulator(cfg, seed=seed)


def _rates_by_phase(cfg, sim, n, start, end, gateway_names, issuer=None):
    """Split a run into before / during / after an incident window and return
    (successes, total) per gateway per phase."""
    phases = {p: {g: [0, 0] for g in gateway_names} for p in ("before", "during", "after")}
    for txn in sim.transactions(n):
        if issuer is not None and txn.issuer != issuer:
            continue
        phase = "before" if txn.ts < start else ("during" if txn.ts < end else "after")
        for g in gateway_names:
            ok, total = phases[phase][g]
            phases[phase][g] = [ok + int(sim.resolve(txn, g).success), total + 1]
    return phases


# ---------------------------------------------------------------------------
# Gateway-level incident — the stated R0 acceptance criteria
# ---------------------------------------------------------------------------


def test_gateway_incident_visibly_drops_the_target_success_rate(clean_world):
    from payroute.sim.incidents import GatewayIncident

    start = START + BUSY_HOUR_OFFSET
    end = start + timedelta(minutes=40)

    sim = _sim(clean_world)
    sim.inject(GatewayIncident(gateway="atlas", start=start, duration=end - start, peak_severity=0.65))

    names = [g.name for g in clean_world.gateways]
    phases = _rates_by_phase(clean_world, sim, 80_000, start, end, names)

    ok, total = phases["during"]["atlas"]
    assert total > 2_000, f"only {total} transactions inside the outage window"
    assert_outside_band(ok / total, marginal_true_prob(clean_world, "atlas"), total, label="atlas during outage")
    assert ok / total < marginal_true_prob(clean_world, "atlas"), "success rate went UP during an outage"


def test_gateway_incident_leaves_other_gateways_untouched(clean_world):
    """The assertion people forget.

    Without it, "I injected an outage" is indistinguishable from "I broke the
    simulator".
    """
    from payroute.sim.incidents import GatewayIncident

    start = START + BUSY_HOUR_OFFSET
    end = start + timedelta(minutes=40)

    sim = _sim(clean_world)
    sim.inject(GatewayIncident(gateway="atlas", start=start, duration=end - start, peak_severity=0.65))

    names = [g.name for g in clean_world.gateways]
    phases = _rates_by_phase(clean_world, sim, 80_000, start, end, names)

    for phase in ("before", "during", "after"):
        for name in ("beacon", "cirrus"):
            ok, total = phases[phase][name]
            assert_within_band(
                ok / total, marginal_true_prob(clean_world, name), total, label=f"{name} {phase}"
            )


def test_gateway_success_rate_recovers_after_the_window(clean_world):
    """Explicitly listed as an expected failure mode: a rate that drops and
    never comes back."""
    from payroute.sim.incidents import GatewayIncident

    start = START + BUSY_HOUR_OFFSET
    end = start + timedelta(minutes=40)
    # skip the recovery ramp itself; we are testing the steady state afterwards
    settled = end + timedelta(minutes=15)

    sim = _sim(clean_world)
    sim.inject(GatewayIncident(gateway="atlas", start=start, duration=end - start, peak_severity=0.65))

    ok = total = 0
    for txn in sim.transactions(120_000):
        if txn.ts < settled:
            continue
        ok += int(sim.resolve(txn, "atlas").success)
        total += 1

    assert total > 2_000
    assert_within_band(
        ok / total, marginal_true_prob(clean_world, "atlas"), total, label="atlas after recovery"
    )


# ---------------------------------------------------------------------------
# "Too uniform to look realistic" — the shape of the severity profile
# ---------------------------------------------------------------------------


def test_severity_profile_is_not_a_step_function(clean_world):
    """A 0 -> 0.65 -> 0 step is visible, satisfies the letter of the acceptance
    criterion, and is wrong. It is also trivially detectable by any change
    detector, which would hand R4 a free pass and make its decay-constant
    tradeoff vacuous."""
    from payroute.sim.incidents import GatewayIncident

    duration = timedelta(minutes=40)
    inc = GatewayIncident(gateway="atlas", start=START, duration=duration, peak_severity=0.65)

    samples = [inc.severity_at(START + duration * (i / 200)) for i in range(201)]
    distinct = len({round(s, 4) for s in samples})
    assert distinct > 20, f"severity takes only {distinct} distinct values — that is a step function"

    # ramps in rather than jumping
    assert samples[0] < 0.25 * inc.peak_severity, "severity jumps to full strength instantly"
    assert max(samples) > 0.8 * inc.peak_severity

    # and fluctuates while it is happening, rather than sitting on a plateau
    mid = samples[80:140]
    assert statistics.pstdev(mid) > 0.01, "severity is perfectly flat mid-outage"


def test_severity_is_zero_outside_the_window(clean_world):
    from payroute.sim.incidents import GatewayIncident

    inc = GatewayIncident(
        gateway="atlas", start=START + timedelta(hours=1), duration=timedelta(minutes=30), peak_severity=0.5
    )
    assert inc.severity_at(START) == 0.0
    assert inc.severity_at(START + timedelta(hours=3)) == 0.0


def test_randomly_sampled_incidents_vary_in_duration_and_severity(clean_world):
    """Fixed-shape incidents let a policy overfit to one outage signature."""
    from payroute.sim.incidents import sample_gateway_incidents

    incidents = sample_gateway_incidents(
        clean_world, seed=13, start=START, horizon=timedelta(days=7)
    )
    assert len(incidents) >= 5, "not enough incidents over a week to compare"
    assert len({i.duration for i in incidents}) > 1, "every incident has the same duration"
    assert len({round(i.peak_severity, 3) for i in incidents}) > 1, "every incident has the same severity"


# ---------------------------------------------------------------------------
# Issuer-level incident — the realistic case
# ---------------------------------------------------------------------------


def test_issuer_incident_degrades_every_gateway_for_that_issuer(clean_world):
    from payroute.sim.incidents import IssuerIncident

    start = START + BUSY_HOUR_OFFSET
    end = start + timedelta(minutes=45)

    sim = _sim(clean_world, seed=17)
    sim.inject(IssuerIncident(issuer="hdfc", start=start, duration=end - start, peak_severity=0.6))

    names = [g.name for g in clean_world.gateways]
    phases = _rates_by_phase(clean_world, sim, 200_000, start, end, names, issuer="hdfc")

    for name in names:
        ok, total = phases["during"][name]
        assert total > 1_000, f"{name}: only {total} hdfc transactions during the window"
        baseline = sum(
            pm * true_prob(clean_world, name, method, "hdfc")
            for method, pm in clean_world.method_mix.items()
        )
        assert ok / total < baseline, f"{name} did not degrade during an hdfc outage"


def test_issuer_incident_degradation_is_ordered_by_gateway_sensitivity(clean_world):
    """The point of the issuer model: gateways reach a bank by different
    acquiring paths, so they do not all suffer equally. The gateway with the
    lowest sensitivity becomes the correct route for the duration — which is
    exactly the decision a static rule cannot make."""
    from payroute.sim.incidents import IssuerIncident

    start = START + BUSY_HOUR_OFFSET
    end = start + timedelta(minutes=45)

    sim = _sim(clean_world, seed=17)
    sim.inject(IssuerIncident(issuer="hdfc", start=start, duration=end - start, peak_severity=0.6))

    names = [g.name for g in clean_world.gateways]
    phases = _rates_by_phase(clean_world, sim, 200_000, start, end, names, issuer="hdfc")

    drops = {}
    for name in names:
        ok_d, tot_d = phases["during"][name]
        ok_b, tot_b = phases["before"][name]
        drops[name] = (ok_b / tot_b) - (ok_d / tot_d)

    by_sensitivity = sorted(
        names,
        key=lambda n: next(g for g in clean_world.gateways if g.name == n).issuer_sensitivity["hdfc"],
    )
    by_observed_drop = sorted(names, key=lambda n: drops[n])
    assert by_sensitivity == by_observed_drop, (
        f"observed drops {drops} do not follow configured hdfc sensitivities"
    )


def test_issuer_incident_leaves_other_issuers_untouched(clean_world):
    from payroute.sim.incidents import IssuerIncident

    start = START + BUSY_HOUR_OFFSET
    end = start + timedelta(minutes=45)

    sim = _sim(clean_world, seed=17)
    sim.inject(IssuerIncident(issuer="hdfc", start=start, duration=end - start, peak_severity=0.6))

    ok = total = 0
    for txn in sim.transactions(200_000):
        if txn.issuer == "hdfc" or not (start <= txn.ts < end):
            continue
        ok += int(sim.resolve(txn, "atlas").success)
        total += 1

    expected = sum(
        pm * pi * true_prob(clean_world, "atlas", method, issuer)
        for method, pm in clean_world.method_mix.items()
        for issuer, pi in clean_world.issuer_mix.items()
        if issuer != "hdfc"
    ) / sum(pi for issuer, pi in clean_world.issuer_mix.items() if issuer != "hdfc")

    assert total > 2_000
    assert_within_band(ok / total, expected, total, label="atlas, non-hdfc traffic during hdfc outage")
