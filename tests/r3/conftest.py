"""Shared pieces for the R3 estimator tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

GATEWAYS = ["atlas", "beacon", "cirrus"]
T0 = datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc)


class StubTarget:
    """A target policy defined directly as a distribution over actions.

    Note what this interface requires: pi_e(a|x) must be a function of CONTEXT.
    That is not a simplification, it is the actual requirement of off-policy
    evaluation, and it is why round_robin cannot be a target policy — its
    action depends on an internal counter rather than on the transaction.
    """

    policy_id = "stub_target"

    def __init__(self, probs):
        self._probs = dict(probs)

    def action_probs(self, context):
        return dict(self._probs)


def record(txn_id, action, propensity, success, probs=None, hour=12, amount=35_000,
           method="upi", issuer="hdfc"):
    from payroute.decisionlog import DecisionRecord
    from payroute.sim import Outcome

    if probs is None:
        others = (1.0 - propensity) / (len(GATEWAYS) - 1)
        probs = {g: (propensity if g == action else others) for g in GATEWAYS}

    return DecisionRecord(
        txn_id=txn_id,
        ts=T0 + timedelta(seconds=int(txn_id.split("-")[-1])),
        context={
            "method": method,
            "issuer": issuer,
            "amount_paise": amount,
            "merchant_category": "food_delivery",
            "hour_of_day": hour,
        },
        action=action,
        propensity=propensity,
        action_probs=probs,
        policy_id="toy_logger",
        policy_version="1",
        outcome=Outcome(
            gateway=action,
            success=bool(success),
            latency_ms=100.0,
            fee_paise=100 if success else 0,
            net_revenue_paise=600 if success else 0,
        ),
    )


@pytest.fixture
def toy_log():
    """Four records with hand-computable weights against an 'always atlas'
    target:

        rec  action  p     r    pi_e   w = pi_e/p
        1    atlas   0.5   1    1.0    2.0
        2    beacon  0.25  0    0.0    0.0
        3    atlas   0.5   1    1.0    2.0
        4    cirrus  0.25  1    0.0    0.0

        IPS   = (2*1 + 0 + 2*1 + 0) / 4        = 1.00
        SNIPS = 4 / (2 + 0 + 2 + 0)            = 1.00
        ESS   = (sum w)^2 / sum w^2 = 16 / 8   = 2.00
    """
    from payroute.decisionlog import DecisionLog

    log = DecisionLog(seed=0, policy_id="toy_logger")
    log.append(record("txn-0-000000001", "atlas", 0.5, 1))
    log.append(record("txn-0-000000002", "beacon", 0.25, 0))
    log.append(record("txn-0-000000003", "atlas", 0.5, 1))
    log.append(record("txn-0-000000004", "cirrus", 0.25, 1))
    return log


@pytest.fixture
def always_atlas():
    return StubTarget({"atlas": 1.0, "beacon": 0.0, "cirrus": 0.0})
