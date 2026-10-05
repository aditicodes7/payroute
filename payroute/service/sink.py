"""Where a decision goes before the caller is told what it is.

The ordering matters and is the point: the decision is written first, and only
then returned. A routing decision that reaches the caller without its
propensity being logged is the one thing this project forbids, and it fails
invisibly — the payment succeeds, nobody notices, and R3 finds a hole months
later.
"""

from __future__ import annotations

from collections import deque
from typing import Protocol

from payroute.decisionlog import DecisionRecord


class SinkUnavailable(RuntimeError):
    """The decision could not be durably recorded."""


class DecisionSink(Protocol):
    def write(self, record: DecisionRecord) -> None: ...


class InMemorySink:
    def __init__(self) -> None:
        self.records: list[DecisionRecord] = []
        self.fail = False

    def write(self, record: DecisionRecord) -> None:
        if self.fail:
            raise SinkUnavailable("downstream sink unavailable")
        self.records.append(record)


class BufferedSink:
    """A bounded write-ahead buffer in front of the real sink.

    Without it, a one-second broker election becomes an outage. Without the
    BOUND, a long outage becomes silent data loss. The capacity is therefore
    the explicit statement of how long we are willing to keep routing while
    unable to log — and when it is exhausted the service says so with a 503
    rather than choosing quietly.
    """

    def __init__(self, downstream: DecisionSink, capacity: int = 10_000):
        self._downstream = downstream
        self.capacity = int(capacity)
        self._buffer: deque[DecisionRecord] = deque()

    @property
    def pending(self) -> int:
        return len(self._buffer)

    def flush(self) -> None:
        while self._buffer:
            try:
                self._downstream.write(self._buffer[0])
            except SinkUnavailable:
                return
            self._buffer.popleft()

    def write(self, record: DecisionRecord) -> None:
        self.flush()
        try:
            self._downstream.write(record)
            return
        except SinkUnavailable:
            pass
        if len(self._buffer) >= self.capacity:
            raise SinkUnavailable(
                f"write-ahead buffer full at {self.capacity} records and the downstream sink is "
                "still unavailable — refusing to route decisions we cannot log"
            )
        self._buffer.append(record)


class KafkaSink:
    """Kafka adapter.

    NOT EXERCISED BY ANY PASSING TEST — no broker is available in this
    environment. See tests/r6/test_integration.py.
    """

    def __init__(self, producer, topic: str = "payroute.decisions"):
        self._producer = producer
        self._topic = topic

    def write(self, record: DecisionRecord) -> None:
        try:
            self._producer.produce(self._topic, record.to_json().encode("utf-8"))
            self._producer.poll(0)
        except Exception as exc:  # noqa: BLE001
            raise SinkUnavailable(str(exc)) from exc
