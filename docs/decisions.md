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

### One world definition, shared by tests and experiments   2026-09-18 — R0

Decision:     The canonical SimulatorConfig lives in
              `config/default_world.py`. `tests/conftest.py` imports it;
              the experiment scripts import it. Neither defines its own.
Alternatives: A test world in conftest and a separate experiment world in
              config/ — which is what existed for the first hour of R0.
Why:          Two copies drift. Once they have, the test suite is
              asserting properties of a world the experiments never run,
              and the acceptance criteria stop covering the artifact. The
              properties the tests enforce — no dominant gateway, and
              cheapest/fastest/most-reliable being three different
              gateways — are exactly the properties the experiment
              depends on for its result to mean anything, so they have to
              be asserted about the same object.
Rung:         R0

### R0 figures use shared y-axes and a validated palette   2026-09-18 — R0

Decision:     Both panels of each R0 figure share one y-axis (25-100%),
              series colours come from a palette validated for
              colour-vision deficiency (worst all-pairs CVD dE 9.2,
              normal-vision dE 24.0), every line is directly labelled in
              addition to the legend, and each figure ships a CSV of the
              binned data beside it.
Alternatives: Per-panel autoscaling; colour-only series identity.
Why:          Autoscaled panels would rescale the clean run to fill its
              axis and make a 1-point wobble look like the 48-point
              outage below it — the comparison the figure exists to make
              would be destroyed by its own formatting. Direct labels and
              the CSV are required relief: the third series colour sits
              below 3:1 contrast on the light surface, so identity must
              not rest on colour alone.
Rung:         R0

### Uniform-random added as a fifth baseline, to be the logging policy   2026-09-18 — R1

Decision:     Round robin logs a propensity of 1.0 (it is deterministic).
              A separate `uniform_random` policy, which genuinely draws
              its action from a uniform distribution, is added and is the
              policy used to generate exploration logs for R3.
Alternatives: (a) Log 1/K for round robin, on the grounds that it sends
              1/K of traffic to each gateway. (b) Log 1.0 and accept that
              R3 has no usable dataset.
Why:          (a) produces correct-looking OPE arithmetic, but only by
              coincidence: it works because round robin's assignment
              happens to be independent of context. Written down as a
              general rule it is false, and a coincidence that yields the
              right answer is how you get a wrong answer later when the
              policy changes slightly. Rather than record a fiction, we
              built a policy for which 1/K is simply true. Round robin
              stays as the illustrative baseline because it is what
              people actually build; uniform random is the logging
              policy. Two policies, two purposes, nothing invented in the
              log.
Rung:         R1

### Deterministic baselines' logs are near-useless for OPE — stated as a result   2026-09-18 — R1

Decision:     Record this explicitly (and assert it in a test) rather
              than treat it as an implementation detail: a static rule's
              propensity is genuinely 1.0, so every importance weight
              against it is 0 or undefined, so its log cannot evaluate
              any policy that would have chosen differently.
Alternatives: Treating it as an inconvenience to be worked around.
Why:          This is the real situation a payments company is in with
              its production rule table: the data those rules generate
              cannot answer whether a different policy would be better,
              because it contains no evidence about the roads not taken.
              It is the load-bearing argument for why exploration must be
              deliberate rather than incidental, and R3 quantifies how
              fast the degradation sets in.
Rung:         R1

### The full action-probability vector is logged, not just the propensity   2026-09-18 — R1

Decision:     Every decision record carries `action_probs` over all
              gateways as well as the scalar `propensity` for the chosen
              action (with a check that the two agree).
Alternatives: Logging the scalar propensity alone.
Why:          Several estimator formulations and most of the diagnostics
              want the logging policy's probability for actions that were
              NOT taken. At three gateways the storage cost is nil; the
              cost of discovering the need later is regenerating every
              experiment in the project. The agreement check exists
              because two fields that must match will drift apart exactly
              when someone adds a policy and updates one of them.
Rung:         R1

### Context is snapshotted into the record, never referenced   2026-09-18 — R1

Decision:     The decision record stores the actual feature values used
              at decision time, copied and immutable, rather than a
              reference to anything recomputed later.
Alternatives: Storing a transaction id and recomputing features when the
              log is read.
Why:          At R1 the features are static and this looks like pointless
              duplication. At R4 they become time-decayed rolling
              aggregates, and a reference would resolve to different
              numbers when R3 reads the log than the policy saw when it
              decided. That is train/serve skew — the same defect flagged
              for R6 and R7, arriving three rungs early in a disguise.
              Closing it now costs nothing.
Rung:         R1

### Fitted baselines carry their training seed; the runner enforces it   2026-09-18 — R1

Decision:     `HistoricallyBest` and `BestPerSegment` record the seed of
              the run they were fitted on, and `run_policy` raises
              `LeakageError` when asked to evaluate a fitted policy on
              that same seed.
