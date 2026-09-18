"""Running a policy over the simulator and logging what it did."""

from __future__ import annotations

from payroute.decisionlog import DecisionLog, DecisionRecord
from payroute.sim import Simulator, Transaction


class LeakageError(RuntimeError):
    """Raised when a fitted policy is evaluated on its own training run."""


def context_of(txn: Transaction) -> dict:
    """The features a policy is allowed to see, snapshotted into the record.

    Note what is absent: anything only knowable after the transaction
    completes. This function is the one place that decides what a policy sees,
    which makes it the one place to audit when R2's results look too good.
    """
    return {
        "method": txn.method,
        "issuer": txn.issuer,
        "amount_paise": txn.amount_paise,
        "merchant_category": txn.merchant_category,
        "hour_of_day": txn.ts.hour,
    }


def run_policy(sim: Simulator, policy, n: int) -> DecisionLog:
    train_seed = getattr(policy, "train_seed", None)
    if train_seed is not None and train_seed == sim.seed:
        raise LeakageError(
            f"{policy.policy_id} was fitted on seed {train_seed} and is being evaluated on the "
            "same seed. It would be scoring itself on the noise it was fitted to — the baseline "
            "looks better than it is, and whatever it is compared against looks worse by exactly "
            "the same amount. Use a different seed for evaluation."
        )

    log = DecisionLog(seed=sim.seed, policy_id=policy.policy_id)
    for txn in sim.transactions(n):
        decision = policy.decide(txn)
        outcome = sim.resolve(txn, decision.action)
        log.append(
            DecisionRecord(
                txn_id=txn.txn_id,
                ts=txn.ts,
                context=context_of(txn),
                action=decision.action,
                propensity=decision.propensity,
                action_probs=decision.action_probs,
                policy_id=policy.policy_id,
                policy_version=policy.policy_version,
                outcome=outcome,
            )
        )
    return log
