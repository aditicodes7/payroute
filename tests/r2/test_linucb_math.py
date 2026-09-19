"""R2: LinUCB's arithmetic, checked against hand computation.

LinUCB is two lines of linear algebra per arm:

    A_a = lambda*I + sum x x^T        b_a = sum r x       theta_a = A_a^-1 b_a
    score(a) = theta_a . x + alpha * sqrt(x^T A_a^-1 x)

Everything interesting about the policy comes from the second term, so it is
worth testing that it behaves the way the explanation claims: shrinking where
data accumulates, staying wide in directions never sampled.
"""

from __future__ import annotations

import numpy as np
import pytest


def _policy(cfg, **kwargs):
    from payroute.features import Featuriser
    from payroute.policies import LinUCB

    params = dict(alpha=1.0, epsilon=0.0, ridge=1.0, seed=0)
    params.update(kwargs)
    return LinUCB(cfg.gateway_names, Featuriser.from_config(cfg), **params)


def _ctx(**overrides):
    base = {
        "method": "upi",
        "issuer": "hdfc",
        "amount_paise": 35_000,
        "merchant_category": "food_delivery",
        "hour_of_day": 13,
    }
    base.update(overrides)
    return base


def test_a_starts_as_ridge_times_identity_and_b_at_zero(clean_world):
    policy = _policy(clean_world, ridge=2.0)
    for name in clean_world.gateway_names:
        assert np.allclose(policy.A(name), 2.0 * np.eye(policy.dim))
        assert np.allclose(policy.b(name), np.zeros(policy.dim))
        assert np.allclose(policy.theta(name), np.zeros(policy.dim))


def test_a_single_update_matches_the_closed_form(clean_world):
    policy = _policy(clean_world, ridge=1.0)
    context = _ctx()
    x = policy.featuriser.transform(context)

    policy.update(context, "atlas", 1.0)

    assert np.allclose(policy.A("atlas"), np.eye(policy.dim) + np.outer(x, x))
    assert np.allclose(policy.b("atlas"), 1.0 * x)
    assert np.allclose(policy.theta("atlas"), np.linalg.solve(policy.A("atlas"), policy.b("atlas")))


def test_only_the_chosen_arm_is_updated(clean_world):
    """Bandit feedback, in one assertion: we learn about the arm we pulled and
    nothing about the others."""
    policy = _policy(clean_world)
    before = {n: policy.A(n).copy() for n in clean_world.gateway_names}

    policy.update(_ctx(), "beacon", 1.0)

    assert not np.allclose(policy.A("beacon"), before["beacon"])
    for name in ("atlas", "cirrus"):
        assert np.allclose(policy.A(name), before[name])


def test_maintained_inverse_stays_equal_to_the_true_inverse(clean_world):
    """A_inv is updated incrementally (Sherman-Morrison) so that scoring is a
    matrix-vector product rather than a 16x16 inversion on every decision. That
    is a real speedup and a real numerical-drift risk, so it gets checked."""
    policy = _policy(clean_world)
    rng = np.random.default_rng(0)

    for _ in range(500):
        context = _ctx(
            method=str(rng.choice(list(clean_world.method_mix))),
            issuer=str(rng.choice(list(clean_world.issuer_mix))),
            amount_paise=int(rng.integers(1_000, 1_000_000)),
            hour_of_day=int(rng.integers(0, 24)),
        )
        policy.update(context, "atlas", float(rng.random() < 0.9))

    assert np.allclose(policy.A_inv("atlas"), np.linalg.inv(policy.A("atlas")), atol=1e-8)


def test_confidence_width_shrinks_where_data_accumulates(clean_world):
    policy = _policy(clean_world)
    context = _ctx()

    before = policy.confidence_width(context, "atlas")
    for _ in range(200):
        policy.update(context, "atlas", 1.0)
    after = policy.confidence_width(context, "atlas")

    assert after < before / 5, f"width barely moved: {before:.4f} -> {after:.4f}"


