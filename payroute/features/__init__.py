"""Context features.

R2's static featuriser lives here. R4's time-decayed per-terminal health
features will join it, which is the point of keeping this separate from
`policies/`: R4's measurement is swapping the feature implementation while
holding the policy fixed, and that comparison is only honest if the two can be
swapped independently.
"""

from payroute.features.context import Featuriser

__all__ = ["Featuriser"]
