"""R1: the baseline policies.

Five policies, three purposes:

  round_robin          the illustrative baseline — what people actually build,
                       and the one whose failure to react is the point
  uniform_random       the LOGGING policy: genuinely randomised, so its
                       propensities are honestly 1/K and R3 has a dataset with
                       full coverage to work from
  always_cheapest      lowest fee for this transaction, success ignored
  historically_best    one fixed gateway forever — the strongest naive baseline
  best_per_segment     a frozen (method, issuer) -> gateway lookup table

The last two are FITTED, which means they can leak. Both carry the seed of the
run they were fitted on, and the runner refuses to evaluate a fitted policy on
that same seed.
"""

from __future__ import annotations

import pytest

from tests.conftest import assert_within_band


def _sim(cfg, seed=7):
    from payroute.sim import Simulator

    return Simulator(cfg, seed=seed)


def _explore_log(cfg, seed, n=40_000):
    """A uniform-random exploration log — the only kind of data from which a
    good static table can honestly be built."""
    from payroute.policies import UniformRandom
    from payroute.policies.runner import run_policy

    return run_policy(_sim(cfg, seed=seed), UniformRandom(cfg.gateway_names, seed=seed), n)


# ---------------------------------------------------------------------------
# round robin
# ---------------------------------------------------------------------------


def test_round_robin_cycles_through_gateways_in_order(clean_world):
    from payroute.policies import RoundRobin

    policy = RoundRobin(clean_world.gateway_names)
    sim = _sim(clean_world)
    chosen = [policy.decide(txn).action for txn in sim.transactions(9)]
    assert chosen == clean_world.gateway_names * 3


def test_round_robin_propensity_is_one_and_its_probs_are_one_hot(clean_world):
    """Round robin is deterministic, so this is the honest answer — and it is
    exactly why its log is nearly useless for off-policy evaluation. Logging
    1/K here would produce correct-looking arithmetic by coincidence, because
    round robin's assignment happens to be independent of context. That
    coincidence is not a rule, and writing it down as one is how you get a
    wrong answer later."""
    from payroute.policies import RoundRobin

    policy = RoundRobin(clean_world.gateway_names)
    for txn in _sim(clean_world).transactions(12):
        decision = policy.decide(txn)
        assert decision.propensity == 1.0
        assert decision.action_probs[decision.action] == 1.0
        assert sum(decision.action_probs.values()) == pytest.approx(1.0)
        assert sorted(decision.action_probs.values()) == [0.0, 0.0, 1.0]


def test_round_robin_splits_traffic_evenly(clean_world):
    from payroute.policies import RoundRobin
    from payroute.policies.runner import run_policy

    log = run_policy(_sim(clean_world), RoundRobin(clean_world.gateway_names), 9_000)
    counts = {}
    for record in log:
        counts[record.action] = counts.get(record.action, 0) + 1
    assert set(counts.values()) == {3_000}


# ---------------------------------------------------------------------------
# uniform random — the logging policy
# ---------------------------------------------------------------------------


def test_uniform_random_has_an_honest_propensity_of_one_over_k(clean_world):
    from payroute.policies import UniformRandom

    k = len(clean_world.gateways)
    policy = UniformRandom(clean_world.gateway_names, seed=3)
    for txn in _sim(clean_world).transactions(200):
        decision = policy.decide(txn)
        assert decision.propensity == pytest.approx(1 / k)
        assert all(p == pytest.approx(1 / k) for p in decision.action_probs.values())


def test_uniform_random_actually_randomises(clean_world):
    """Not merely claims to. If this policy were secretly deterministic its
    logged propensities would be a fiction, and every OPE estimate built on
    them would be wrong in a way nothing downstream could detect."""
    from payroute.policies import UniformRandom
    from payroute.policies.runner import run_policy

    n = 30_000
    log = run_policy(_sim(clean_world), UniformRandom(clean_world.gateway_names, seed=3), n)
    counts = {name: 0 for name in clean_world.gateway_names}
    for record in log:
        counts[record.action] += 1
    for name, count in counts.items():
        assert_within_band(count / n, 1 / 3, n, label=f"uniform share of {name}")


def test_uniform_random_choice_is_independent_of_context(clean_world):
    """Coverage is the property that matters for R3: every segment must contain
    every gateway, or the estimators have nothing to reweight."""
    from payroute.policies import UniformRandom
    from payroute.policies.runner import run_policy

    log = run_policy(_sim(clean_world), UniformRandom(clean_world.gateway_names, seed=3), 40_000)
    seen = {}
    for record in log:
        key = (record.context["method"], record.context["issuer"])
        seen.setdefault(key, set()).add(record.action)

    assert len(seen) == 15, f"expected all 3x5 segments, saw {len(seen)}"
    for key, actions in seen.items():
        assert actions == set(clean_world.gateway_names), f"segment {key} never saw {set(clean_world.gateway_names) - actions}"


