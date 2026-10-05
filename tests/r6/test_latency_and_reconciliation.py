"""R6: the latency budget, and detecting the two stores drifting apart.

ON THE LATENCY NUMBERS. These run against in-process fakes, because this
environment has no container runtime. A p99 measured against a dict is a
statement about our own serialisation, featurisation and scoring — which is
worth measuring and worth regressing on — but it is NOT a measurement of a
deployment, and the README says so. The container-based version is written and
skipped in test_integration.py.
"""

from __future__ import annotations

import time

import pytest

from tests.r6.conftest import REQUEST

BUDGET_MS = 30.0


def _percentile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def test_the_decision_path_stays_inside_the_budget(client):
    client.post("/decide", json=REQUEST)  # warm the import and JIT paths

    timings = []
    for _ in range(400):
        start = time.perf_counter()
        response = client.post("/decide", json=REQUEST)
        timings.append((time.perf_counter() - start) * 1000.0)
        assert response.status_code == 200

    p50, p95, p99 = (_percentile(timings, q) for q in (0.50, 0.95, 0.99))
    assert p99 < BUDGET_MS, f"p50 {p50:.1f}ms p95 {p95:.1f}ms p99 {p99:.1f}ms against {BUDGET_MS}ms"


def test_the_decision_path_reads_the_state_store_exactly_once(client, store):
    """An N+1 read is the classic way a 2ms budget becomes a 20ms one, and it
    is invisible until load. One pipelined read for all gateways, asserted."""
    before = store.reads
    client.post("/decide", json=REQUEST)
    assert store.reads - before == 1


def test_reconciliation_reports_no_drift_when_the_stores_agree(world, store):
    from payroute.service.reconcile import compare_health

    recomputed = {g: 0.94 for g in world.gateway_names}
    report = compare_health(store, recomputed)

    assert report.max_drift == pytest.approx(0.0, abs=1e-9)
    assert report.drifted == []


def test_reconciliation_names_the_gateway_that_drifted(world, store):
    """Two stores derived from the same ordered stream can lag; they should not
    disagree. This is the detector that turns the difference between those two
    into a number instead of a surprise."""
    from payroute.service.reconcile import compare_health

    recomputed = {g: 0.94 for g in world.gateway_names}
    recomputed["beacon"] = 0.61

    report = compare_health(store, recomputed, threshold=0.05)
    assert report.max_drift == pytest.approx(0.33, abs=1e-6)
    assert [d.gateway for d in report.drifted] == ["beacon"]
    assert "beacon" in str(report)


def test_small_drift_is_reported_but_not_flagged(world, store):
    from payroute.service.reconcile import compare_health

    recomputed = {g: 0.94 for g in world.gateway_names}
    recomputed["atlas"] = 0.9415

    report = compare_health(store, recomputed, threshold=0.05)
    assert report.max_drift > 0
    assert report.drifted == []


def test_the_schema_will_not_accept_a_decision_without_a_propensity():
    """The invariant enforced one layer below Python, where no future code path
    can skip it."""
    from pathlib import Path

    schema = Path("payroute/service/schema.sql").read_text().lower()
    assert "propensity" in schema
    propensity_line = next(line for line in schema.splitlines() if "propensity" in line)
    assert "not null" in propensity_line
    assert "action_probs" in schema and "context" in schema
