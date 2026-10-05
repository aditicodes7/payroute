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

### Stateful policies cannot be off-policy evaluated   2026-09-19 — R3

Decision:     Target policies must expose `action_probs(context)` — a
              distribution over actions that is a function of CONTEXT
              alone. round_robin therefore cannot be a target policy and
              is excluded. The target set is uniform_random,
              always_cheapest, historically_best, best_per_segment and
              LinUCB-greedy.
Alternatives: Treat round_robin as uniform 1/K for evaluation purposes.
Why:          Round robin's action depends on an internal counter, not on
              the transaction. Calling it "uniform" would be the same
              fiction we refused to log at R1, reappearing on the
              evaluation side: correct arithmetic for the wrong reason,
              true only because round robin's assignment happens to be
              independent of context.
              This is a real constraint rather than a limitation of our
              implementation — off-policy evaluation reweights by
              pi_e(a|x), and a policy whose action is not a function of x
              has no such quantity. Worth knowing before someone tries to
              evaluate a production router with session state in it.
Rung:         R3

### Two-fold cross-fitting for the reward model   2026-09-19 — R3

Decision:     The reward model used by the Direct Method and by Doubly
              Robust is cross-fitted: fit on fold A, score fold B, swap,
              average. A record is never scored by a model that saw it.
Alternatives: A single train/evaluate split (wastes half the data), or
              fitting on everything (leaks).
Why:          A reward model fitted on the rows it then scores is
              optimistically accurate, so DM looks better than it is and
              DR inherits the flattery. DR is the estimator we expect to
              recommend, so that bias points straight at the conclusion
              we most want to be true — exactly the direction of error a
              reader should be most suspicious of. Cross-fitting costs
              nothing extra and is what makes DR's guarantee hold.
Rung:         R3

### Bootstrap intervals primary; the normal interval shown failing   2026-09-19 — R3

Decision:     Every OPE result carries a bootstrap percentile interval as
              its `ci`, AND the normal-approximation interval as
              `normal_ci`, reported alongside.
Alternatives: Bootstrap only; or the normal approximation only, which is
              what most quick implementations use.
Why:          The normal approximation assumes the sample standard
              deviation describes the spread. On a heavy-tailed
              importance-weight distribution it does not, and the
              interval comes out narrow, confident and wrong — worst
              precisely in the low-exploration regime where the estimate
              deserves the most scepticism. Keeping both makes that
              failure visible rather than avoided: a reader can see that
              the naive error bar does not merely widen as the log
              degrades, it stays deceptively tight.
Rung:         R3

### Refuse rather than estimate when overlap fails   2026-09-19 — R3

Decision:     An estimator raises `NoOverlapError` when the target policy
              puts probability on actions the logging policy never took
              in that context. Results that are computable but thin carry
              trust = "untrustworthy" with warnings that quote the
              effective sample size.
Alternatives: Return the number and let the caller check the diagnostics.
Why:          The stated expected failure for this rung is IPS blowing up
              silently — a huge-variance number reported as a result
              because nobody checked. At zero exploration the problem is
              not high variance that more data would fix: the estimand is
              NOT IDENTIFIED, because the log contains no information
              about the actions the target would have taken differently.
              Returning a number there would be inventing one. Rule 10 is
              enforced by the return type: there is no way to obtain a
              point estimate without its interval, its ESS and its
              verdict.
Rung:         R3

### Clipped IPS included as a fifth estimator   2026-09-19 — R3

Decision:     Implement weight clipping at a configurable threshold as a
              separate estimator alongside DM, IPS, SNIPS and DR.
Alternatives: Leaving it out — the rung's stated scope is four
              estimators.
Why:          It is what gets deployed in practice, and it turns the
              bias/variance tradeoff from a sentence into a knob with a
              number attached: on the toy log, clipping at 1.5 moves the
              estimate from 1.00 to 0.75, and that movement IS the bias
              being purchased. Having it in the comparison also means the
              final chart can show a practitioner's fix sitting between
              the naive and the principled ones.
Rung:         R3

### Limitation: our simulator flatters the Direct Method   2026-09-19 — R3

Decision:     State this in the README rather than letting the R3 chart
              imply that DM is safe.