Alternatives: A convention that fitting and evaluation use different
              seeds, enforced by remembering.
Why:          Fitting a table on the transactions it is then scored
              against lets it peek at the noise in its own test set: the
              baseline looks better than it is, and R2 looks worse by
              exactly the same amount — a bias that flatters the
              conclusion we most want to be true. Conventions enforced by
              memory fail silently; this one fails loudly.
Rung:         R1

### best_per_segment included, to separate two claims about R2   2026-09-18 — R1

Decision:     Build a frozen `(method, issuer) -> gateway` lookup table
              fitted from an exploration log, in addition to the simpler
              baselines.
Alternatives: Comparing R2 only against round robin and
              always-historically-best.
Why:          Against those two alone, a LinUCB win conflates two
              different claims: that the policy is contextual, and that
              it adapts over time. best_per_segment already captures the
              contextual structure but is frozen, so beating it isolates
              the adaptivity claim; beating round robin on a clean run
              isolates the contextual one. Two baselines, two separable
              claims, and an answer to "which part of your model is
              earning its keep?"
Rung:         R1

### Fee spread currently dominates net revenue — flagged, not yet changed   2026-09-18 — R1

Decision:     Keep the current economics (take rate 200bps; gateway fees
              atlas 90bps, beacon 110bps, cirrus 70bps + 100 paise flat)
              for now, and record what they imply.
Alternatives: Narrow the fee spread so success rate carries more of the
              objective; widen the success-rate spread instead; make the
              take rate method-dependent.
Why:          The R1 table exposed the consequence. Margin per successful
              transaction is 130bps via cirrus, 110 via atlas and 90 via
              beacon — a 44% swing — while the success-rate spread
              between gateways is a few points. So net revenue is
              currently fee-dominated: `always_cheapest` has the WORST
              success rate of any baseline and the BEST revenue, and
              `best_per_segment` wins on success by 3.6 points while
              losing 67,588 paise per 1,000 transactions.
              This is good for demonstrating R5's thesis — success rate
              is not a proxy for revenue — but it is close to the point
              where "maximise net revenue" collapses into "always pick
              the cheapest gateway", which would leave R5's constrained
              selection with little to do beyond a fee lookup. The
              spreads should be revisited before R5 so that neither
              objective trivially dominates. Recorded here rather than
              silently retuned, because changing it after seeing the
              results would be fitting the world to the conclusion.
Rung:         R1 (to revisit at R5)

### R2 optimises success probability, not net revenue directly   2026-09-18 — R2

Decision:     LinUCB's reward is success (0/1). Net revenue is reported
              as a consequence, not regressed on. At R5 the objective
              becomes `p_hat(x,a) * value(x) - fee(x,a)`, computed
              analytically from the learned probability.
Alternatives: Reward = net revenue in paise, so the bandit optimises the
              headline number directly.
Why:          UCB's confidence bounds assume a bounded reward with
              well-behaved noise. Net revenue is heavy-tailed — the
              amount distribution has a p99/p50 ratio above 8 — so the
              noise term would be dominated by amount variance that says
              nothing about gateway quality, the confidence widths would
              be wrong, and the exploration budget would be spent chasing
              large transactions rather than learning about gateways.
              Success is bounded in [0,1] and learnable; value and fee
              are known exactly from the amount and the fee schedule.
              There is no reason to learn what can be computed.
Rung:         R2 (sets up R5)

### Epsilon-greedy wrapper on LinUCB; Thompson sampling rejected   2026-09-18 — R2

Decision:     LinUCB takes an `epsilon` parameter. With probability
              1-epsilon it plays the UCB argmax, otherwise it picks
              uniformly. Propensity is exactly
              (1-eps) + eps/K for the greedy arm and eps/K otherwise.
              Epsilon is swept across runs to feed R3's determinism axis,
              INCLUDING epsilon = 0.
Alternatives: (a) Softmax over UCB scores — informed exploration and an
              exact propensity, but the scores are on an arbitrary scale
              so the temperature needs per-problem tuning. Still
              available as a second axis at R3 if wanted.
              (b) Thompson sampling — naturally stochastic, often better
              regret.
Why:          Textbook LinUCB is deterministic, so its honest propensity
              is 1.0 and its log can evaluate nothing. Epsilon-greedy
              fixes that with an exactly computable propensity and a
              single knob that maps directly onto R3's x-axis.
              Thompson sampling was rejected specifically because its
              propensity has NO CLOSED FORM — it would have to be
              estimated by repeated sampling, and an approximate
              propensity sitting in a denominator is a bias nothing
              downstream can detect. In a project whose premise is that
              propensities must be exact and logged, that is
              disqualifying.
              Keeping epsilon = 0 in the sweep is deliberate: it is the
              best-performing policy and the one whose log is provably
              unusable, which is the tradeoff R3 exists to price. We are
              paying revenue for the ability to measure.
