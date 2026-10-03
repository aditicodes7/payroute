"""Off-policy evaluation.

The rung this project is pointed at. Because the world is synthetic we know the
true value of any policy, so these estimators can be scored against an answer
key — something a real deployment can never do, since there it can only compare
one estimate against another.
"""

from payroute.ope.estimators import (
    ESS_SUSPECT,
    ESS_UNTRUSTWORTHY,
    NoOverlapError,
    OPEResult,
    clipped_ips,
    direct_method,
    doubly_robust,
    ips,
    snips,
)
from payroute.ope.regret import cumulative_regret, instantaneous_regret
from payroute.ope.reward_model import CrossFittedRewardModel, RidgeRewardModel

__all__ = [
    "NoOverlapError",
    "OPEResult",
    "ESS_SUSPECT",
    "ESS_UNTRUSTWORTHY",
    "ips",
    "snips",
    "clipped_ips",
    "direct_method",
    "doubly_robust",
    "CrossFittedRewardModel",
    "RidgeRewardModel",
    "cumulative_regret",
    "instantaneous_regret",
]