Alternatives: Reporting the estimator table as-is; or manufacturing a
              failure until DM looked bad.
Why:          In our world, success probability is
              base[method] + adjust[issuer] — EXACTLY linear in the
              features the reward model uses. DM's model is therefore
              perfectly specified and it has no bias to exhibit: its
              error is under 0.0011 at every exploration level, making it
              the most accurate estimator in the table.
              We tried to remove that advantage with a deliberately
              misspecified variant that cannot see the issuer. It barely
              moved (error +0.0007 at eps=0.01), and the reason is
              instructive: the reward model is fitted on a log whose
              action distribution is already correlated with the target
              policy's, so the model's per-gateway averages are taken
              over exactly the contexts where the target sends traffic,
              and the errors cancel.
              So we did NOT reproduce DM's known failure mode, and we
              decline to manufacture one. What the table does show is
              the danger in its purest form: at eps = 0, where every
              propensity-based estimator refuses, DM returns 0.9471 with
              an interval of width 0.0004 from a log containing zero
              information about 16,356 of the 60,000 decisions. It is
              accurate there by luck of a correct model, and nothing in
              its output would look any different if the model were
              wrong.
Rung:         R3

### Decay lives in the features, not in the bandit's statistics   2026-10-03 — R4

Decision:     Add explicit time-decayed per-gateway health features to the
              context and leave LinUCB's A and b as unweighted sums.
              Discounted LinUCB (A <- gamma*A + xx^T) is implemented as a
              CONTROL to measure against, not as the primary design.
Alternatives: (a) Discount the sufficient statistics only. (b) Both.
Why:          This world has two timescales. The (method, issuer,
              gateway) quality matrix is stationary — a bank's acquiring
              relationship with a gateway does not change hour to hour.
              Gateway health is non-stationary and moves in minutes.
              Discounting the sufficient statistics decays both at the
              same rate, so adaptivity is bought by permanently degrading
              the estimate of the thing that never changes: you forget
              the issuer structure in order to notice an outage.
              Separating them lets each be learned at its own timescale.
              It also matches R6: terminal health lives in Redis and is
              read per request, so the signal is data the service looks
              up rather than a property of the model. R4 and R6 then
              agree instead of fighting.
Rung:         R4

### Decay is in wall-clock time, never in transaction counts   2026-10-03 — R4

Decision:     The health store decays by elapsed time with a half-life in
              minutes.
Alternatives: A rolling window of the last N transactions, which is
              simpler and is what most implementations reach for.
Why:          Arrivals are diurnal by a factor of seven: 1,000
              transactions span 33 minutes at the 03:00 trough and 5
              minutes at the 15:00 peak. A count-based window would react
              at a different wall-clock speed depending on the hour, so
              "time to react" would not be a single quantity and the
              rung's headline number would be uninterpretable.
Rung:         R4

### The prior does not decay                      2026-10-03 — R4

Decision:     health = (S + prior_successes) / (N + prior_trials), with
              the prior added at read time rather than initialised into
              the decaying counters.
Alternatives: Seeding S and N with the prior and letting it decay with
              everything else.
Why:          An exponentially weighted count shrinks toward zero during
              quiet periods. If the prior decays too, the store ends up
              with no evidence at all, and the next single observation
              dominates completely — one failure swings health from 0.95
              to near zero and the policy abandons a working gateway.
              That is exactly the overreaction failure predicted for this
              rung. A non-decaying prior is a permanent floor on the
              amount of evidence required to move the number; the
              half-life governs how fast real evidence wins.
Rung:         R4

### Feedback delay is modelled now, defaulting to zero   2026-10-03 — R4

Decision:     `TerminalHealth` takes a `feedback_delay`, and
              `health_at(t)` folds in only outcomes whose observation
              time is at or before t. R4 runs with delay zero.
Alternatives: Assume instantaneous feedback and add the delay at R6.
Why:          In the simulator an outcome is available the instant the
              transaction resolves. In the service it arrives over Kafka,
              seconds later. A policy tuned on zero-latency feedback
              behaves differently when the feedback is late, and nothing
              in the output would say so — the same train/serve skew
              flagged for R6 and R7, arriving early. The mechanism costs
              one timestamp field now; retrofitting it would mean
              re-running every R4 number.
