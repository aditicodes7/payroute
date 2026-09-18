# PayRoute — Working Agreement

PayRoute is a decision service that picks the payment gateway and retry
strategy per transaction to maximise expected net revenue under a latency
constraint, validated entirely offline via off-policy evaluation before
anything is ever deployed live.

**Everything in this repo runs against a SIMULATOR.** There are no real
gateways, no real payment data, and nothing is ever deployed live. This
must be stated explicitly in the README.

## Who I am working with

Third-year CSE student. Basic-to-intermediate Python. Does NOT yet know
contextual bandits, off-policy evaluation, or FastAPI/Redis/Kafka in
depth. Assume each concept needs explaining before its code gets written,
and assume "why" will be asked often. That is intended, not a delay.

## The load-bearing invariant

Every routing decision logs its **PROPENSITY** — the probability the
acting policy assigned to the action it actually took, recorded at
decision time. Without logged propensities, off-policy evaluation is
impossible after the fact. If a decision path is ever written that does
not log a propensity: **STOP AND FLAG IT.** Never silently skip it.

## Rules — these override any default behaviour

1. **Explain the concept BEFORE writing code, then STOP and wait for an
   explicit "go".** Never explain and implement in the same message.

4. **Write the failing test BEFORE the implementation.**

5. **When something breaks, give 2-3 HYPOTHESES and how to distinguish
   them. Do NOT give the fix until explicitly asked.** This is the most
   important rule — the debugging is the part being learned here, and
   it's what gets asked about in interviews.

7. **Append every real decision to `docs/decisions.md`**: what was
   chosen, what was rejected, why. As we go, not at the end.

8. **Append every bug to `docs/bugs.md`** in the documented format,
   BEFORE fixing it.

9. **Never optimise without a before/after measurement.**

10. **Never claim an off-policy-evaluated policy is "better" without also
    reporting the estimator's confidence interval and effective sample
    size.** A point estimate with no uncertainty is not a result here.

12. **Never deploy or claim to deploy anything against real payment data
    or real gateways.** Everything here is a simulator. Say so in the
    README explicitly.

> Rules 2, 3, 6 and 11 were not specified in the original brief. The
> numbering above is preserved exactly as given so those slots can be
> filled in later without renumbering anything.

## The rung plan

Each rung produces a NUMBER and a way to check it isn't fooling us.

| Rung | Build | Headline measurement |
|------|-------|----------------------|
| R0 | Gateway simulator: method mix, issuer distribution, correlated time-varying degradation | Per-gateway success rate over time, with/without injected outage |
| R1 | Round-robin + static rule baselines | Success rate & net revenue per 1,000 txns — the number everything must beat |
| R2 | LinUCB contextual bandit (propensity logged from here on, no exceptions) | Success rate & net revenue vs R1; cumulative regret |
| R3 | Direct Method, IPS, SNIPS, Doubly Robust estimators vs known ground truth | Estimator error vs ground truth; error vs logging-policy determinism |
| R4 | Rolling time-decayed per-terminal features | Time-to-react to an outage (start and recovery) |
| R5 | Constrained multi-objective selection under a p95 latency budget | Net revenue vs success-rate-only policy; p95 constraint actually holds |
| R6 | FastAPI decision endpoint, Redis health state, Kafka feedback, Postgres decision log | p50/p95/p99 decision latency under load (30ms budget) |
| R7 | Tabular Q-learner for retry decisions | Recovered-transaction rate vs fixed retry-once |

## Final deliverables

- One measurement table across all rungs vs the R1 baselines.
- The OPE estimator comparison chart (error vs ground truth; variance vs
  propensity determinism) — the single most interview-worthy artifact.
- An explicit "when this OPE result should NOT be trusted" section in the
  README, quantified with effective sample size.
- `docs/bugs.md` and `docs/decisions.md`, maintained throughout, never
  written retroactively.
