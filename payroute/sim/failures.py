"""Why a transaction failed, and what that implies about retrying it.

The reasons are the substance of R7, not decoration. `issuer_down` and
`gateway_timeout` both present as a timeout and call for opposite behaviour: a
different gateway routes around a dead gateway but reaches the same dead bank.
A retry policy with no access to the reason cannot tell those apart and is
reduced to guessing.
"""

from __future__ import annotations

FAILURE_REASONS = (
    "insufficient_funds",
    "invalid_credentials",
    "issuer_down",
    "gateway_timeout",
    "do_not_honour",
    "risk_declined",
)

# What a failure looks like when nothing is wrong with the infrastructure.
CLEAN_MIX = (
    ("insufficient_funds", 0.34),
    ("do_not_honour", 0.28),
    ("risk_declined", 0.18),
    ("invalid_credentials", 0.08),
    ("gateway_timeout", 0.07),
    ("issuer_down", 0.05),
)

# How much of the original success probability survives a retry, given why the
# previous attempt failed. This is the gradient the Q-learner exists to find:
# a timeout is worth another go, an empty account is not.
RETRY_SUCCESS_MULTIPLIER = {
    "gateway_timeout": 1.00,
    "do_not_honour": 0.50,
    "risk_declined": 0.30,
    "issuer_down": 0.15,
    "insufficient_funds": 0.05,
    "invalid_credentials": 0.02,
}


def choose_reason(u: float, gateway_severity: float, issuer_severity: float) -> str:
    """Pick a reason for a failure that has already been decided.

    Deterministic in `u`, which is drawn once per transaction from its own RNG
    stream — so this function cannot perturb whether the transaction succeeded.
    """
    w_gateway = max(0.0, gateway_severity)
    w_issuer = max(0.0, issuer_severity)
    w_clean = max(1e-9, (1.0 - w_gateway) * (1.0 - w_issuer))
    total = w_gateway + w_issuer + w_clean

    x = u * total
    if x < w_gateway:
        return "gateway_timeout"
    x -= w_gateway
    if x < w_issuer:
        return "issuer_down"
    x -= w_issuer

    share = x / w_clean
    cumulative = 0.0
    for reason, weight in CLEAN_MIX:
        cumulative += weight
        if share < cumulative:
            return reason
    return CLEAN_MIX[-1][0]