Rung:         R4

### Time to react is defined with a persistence requirement   2026-10-03 — R4

Decision:     Onset = minutes from incident start until the share of
              traffic to the degraded gateway falls below half its
              pre-incident share AND stays below for a sustained window.
              Recovery = minutes from incident end until the share
              returns to within a band of its pre-incident level.
              Measured per incident across many incidents and reported as
              a distribution.
Alternatives: First bin below the threshold; or eyeballing the chart.
Why:          Without persistence, one noisy bin claims a reaction that
              did not happen — and with background drift switched on,
              noisy bins are guaranteed. Without a distribution across
              incidents, a single favourable outage becomes the headline
              number. A policy that cannot react at all must report None
              rather than a number produced by noise, which is asserted
              against round robin.
Rung:         R4

### The tradeoff is measured on both axes   2026-10-03 — R4

Decision:     The half-life sweep reports median time-to-react AND a
              routing-instability measure taken on CLEAN periods, where
              every diversion away from a healthy gateway is a false
              alarm paid for in revenue.
Alternatives: Reporting time-to-react alone and picking the fastest.
Why:          With only the first axis, "shorter half-life is better"
              looks true and the choice of half-life becomes a tuned
              constant nobody can defend. The second axis is what makes
              it a decision. R0's background drift exists precisely so
              this cost is real rather than theoretical — in a world
              without noise, any half-life would look good.
Rung:         R4

### Finding: onset does not discriminate; recovery does   2026-10-04 — R4

Decision:     Report recovery time as R4's headline number, and report
              that onset — the quantity the rung was specified around —
              turned out not to separate the policies at all.
Alternatives: Leading with onset because that is what "time to react"
              normally means, and burying the recovery column.
Why:          Across 12 measured incidents, median ONSET is
              1.9 / 0.7 / 2.0 / 0.6 / 1.9 minutes for half-lives of
              0.5 / 2 / 5 / 15 / 60, with static LinUCB at 1.4 and
              discounted at 0.7. That is noise: there is no ordering, and
              the unweighted static policy is faster than three of the
              five adaptive settings. Every policy notices a severe
              outage within about two minutes, so onset has nothing to
              distinguish.
              Median RECOVERY is where they separate, and it separates
              hard:
                hl=2m       8.2 min
                hl=5m      14.3 min
                hl=0.5m    20.7 min
                hl=15m     38.1 min
                hl=60m     90.5 min
                discounted 115.9 min
                static    148.2 min
              Static LinUCB takes 18x longer than the best adaptive
              setting to put traffic back. That is the R2 observation —
              unweighted sums cannot let go of a finished outage —
              measured directly.
              Noticing a problem is easy; forgetting it is the hard part,
              and the rung was specified around the easy half.
Rung:         R4

### Finding: the half-life tradeoff does not appear here   2026-10-04 — R4

Decision:     Report that the expected cost of a short memory is absent
              at every half-life tested, rather than tuning until it
              appears.
Alternatives: Weakening the prior, lowering traffic volume or raising
              drift until the curve bends the way the design predicted.
Why:          Routing jitter on a clean run is
              0.0703 / 0.0839 / 0.0717 / 0.0705 / 0.0734 across
              half-lives from 0.5 to 60 minutes — no trend, and the
              shortest memory is not the jumpiest. With roughly a
              thousand transactions per bin and a prior worth ten
              observations, health barely moves whatever the half-life.
              The mechanism does exist: a quarter-minute half-life is
              measurably jumpier than sixty minutes, which is asserted in
              tests/r4. It is simply too small to matter at any setting
              anyone would choose for this traffic volume.
              So R4's result is one-sided — real benefit, no measurable
              cost — which is a weaker and more defensible claim than
              naming an optimal half-life. Changing the world until the
              tradeoff showed up would have been fitting the experiment
              to the design.
              It also means the honest recommendation is a half-life in
              the 2-5 minute range chosen for recovery time alone, with
              the note that anything from 0.5 to 15 minutes performs
              comparably.
Rung:         R4

### Finding: features beat discounting, as the two-timescale argument predicted   2026-10-04 — R4

Decision:     Keep decay in the features. The control confirms the
              reasoning that chose it.
