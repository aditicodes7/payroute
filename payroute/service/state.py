"""Terminal health state, read on every decision.

This is a CACHE of derived state, not a source of truth. It and the Postgres
decision log are two projections of the same ordered outcome stream, each with
its own offset: they can be behind one another, they should not disagree, and
`payroute/service/reconcile.py` is what turns the difference between those two
statements into a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol

# What the `health_source` field on a decision records. The in-memory store
# reports the same value as the Redis one on purpose: it stands in for Redis in
# the tests, and a fake that reported something different would exercise a code
# path production never takes.
LIVE = "redis"
FALLBACK = "fallback"


class StoreUnavailable(RuntimeError):
    """The state store could not be read. Callers fall back to priors and
    record that they did."""


@dataclass(frozen=True)
class HealthSnapshot:
    values: dict[str, dict[str, float]]
    source: str


class HealthStore(Protocol):
    def read(self, gateways: Iterable[str]) -> HealthSnapshot: ...
    def write(self, gateway: str, health: float, p95_ms: float) -> None: ...


class InMemoryHealthStore:
    """The fake used by the fast test suite, and by `--no-infra` local runs."""

    def __init__(self) -> None:
        self._values: dict[str, dict[str, float]] = {}
        self.reads = 0
        self.fail = False

    def write(self, gateway: str, health: float, p95_ms: float) -> None:
        self._values[gateway] = {"health": float(health), "p95_ms": float(p95_ms)}

    def read(self, gateways: Iterable[str]) -> HealthSnapshot:
        if self.fail:
            raise StoreUnavailable("in-memory store set to fail")
        # One read for all gateways. An N+1 here is how a 1ms budget line item
        # becomes a 10ms one, invisibly, until load.
        self.reads += 1
        return HealthSnapshot({g: dict(self._values[g]) for g in gateways}, LIVE)


class UnavailableHealthStore:
    """Always down. Used to assert the service keeps routing without it."""

    reads = 0

    def write(self, gateway: str, health: float, p95_ms: float) -> None:
        raise StoreUnavailable("store is unavailable")

    def read(self, gateways: Iterable[str]) -> HealthSnapshot:
        raise StoreUnavailable("store is unavailable")


class RedisHealthStore:
    """Redis adapter.

    NOT EXERCISED BY ANY PASSING TEST. This environment has no container
    runtime, so it is written against the redis-py API and verified only by
    reading. See tests/r6/test_integration.py and docs/decisions.md.
    """

    def __init__(self, client, prefix: str = "payroute"):
        self._client = client
        self._prefix = prefix

    @classmethod
    def from_url(cls, url: str, prefix: str = "payroute") -> "RedisHealthStore":
        import redis  # imported lazily so the package works without the dependency

        return cls(redis.Redis.from_url(url, decode_responses=True), prefix=prefix)

    def _key(self, gateway: str) -> str:
        return f"{self._prefix}:health:{gateway}"

    def write(self, gateway: str, health: float, p95_ms: float) -> None:
        try:
            self._client.hset(self._key(gateway), mapping={"health": health, "p95_ms": p95_ms})
        except Exception as exc:  # noqa: BLE001 - any client error is unavailability here
            raise StoreUnavailable(str(exc)) from exc

    def read(self, gateways: Iterable[str]) -> HealthSnapshot:
        names = list(gateways)
        try:
            pipe = self._client.pipeline()
            for gateway in names:
                pipe.hgetall(self._key(gateway))
            rows = pipe.execute()
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(str(exc)) from exc

        values = {}
        for gateway, row in zip(names, rows):
            if not row:
                raise StoreUnavailable(f"no health recorded for {gateway!r}")
            values[gateway] = {"health": float(row["health"]), "p95_ms": float(row["p95_ms"])}
        return HealthSnapshot(values, LIVE)
