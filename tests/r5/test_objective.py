"""R5: the money arithmetic.

Expected net revenue is

    E[net | x, g] = p(x, g) * ( value(x) - fee(x, g) )

Note the parentheses. R0 decided the gateway fee is charged ON SUCCESS ONLY, so
the fee belongs inside the probability-weighted term — not `p*value - fee`, as
the original brief wrote it. On a failure we earn nothing and pay nothing, and
the two formulas disagree by `(1 - p) * fee`, which is not small.

We never regress on revenue. R2 established why: revenue is heavy-tailed and
would wreck the confidence bounds. The probability is learned; the money is
computed.
"""

from __future__ import annotations

import pytest


def _net(amount, take_bps, flat, bps, p):
    from payroute.policies.objective import expected_net_revenue

    return expected_net_revenue(
        amount_paise=amount, take_rate_bps=take_bps, flat_paise=flat, bps=bps, p_success=p
    )


def test_expected_net_revenue_matches_hand_computation():
    # amount 100,000 paise, take rate 200bps -> value 2,000
    # gateway: 100 flat + 90bps -> fee 1,000; margin 1,000; p = 0.9 -> 900
    assert _net(100_000, 200, 100, 90, 0.9) == pytest.approx(900.0)


def test_the_fee_sits_inside_the_probability_term():
    """The two formulations differ by (1-p)*fee. Asserted directly, because
    getting this wrong would silently overcharge every failure and bias the
    whole rung toward reliable gateways."""
    amount, take, flat, bps, p = 100_000, 200, 100, 90, 0.9
    value = (take * amount) // 10_000
    fee = flat + (bps * amount) // 10_000

    correct = _net(amount, take, flat, bps, p)
    wrong = p * value - fee
    assert correct == pytest.approx(p * (value - fee))
    assert correct - wrong == pytest.approx((1 - p) * fee)
    assert correct != pytest.approx(wrong)


def test_a_lower_success_gateway_can_be_worth_more():
    """The rung's premise, as arithmetic.

    A gateway at 85% with a 130bps margin beats one at 95% with a 90bps margin,
    and the test states by how much so the claim cannot drift.
    """
    amount, take = 100_000, 200
    cheap = _net(amount, take, 100, 70, 0.85)      # margin 1,200 paise
    reliable = _net(amount, take, 0, 110, 0.95)    # margin 900 paise

    assert cheap > reliable
    assert cheap == pytest.approx(0.85 * (2_000 - 800))
    assert reliable == pytest.approx(0.95 * (2_000 - 1_100))


def test_the_ranking_flips_when_the_fee_spread_narrows():
    """And the premise has a limit. Narrow the spread and reliability wins —
    which is what the fee-spread sensitivity sweep is built to locate."""
    amount, take, p_cheap, p_reliable = 100_000, 200, 0.85, 0.95

    wide = _net(amount, take, 0, 70, p_cheap) > _net(amount, take, 0, 110, p_reliable)
    narrow = _net(amount, take, 0, 95, p_cheap) > _net(amount, take, 0, 100, p_reliable)
    assert wide and not narrow


def test_a_flat_fee_makes_the_best_choice_depend_on_the_amount():
    """100 paise flat is 0.3% of a 35,000 paise UPI payment and 0.02% of a
    450,000 paise netbanking one, so a policy that picks one gateway forever is
    wrong on one end of the distribution."""
    small = _net(10_000, 200, 100, 70, 0.9) > _net(10_000, 200, 0, 90, 0.9)
    large = _net(1_000_000, 200, 100, 70, 0.9) > _net(1_000_000, 200, 0, 90, 0.9)
    assert not small and large


def test_expected_net_revenue_is_zero_when_success_is_impossible():
    assert _net(100_000, 200, 100, 90, 0.0) == pytest.approx(0.0)


def test_a_gateway_whose_fee_exceeds_the_take_rate_is_worth_negative():
    """Worth asserting because such a gateway should never be chosen, and a
    max() over a wrongly-clamped objective would hide it."""
    assert _net(100_000, 200, 0, 250, 0.9) < 0
