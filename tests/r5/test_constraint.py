"""R5: the constraint, and the failure mode it is built to avoid.

The expected bug for this rung: a "constrained" selection that silently ignores
the latency constraint when no gateway satisfies it. A payment router cannot
refuse to route, so failing loudly cannot mean dropping the transaction — it
means the decision RECORDS that the constraint was not met, so the violation
rate is a number you report rather than something nobody notices.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

BUDGET_MS = 420.0


def _policy(cfg, budget_ms=BUDGET_MS, epsilon=0.05, **kwargs):
    from payroute.policies import ConstrainedLinUCB

    return ConstrainedLinUCB.for_config(
        cfg, p95_budget_ms=budget_ms, half_life_minutes=5.0, epsilon=epsilon, seed=11, **kwargs
    )


def _run(cfg, policy, n=25_000, seed=5):
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    return run_policy(Simulator(cfg, seed=seed), policy, n)


def test_a_slow_gateway_is_excluded_nearly_always(clean_world):
    """beacon's configured median is 320ms with log-sigma 0.55, so its p95 is
    around 790ms — well outside a 420ms budget.

    "Nearly always" rather than "always", and the gap is deliberate. Once
    excluded, beacon stops producing latency samples; after the window empties
    the optimistic prior readmits it, it is tried, measured slow, and excluded
    again. That cycle is the mechanism by which a RECOVERED gateway gets back
    in, so forbidding it outright would forbid recovery. What matters is that
    the rate is small and reported rather than assumed to be zero.
    """
    policy = _policy(clean_world)
    log = _run(clean_world, policy, n=20_000)

    late = [r for r in log if r.ts > log[2_000].ts]
    assert late, "run too short to let the latency window fill"

    admitted = [r for r in late if r.action == "beacon" and r.constraint_status == "satisfied"]
    rate = len(admitted) / len(late)
    assert rate < 0.005, (
        f"{rate:.2%} of decisions routed to the slow gateway while claiming the constraint held"
    )


def test_the_chosen_gateway_is_always_in_the_feasible_set(clean_world):
    policy = _policy(clean_world)
    log = _run(clean_world, policy)

    for record in log:
        if record.constraint_status == "satisfied":
            assert record.predicted_p95_ms <= BUDGET_MS, (
                f"{record.txn_id} marked satisfied with p95 {record.predicted_p95_ms:.0f}ms "
                f"against a {BUDGET_MS:.0f}ms budget"
            )


def test_every_decision_records_whether_the_constraint_held(clean_world):
    """The guard against the silent-fallback bug. There is no third state and
    no missing value: every record says one or the other."""
    log = _run(clean_world, _policy(clean_world))
    statuses = {r.constraint_status for r in log}
    assert statuses <= {"satisfied", "infeasible"}
    assert None not in statuses
    assert all(r.predicted_p95_ms is not None for r in log)


def test_an_impossible_budget_still_routes_but_says_so(clean_world):
    """With a 1ms budget nothing is feasible. The policy must keep routing — a
    payment router that stops is worse than a slow one — and must mark every
    decision infeasible rather than quietly dropping the constraint."""
    log = _run(clean_world, _policy(clean_world, budget_ms=1.0), n=5_000)

    assert len(log) == 5_000
    assert all(r.constraint_status == "infeasible" for r in log)
    assert all(0.0 < r.propensity <= 1.0 for r in log)


def test_when_infeasible_it_picks_the_fastest(clean_world):
    """Not the most profitable. If the constraint cannot be met, the thing it
    was protecting is what should be optimised."""
    from payroute.policies import ConstrainedLinUCB

    policy = _policy(clean_world, budget_ms=1.0)
    log = _run(clean_world, policy, n=4_000)

    tail = list(log)[2_000:]
    p95 = policy.latency.p95_at(tail[-1].ts)
    fastest = min(p95, key=lambda g: p95[g])
    share = sum(r.action == fastest for r in tail) / len(tail)
    assert share > 0.8, f"only {share:.1%} of infeasible decisions went to the fastest gateway"


def test_propensities_are_computed_over_the_feasible_set(clean_world):
    """Epsilon-greedy restricted to the feasible gateways: the greedy arm gets
    (1-eps) + eps/|F|, the other feasible arms eps/|F|, and anything excluded
    gets exactly zero."""
    policy = _policy(clean_world, epsilon=0.3)
    log = _run(clean_world, policy, n=20_000)

    checked = 0
    for record in list(log)[5_000:]:
        probs = record.action_probs
        feasible = [g for g, p in probs.items() if p > 0]
        if len(feasible) == len(probs):
            continue
        checked += 1
        share = 0.3 / len(feasible)
        assert sum(probs.values()) == pytest.approx(1.0)
        assert max(probs.values()) == pytest.approx(0.7 + share)
        for gateway in feasible:
            assert probs[gateway] == pytest.approx(share) or probs[gateway] == pytest.approx(0.7 + share)
    assert checked > 0, "the constraint never excluded anything, so this proved nothing"


def test_constraining_the_action_set_destroys_overlap(clean_world):
    """Not a bug — a real consequence, and one worth measuring.

    An excluded gateway has propensity zero, which is exactly R3's no-overlap
    condition. So the more aggressively a policy constrains its action space,
    the less its log can say about the alternatives it refused to try.
    """
    from payroute.ope import NoOverlapError, ips
    from payroute.policies import HistoricallyBest, UniformRandom
    from payroute.policies.runner import run_policy
    from payroute.sim import Simulator

    log = _run(clean_world, _policy(clean_world), n=20_000)

    training = run_policy(
        Simulator(clean_world, seed=101), UniformRandom(clean_world.gateway_names, seed=101), 20_000
    )
    forced = HistoricallyBest("beacon", clean_world.gateway_names, train_seed=training.seed)

    with pytest.raises(NoOverlapError):
        ips(log, forced)


def test_the_fleet_p95_is_within_budget_when_the_constraint_held(clean_world):
    """The guarantee the feasible-set design buys: if every component
    distribution has P(latency > B) <= 0.05, so does any mixture of them. The
    fleet p95 holds by construction rather than by luck."""
    log = _run(clean_world, _policy(clean_world), n=30_000)

    satisfied = [r for r in list(log)[5_000:] if r.constraint_status == "satisfied"]
    assert len(satisfied) > 1_000, "not enough satisfied decisions to measure"

    latencies = sorted(r.outcome.latency_ms for r in satisfied)
    realised_p95 = latencies[int(len(latencies) * 0.95)]
    assert realised_p95 <= BUDGET_MS * 1.15, (
        f"realised p95 {realised_p95:.0f}ms against a {BUDGET_MS:.0f}ms budget"
    )


def test_it_sometimes_picks_a_lower_success_gateway_and_is_right_to(clean_world):
    """The rung's stated acceptance criterion: find such a transaction and show
    the arithmetic that justifies it."""
    from payroute.policies.objective import expected_net_revenue
    from payroute.sim import Simulator

    policy = _policy(clean_world)
    sim = Simulator(clean_world, seed=5)
    log = _run(clean_world, policy, n=25_000)

    replay = sim.fresh()
    oracle = replay.oracle
    fees = {g.name: (g.fee.flat_paise, g.fee.bps) for g in clean_world.gateways}

    def net(amount, gateway, p):
        flat, bps = fees[gateway]
        return expected_net_revenue(amount, clean_world.take_rate_bps, flat, bps, p)

    def margin(amount, gateway):
        flat, bps = fees[gateway]
        return (clean_world.take_rate_bps * amount) // 10_000 - (flat + (bps * amount) // 10_000)

    examined = justified = 0
    example = None
    for txn, record in zip(replay.transactions(len(log)), log):
        if record.constraint_status != "satisfied":
            continue
        feasible = [g for g, p in record.action_probs.items() if p > 0]
        chosen_p = oracle.true_success_prob(txn, record.action)
        rivals = [g for g in feasible if oracle.true_success_prob(txn, g) > chosen_p + 0.01]
        # Near-ties are not a tradeoff. At an amount around 50,000 paise cirrus's
        # 100-paise flat fee exactly cancels its 20bps rate advantage over atlas,
        # and there the two margins are identical — any choice is a coin flip and
        # auditing one proves nothing.
        rivals = [
            g for g in rivals
            if abs(margin(txn.amount_paise, g) - margin(txn.amount_paise, record.action)) >= 50
        ]
        if not rivals:
            continue

        examined += 1
        mine = net(txn.amount_paise, record.action, chosen_p)
        if all(mine > net(txn.amount_paise, g, oracle.true_success_prob(txn, g)) for g in rivals):
            justified += 1
            example = example or (record, chosen_p, rivals[0], oracle.true_success_prob(txn, rivals[0]))
        if examined >= 300:
            break

    assert examined > 50, "too few lower-success choices with a real margin gap to judge"
    assert justified / examined > 0.9, (
        f"only {justified}/{examined} lower-success choices are justified by their margin"
    )
    assert example is not None
