"""R6: the decision endpoint, and the contract it cannot break.

The project's invariant reaches its sharpest form here: a decision that is
returned to a caller but not logged is unrecoverable, and it is invisible —
the payment succeeds, nobody notices, and R3 finds a hole months later. So the
endpoint must not be able to answer without having logged the propensity first.
"""

from __future__ import annotations

import pytest

from tests.r6.conftest import REQUEST


def test_a_decision_is_returned(client):
    response = client.post("/decide", json=REQUEST)
    assert response.status_code == 200
    body = response.json()
    assert body["gateway"] in {"atlas", "beacon", "cirrus"}
    assert 0.0 < body["propensity"] <= 1.0
    assert body["decision_id"]


def test_the_response_carries_the_full_action_distribution(client):
    body = client.post("/decide", json=REQUEST).json()
    probs = body["action_probs"]
    assert sum(probs.values()) == pytest.approx(1.0)
    assert probs[body["gateway"]] == pytest.approx(body["propensity"])


def test_the_decision_is_logged_before_it_is_returned(client, sink):
    body = client.post("/decide", json=REQUEST).json()
    assert len(sink.records) == 1

    record = sink.records[0]
    assert record.txn_id == body["decision_id"]
    assert record.action == body["gateway"]
    assert record.propensity == pytest.approx(body["propensity"])
    assert record.policy_id and record.policy_version


def test_the_logged_context_is_what_the_policy_saw(client, sink):
    client.post("/decide", json=REQUEST)
    context = sink.records[0].context

    assert context["amount_paise"] == REQUEST["amount_paise"]
    assert context["method"] == REQUEST["method"]
    assert context["issuer"] == REQUEST["issuer"]
    assert any(k.startswith("health_") for k in context)


def test_no_decision_is_returned_if_it_cannot_be_logged(client, sink):
    """The uncomfortable coupling, made explicit.

    Returning a routing decision we failed to log would create exactly the
    unlogged decision this project forbids — and silently, since the caller
    sees a normal success. So the endpoint fails instead, and the tradeoff
    ("we stop routing when we cannot log") is a visible 503 rather than a quiet
    hole in the dataset.
    """
    sink.fail = True
    response = client.post("/decide", json=REQUEST)

    assert response.status_code == 503
    assert "log" in response.json()["detail"].lower()
    assert sink.records == []


def test_a_transient_sink_outage_is_buffered_not_dropped(world, policy, store, priors):
    """Failing on the first hiccup would be its own kind of wrong. The buffer
    is what makes "we stop routing when we cannot log" a tolerance rather than
    a hair trigger."""
    from fastapi.testclient import TestClient

    from payroute.service.app import create_app
    from payroute.service.sink import BufferedSink, InMemorySink

    downstream = InMemorySink()
    buffered = BufferedSink(downstream, capacity=50)
    client = TestClient(
        create_app(policy=policy, store=store, sink=buffered, priors=priors,
                   gateways=world.gateway_names)
    )

    downstream.fail = True
    for _ in range(40):
        assert client.post("/decide", json=REQUEST).status_code == 200
    assert buffered.pending == 40
    assert downstream.records == []

    downstream.fail = False
    buffered.flush()
    assert buffered.pending == 0
    assert len(downstream.records) == 40


def test_the_buffer_fails_loudly_once_it_is_full(world, policy, store, priors):
    from fastapi.testclient import TestClient

    from payroute.service.app import create_app
    from payroute.service.sink import BufferedSink, InMemorySink

    downstream = InMemorySink()
    downstream.fail = True
    buffered = BufferedSink(downstream, capacity=5)
    client = TestClient(
        create_app(policy=policy, store=store, sink=buffered, priors=priors,
                   gateways=world.gateway_names)
    )

    codes = [client.post("/decide", json=REQUEST).status_code for _ in range(10)]
    assert codes[:5] == [200] * 5
    assert codes[5:] == [503] * 5, "the buffer silently discarded decisions once full"


def test_an_invalid_request_is_rejected_without_logging_anything(client, sink):
    response = client.post("/decide", json={"amount_paise": -5, "method": "upi",
                                            "issuer": "hdfc", "merchant_category": "ecommerce"})
    assert response.status_code == 422
    assert sink.records == []
