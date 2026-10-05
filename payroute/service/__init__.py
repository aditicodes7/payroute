"""R6: the decision service.

FastAPI for the endpoint, Redis for terminal health read on every request,
Kafka as the ordered system of record for decisions and outcomes, Postgres as
the queryable projection R3 runs SQL over.

The Redis, Kafka and Postgres adapters here are written against the real client
APIs and are NOT exercised by any passing test: this environment has no
container runtime. The contracts are covered by in-process fakes, and the
integration suite is written and skipped. See docs/decisions.md.
"""

from payroute.service.app import create_app
from payroute.service.reconcile import Drift, DriftReport, compare_health
from payroute.service.sink import BufferedSink, DecisionSink, InMemorySink, SinkUnavailable
from payroute.service.state import (
    HealthSnapshot,
    HealthStore,
    InMemoryHealthStore,
    StoreUnavailable,
    UnavailableHealthStore,
)

__all__ = [
    "create_app",
    "BufferedSink",
    "DecisionSink",
    "InMemorySink",
    "SinkUnavailable",
    "HealthSnapshot",
    "HealthStore",
    "InMemoryHealthStore",
    "StoreUnavailable",
    "UnavailableHealthStore",
    "Drift",
    "DriftReport",
    "compare_health",
]