# ---------------------------------------------------------------------------
# always cheapest
# ---------------------------------------------------------------------------


def test_always_cheapest_is_per_transaction_not_a_fixed_gateway(clean_world):
    """A flat fee component makes "cheapest" depend on the amount: cirrus
    charges 100 paise + 70bps, atlas charges 90bps and no flat fee, so they
    cross at 50,000 paise. A baseline that picks one gateway forever would be
    getting this wrong and calling itself cheapest."""
    from payroute.policies import AlwaysCheapest
    from payroute.policies.runner import run_policy

    log = run_policy(_sim(clean_world), AlwaysCheapest(clean_world), 20_000)
    chosen = {record.action for record in log}
    assert len(chosen) > 1, f"always_cheapest never varied its choice: {chosen}"


def test_always_cheapest_picks_the_lowest_actual_fee(clean_world):
    from payroute.policies import AlwaysCheapest
    from payroute.policies.runner import run_policy

    log = run_policy(_sim(clean_world), AlwaysCheapest(clean_world), 5_000)
    for record in log:
        amount = record.context["amount_paise"]
        fees = {
            g.name: g.fee.flat_paise + (g.fee.bps * amount) // 10_000 for g in clean_world.gateways
        }
        assert fees[record.action] == min(fees.values())


def test_always_cheapest_is_deterministic(clean_world):
    from payroute.policies import AlwaysCheapest

    policy = AlwaysCheapest(clean_world)
    for txn in _sim(clean_world).transactions(100):
        assert policy.decide(txn).propensity == 1.0


# ---------------------------------------------------------------------------
# fitted baselines
# ---------------------------------------------------------------------------


def test_historically_best_picks_one_gateway_and_never_moves(clean_world):
    from payroute.policies import HistoricallyBest

    policy = HistoricallyBest.fit(_explore_log(clean_world, seed=101))
    chosen = {policy.decide(txn).action for txn in _sim(clean_world, seed=5).transactions(500)}
    assert len(chosen) == 1
    assert policy.decide(next(iter(_sim(clean_world, seed=5).transactions(1)))).propensity == 1.0


def test_best_per_segment_varies_its_choice_by_segment(clean_world):
    """The whole reason this baseline exists. If it collapsed to one gateway it
    would just be historically_best under another name, and R2's eventual win
    would still conflate "contextual" with "adaptive"."""
    from payroute.policies import BestPerSegment

    policy = BestPerSegment.fit(_explore_log(clean_world, seed=101))
    chosen = {policy.decide(txn).action for txn in _sim(clean_world, seed=5).transactions(5_000)}
    assert len(chosen) >= 2, f"best_per_segment behaves like a single fixed gateway: {chosen}"


def test_best_per_segment_recovers_the_known_best_gateway_per_segment(clean_world):
    """Scored against the simulator's configured matrix, which we know."""
    from tests.conftest import true_prob
    from payroute.policies import BestPerSegment

    policy = BestPerSegment.fit(_explore_log(clean_world, seed=101, n=80_000))

    correct = total = 0
    for method in clean_world.method_mix:
        for issuer in clean_world.issuer_mix:
            truth = max(
                clean_world.gateway_names, key=lambda n: true_prob(clean_world, n, method, issuer)
            )
            if policy.table.get((method, issuer)) == truth:
                correct += 1
            total += 1
    assert correct / total >= 0.8, f"recovered only {correct}/{total} segment winners"


def test_best_per_segment_falls_back_for_an_unseen_segment(clean_world):
    from payroute.policies import BestPerSegment

    policy = BestPerSegment.fit(_explore_log(clean_world, seed=101))
    policy.table.pop(("upi", "hdfc"), None)

    txn = next(
        t for t in _sim(clean_world, seed=5).transactions(5_000) if (t.method, t.issuer) == ("upi", "hdfc")
    )
    decision = policy.decide(txn)
    assert decision.action in clean_world.gateway_names
    assert decision.propensity == 1.0


def test_fitted_policies_refuse_to_be_evaluated_on_their_training_run(clean_world):
    """In-sample leakage, made structurally impossible rather than remembered.

    Fit the table on the same transactions it is scored against and it gets to
    peek at the noise in its own test set — it looks better than it is, and R2
    looks worse than it is by exactly the same amount.
    """
    from payroute.policies import BestPerSegment
    from payroute.policies.runner import LeakageError, run_policy

    policy = BestPerSegment.fit(_explore_log(clean_world, seed=101))
    assert policy.train_seed == 101

    with pytest.raises(LeakageError):
        run_policy(_sim(clean_world, seed=101), policy, 100)

    run_policy(_sim(clean_world, seed=202), policy, 100)  # a different seed is fine