Alternatives: Switching to discounted LinUCB, which is one line and
              needs no health store.
Why:          Under a dense incident schedule, success rate runs
              adaptive 93.5-94.2% > discounted 93.3% > static 92.1%, and
              discounted LinUCB has the HIGHEST routing jitter of
              anything measured (0.0945 against 0.0663-0.0839). Both are
              what the two-timescale argument predicts: discounting the
              sufficient statistics forgets the stationary issuer
              structure at the same rate as the health signal, so it pays
              for adaptivity with a permanently noisier estimate of the
              part that never changes.
              The argument was made before the measurement, and the
              measurement agrees with it. That is worth more than either
              on its own.
Rung:         R4

### The fee sits inside the probability term   2026-10-04 — R5

Decision:     Expected net revenue is
              `p(x,g) * (value(x) - fee(x,g))`, not
              `p(x,g) * value(x) - fee(x,g)` as the original brief wrote
              it.
Alternatives: Following the brief's formula literally.
Why:          R0 decided the gateway fee is charged on success only, so on
              a failure we earn nothing and pay nothing. The two
              formulations differ by `(1 - p) * fee`, which at p = 0.9 and
              a 1,000-paise fee is 100 paise on every transaction —
              roughly 5% of the margin, and biased consistently toward
              reliable gateways. Flagged at R1 when the fee convention was
              set; corrected here where the objective is actually used,
              and asserted by a test that compares against the wrong
              formula explicitly.
Rung:         R5

### Feasible-set filter on per-gateway p95   2026-10-04 — R5

Decision:     Keep only gateways whose recent p95 latency is within
              budget, then maximise expected net revenue over that set.
Alternatives: (a) A per-transaction hard latency bound — wrong, since
              latency is stochastic and any gateway can exceed on any
              transaction. (b) A portfolio constraint allowing individual
              transactions to exceed so long as the fleet p95 holds.
Why:          The filter gives a guarantee rather than a hope: if every
              gateway in the feasible set has P(latency > B) <= 0.05,
              then so does any mixture of them, so the FLEET p95 is within
              budget by construction and can be proved in a line instead
              of checked after the fact.
              The portfolio version is more efficient — it could send 5%
              of traffic to a slow but excellent gateway — but it needs
              global budget accounting across requests, which is awkward
              inside R6's 30ms budget. The filter is conservative and
              leaves revenue on the table; that is a stated limitation,
              not a hidden one.
Rung:         R5

### Infeasible decisions are recorded, never silently relaxed   2026-10-04 — R5

Decision:     When no gateway meets the budget the policy routes to the
              FASTEST one and records `constraint_status = "infeasible"`
              with `predicted_p95_ms`. Two explicit fields on the
              decision record; no general metadata bag.
Alternatives: (a) Fall back to unconstrained argmax — the predicted bug
              for this rung. (b) Raise and drop the transaction. (c) A
              `meta: dict` for this and anything else later.
Why:          A payment router cannot refuse to route, so "fail loudly"
              has to mean "the log says it failed" rather than "the
              request dies". With the status on every record, the
              violation rate becomes a number reported next to the p95:
              the claim is "the constraint held on 99.x% of decisions",
              never an unqualified "the constraint holds".
              Picking the fastest rather than the most profitable when
              infeasible means that when the constraint cannot be met,
              the thing it was protecting is what gets optimised.
              Named fields rather than a metadata bag because a bag
              invites everything to be dumped in it unreviewed, and
              because named fields can be asserted on.
Rung:         R5

### The latency window starts optimistic   2026-10-04 — R5

Decision:     A gateway with no latency samples is assigned a configured
              prior p95 that keeps it inside the feasible set.
Alternatives: Treating unseen gateways as infeasible until proven fast.
Why:          A pessimistic cold start is a trap, not a constraint: an
              excluded gateway is never routed to, so it never produces
              latency samples, so it is never readmitted. Permanent
              exclusion is a deletion. The optimistic prior costs a small
              number of slow transactions while the window fills.
Rung:         R5

### Known consequence: constraining the action set destroys overlap   2026-10-04 — R5

Decision:     Record this rather than work around it, and measure how
              much of the policy space the constrained log can no longer
              evaluate.
