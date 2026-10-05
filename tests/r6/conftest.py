"""Fixtures for the service tests.

Everything here runs against in-process fakes. There is no Docker in this
environment, so the Redis, Kafka and Postgres adapters exist and are written
against the real client APIs but are NOT exercised by these tests. The
container-based suite is written and marked skipped; see tests/r6/test_integration.py.

That distinction matters most for the latency numbers: a p99 measured against a
dict is a statement about our own code, not about a deployment.
"""

from __future__ import annotations

import pytest

from config.default_world import build_default_world


@pytest.fixture
def world():
    return build_default_world()


@pytest.fixture
def priors(world):
    from payroute.service.state import HealthSnapshot

    return HealthSnapshot(
        values={g: {"health": 0.9, "p95_ms": 250.0} for g in world.gateway_names},
        source="fallback",
    )


@pytest.fixture
def store(world):
    from payroute.service.state import InMemoryHealthStore

    store = InMemoryHealthStore()
    for gateway in world.gateway_names:
        store.write(gateway, health=0.94, p95_ms=300.0)
    return store


@pytest.fixture
def sink():
    from payroute.service.sink import InMemorySink

    return InMemorySink()


@pytest.fixture
def policy(world):
    from payroute.policies import ConstrainedLinUCB

    return ConstrainedLinUCB.for_config(world, p95_budget_ms=420.0, epsilon=0.1, seed=11)


@pytest.fixture
def client(world, policy, store, sink, priors):
    from fastapi.testclient import TestClient

    from payroute.service.app import create_app

    app = create_app(policy=policy, store=store, sink=sink, priors=priors, gateways=world.gateway_names)
    return TestClient(app)


REQUEST = {
    "amount_paise": 125_000,
    "method": "upi",
    "issuer": "hdfc",
    "merchant_category": "ecommerce",
}
