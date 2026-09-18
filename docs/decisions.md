# Decision Log

Every real design decision gets an entry here as it is made, not
retroactively. "Alternatives" must be non-empty — if nothing else was
considered, it wasn't a decision. Newest entries appended at the bottom.

## Format

```
### <short title>            <date> — <rung>

Decision:     what we chose
Alternatives: what else was considered
Why:          the reasoning
Rung:         which rung this was made at
```

---

### Repo structure                2026-09-18 — R0

Decision:     Package `payroute/` split into sim, features, policies, ope,
              retry, decisionlog, service. Separate top-level `tests/`
              (fast, CI, invariants) and `experiments/` (slow, seeded,
              produce numbers into `results/`). Simulator parameters live
              in `config/` as data, not as constants in code.
Alternatives: (a) Flat module layout — rejected, the R2/R3/R6 boundary
              between "who writes a decision record" and "who consumes
              it" would have no home. (b) `features/` inside `policies/`
              — rejected, R4's whole measurement is swapping the feature
              implementation while holding the policy fixed; coupling
              them makes that comparison impossible to do honestly.
              (c) Tests and experiments in one directory — rejected,
              forces either a slow CI or untested experiment code.
Why:          `decisionlog/` is a package of its own because the decision
              record (context, action, propensity, reward) is the
              contract shared by R2, R3 and R6; giving it one owner makes
              it hard to write a decision path that omits a field. Named
              `decisionlog` rather than `logging` to avoid shadowing the
              stdlib module. Config-as-data because every simulator
              parameter is an assumption that has to be pointed at, not
              grepped for.
Rung:         R0

### Time is wall-clock, not tick counts       2026-09-18 — R0

Decision:     Transactions carry timezone-aware UTC `datetime` timestamps
              with a diurnal arrival rate, rather than an integer tick
              index.
Alternatives: Tick-time (transaction index as the clock) — simpler, no
              arrival process needed.
Why:          R4's decay constant should be expressible in minutes ("a
              5-minute half-life"), not in transactions, because the
              quantity that matters is how long a policy keeps routing
              into a dead gateway. R5's p95 latency budget and R6's 30ms
              service budget are both wall-clock quantities. Tick-time
              pushes an awkward translation into three later rungs to
              save a small amount of work in one.
Rung:         R0

### Two incident types, both built at R0      2026-09-18 — R0

Decision:     GatewayIncident (one gateway's stack degrades, others
              unaffected) AND IssuerIncident (a bank degrades; every
              gateway's success rate for that issuer drops, scaled by a
              per-gateway `issuer_sensitivity`).
Alternatives: (a) Gateway-level only, defer issuer-level to R4.
              (b) Issuer-level only.
Why:          The issuer model is the one that justifies the project's
              premise — bank-side downtime affects all gateways, but
              unequally, so there is still a correct route during the
              outage and it changes over time. That is precisely what a
              static rule table cannot express. Building it later would
              mean rewriting an R0 test suite whose assumptions are all
              single-gateway, not adding to it.
Rung:         R0

### Severity is a shaped profile, not a step  2026-09-18 — R0

Decision:     Incident severity ramps in, fluctuates during the window,
              and ramps out; duration and peak severity are drawn from
              distributions rather than fixed.
Alternatives: A rectangular step from baseline to degraded and back.
Why:          A step function is trivially detectable by any change
              detector, which would hand R4 a free pass and make its
              "react fast vs overreact to noise" tradeoff vacuous. Fixed
              duration/severity would also let a policy overfit to a
              single outage signature.
Rung:         R0

### Background drift is always on             2026-09-18 — R0

Decision:     Each gateway's success probability follows a mean-reverting
              AR(1) wobble around its baseline even when no incident is
              active (sigma 0.015, 45-minute reversion half-life,
              independent across gateways).
Alternatives: A piecewise-constant world where the only variation is
              injected incidents.