Rung:         R2

### R2 uses static context only — no gateway health features   2026-09-18 — R2

Decision:     The R2 feature vector is method, issuer, merchant category,
              standardised log amount, cyclic hour-of-day, and a bias
              term (d = 16). No gateway health signal of any kind.
Alternatives: Including a rolling success-rate feature now, since it
              would obviously help.
Why:          Two reasons. First, R4's entire measurement is the delta
              from adding time-decayed health features; if R2 already had
              one, that delta would be a confound rather than a result.
              Second, a health feature computed from a window that
              includes the transaction being decided is exactly the leak
              this rung is expected to produce — it looks innocent, it is
              one off-by-one in a window boundary, and it yields a model
              that appears to predict failures brilliantly because it is
              reading them. Keeping health out of R2 means that when it
              arrives at R4 it gets built once, deliberately, with the
              boundary as the main design question.
Rung:         R2

### The featuriser's only input is the logged context dict   2026-09-18 — R2

Decision:     `Featuriser.transform()` accepts a plain mapping and
              nothing else — never a Transaction, never the Simulator. A
              test asserts, using a key-recording dict, that it reads only
              keys `context_of()` provides.
Alternatives: Passing the Transaction and letting the featuriser pick
              fields off it.
Why:          Handing it the Transaction would put the whole simulator one
              attribute access away, and the oracle two. With a plain
              mapping, leaking requires adding a field to `context_of()`
              — one visible, reviewable line instead of an accident. The
              guard is checked as a fact about which keys the code
              touched, not as a promise about which keys it intends to
              touch.
Rung:         R2

### Sherman-Morrison inverse, with a drift check   2026-09-18 — R2

Decision:     Maintain A^-1 incrementally by rank-one update rather than
              inverting A on every decision, and assert in a test that
              the maintained inverse still equals the true inverse after
              hundreds of updates.
Alternatives: Inverting the 16x16 matrix per arm per decision.
Why:          Scoring becomes a matrix-vector product, which matters for
              R6's 30ms budget and for keeping the test suite runnable.
              It is also a real numerical-drift risk, so the speedup
              ships with the check that makes it safe rather than on
              trust. Cold-start ties are broken randomly for a related
              reason: at t=0 every arm scores identically, and an argmax
              that breaks ties by list order hands all early traffic to
              one gateway and starts the bandit from a skewed sample.
Rung:         R2

### Finding: exploration is insurance, not only a cost   2026-09-19 — R2

Decision:     Record this before R3 and R4 are built, because it
              reverses the framing we adopted at R2 and changes what R4
              has to demonstrate.
Alternatives: Reporting only the clean-world result, where the story is
              tidy.
Why:          On the CLEAN world, epsilon = 0 is strictly better: tail
              regret 0.00050 per transaction against 0.00298 at
              epsilon = 0.05. Exploration is a pure tax, and the tax is
              the price of a loggable propensity.
              Under INCIDENTS the ranking inverts:
                eps 0.00 -> tail regret 0.03680, net revenue 1,574,982
                eps 0.01 -> tail regret 0.01755, net revenue 1,926,913
                eps 0.05 -> tail regret 0.01798, net revenue 1,927,329
                eps 0.30 -> tail regret 0.02080, net revenue 1,904,288
              A deterministic LinUCB converges, its confidence widths
              collapse, it stops sampling the arms it believes are worse,
              and it therefore cannot notice when the arm it settled on
              degrades. Exploration is what keeps the estimates alive in
              a world that moves. So epsilon buys two different things
              at once — a usable propensity AND robustness to
              non-stationarity — and only the first is a pure cost.
Rung:         R2

### Finding: LinUCB has no forgetting, and the regret curve shows it   2026-09-19 — R2

Decision:     Record the mechanism now; R4 is the fix.
Alternatives: Treating the R2 vs best_per_segment gap as tuning noise.
Why:          Under incidents LinUCB (eps=0.05) reaches 92.83% success
              against best_per_segment's 94.18% — it LOSES by 1.35
              points [-1.43, -1.28] to a frozen lookup table, while
              beating it on net revenue by 139,483 per 1k. On the clean
              world the two are within 1 point.
              The regret curve explains it. Both are flat until the
              incident around transaction 75,000. best_per_segment takes
              the hit and returns to flat. LinUCB's regret accelerates
              from 75k to roughly 110k and is still accumulating at
              150k — it never recovers within the run.
              The mechanism is that A_a and b_a are unweighted sums over
              all history. After 75,000 observations a 40-minute outage
              is a vanishing fraction of the evidence, so the estimate
              barely moves during the outage and is then slow to unwind
              afterwards. The policy has no way to say "recent data
              matters more". That is exactly what R4's time-decayed
              features exist to fix, so R4's acceptance number —
              time-to-react — now has a concrete baseline to beat rather
              than an abstract one.
Rung:         R2 (motivates R4)