Alternatives: Giving excluded gateways a small floor propensity so that
              overlap survives.
Why:          An excluded gateway has propensity exactly zero, which is
              R3's no-overlap condition — so a constrained policy's log
              raises NoOverlapError for any target that would have chosen
              an excluded gateway. This is the exploration/evaluability
              tradeoff from R2 and R3 arriving from a different
              direction: the more aggressively you constrain, the less
              your logs can tell you about the alternatives you refused.
              A floor propensity would preserve evaluability at the cost
              of deliberately violating the latency budget on some
              traffic — a real option, but one that should be chosen
              explicitly with its price attached rather than slipped in
              to make the estimators happy.
Rung:         R5

### Keep the fee economics; measure the spread instead of retuning   2026-10-04 — R5

Decision:     Leave the gateway fees as configured and add a fee-spread
              sensitivity sweep: hold everything else fixed, vary the
              spread, and locate the point where the success term starts
              to outweigh the fee term.
Alternatives: Retune the fees now so R5 has a richer tradeoff, as the R1
              entry contemplated.
Why:          Margin per successful transaction is 130/110/90 bps across
              cirrus/atlas/beacon — a 44% spread — while success varies
              by at most a 13% ratio, so the optimum is fee-dominated and
              R5 will collapse toward "always cheapest". Retuning would
              make the rung look better AND would invalidate R0-R4 for
              comparison AND would be changing the world after seeing the
              results, which the R1 entry explicitly warned against.
              The sweep turns the awkwardness into a result — "at what
              fee spread does reliability start paying for itself?" —
              costs one loop, rewrites no history, and is a better
              artifact than a retuned world would have been.
Rung:         R5 (closes the R1 open item)

### Finding: routing for success rate is never revenue-optimal here   2026-10-05 — R5

Decision:     Report that the question the fee-spread sweep was built to
              answer has no crossing point, rather than forcing one.
Alternatives: Presenting a "break-even spread" by picking a threshold.
Why:          The sweep asked at what fee spread reliability stops being
              the right objective. The answer is that it never was:

                spread   fee bps        money disagrees   gap/1k
                  0.00   90/90/90                 17.8%   14,380
                  0.50   90/100/80                44.7%   74,708
                  1.00   90/110/70 (ours)         68.3%  250,474
                  2.00   90/130/50                74.9%  635,964

              Even at ZERO basis-point spread the two objectives disagree
              on 17.8% of transactions, because cirrus carries a
              100-paise flat fee that no rate equality can cancel. The
              disagreement rate saturates around 75% while the revenue
              gap keeps growing roughly linearly — so past a point the
              question stops being WHICH transactions are misrouted and
              becomes HOW MUCH each misrouting costs.
              At our configured spread, success-rate-optimal routing
              leaves 250,474 paise per 1,000 transactions on the table —
              about 13% of total net revenue — measured against the
              oracle's true probabilities with no policy involved. That
              is the size of the problem R5 exists to address, stated
              independently of how well our solution addresses it.
Rung:         R5 (closes the R1 fee-spread open item)

### Finding: the constrained policy captures a small share of the available gap   2026-10-05 — R5

Decision:     Report the shortfall rather than only the win.
Alternatives: Reporting the paired +37,182 improvement on its own.
Why:          Paired against R4's success-oriented policy on identical
              transactions, the constrained policy gives up 2.71 points
              of success rate [-2.89, -2.54] and earns 37,182 more paise
              per 1,000 [+29,147, +45,217], while bringing realised p95
              from 601ms to 348ms against a 420ms budget. The constraint
              held on 100% of decisions, and the unconstrained policy's
              601ms would have VIOLATED the budget — so the constraint is
              doing real work, not decorating a policy that satisfied it
              anyway.
              But the oracle says 250,474 paise per 1,000 is available
              from revenue-aware routing, and we captured 37,182 — about
              15%. The two numbers are not strictly comparable (the
              oracle figure is unconstrained and uses true probabilities
              rather than learned ones, and the latency constraint itself
              costs revenue), but the order of magnitude is the point:
              the policy is nowhere near the achievable optimum, and the
              headline improvement should be read next to that.
Rung:         R5

### Kafka is the system of record; Postgres is a projection   2026-10-05 — R6

