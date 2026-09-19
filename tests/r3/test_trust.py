"""R3: making an untrustworthy estimate impossible to report as a result.

The expected failure mode for this rung is IPS blowing up silently — an
enormous-variance number reported as a finding because nobody checked whether
it was trustworthy. The defence is the same one used at R1: enforce rule 10 in
the return type, so there is no way to obtain a point estimate without its
interval, its effective sample size and its verdict.
"""

from __future__ import annotations

import math

import pytest

from tests.r3.conftest import GATEWAYS, StubTarget, record


def _log(pairs, policy_id="logger"):
    from payroute.decisionlog import DecisionLog

    log = DecisionLog(seed=0, policy_id=policy_id)
    for i, (action, propensity, success) in enumerate(pairs):
        log.append(record(f"txn-0-{i:09d}", action, propensity, success))
    return log


ALL_ESTIMATORS = ["ips", "snips", "direct_method", "doubly_robust", "clipped_ips"]


@pytest.mark.parametrize("name", ALL_ESTIMATORS)
def test_no_estimator_can_return_a_bare_number(toy_log, always_atlas, name):
    import payroute.ope as ope

    fn = getattr(ope, name)
    kwargs = {}
    if name in ("direct_method", "doubly_robust"):
        kwargs["reward_model"] = lambda rec, action: 0.5
    if name == "clipped_ips":
        kwargs["clip"] = 10.0

    result = fn(toy_log, always_atlas, **kwargs)
    assert not isinstance(result, float)
    for field in ("estimate", "ci", "normal_ci", "ess", "n", "max_weight", "trust"):
        assert hasattr(result, field), f"{name} result is missing {field}"
    assert result.ci[0] <= result.estimate <= result.ci[1]


def test_a_log_with_no_overlap_is_refused_rather_than_estimated():
    """At epsilon = 0 the logging policy is deterministic: every action it did
    not take has probability zero. The target policy's value is then not merely
    hard to estimate, it is NOT IDENTIFIED — the log contains no information
    about the actions it would have taken differently. Returning a number here
    would be inventing one.
    """
    from payroute.ope import NoOverlapError, ips

    deterministic = _log([("beacon", 1.0, 1)] * 200, policy_id="linucb_eps0")
    target = StubTarget({"atlas": 1.0, "beacon": 0.0, "cirrus": 0.0})

    with pytest.raises(NoOverlapError) as excinfo:
        ips(deterministic, target)
    assert "overlap" in str(excinfo.value).lower()


def test_partial_overlap_is_flagged_not_silently_dropped():
    """Softer version of the same failure: the target takes actions the logger
    took only in some contexts. The estimate is computable but rests on very
    little, and the result has to say so."""
    from payroute.ope import ips

    pairs = [("atlas", 0.002, 1)] + [("beacon", 0.998, 1)] * 2_000
    result = ips(_log(pairs), StubTarget({"atlas": 1.0, "beacon": 0.0, "cirrus": 0.0}))

    assert result.trust == "untrustworthy"
    assert any("effective sample size" in w.lower() for w in result.warnings)


def test_trust_verdict_tracks_effective_sample_size(toy_log, always_atlas):
    from payroute.ope import ips

    healthy = _log([(g, 1 / 3, i % 2) for i, g in enumerate(GATEWAYS * 400)])
    uniform = StubTarget({g: 1 / 3 for g in GATEWAYS})
    assert ips(healthy, uniform).trust == "ok"

    thin = _log([("atlas", 0.01, 1)] + [("beacon", 0.99, 0)] * 300)
    assert ips(thin, StubTarget({"atlas": 1.0, "beacon": 0.0, "cirrus": 0.0})).trust != "ok"


def test_the_normal_interval_understates_uncertainty_on_a_skewed_log():
    """Reported alongside the bootstrap interval specifically to be seen
    failing.

    The normal approximation assumes the sample standard deviation describes the
    spread. On a heavy-tailed weight distribution it does not, and the interval
    comes out narrow, confident and wrong — worst exactly in the regime where
    the estimate needs the most scepticism.
    """
    from payroute.ope import ips

    pairs = [("atlas", 0.004, 1)] * 3 + [("beacon", 0.996, 1)] * 1_500
    result = ips(_log(pairs), StubTarget({"atlas": 1.0, "beacon": 0.0, "cirrus": 0.0}))

    normal_width = result.normal_ci[1] - result.normal_ci[0]
    bootstrap_width = result.ci[1] - result.ci[0]
    assert normal_width < bootstrap_width, (
        f"expected the normal interval to be the narrower (and more misleading) one: "
        f"normal {normal_width:.4f} vs bootstrap {bootstrap_width:.4f}"
    )


def test_warnings_name_the_actual_problem(toy_log, always_atlas):
    """A warning that says 'check your data' teaches nothing. These have to name
    the quantity that went wrong and its value."""
    from payroute.ope import ips

    pairs = [("atlas", 0.002, 1)] + [("beacon", 0.998, 1)] * 2_000
    result = ips(_log(pairs), StubTarget({"atlas": 1.0, "beacon": 0.0, "cirrus": 0.0}))
    joined = " ".join(result.warnings).lower()
    assert "effective sample size" in joined
    assert any(ch.isdigit() for ch in joined), "warnings should quote the numbers"
