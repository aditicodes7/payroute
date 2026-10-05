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


def default_reward(txn: Transaction, outcome) -> float:
    """R2's reward is success, not net revenue: UCB's confidence bounds assume a
    bounded reward, and revenue is heavy-tailed. R5 computes expected revenue
    analytically from the learned probability instead."""
    return float(outcome.success)


def run_policy(sim: Simulator, policy, n: int, reward_fn=default_reward) -> DecisionLog:
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
        # A policy may build a richer context than the static one — R4's health
        # features. Whatever it used to decide is what gets snapshotted into
        # the log, so the record always reflects what the policy actually saw.
        context = policy.context_for(txn) if hasattr(policy, "context_for") else context_of(txn)
        log.append(
            DecisionRecord(
                txn_id=txn.txn_id,
                ts=txn.ts,
                context=context,
                action=decision.action,
                propensity=decision.propensity,
                action_probs=decision.action_probs,
                policy_id=policy.policy_id,
                policy_version=policy.policy_version,
                outcome=outcome,
                constraint_status=decision.constraint_status,
                predicted_p95_ms=decision.predicted_p95_ms,
            )
        )
        # Policies that learn get their feedback here. Baselines have no
        # `update`, which is what makes them baselines.
        if hasattr(policy, "update"):
            policy.update(context, decision.action, reward_fn(txn, outcome))
        # Outcome feedback for policies that keep state outside the model —
        # deliberately after the context snapshot above, so a decision can
        # never be scored against its own result.
        if hasattr(policy, "observe"):
            policy.observe(txn, decision.action, outcome)
    return log
