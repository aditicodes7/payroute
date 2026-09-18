"""A log of decision records, plus the checks that say whether it is usable."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator

from payroute.decisionlog.record import DecisionRecord


class DecisionLog:
    """Records from one policy over one seeded run.

    The seed is carried on the log rather than left implicit, because two
    things depend on it: the leakage guard (a fitted policy must not be
    evaluated on the run it was fitted from) and paired comparison (two logs
    can only be paired if they saw the same transactions).
    """

    def __init__(self, seed: int, policy_id: str, records: Iterable[DecisionRecord] | None = None):
        self.seed = seed
        self.policy_id = policy_id
        self._records: list[DecisionRecord] = list(records or ())

    def append(self, record: DecisionRecord) -> None:
        self._records.append(record)

    @property
    def records(self) -> list[DecisionRecord]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[DecisionRecord]:
        return iter(self._records)

    def __getitem__(self, index):
        return self._records[index]

    def validate(self) -> list[str]:
        """Return everything wrong with this log, empty if it is sound.

        Propensity validity is enforced at construction, so what is left to
        check here is completeness — decisions whose outcome never arrived.
        """
        problems: list[str] = []
        missing = [r.txn_id for r in self._records if not r.is_complete]
        if missing:
            problems.append(
                f"{len(missing)} decision(s) have no outcome attached, first: {missing[0]}"
            )
        return problems

    # -- persistence -------------------------------------------------------

    def to_jsonl(self, path: str | Path) -> None:
        path = Path(path)
        with path.open("w") as fh:
            fh.write(json.dumps({"__meta__": {"seed": self.seed, "policy_id": self.policy_id}}) + "\n")
            for record in self._records:
                fh.write(record.to_json() + "\n")

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "DecisionLog":
        path = Path(path)
        with path.open() as fh:
            header = json.loads(fh.readline())
            meta = header["__meta__"]
            log = cls(seed=meta["seed"], policy_id=meta["policy_id"])
            for line in fh:
                if line.strip():
                    log.append(DecisionRecord.from_json(json.loads(line)))
        return log