def test_confidence_width_is_directional(clean_world):
    """The property that makes LinUCB more data-efficient than a per-segment
    lookup table: being confident about UPI-HDFC teaches it nothing about
    netbanking-kotak, and it knows that."""
    policy = _policy(clean_world)
    seen = _ctx(method="upi", issuer="hdfc")
    # Genuinely unrelated: every categorical AND both continuous features
    # differ. Changing only method and issuer leaves five of sixteen directions
    # shared (category, amount, the two hour terms, bias), and learning about
    # those legitimately reduces uncertainty for both contexts.
    unrelated = _ctx(
        method="netbanking", issuer="kotak", merchant_category="travel",
        hour_of_day=3, amount_paise=900_000,
    )

    before_unrelated = policy.confidence_width(unrelated, "atlas")
    for _ in range(300):
        policy.update(seen, "atlas", 1.0)

    assert policy.confidence_width(seen, "atlas") < 0.2 * before_unrelated
    assert policy.confidence_width(unrelated, "atlas") > 0.95 * before_unrelated


def test_partially_shared_contexts_do_generalise(clean_world):
    """The other half of the same property, and the reason the test above had
    to be written carefully: uncertainty is directional, so a context that
    shares SOME directions with the training data does get more confident.

    That is not a leak — it is exactly what makes LinUCB more data-efficient
    than a per-segment lookup table, which learns nothing about a cell until it
    has observations in that cell.
    """
    policy = _policy(clean_world)
    seen = _ctx(method="upi", issuer="hdfc")
    overlapping = _ctx(method="netbanking", issuer="kotak")  # same category, amount, hour

    before = policy.confidence_width(overlapping, "atlas")
    for _ in range(300):
        policy.update(seen, "atlas", 1.0)
    after = policy.confidence_width(overlapping, "atlas")

    assert 0.5 * before < after < 0.9 * before, (
        f"expected partial generalisation, got ratio {after / before:.3f}"
    )


def test_theta_recovers_a_known_linear_reward(clean_world):
    """The estimator works: feed it rewards generated by a known theta and it
    should converge to that theta."""
    policy = _policy(clean_world, ridge=1e-6)
    rng = np.random.default_rng(3)
    truth = rng.normal(size=policy.dim) * 0.1

    contexts = []
    for _ in range(4_000):
        context = _ctx(
            method=str(rng.choice(list(clean_world.method_mix))),
            issuer=str(rng.choice(list(clean_world.issuer_mix))),
            merchant_category=str(rng.choice(list(clean_world.merchant_category_mix))),
            amount_paise=int(rng.integers(1_000, 1_000_000)),
            hour_of_day=int(rng.integers(0, 24)),
        )
        contexts.append(context)
        x = policy.featuriser.transform(context)
        policy.update(context, "atlas", float(x @ truth))

    # theta itself is NOT identifiable, and asserting that it is would be
    # asserting something false about the model. The three method one-hots sum
    # to the bias column, as do the five issuer one-hots and the four category
    # one-hots — three exact linear dependencies, so the design matrix has rank
    # 13 of 16 and infinitely many parameter vectors give identical
    # predictions. Ridge returns the minimum-norm one.
    design = policy.A("atlas") - policy.ridge * np.eye(policy.dim)
    assert np.linalg.matrix_rank(design) == policy.dim - 3

    # What must hold is that PREDICTIONS are recovered.
    errors = [
        abs(float(policy.featuriser.transform(c) @ (policy.theta("atlas") - truth)))
        for c in contexts[:500]
    ]
    assert max(errors) < 1e-5, f"max prediction error {max(errors):.2e}"

    # And the minimum-norm solution is no larger than the generating one.
    assert np.linalg.norm(policy.theta("atlas")) <= np.linalg.norm(truth) + 1e-9


def test_score_is_prediction_plus_alpha_times_width(clean_world):
    policy = _policy(clean_world, alpha=2.5)
    context = _ctx()
    policy.update(context, "atlas", 1.0)

    x = policy.featuriser.transform(context)
    expected = float(policy.theta("atlas") @ x) + 2.5 * policy.confidence_width(context, "atlas")
    assert policy.scores(context)["atlas"] == pytest.approx(expected)


def test_alpha_zero_turns_off_exploration_entirely(clean_world):
    """Useful as a control in the alpha sweep: with no exploration bonus the
    policy is pure greedy exploitation of its current estimate."""
    policy = _policy(clean_world, alpha=0.0)
    context = _ctx()
    x = policy.featuriser.transform(context)
    for name in clean_world.gateway_names:
        assert policy.scores(context)[name] == pytest.approx(float(policy.theta(name) @ x))
