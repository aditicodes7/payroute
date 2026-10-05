"""R6: what happens when the state store is gone.

The acceptance criterion is that killing Redis mid-request "fails safely —
doesn't crash, doesn't silently guess". The second clause is the design. We can
route without health data by falling back to priors, but doing so SILENTLY is
the forbidden behaviour: those decisions would be indistinguishable in the log
from fully-informed ones, and R3 would later treat them as one population.
"""

from __future__ import annotations

import pytest

from tests.r6.conftest import REQUEST


@pytest.fixture
def broken_store():
    from payroute.service.state import UnavailableHealthStore

    return UnavailableHealthStore()


@pytest.fixture
def broken_client(world, policy, broken_store, sink, priors):
    from fastapi.testclient import TestClient

    from payroute.service.app import create_app

    return TestClient(
        create_app(policy=policy, store=broken_store, sink=sink, priors=priors,
                   gateways=world.gateway_names)
    )


def test_the_service_still_routes_without_the_state_store(broken_client):
    response = broken_client.post("/decide", json=REQUEST)
    assert response.status_code == 200
    assert response.json()["gateway"]


def test_a_blind_decision_says_so(broken_client, sink):
    """The whole point. Without this field, 4% of traffic served without health
    data looks exactly like the other 96% forever after."""
    body = broken_client.post("/decide", json=REQUEST).json()
    assert body["health_source"] == "fallback"
    assert sink.records[0].health_source == "fallback"


def test_a_normal_decision_says_so_too(client, sink):
    body = client.post("/decide", json=REQUEST).json()
    assert body["health_source"] == "redis"
    assert sink.records[0].health_source == "redis"


def test_the_fallback_uses_the_configured_prior_and_invents_nothing(broken_client, sink, priors):
    broken_client.post("/decide", json=REQUEST)
    context = sink.records[0].context
    for gateway, values in priors.values.items():
        assert context[f"health_{gateway}"] == pytest.approx(values["health"])
        assert context[f"p95_{gateway}"] == pytest.approx(values["p95_ms"])


def test_a_store_that_fails_midway_does_not_crash_the_service(world, policy, sink, priors):
    """Not a clean outage: the store works, then stops, then works again —
    which is what a failover actually looks like."""
    from fastapi.testclient import TestClient

    from payroute.service.app import create_app
    from payroute.service.state import InMemoryHealthStore

    store = InMemoryHealthStore()
    for gateway in world.gateway_names:
        store.write(gateway, health=0.94, p95_ms=300.0)
    client = TestClient(
        create_app(policy=policy, store=store, sink=sink, priors=priors,
                   gateways=world.gateway_names)
    )

    sources = []
    for i in range(30):
        store.fail = 10 <= i < 20
        response = client.post("/decide", json=REQUEST)
        assert response.status_code == 200
        sources.append(response.json()["health_source"])

    assert sources[:10] == ["redis"] * 10
    assert sources[10:20] == ["fallback"] * 10
    assert sources[20:] == ["redis"] * 10


def test_the_health_endpoint_reports_degradation(broken_client):
    body = broken_client.get("/healthz").json()
    assert body["state_store"] == "unavailable"
    assert body["status"] in {"degraded", "ok"}
