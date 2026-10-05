"""R0: the gateway simulator.

This is the measuring instrument for the whole project. Every number in the
final table is a statement about this simulator's world, and R3 works at all
only because the world is synthetic: we know the true value of any policy, so
we can check whether an estimator recovered it.
"""

from payroute.sim.config import (
    AmountConfig,
    ArrivalConfig,
    DriftConfig,
    FeeConfig,
    GatewayConfig,
    LatencyConfig,
    SimulatorConfig,
)
from payroute.sim.failures import FAILURE_REASONS, choose_reason
from payroute.sim.incidents import (
    GatewayIncident,
    IssuerIncident,
    sample_gateway_incidents,
    sample_issuer_incidents,
)
from payroute.sim.world import Oracle, Outcome, Simulator, Transaction

__all__ = [
    "AmountConfig",
    "ArrivalConfig",
    "DriftConfig",
    "FeeConfig",
    "GatewayConfig",
    "LatencyConfig",
    "SimulatorConfig",
    "FAILURE_REASONS",
    "choose_reason",
    "GatewayIncident",
    "IssuerIncident",
    "sample_gateway_incidents",
    "sample_issuer_incidents",
    "Oracle",
    "Outcome",
    "Simulator",
    "Transaction",
]
