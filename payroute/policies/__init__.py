"""Routing policies.

R1's baselines live here. R2's LinUCB and R5's constrained selection will join
them, behind the same `Policy` interface — which means the propensity test in
tests/r1/test_propensity_invariant.py covers them automatically.
"""

from payroute.decisionlog import Decision
from payroute.policies.baselines import (
    AlwaysCheapest,
    BestPerSegment,
    HistoricallyBest,
    Policy,
    RoundRobin,
    UniformRandom,
)

__all__ = [
    "Decision",
    "Policy",
    "RoundRobin",
    "UniformRandom",
    "AlwaysCheapest",
    "HistoricallyBest",
    "BestPerSegment",
]
