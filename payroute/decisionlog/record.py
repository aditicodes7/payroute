"""The decision record.

This schema is the contract between the rung that writes decisions (R1, R2),
the rung that evaluates them (R3), and the service that persists them (R6).

Its one non-negotiable field is `propensity`: the probability the acting policy
assigned to the action it actually took, at the moment it took it. It is a
required constructor argument rather than an optional field with a default,
because a decision logged without one is not recoverable afterwards — the
policy parameters, the health state and the feature values that produced it are
all gone by the time anyone notices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from payroute.sim import Outcome

_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Decision:
    """What a policy returns: the action, the full distribution it drew from,
    and the probability of the action it actually picked."""

    action: str
    action_probs: Mapping[str, float]
    propensity: float
    # Set by policies that select under a constraint (R5). "satisfied" or
    # "infeasible" — never absent when a constraint was in force, so that a
    # violation cannot be silently relaxed.
    constraint_status: str | None = None
    predicted_p95_ms: float | None = None


@dataclass(frozen=True)
class DecisionRecord:
    txn_id: str
    ts: datetime
    context: Mapping[str, Any]
    action: str
    propensity: float
    action_probs: Mapping[str, float]
    policy_id: str
    policy_version: str
    outcome: Outcome | None = None
    constraint_status: str | None = None
    predicted_p95_ms: float | None = None

    def __post_init__(self) -> None:
        # Range first: zero is as invalid as negative. An action taken with
        # probability zero is a contradiction, and it becomes a division by
        # zero in every importance weight downstream.
        if not 0.0 < self.propensity <= 1.0:
            raise ValueError(
                f"propensity must be in (0, 1], got {self.propensity!r} — "
                "a zero or negative propensity makes every importance weight undefined"
            )

        probs = dict(self.action_probs)
        total = sum(probs.values())
        if abs(total - 1.0) > _TOLERANCE:
            raise ValueError(f"action_probs must sum to 1.0, got {total!r}")
        if self.action not in probs:
            raise ValueError(f"chosen action {self.action!r} is not in action_probs {sorted(probs)}")
        # Two fields that must agree, so they are checked rather than trusted:
        # they drift apart exactly when someone adds a policy and updates one.
        if abs(probs[self.action] - self.propensity) > _TOLERANCE:
            raise ValueError(
                f"propensity {self.propensity!r} does not match "
                f"action_probs[{self.action!r}] = {probs[self.action]!r}"
            )
        if self.outcome is not None and self.outcome.gateway != self.action:
            raise ValueError(
                f"outcome is for gateway {self.outcome.gateway!r} but the logged action "
                f"was {self.action!r} — an outcome joined to the wrong decision is worse "
                "than a missing one, because nothing downstream can see it"
            )

        # Snapshot, never reference. At R4 the features become time-decayed
        # rolling aggregates, and a reference would resolve to different numbers
        # when R3 reads the log than the policy saw when it decided.
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))
        object.__setattr__(self, "action_probs", MappingProxyType(probs))

    @property
    def is_complete(self) -> bool:
        return self.outcome is not None

    def with_outcome(self, outcome: Outcome) -> "DecisionRecord":
        """Return a NEW record carrying the outcome. Decisions and outcomes
        arrive at different times — immediately here, seconds apart over Kafka
        at R6 — so the schema models them as two events rather than one."""
        return replace(self, outcome=outcome)

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "txn_id": self.txn_id,
            "ts": self.ts.isoformat(),
            "context": dict(self.context),
            "action": self.action,
            "propensity": self.propensity,
            "action_probs": dict(self.action_probs),
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "constraint_status": self.constraint_status,
            "predicted_p95_ms": self.predicted_p95_ms,
            "outcome": None
            if self.outcome is None
            else {
                "gateway": self.outcome.gateway,
                "success": self.outcome.success,
                "latency_ms": self.outcome.latency_ms,
                "fee_paise": self.outcome.fee_paise,
                "net_revenue_paise": self.outcome.net_revenue_paise,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, payload: str | dict) -> "DecisionRecord":
        data = json.loads(payload) if isinstance(payload, str) else payload
        outcome = data.get("outcome")
        return cls(
            txn_id=data["txn_id"],
            ts=datetime.fromisoformat(data["ts"]),
            context=data["context"],
            action=data["action"],
            propensity=data["propensity"],
            action_probs=data["action_probs"],
            policy_id=data["policy_id"],
            policy_version=data["policy_version"],
            constraint_status=data.get("constraint_status"),
            predicted_p95_ms=data.get("predicted_p95_ms"),
            outcome=None if outcome is None else Outcome(**outcome),
        )
