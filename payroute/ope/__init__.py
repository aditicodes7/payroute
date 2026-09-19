"""Off-policy evaluation.

R3's estimators (Direct Method, IPS, SNIPS, Doubly Robust) and their
diagnostics live here. R2 contributes the regret computation, which is the one
quantity in this package that is exact rather than estimated.
"""

from payroute.ope.regret import cumulative_regret, instantaneous_regret

__all__ = ["cumulative_regret", "instantaneous_regret"]