Decision:     The decision endpoint produces the decision record to Kafka
              inside the request. A consumer writes it to Postgres.
              Outcomes arrive on a second topic and are consumed into both
              Redis (health state) and Postgres (outcome rows).
Alternatives: (a) Insert into Postgres synchronously in the request.
              (b) Fire-and-forget the Postgres write.
Why:          (a) blows the 30ms budget and makes routing unavailable
              whenever the LOG is unavailable, which is a strange thing
              to couple. (b) loses records silently, which for a
              propensity is fatal and undetectable.
              Producing to an ordered durable log is fast enough (~1ms at
              acks=1) and gives the property that matters for R6's
              predicted bug: Redis and Postgres become two projections of
              the SAME ordered stream, each with its own offset. They can
              be behind one another; they cannot be inconsistent, because
              they replay identical events in identical order. Divergence
              becomes lag, which is a number you can watch.
Rung:         R6

### Routing availability is deliberately coupled to logging   2026-10-05 — R6

Decision:     Decisions are written through a bounded write-ahead buffer.
              Transient sink unavailability is absorbed; when the buffer
              fills, the endpoint returns 503 and stops routing.
Alternatives: (a) Return the decision anyway and accept log loss.
              (b) Fail on the first produce error, with no buffer.
Why:          (a) creates an unlogged decision — the one thing this whole
              project forbids — and does it invisibly: the payment
              succeeds, nobody notices, and R3 finds a hole months later.
              (b) turns a one-second broker election into an outage.
              The buffer makes the tradeoff a tunable tolerance ("we will
              route through N seconds of logging outage") and a visible
              503 rather than a silent choice.
              This is an arguable position, not an obviously correct one:
              a payments company might well prefer to keep routing and
              lose the log. What matters is that it is chosen, written
              down, and observable in the metrics rather than defaulted
              into.
Rung:         R6

### A blind decision records that it was blind   2026-10-05 — R6

Decision:     If the state store is unavailable the service falls back to
              the configured priors, keeps routing, and records
              `health_source = "fallback"` on the decision. A named
              field, like R5's constraint_status.
Alternatives: (a) Fail the request. (b) Fall back silently.
Why:          The acceptance criterion is "doesn't crash, doesn't
              silently guess", and the second clause is the design. A
              decision made without health data is a different kind of
              decision; if it is indistinguishable in the log from an
              informed one, then R3 pools two populations and every
              estimate inherits the contamination. With the field, "we
              served 4% of traffic blind" is a reportable number and
              those rows can be segmented or excluded.
Rung:         R6

### No container runtime here: the adapters are unexercised   2026-10-05 — R6

Decision:     Build the Redis, Kafka and Postgres adapters against the
              real client APIs, test the contracts against in-process
              fakes, and write the integration suite so it is ready —
              marked skipped with its reason. State in the README that
              the reported p50/p95/p99 are measured against FAKES.
Alternatives: (a) Report the fake-based latency as if it were a
              deployment measurement. (b) Skip the adapters entirely and
              ship only the in-memory version.
Why:          This environment has no Docker, Colima or Podman, so there
              is nothing to run the dependencies against. A latency
              regression test against in-process fakes is still worth
              having — it catches an N+1 read or a serialisation
              regression in our own code, and a test asserts exactly that
              one state-store read happens per decision. But it says
              nothing about Redis round trips or broker acks, and
              presenting it as a deployment p99 would be the most
              straightforwardly dishonest thing in the project.
              The adapters being written but never executed is a real
              limitation and is recorded as one in three places rather
              than left to be discovered.
Rung:         R6

### propensity is NOT NULL in the schema   2026-10-05 — R6

Decision:     The Postgres decision table declares propensity NOT NULL,
              alongside context and action_probs.
Alternatives: Relying on the Python dataclass validation already in
              payroute/decisionlog.
Why:          The dataclass refuses to construct a record without a
              propensity, which covers every path through our code. The
              database constraint covers paths that do not go through our
              code — a backfill script, a manual insert, a future service
              written by someone who did not read CLAUDE.md. The
              invariant is worth stating at the lowest layer that can
              enforce it.
Rung:         R6

### Retries are a Markov decision process, not another bandit   2026-10-05 — R7

Decision:     Model retries with a tabular Q-learner over
              (attempt, method, last_failure_reason), not with another
              contextual bandit.
Alternatives: Treating each retry attempt as an independent bandit
              decision with the attempt number as a context feature.
Why:          A bandit's action does not change the next context — that
              is what made R2 tractable. A failed attempt DOES change the
              state: the attempt number rises, the failure reason is now
              known, the sensible options have narrowed and the customer
              is closer to abandoning. So the action at attempt 1
              determines the state at attempt 2, and the consequence is
              that a greedy per-attempt choice is not optimal: retrying
              somewhere with a 60% chance now can be worse than giving up
              if it burns the attempt budget. Q-learning's bootstrap term,
              max over the next state's actions, is exactly the quantity a
              bandit does not have.
Rung:         R7

### Failure reasons added to R0 as a provably inert change   2026-10-05 — R7

Decision:     Add six failure reasons to the simulator, drawn from their
              own RNG stream conditional on a failure that has already
              been decided. Prove the addition perturbs nothing with
              fingerprint tests whose hashes were captured BEFORE the
              change.
Alternatives: Inferring a reason after the fact from the incident state,
              or skipping reasons and using a coarser state.
Why:          The reasons are the substance of this rung, not decoration:
              `issuer_down` and `gateway_timeout` both look like a
              timeout and call for opposite behaviour, because a
              different gateway routes around a dead gateway but reaches
              the same dead bank. Without them the retry policy has
              nothing to learn.
              Drawing from a new spawned stream leaves the context,
              outcome and drift streams byte-identical, so no number in
              R0 through R6 moves. That claim is worth proving rather
              than asserting, so the success outcomes of 20,000
              transactions across two seeds are hashed and pinned. If a
              future change shifts a stream, those tests fail and name
              the consequence.
Rung:         R7 (touches R0)

### A retry is a fresh draw, not a replay   2026-10-05 — R7

Decision:     Each attempt beyond the first uses its own pre-drawn
              uniform, stored on the transaction. Attempt 1 is unchanged.
Alternatives: Re-resolving with the same common random number.
Why:          Common random numbers are what make counterfactuals
              coherent at R0 and R3 — but they also mean re-resolving a
              transaction returns the identical outcome, so a retry on
              the same gateway would be a replay and the whole rung would
              be empty. Per-attempt draws fix that while leaving attempt
              1 byte-identical, which is what keeps the fingerprint
              intact.
Rung:         R7

### Actions are whether to retry, not where   2026-10-05 — R7

Decision:     Actions are {give_up, retry_same_gateway,
              retry_different_gateway}. Which gateway "different" means
              is delegated to R5's constrained policy over the remaining
              set. Gamma is 1.0, episodes cap at three attempts.
Alternatives: Making the action space the full gateway set at each
              attempt, so the Q-learner chooses where as well as whether.
Why:          Three actions keeps the table at 54 states x 3, small
              enough to enumerate, print and defend. Folding the gateway
              choice in would multiply the state-action space and
              duplicate work R2 through R5 already do better with
              context the tabular model cannot see.
              Gamma 1.0 because the horizon is three attempts: there is
              no infinite sum to tame, and undiscounted episodic return
              IS the quantity we care about — total revenue from this
              transaction.
Rung:         R7

### Train/serve parity is enforced by enumeration   2026-10-05 — R7

Decision:     One `build_state` function used by training and serving; an
              unknown failure reason or method raises rather than
              defaulting; and `assert_covers` checks the Q-table against
              the fully enumerated state space at load time.
Alternatives: A defensive default action for unseen states.
Why:          The predicted failure for this rung is the retry state
              being defined slightly differently in training and in the
              service, so the learned policy never fires. It is the worst
              kind because it is silent: the lookup misses, every
              transaction with that reason gets the fallback, and nothing
              errors. A defensive default is precisely the mechanism that
              makes it silent.
              Enumeration is the defence tabular buys. With 54 states
              every one can be checked before serving a single request,
              so a state the server can construct but the trainer never
              visited is a startup failure instead of a quiet
              degradation. That is a concrete argument for starting
              tabular rather than reaching for a neural Q-function:
              coverage can be proved.
Rung:         R7
