"""R3: each estimator against a log small enough to compute by hand.

Statistical tests can hide an arithmetic error inside sampling noise. These
cannot: every expected number in this file was worked out on paper first (see
the toy_log fixture) and the test is checking the implementation against that,
not against another run of itself.
"""

from __future__ import annotations

import math

import pytest

from tests.r3.conftest import GATEWAYS, StubTarget


def test_ips_matches_the_hand_computed_value(toy_log, always_atlas):
    from payroute.ope import ips

    result = ips(toy_log, always_atlas)
    assert result.estimate == pytest.approx(1.0)
    assert result.n == 4


def test_snips_matches_the_hand_computed_value(toy_log, always_atlas):
    from payroute.ope import snips

    assert snips(toy_log, always_atlas).estimate == pytest.approx(1.0)


def test_effective_sample_size_matches_the_hand_computed_value(toy_log, always_atlas):
    from payroute.ope import ips

    assert ips(toy_log, always_atlas).ess == pytest.approx(2.0)


def test_ess_equals_n_when_every_weight_is_equal(toy_log):
    """The sanity anchor: if the target policy is the logging policy, nothing is
    reweighted and all the information in the log is usable."""
    from payroute.ope import ips

    uniform = StubTarget({g: 1 / 3 for g in GATEWAYS})
    log_uniform = _uniform_log()
    result = ips(log_uniform, uniform)
    assert result.ess == pytest.approx(result.n)


def _uniform_log():
    from payroute.decisionlog import DecisionLog
    from tests.r3.conftest import record

    log = DecisionLog(seed=0, policy_id="uniform")
    for i, action in enumerate(GATEWAYS * 10):
        log.append(
            record(f"txn-0-{i:09d}", action, 1 / 3, i % 2, probs={g: 1 / 3 for g in GATEWAYS})
        )
    return log


def test_ess_collapses_when_one_weight_dominates():
    """Ten thousand rows whose weight mass sits in one of them is a report about
    one transaction, whatever the row count says."""
    from payroute.decisionlog import DecisionLog
    from payroute.ope import ips
    from tests.r3.conftest import record

    log = DecisionLog(seed=0, policy_id="skewed")
    log.append(record("txn-0-000000000", "atlas", 0.001, 1))
    for i in range(1, 500):
        log.append(record(f"txn-0-{i:09d}", "beacon", 0.999, 1))

    result = ips(log, StubTarget({"atlas": 1.0, "beacon": 0.0, "cirrus": 0.0}))
    assert result.n == 500
    assert result.ess == pytest.approx(1.0, abs=1e-6)
    assert result.max_weight == pytest.approx(1000.0)


def test_direct_method_ignores_propensities_entirely(toy_log, always_atlas):
    """A constant reward model must give a constant answer, whatever the log
    says. That is DM's defining weakness stated as a test: it cannot be
    corrected by evidence it does not look at."""
    from payroute.ope import direct_method

    result = direct_method(toy_log, always_atlas, lambda rec, action: 0.9)
    assert result.estimate == pytest.approx(0.9)


def test_doubly_robust_reduces_to_ips_when_the_model_predicts_zero(toy_log, always_atlas):
    """DR = DM + importance-weighted residuals. Zero out the model and the
    residual is the raw reward, so DR must become IPS exactly."""
    from payroute.ope import doubly_robust, ips

    dr = doubly_robust(toy_log, always_atlas, lambda rec, action: 0.0)
    assert dr.estimate == pytest.approx(ips(toy_log, always_atlas).estimate)


def test_doubly_robust_reduces_to_the_direct_method_when_the_model_is_perfect(
    toy_log, always_atlas
):
    """The other limit: if the model predicts every reward exactly, the
    residuals vanish and the weights multiply zero. This is the mechanism that
    makes DR stable — a weight of 300 times a residual near zero is harmless."""
    from payroute.ope import doubly_robust

    perfect = lambda rec, action: float(rec.outcome.success)
    result = doubly_robust(toy_log, always_atlas, perfect)
    expected = sum(float(r.outcome.success) for r in toy_log) / len(toy_log)
    assert result.estimate == pytest.approx(expected)


def test_clipped_ips_with_no_clipping_equals_ips(toy_log, always_atlas):
    from payroute.ope import clipped_ips, ips

    assert clipped_ips(toy_log, always_atlas, clip=math.inf).estimate == pytest.approx(
        ips(toy_log, always_atlas).estimate
    )


def test_clipped_ips_trades_bias_for_variance(toy_log, always_atlas):
    """Capping the weight at 1.5 turns the two weights of 2.0 into 1.5:
    (1.5 + 0 + 1.5 + 0) / 4 = 0.75, against an unclipped 1.00. The estimate
    moves, on purpose — that is the bias being bought."""
    from payroute.ope import clipped_ips

    result = clipped_ips(toy_log, always_atlas, clip=1.5)
    assert result.estimate == pytest.approx(0.75)
    # max_weight and ESS report the RAW weights, because they describe how much
    # evidence the log holds about this target — not what the estimator did to
    # its own arithmetic afterwards. Reporting the post-clip maximum (1.5) would
    # hide precisely what a reader needs to see.
    assert result.max_weight == pytest.approx(2.0)
    assert any("importance weight" in w for w in result.warnings)


def test_ips_can_return_a_value_outside_the_possible_reward_range():
    """IPS is not bounded by the range of the rewards it averages. A success
    rate above 1.0 is impossible and therefore an unusually legible signal that
    the estimate is not to be trusted. SNIPS cannot do this, which is both its
    advantage and the reason it hides the problem."""
    from payroute.decisionlog import DecisionLog
    from payroute.ope import ips, snips
    from tests.r3.conftest import record

    log = DecisionLog(seed=0, policy_id="rare")
    log.append(record("txn-0-000000000", "atlas", 0.01, 1))
    for i in range(1, 50):
        log.append(record(f"txn-0-{i:09d}", "beacon", 0.99, 1))

    target = StubTarget({"atlas": 1.0, "beacon": 0.0, "cirrus": 0.0})
    assert ips(log, target).estimate > 1.0
    assert 0.0 <= snips(log, target).estimate <= 1.0
