"""The decision endpoint.

Budget: 30ms. The happy path is nowhere near it — one pipelined state read, a
featurisation, three scorings and a produce. The risk is the tail, which is why
the acceptance measurement is p99 under load rather than a mean.

The ordering inside the handler is the part that matters: read state, decide,
LOG, then answer. Never answer before logging.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from payroute.decisionlog import DecisionRecord
from payroute.service.sink import SinkUnavailable
from payroute.service.state import StoreUnavailable


class DecisionRequest(BaseModel):
    amount_paise: int = Field(gt=0)
    method: str = Field(min_length=1)
    issuer: str = Field(min_length=1)
    merchant_category: str = Field(min_length=1)
    ts: datetime | None = None


def create_app(policy, store, sink, priors, gateways) -> FastAPI:
    app = FastAPI(title="PayRoute decision service")
    names = list(gateways)

    def _snapshot():
        try:
            return store.read(names)
        except StoreUnavailable:
            # Keep routing, but never silently: the source is recorded on the
            # decision so these rows can be segmented out later.
            return priors

    @app.post("/decide")
    def decide(request: DecisionRequest):
        now = request.ts or datetime.now(timezone.utc)
        snapshot = _snapshot()

        context = {
            "method": request.method,
            "issuer": request.issuer,
            "amount_paise": request.amount_paise,
            "merchant_category": request.merchant_category,
            "hour_of_day": now.hour,
        }
        for gateway, values in snapshot.values.items():
            context[f"health_{gateway}"] = values["health"]
            context[f"p95_{gateway}"] = values["p95_ms"]

        decision = policy.decide_from_context(context)
        record = DecisionRecord(
            txn_id=f"dec-{uuid4().hex}",
            ts=now,
            context=context,
            action=decision.action,
            propensity=decision.propensity,
            action_probs=decision.action_probs,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            constraint_status=decision.constraint_status,
            predicted_p95_ms=decision.predicted_p95_ms,
            health_source=snapshot.source,
        )

        try:
            sink.write(record)
        except SinkUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "refusing to return a decision we cannot log: a routing decision without a "
                    f"recorded propensity is unrecoverable and invisible. {exc}"
                ),
            ) from exc

        return {
            "decision_id": record.txn_id,
            "gateway": record.action,
            "propensity": record.propensity,
            "action_probs": dict(record.action_probs),
            "constraint_status": record.constraint_status,
            "predicted_p95_ms": record.predicted_p95_ms,
            "health_source": record.health_source,
        }

    @app.get("/healthz")
    def healthz():
        try:
            store.read(names)
            state = "ok"
        except StoreUnavailable:
            state = "unavailable"
        return {"status": "ok" if state == "ok" else "degraded", "state_store": state}

    return app
