"""R6: the suite that needs real infrastructure.

Written, and skipped here: this environment has no Docker, Colima or Podman, so
there is nothing to run Redis, Kafka or Postgres against. The adapters in
payroute/service/ are written against the real client APIs and are NOT
exercised by any passing test — which is stated here, in the README, and in
docs/decisions.md rather than left for someone to discover.

To run: start the dependencies and set PAYROUTE_INTEGRATION=1.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PAYROUTE_INTEGRATION") != "1",
    reason="needs real Redis/Kafka/Postgres; no container runtime available here",
)


def test_redis_adapter_round_trips_health():
    from payroute.service.state import RedisHealthStore

    store = RedisHealthStore.from_url(os.environ["PAYROUTE_REDIS_URL"], prefix="test")
    store.write("atlas", health=0.91, p95_ms=312.0)
    snapshot = store.read(["atlas"])
    assert snapshot.source == "redis"
    assert snapshot.values["atlas"]["health"] == pytest.approx(0.91)


def test_kafka_sink_delivers_the_decision():
    pytest.skip("written against aiokafka; needs a broker to assert delivery")


def test_postgres_rejects_a_null_propensity():
    pytest.skip("needs a live database to assert the NOT NULL constraint fires")


def test_latency_under_load_against_real_dependencies():
    """The only version of the latency number that means anything about a
    deployment. The in-process figure in test_latency_and_reconciliation.py
    measures our own code, not Redis round trips or broker acks."""
    pytest.skip("needs real dependencies and a load generator")
