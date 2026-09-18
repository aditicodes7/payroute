"""The decision record and its log.

This package exists on its own because the record is a contract shared by three
rungs — R1/R2 write it, R3 consumes it, R6 persists it — and giving it one
owner makes it hard to write a decision path that omits a field.

Named `decisionlog` rather than `logging` so it does not shadow the stdlib.
"""

from payroute.decisionlog.log import DecisionLog
from payroute.decisionlog.metrics import PairedDifference, Summary, paired_difference, summarize
from payroute.decisionlog.record import Decision, DecisionRecord

__all__ = [
    "Decision",
    "DecisionRecord",
    "DecisionLog",
    "PairedDifference",
    "Summary",
    "paired_difference",
    "summarize",
]