Why:          Without background noise there is nothing for R4's decay
              constant to trade off against — every change is a clean
              step and any decay constant "works". Drift is independent
              across gateways on purpose: correlated drift would be a
              second, undeclared incident mechanism, and correlation
              belongs in the issuer model where it is explicit and
              parameterised.
Rung:         R0

### Common random numbers for outcomes        2026-09-18 — R0

Decision:     Separate RNG streams for context generation, the incident
              process, and outcome draws. Each transaction carries one
              pre-drawn uniform `u`; the outcome under gateway g is
              `u < p(g, context, t)`.
Alternatives: A single global RNG; independent outcome draws per gateway.
Why:          With a shared RNG, two policies that choose differently
              consume different numbers of draws, the streams
              desynchronise, and the policies end up running on different
              traffic — a comparison that has silently stopped being a
              comparison, with nothing in the output to say so. The
              shared uniform additionally makes every counterfactual
              well-defined ("what would gateway C have done for this
              transaction?"), which is exactly what R3 needs to have a
              ground truth, and gives paired policy comparisons with far
              lower variance than independent runs.
Known assumption it introduces: sharing `u` across gateways means a
              transaction that "would have failed anywhere" is a coherent
              object. That is defensible for bank-side failures but it IS
              an assumption about the correlation structure of
              counterfactual outcomes, and a skeptical reader should be
              pointed at it.
Rung:         R0

### Money is integer paise; fee charged on success only   2026-09-18 — R0

Decision:     All monetary quantities are integer paise. Revenue on a
              successful transaction is `take_rate_bps * amount / 10000`;
              the gateway fee is charged on success only
              (`fee_on_failure = False`, configurable). Net revenue on a
              failure is 0.
Alternatives: Floats for money (rejected outright); charging the fee
              regardless of outcome, which is what the brief's formula
              `P(success) x value - fee` literally implies.
Why:          Integer paise removes a whole class of rounding bugs from
              R5's objective. Fee-on-success-only matches how gateway
              pricing usually works, and keeps the cost/reliability
              tension R5 needs (a cheaper, slightly worse gateway can
              still win). FLAGGED: this differs from the brief's stated
              formula; it is a config flag so it can be flipped, but the
              default should be agreed rather than assumed.
Rung:         R0

### Test world configured so the problem is non-trivial   2026-09-18 — R0

Decision:     Gateway parameters chosen so that (a) no single gateway is
              best in every (method, issuer) segment, and (b) the
              cheapest, the fastest and the most reliable gateway are
              three different gateways. Both properties are asserted by
              tests, not just intended.
Alternatives: Plausible-looking numbers chosen by eye.
Why:          If one gateway dominated every segment, R2 could not beat
              R1 however good LinUCB is, and the result would be a
              statement about the config rather than the algorithm. If
              the cheapest gateway were also the most reliable, R5's
              constrained objective would collapse into the
              success-rate-only objective and the rung would have nothing
              to demonstrate. These are properties of the experiment's
              validity, so they are asserted as tests.
Rung:         R0

### Statistical bands account for autocorrelated variance   2026-09-18 — R0

Decision:     Where a measured rate carries background drift as well as
              sampling noise, the acceptance band is built from
              `binomial_var + drift_var`, with
              `drift_var = sigma^2 * 2*tau/T` for an AR(1) of stationary
              variance sigma^2 and correlation time tau over span T.
              Bands are never widened by raising z until a test stops
              failing.
Alternatives: (a) A binomial band with an inflated z (the original test
              used z=12). (b) Dropping the assertion and eyeballing a
              chart.
Why:          A binomial band is the wrong DISTRIBUTION here, not merely a
              too-narrow one, so no choice of z makes it correct — it just
              makes it insensitive. The principled band came out TIGHTER
              in absolute terms (+/-0.018 vs +/-0.029) while cutting the
              worst observed deviation from 9.6 sigma to 1.4 sigma across
              eight seeds: less flaky AND more sensitive. This recurs at
              R4, where "has the policy reacted to the outage?" is exactly
              a question about distinguishing a real shift from
              autocorrelated noise, so the habit is worth fixing here.
Rung:         R0
