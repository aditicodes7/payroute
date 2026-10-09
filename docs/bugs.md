# Bug Log

Every bug gets an entry here **before** it is fixed. The hypotheses are
written down while they are still genuinely uncertain — that is the point
of the log. Newest entries appended at the bottom.

## Format

```
### <short title>            <date> — <rung>

Symptom:      what I actually saw
Expected:     what I thought would happen
Hypotheses:   1) ...
              2) ...
              3) ...
Test that distinguishes them: ...
Actual cause: ...
Fix:          ...
Why it worked: ...
```

---

### Too few transactions inside the incident window      2026-09-18 — R0

Symptom:      Three incident tests fail on their sample-size guards, not on
              their actual assertions:
                test_gateway_incident_visibly_drops_the_target_success_rate
                  -> "only 1320 transactions inside the outage window"
                test_issuer_incident_degrades_every_gateway_for_that_issuer
                  -> "atlas: only 374 hdfc transactions during the window"
                test_issuer_incident_leaves_other_issuers_untouched
                  -> assert 1017 > 2000
              The other 36 tests pass, including every distributional test
              and both gateway-incident tests that do not depend on window
              volume.

Expected:     A 40-45 minute window inside a 200,000-transaction run at a
              configured 120 txn/min should contain roughly 5,000 txns, of
              which ~28% (the hdfc share) is ~1,500 — comfortably above the
              guards.

Hypotheses:   1) Diurnal placement. The windows start at START + 2h, i.e.
                 02:00 UTC, and the arrival model peaks at 15:00 UTC with
                 amplitude 0.75. If 02:00 sits near the trough, the
                 instantaneous rate there is a fraction of 120/min and the
                 window is genuinely thin. Simulator correct, test's
                 placement wrong.
              2) Marginal guards. Even at the full configured rate, 45 min
                 x 120/min x 0.28 = 1,512 — only 1.5x the 1,000 guard. The
                 guards may simply have been set with no margin, so any
                 rate reduction at all breaks them. Simulator correct, test
                 thresholds wrong.
              3) Time axis is wrong. The interarrival computation
                 (`t += gap / rate`) could be inverted or mis-scaled, so
                 simulated time advances faster than it should and a
                 fixed-duration window covers far fewer transactions than
                 the configured rate implies. Simulator WRONG.

Test that distinguishes them:
              Generate 200,000 transactions from the clean world and print
              (a) the total wall-clock span of the run and (b) the
              transaction count per hour-of-day.
                - Span ~= 200000/120 min ~= 27.8 hours  -> rate is as
                  configured, hypothesis 3 is ruled out.
                - Span far from 27.8 hours              -> hypothesis 3.
                - Peak-hour count ~7x trough-hour count, with hour 02 near
                  the trough                            -> hypothesis 1.
                - Hour-02 count close to the uniform expectation           
                                                        -> hypothesis 2.
              Hypotheses 1 and 2 are not exclusive; the per-hour counts
              show how much of the shortfall each one explains.

Actual cause: Hypothesis 1, with hypothesis 2 as a contributing factor.
              Hypothesis 3 ruled out: over one complete simulated day the
              run produced 172,731 transactions against a configured
              expectation of 172,800, so the arrival rate is correct and
              the time axis is not distorted.
              Per-hour counts over a single day: peak 15:00 at 209/min,
              trough 03:00 at 29.9/min, a 7.0x swing exactly as
              configured. All three windows started at 02:00, where the
              rate is 30.5/min. A 45-minute window there holds ~1,370
              transactions, of which ~28% (hdfc) is ~384 — which matches
              the 374 actually observed.
              Hypothesis 2 contributed: the guards were written assuming
              uniform arrivals and had only ~1.5x margin, so any rate
              reduction at all would have broken them.

              Sub-finding worth recording: the distinguishing test I first
              wrote was itself faulty. It compared the run's wall-clock
              span (32.7h) against "200,000 / 120 per min = 27.8h" and
              treated a mismatch as evidence for hypothesis 3. That
              expectation is wrong — accumulating a FIXED COUNT of
              transactions takes longer when the rate varies, because a
              disproportionate share of wall-clock time is spent in the
              slow hours. The span was never a valid discriminator. The
              valid one is the transaction total over a complete day.

Fix:          Added `BUSY_HOUR_OFFSET = timedelta(hours=13)` to
              tests/conftest.py, with a comment explaining the arrival
              model, and moved all six incident-window placements in
              tests/sim/test_incidents.py from START + 2h to
              START + BUSY_HOUR_OFFSET. No simulator code changed.

Why it worked: The assertions were never wrong — they were never reached.
              At 13:00 the rate is ~205/min, so a 45-minute window holds
              ~9,200 transactions and ~2,600 hdfc transactions, clearing
              the guards by 2.5x to 9x. The failure was a statement about
              where the tests looked, not about what the simulator does.

### Drift mean-reversion test compared against the wrong distribution   2026-09-18 — R0

Symptom:      test_drift_is_mean_reverting passed, but a probe across six
              seeds gave z-scores of +1.7, +7.2, +0.7, -6.0, +9.6, +1.9
              against a z=12 limit. Passing, but one seed change away
              from red.
Expected:     A long-run average over 150,000 transactions should sit a
              fraction of a sigma from the baseline, not nine sigma.
Hypotheses:   1) The drift process is not actually mean-reverting and the
                 long-run average really is wandering.
              2) The band is the wrong shape: the observed rate carries
                 drift variance on top of binomial variance, so a
                 binomial band understates the spread at any z and the
                 z=12 was a fudge factor covering for it.
              3) Drift sigma is mis-scaled — e.g. the AR(1) step sigma
                 used as if it were the stationary sigma.
Test that distinguishes them:
              Compute the expected variance of the time-average of an
              AR(1) with stationary variance sigma^2 and correlation time
              tau over a span T: approximately sigma^2 * 2*tau/T. Add it
              to the binomial variance and re-express each seed's
              deviation in units of the combined standard error. If the
              z-scores collapse to order 1, hypothesis 2. If they stay
              large, hypothesis 1 or 3.
Actual cause: Hypothesis 2. Under the combined band the same eight seeds
              give |z| of 0.25, 1.04, 0.10, 0.87, 1.38, 0.27, 0.91, 0.56
              — worst case 1.38 against a 4.0 limit. The drift process is
              mean-reverting exactly as configured; the yardstick was
              wrong.
Fix:          Added `drift_adjusted_band()` to tests/conftest.py, which
              builds the band from binomial_var + drift_var where
              drift_var = sigma^2 * 2*tau/T and tau = half_life / ln(2).
              test_drift_is_mean_reverting now uses it at z=4.
Why it worked: The quantity being measured has two independent variance
              sources and the band now accounts for both, so the limit
              means what it claims to mean. The band also TIGHTENED in
              absolute terms (+/-0.018 rather than +/-0.029), so the test
              is both less flaky and more sensitive than before — a true
              random walk with the same step size would fail it. Raising
              z until the failures stopped would have done the opposite
              and hidden the modelling error.

### paired_difference accepted two logs from different runs      2026-09-18 — R1

Symptom:      test_paired_difference_refuses_mismatched_logs fails with
              "DID NOT RAISE ValueError". Two logs produced from
              Simulator(seed=55) and Simulator(seed=56) — different
              transaction streams — are accepted as pairable, and
              paired_difference happily returns a number computed by
              pairing unrelated transactions with each other.
Expected:     The txn_id comparison inside paired_difference should find
              a mismatch at position 0 and refuse.

Hypotheses:   1) txn_id is not unique across runs. The simulator numbers
                 transactions `txn-{counter:09d}` with the counter
                 starting at 0 for every Simulator instance, so BOTH logs
                 begin at txn-000000000 and the ids match position for
                 position even though the underlying transactions are
                 entirely different. The guard is comparing labels that
                 carry no run identity. (Implementation defect, and a
                 wider one than this test: an id that is not unique is
                 also not a usable primary key for the R6 decision table,
                 and it silently breaks any join between two runs.)
              2) The mismatch check in paired_difference is faulty — for
                 example zip() truncating, or the comparison never
                 executing on the path taken.
              3) Seeds 55 and 56 genuinely produce identical transactions
                 — i.e. the seed is not reaching context generation.
                 Largely ruled out already, since
                 test_different_seeds_produce_different_streams passes,
                 but worth confirming rather than assuming.

Test that distinguishes them:
              Print the first three (txn_id, context) pairs from each of
              the two logs.
                - ids identical, contexts different  -> hypothesis 1
                - ids identical, contexts identical  -> hypothesis 3
                - ids different (so the check had a real mismatch to
                  find and missed it) -> hypothesis 2

Actual cause: Hypothesis 1. The diagnostic printed, for seeds 55 and 56:
                55 [('txn-000000000', 'netbanking', 389224), ...]
                56 [('txn-000000000', 'netbanking', 722823), ...]
              Identical ids, different transactions. The guard was
              comparing labels that carry no run identity, so it had
              nothing to detect.

Fix:          Two layers.
              (a) Root cause: the simulator now issues
                  `txn-{seed}-{counter:09d}`, so an id identifies a
                  transaction within a run AND identifies the run. Two
                  logs from the same seed still pair, which is the
                  behaviour paired comparison depends on.
              (b) Defence in depth: paired_difference now also compares
                  the two logs' seeds when both are DecisionLogs, and
                  fails with a message that names the actual problem
                  rather than reporting a position mismatch.

Why it worked: The id is now a real identifier rather than a position
              counter. This was a wider defect than the failing test
              suggested — a non-unique id is not a usable primary key for
              R6's decision table either, and any future join between two
              runs would have matched unrelated rows and returned a
              plausible wrong answer rather than an error. The test that
              caught it was only checking that a guard fired; what it
              actually found was that the thing the guard compares was
              meaningless.

### Confidence width shrank for a context the arm had never seen   2026-09-19 — R2

Symptom:      test_confidence_width_is_directional fails. After 300
              updates on (upi, hdfc), the width for the never-seen
              (netbanking, kotak) fell from 2.292 to 1.800 — a 21% drop,
              where the test allowed at most 20%.
Expected:     Learning about one segment should leave uncertainty about
              an unrelated segment essentially untouched.
Hypotheses:   1) The two contexts are not unrelated. They differ only in
                 method and issuer; merchant_category, amount, hour and
                 bias are identical, so several of the 16 feature
                 directions ARE being learned. Partial generalisation
                 across shared directions is exactly what LinUCB is for,
                 so the shrinkage is correct and the test's contexts are
                 not distinct enough.
              2) The A_inv update leaks across arms or across directions
                 — e.g. Sherman-Morrison applied to the wrong matrix.
                 Partly ruled out already:
                 test_only_the_chosen_arm_is_updated and
                 test_maintained_inverse_stays_equal_to_the_true_inverse
                 both pass.
              3) The one-hots are not actually distinguishing method and
                 issuer, making the two contexts nearly identical
                 vectors. Largely ruled out by the featuriser tests.
Test that distinguishes them:
              Print both feature vectors and count how many nonzero
              directions they share, then repeat the measurement with a
              second context that differs in method, issuer, category,
              hour AND amount.
                - many shared directions, and the width ratio rises
                  toward 1.0 as the contexts are made more distinct
                  -> hypothesis 1
                - ratio unchanged however distinct the contexts
                  -> hypothesis 2

Actual cause: Hypothesis 1. The two contexts share 5 of 16 nonzero
              directions: category=food_delivery, log_amount_z, hour_sin,
              hour_cos and bias. Learning about those is real learning
              that legitimately applies to both. Making the contexts
              genuinely distinct (different method, issuer, category,
              hour and amount) moves the ratio from 0.785 to 0.993 — so
              the implementation is behaving exactly as the design
              claims, and the test's "unrelated" contexts were not
              unrelated. Partial generalisation across shared feature
              directions is the property that makes LinUCB more
              data-efficient than a per-segment table; this test was
              accidentally measuring it and calling it a fault.
Fix:          The test now uses contexts that differ in method, issuer,
              category, hour AND amount, and asserts the width stays
              above 95% of its prior value. A companion test,
              test_partially_shared_contexts_do_generalise, pins the
              other half of the behaviour: contexts that share some
              directions DO get more confident, by a factor between 0.5
              and 0.9.
Why it worked: The implementation never needed changing. Splitting the
              property in two turns an accidental near-miss into two
              explicit statements — uncertainty is directional, and
              partial overlap generalises — and the second is the
              property that makes LinUCB beat a per-segment table, so it
              is worth a test of its own rather than a footnote.

### theta does not converge to the generating parameters   2026-09-19 — R2

Symptom:      test_theta_recovers_a_known_linear_reward fails. After
              4,000 noiseless observations generated from a known theta
              with ridge = 1e-6, the estimate is close on some components
              and off by up to 0.08 on others (e.g. -0.20 vs -0.14,
              +0.33 vs +0.27).
Expected:     With noiseless rewards and negligible regularisation, ridge
              regression should recover the generating parameters almost
              exactly.
Hypotheses:   1) The design matrix is rank-deficient — the classic
                 dummy-variable trap. The three method one-hots sum to 1,
                 the five issuer one-hots sum to 1, and the four category
                 one-hots sum to 1, and there is also an explicit bias
                 column equal to 1. That is three exact linear
                 dependencies, so theta is NOT IDENTIFIABLE: infinitely
                 many parameter vectors give identical predictions, and
                 ridge picks the minimum-norm one rather than the
                 generating one. Predictions would still be correct.
              2) Ridge at 1e-6 is still shrinking theta toward zero
                 enough to matter. Would show as roughly uniform
                 shrinkage across all components.
              3) Insufficient data or poor conditioning — 4,000 samples
                 for d = 16 would have to be far worse conditioned than
                 expected for this to be the cause.
Test that distinguishes them:
              (a) Compute matrix_rank(A - ridge*I). If it is below 16,
                  hypothesis 1.
              (b) Compare PREDICTIONS rather than parameters: draw random
                  contexts and check x@theta_hat against x@truth. If
                  predictions agree to high precision while parameters do
                  not, hypothesis 1 is confirmed and the test is asserting
                  the wrong quantity.
              (c) If predictions are also off, compare the ratio
                  theta_hat/truth componentwise — a roughly constant
                  ratio below 1 points at hypothesis 2.

Actual cause: Hypothesis 1, confirmed on both checks.
              matrix_rank(sum x x^T) = 13 of 16 — exactly the three
              dependencies predicted (method one-hots, issuer one-hots
              and category one-hots each sum to the bias column).
              Predictions are essentially exact: max error 4.1e-07 over
              500 contexts, against a max PARAMETER error of 6.6e-02.
              ||theta_hat|| = 0.5055 vs ||truth|| = 0.5416, i.e. ridge
              returned the minimum-norm member of the solution set.
              The test asserted parameter recovery for a model whose
              parameters are not identifiable.
              Worth recording: the rank deficiency is harmless here. The
              unexcited directions form a subspace no real feature vector
              ever enters, A is block-diagonal with respect to that
              split, so the confidence widths are unaffected and only
              theta's interpretability is lost — which we never rely on.
Fix:          The test now asserts what is actually identifiable:
              PREDICTIONS agree to better than 1e-5 over 500 contexts,
              the design matrix has rank dim - 3, and the fitted theta's
              norm does not exceed the generating one (the minimum-norm
              property). It no longer asserts parameter recovery.
Why it worked: The model's parameters are not identifiable, so no amount
              of data would have made the original assertion true.
              Pinning rank == dim - 3 turns the dummy-variable trap from
              something rediscovered painfully into something the suite
              states out loud, and pinning the norm records WHICH member
              of the solution set ridge returns. The deficiency is
              harmless: the unexcited directions form a subspace no real
              feature vector enters, so confidence widths are unaffected.

### Test and policy disagreed about which arm is "greedy"   2026-09-19 — R2

Symptom:      test_propensities_follow_the_epsilon_greedy_formula fails.
              With epsilon = 0.3 and K = 3 the greedy arm should carry
              probability 0.8; the test observed 0.1, which is exactly
              eps/K — the NON-greedy value.
Expected:     probs[greedy] == (1 - eps) + eps/K.
Hypotheses:   1) There is no unique greedy arm at the moment the test
                 runs. The policy is fresh, so theta = 0 and A = lambda*I
                 for every arm, and all three scores are identical. The
                 test picks the greedy arm with a plain max() over a
                 dict, which returns the first key in insertion order;
                 the policy breaks ties with a seed-derived priority, on
                 purpose, so that cold-start traffic is not all funnelled
                 into whichever gateway happens to be listed first. Both
                 are defensible and they disagree, so the test is
                 asserting against an arbitrary tie-break.
              2) The probability formula in the implementation assigns
                 1 - eps to the wrong arm.
              3) scores_for() recomputes something different from what
                 decide() used, so the test is reading a different
                 ranking than the policy acted on.
Test that distinguishes them:
              Print the three scores from a fresh policy together with
              policy.greedy_action(context).
                - all three scores exactly equal -> hypothesis 1
                - scores differ and greedy_action is not their argmax
                  -> hypothesis 2 or 3, separable by checking whether
                  action_probs puts 1-eps+eps/K on greedy_action

Actual cause: Hypothesis 1. On a fresh policy all three scores are
              identical to twelve decimal places (2.292215056449), so
              there is no unique greedy arm to agree about. The policy's
              seed-derived tie-break chose cirrus; the test's plain
              max() over the dict returned atlas, the first key. Both are
              correct; the test was asserting against an arbitrary
              choice, at the one moment in the run when the choice is
              arbitrary by construction.
Fix:          The test warms the policy with 200 asymmetric updates
              (beacon rewarded, atlas not) before asserting the formula,
              and checks that all three scores are now distinct. The
              cold-start tie-break behaviour keeps its own test.
Why it worked: The probability formula was never wrong; the test was
              evaluating it at the one moment when "the greedy arm" is
              undefined. Warming up makes the quantity the test names
              actually exist. The two behaviours — a well-defined argmax
              once there is evidence, and a randomised tie-break when
              there is none — are now tested separately instead of
              colliding in one assertion.

### Cumulative regret is linear, not sublinear                    2026-09-19 — R2

Symptom:      test_cumulative_regret_is_sublinear fails. Over 60,000
              transactions, cumulative regret at the halfway point is
              162.1 and at the end 266.6 — a ratio of 1.645 against a
              threshold of 1.6. So the second half accumulated 104.5,
              only a third less than the first half's 162.1.
Expected:     Doubling the horizon should add materially less than the
              first half did: LinUCB's regret bound is O(d sqrt(T log T)),
              which is sublinear.
Hypotheses:   1) Fixed-epsilon exploration has a LINEAR regret floor by
                 construction. The wrapper explores at rate epsilon
                 forever, so it pays roughly
                 epsilon * E[gap to a uniformly chosen arm] per
                 transaction indefinitely, and cumulative regret is
                 Theta(T) asymptotically. The sublinear guarantee belongs
                 to pure LinUCB (epsilon = 0), not to the epsilon-greedy
                 wrapper we bolted on for propensity reasons. If so the
                 test asserts a property this policy provably does not
                 have, and the fault is in the test.
              2) LinUCB is not converging: the greedy arm is still wrong
                 at a roughly constant rate, because alpha is too large,
                 the features are inadequate, or the ridge is mis-scaled.
              3) The world is non-stationary, so there is a moving target
                 to track. Ruled out by construction — this test uses
                 clean_world, which has drift disabled and no incidents.
Test that distinguishes them:
              Split the late-run regret by whether the action taken was
              the greedy one. The greedy arm is recoverable from the log:
              it is the action carrying probability (1-eps)+eps/K.
                - late regret almost entirely on NON-greedy actions, and
                  the greedy-action regret near zero -> hypothesis 1
                - substantial regret on greedy actions late in the run
                  -> hypothesis 2
              Then re-run with epsilon = 0 and compare the ratio: if it
              drops well below 1.6, hypothesis 1 is confirmed and the
              sublinear property is present in the policy that actually
              claims it.

Actual cause: Hypothesis 1, confirmed by the epsilon = 0 control.

                eps     ratio full/half    mean regret/txn (last 12k)
                0.05    1.645              0.00298
                0.00    1.118              0.00050

              At epsilon = 0 the ratio drops to 1.118 — comfortably
              sublinear, and the property LinUCB actually claims. The
              linear floor is a consequence of the epsilon-greedy wrapper
              we added for propensity reasons, not a defect in LinUCB or
              in the features. Hypothesis 3 was ruled out by
              construction (clean_world has drift disabled).

              One part of the decomposition did NOT come out as
              hypothesis 1 predicted, and is recorded rather than glossed
              over. The prediction was that late regret under epsilon =
              0.05 would sit almost entirely on non-greedy actions. It
              splits closer to evenly: 18.72 on non-greedy against 17.01
              on greedy over the last 12,000 transactions. So roughly
              half the floor is the exploration tax and half is residual
              error in the greedy choice — and that residual is itself
              about three times larger than the greedy-action regret at
              epsilon = 0 (0.00142 vs 0.00050 per transaction), which is
              not explained by the exploration argument alone. It does
              not change the cause of the failure, and it is small in
              absolute terms, but it is an open observation rather than a
              closed one.
Fix:          Split the assertion in two. The sublinearity test now runs
              at epsilon = 0, where the bound applies. A companion test,
              test_epsilon_greedy_pays_a_linear_regret_floor, asserts the
              OPPOSITE for epsilon = 0.05 — that its steady-state regret
              is more than 3x the epsilon = 0 floor.
Why it worked: The original test asserted a theorem about a policy the
              theorem does not cover. Both properties are real and both
              matter, so both are now pinned: LinUCB learns, and the
              exploration wrapper costs something measurable. Turning the
              failure into a second assertion rather than deleting it
              makes the price of loggability a regression test instead of
              a paragraph someone has to remember.

### Normal and bootstrap interval widths were not reliably ordered   2026-09-19 — R3

Symptom:      test_the_normal_interval_understates_uncertainty_on_a_skewed_log
              fails by a hair: normal width 1.1286 against bootstrap
              width 1.1266. The assertion wanted the normal interval to
              be the narrower of the two; it came out 0.2% wider.
Expected:     On a heavy-tailed importance-weight distribution the normal
              approximation should visibly understate the spread.
Hypotheses:   1) The test's premise is wrong. Percentile-bootstrap width
                 and normal width are not reliably ordered — both are
                 driven by the same underlying variance. The normal
                 interval's real failure is in SHAPE and COVERAGE, not
                 width: it is symmetric about the estimate and can extend
                 into values the estimand cannot take, which a percentile
                 interval cannot do. With only three heavy records the
                 bootstrap distribution is essentially Poisson(3) on the
                 count of heavy draws, and its spread happens to match.
              2) 400 bootstrap resamples is too few to reach the tail, so
                 the percentiles are understated and the interval is
                 artificially narrow. More resamples would widen it.
              3) The normal interval is computed wrongly — a mistake in
                 the influence function or in ddof — making it too wide.
Test that distinguishes them:
              (a) Re-run with 5,000 resamples. If the bootstrap width
                  grows materially, hypothesis 2.
              (b) Print both intervals in full. A success rate cannot be
                  negative or above 1; if the normal interval crosses
                  those bounds while the bootstrap stays inside them,
                  that is hypothesis 1 and it identifies what the test
                  should have been asserting.
              (c) Recompute the normal half-width directly as
                  1.96 * sd(w*r) / sqrt(n) and compare. Agreement rules
                  out hypothesis 3.

Actual cause: Hypothesis 1, with hypothesis 2 as a minor contributor.
              Hypothesis 3 ruled out: the implementation's normal
              half-width is 0.5643, identical to a direct
              1.96*sd(w*r)/sqrt(n) computation.

              (b) is decisive. The two intervals are:
                normal    [-0.0653, +1.0633]
                bootstrap [+0.0000, +1.1266]
              The normal interval is symmetric about the estimate and
              runs to a NEGATIVE success rate, which is not a value the
              estimand can take. The bootstrap's lower bound is exactly
              0, because no resample can produce a negative one. That —
              not the width — is the property that separates them, and it
              is what the test should have asserted.
              (Both upper bounds exceed 1, but that is a property of IPS
              itself rather than of the interval method, and it is
              already covered by
              test_ips_can_return_a_value_outside_the_possible_reward_range.)

              (a) contributes a little: raising the resample count from
              400 to 5,000 widens the bootstrap interval from 1.1453 to
              1.1597, about 1.3%. So 400 does slightly understate the
              tail on a log this skewed, though nowhere near enough to
              explain the failure.
Fix:          The test now asserts the shape property instead of the
              width one: the normal interval's lower bound must be below
              zero, and the bootstrap's must not be. Renamed to
              test_the_normal_interval_extends_into_impossible_values.
              BOOTSTRAP_RESAMPLES raised from 400 to 1,000, with the
              measurement that motivated it recorded in a comment.
Why it worked: The original assertion compared two quantities that are
              driven by the same variance and are therefore not reliably
              ordered — it passed or failed on a coin flip. The shape
              property is structural: a symmetric interval around a
              skewed estimate WILL run off the end of the estimand's
              support, and a percentile interval never can. The test now
              fails only if the thing it names stops being true.

### clipped_ips reports a healthy ESS while being wildly biased   2026-09-19 — R3

Symptom:      Two symptoms, one suspected cause.
              (a) In the R3 table, clipped_ips is the worst estimator in
                  the set by a wide margin — errors of -0.088, -0.171,
                  -0.256, -0.231 as epsilon falls, against IPS's worst of
                  +0.044 — and its trust verdict says "ok" at every one
                  of those points. Its reported ESS RISES as exploration
                  falls: 11,384 -> 14,926 -> 21,596 -> 29,862, while
                  IPS's falls 6,778 -> 3,370 -> 1,302 -> 705 on the same
                  logs.
              (b) The estimator-error chart is unreadable because
                  clipped_ips zigzags: plotted against its own ESS its
                  points are not ordered by epsilon at all. direct_method
                  meanwhile collapses to a single x position, since its
                  ESS is always n.
Expected:     ESS should fall as the logging policy becomes
              deterministic, for every estimator, because it is supposed
              to describe how much usable evidence the LOG contains.
Hypotheses:   1) ESS is being computed on each estimator's MODIFIED
                 weights. Clipping makes the weights artificially
                 uniform, which raises ESS by construction — so the
                 number stops measuring coverage of the log and starts
                 measuring the variance of a deliberately truncated
                 statistic. Coverage is a property of the (log, target)
                 pair, not of an estimator's variance-reduction trick.
                 This would explain both symptoms at once: the same
                 mistake makes the verdict wrong and the x-axis
                 meaningless.
              2) The clip threshold of 20 is simply too aggressive for
                 these logs. The bias would then be real and correctly
                 reported, with nothing wrong with the diagnostic.
              3) The verdict logic escalates only on ESS and max_weight
                 and never on clipped mass, so even a correct ESS would
                 not flag this — the clipped-fraction warning is emitted
                 but cannot change `trust`.
Test that distinguishes them:
              For each epsilon, print the ESS computed on RAW weights
              alongside the ESS on clipped weights, plus the fraction of
              weights clipped.
                - raw ESS falls monotonically while clipped ESS rises
                  -> hypothesis 1
              Then sweep clip in {5, 20, 100, inf} at one epsilon.
                - bias shrinking toward zero as clip grows, with clipped
                  ESS converging to raw ESS -> hypothesis 2 contributes
                - a large clipped fraction coinciding with a healthy raw
                  ESS -> hypothesis 3 matters independently of 1

Actual cause: Hypothesis 1 confirmed, with 2 and 3 both contributing.

                eps   raw ESS   clipped ESS   % clipped   clipped err
                0.30   15,686        15,686        0.0%      +0.0105
                0.10    6,778        11,384        0.9%      -0.0881
                0.05    3,370        14,926        0.5%      -0.1714
                0.02    1,302        21,596        0.2%      -0.2556
                0.01      705        29,862        0.1%      -0.2310

              Raw ESS falls monotonically; clipped ESS RISES. Computing
              ESS on the estimator's own modified weights makes it
              measure the variance of a deliberately truncated statistic
              rather than the coverage of the log — so the more
              aggressively we throw information away, the healthier the
              diagnostic claims to be.

              The clip sweep at eps=0.02 confirms hypothesis 2 as a
              contributor: error -0.283 at clip=5, -0.256 at 20, -0.110
              at 100, and -0.0195 at clip=inf, where it equals IPS
              exactly and clipped ESS converges to the raw 1,302.

              The sharpest part is hypothesis 3, and it is worse than
              expected. At eps=0.02 only 0.2% OF RECORDS are clipped, and
              that removes roughly 27% of the estimate's value. At low
              exploration nearly all the information lives in a handful
              of high-weight records, so the fraction of records clipped
              is a reassuring and useless number. The quantity that
              matters is the fraction of WEIGHT MASS removed, which we
              were not computing at all.

              Worth stating plainly: this is precisely the failure this
              rung exists to study — a diagnostic that reports "ok" while
              the estimate is off by 0.26 — and we built one.
Fix:          Three changes.
              (a) `_result` now documents and enforces that `weights` is
                  always the RAW importance weights, so `ess` and
                  `max_weight` describe the (log, target) pair rather
                  than an estimator's internal arithmetic. clipped_ips
                  passes raw weights; direct_method now reports the
                  log-level ESS too, with a warning that its own interval
                  reflects only the spread of its predictions.
              (b) The verdict gained an escalation path that ESS cannot
                  see: clipped WEIGHT MASS above 5% marks an estimate
                  suspect and above 20% untrustworthy, and the warning
                  quotes the mass alongside the (misleadingly small)
                  record count.
              (c) The chart's x axis is the log-level ESS, shared by
                  every estimator. Plotting each against its own ESS made
                  clipped_ips zigzag and collapsed direct_method to a
                  single point.
              One test changed with it:
              test_clipped_ips_trades_bias_for_variance now expects
              max_weight 2.0 (raw) rather than 1.5 (post-clip).
Why it worked: The diagnostic now measures the thing it is named after.
              clipped_ips at eps=0.02 went from ESS 21,596 / trust "ok"
              to ESS 1,302 / trust "untrustworthy", which is what a
              reader needs in order to disbelieve an estimate that is
              0.26 away from the truth. The interval-width panel of the
              chart now shows the signature plainly: clipped_ips's
              reported interval gets NARROWER as its error grows, which
              is the precise shape of a diagnostic pointing the wrong
              way.

### Both half-life tests assert the decay backwards            2026-10-03 — R4

Symptom:      Two tests fail together:
                test_decay_is_in_wall_clock_time_not_in_observations
                test_a_shorter_half_life_reacts_faster
              In the first, ten failures compressed into one minute were
              expected to leave MORE evidence standing at a read an hour
              later than ten spread over that hour. In the second, a
              2-minute half-life was expected to show lower health than a
              30-minute one after sixty failures over ten minutes.
Expected:     Shorter memory reacts faster; compressed evidence counts
              for more.
Hypotheses:   1) Both tests start from a COLD store, so there is no
                 stale evidence for the decay to discard. With only
                 accumulation, a longer half-life retains more of the
                 observations and therefore shows MORE evidence of
                 failure, not less. Reaction speed is about discarding
                 old GOOD evidence after a change, and these tests gave
                 the store no history to discard — they measure
                 accumulation and call it reaction. Also, in the first
                 test the "compressed" batch is entirely 60 minutes old
                 at read time while the "spread" batch includes an
                 observation six minutes old, so the spread one is
                 legitimately the fresher of the two.
              2) The decay is inverted in the implementation — e.g.
                 0.5**(half_life/dt) instead of 0.5**(dt/half_life) — so
                 older evidence is weighted more heavily. Largely ruled
                 out already: test_decay_matches_the_hand_computed_value
                 passes, and it pins one half-life to exactly 0.5.
              3) With an all-failure stream S stays 0, so
                 health = prior_successes / (N + prior_trials) is a
                 monotone function of N alone. These tests can therefore
                 only ever compare evidence MASS, never the success rate
                 — the quantity they name is not the quantity they
                 measure.
Test that distinguishes them:
              (a) Print evidence_at() and health for both stores at
                  several times. If the longer half-life consistently
                  holds more evidence under a pure failure stream, and
                  health tracks it monotonically, that is 1 and 3.
              (b) Warm both stores with a long run of SUCCESSES, then
                  switch to failures, and measure how long each takes to
                  fall below a threshold. If the short half-life is
                  faster there, the implementation is correct and the
                  tests were set up without the history that makes
                  "reaction" meaningful.

Actual cause: Hypotheses 1 and 3, confirmed on both checks. Hypothesis 2
              was already excluded by the passing hand-computed decay
              test.

              (a) Cold store, pure failure stream:
                    min   N(hl=2)  health   N(hl=30)  health
                      1      5.93   0.565       6.92   0.532
                     10     16.29   0.342      53.47   0.142
                  The longer half-life holds more evidence and therefore
                  reports lower health. With S = 0 throughout,
                  health = 9/(N+10) is a monotone function of evidence
                  mass alone — so these tests could only ever compare how
                  much data each store held. The quantity they named was
                  never the quantity they measured.

              (b) Warm store (30 minutes of successes, then failures):
                    min after switch   hl=2    hl=30
                                   1  0.751    0.945
                                   5  0.430    0.804
                                  10  0.355    0.673
                  With stale good evidence present to discard, the short
                  half-life reacts exactly as designed.
Fix:          Both tests restructured so that the thing they name is the
              thing they measure. The wall-clock test now reads each
              store at the moment of its OWN last observation, so the two
              differ only in how long their evidence has had to decay.
              The half-life test warms both stores with thirty minutes of
              successes before the failures start, and requires a gap of
              at least 0.2 rather than a bare inequality.
Why it worked: Reaction speed is a property of discarding stale evidence
              after a change. Measuring it on a cold store measures
              accumulation instead, and accumulation runs the other way.
              No implementation change was needed — the store was correct
              throughout, and the warm-store numbers above are now the
              evidence that it was.

### Adaptive and static policies tied on time-to-react          2026-10-03 — R4

Symptom:      test_decayed_features_react_faster_than_static_ones fails
              on equality: both reported reacted_after = 300s, exactly
              one 5-minute bin. The two Reaction objects differ
              elsewhere though — adaptive recovered_after = 1500s, static
              recovered_after = None.
Expected:     Decayed health features should divert traffic sooner than
              unweighted sufficient statistics.
Hypotheses:   1) Bin resolution. Both reacted inside the first 5-minute
                 bin, so the measurement cannot separate them. The real
                 difference is in RECOVERY, which the test does not look
                 at: static never returns.
              2) The static policy genuinely reacts as fast here. The
                 outage is severe (peak 0.75) and 45 minutes long at
                 roughly 200 txn/min, so about 9,000 failures land on one
                 gateway — enough to move even an unweighted estimate
                 built from 120,000 observations. There may be no onset
                 difference to find at this severity.
              3) The health features are not actually influencing the
                 decision — the model has not learned a meaningful
                 coefficient on them — so the two policies are behaving
                 identically for the same reason.
Test that distinguishes them:
              Re-measure both with 1-minute bins. If onsets separate,
              hypothesis 1. If they stay tied, print the learned
              coefficient on each health feature: near zero implies 3,
              clearly non-zero with still-tied onsets implies 2. Also
              sweep severity downward — a mild outage is where an
              unweighted estimator should struggle most.

Actual cause: Hypothesis 1, with hypothesis 2 adding force and
              hypothesis 3 ruled out.

                severity  bins   adaptive onset/recover   static onset/recover
                    0.75  1min        3:00 / 24:00            8:00 / never
                    0.75  5min        5:00 / 25:00            5:00 / never
                    0.35  1min        5:00 / 10:00           14:00 / 3:05:00
                    0.35  5min        5:00 / 10:00           15:00 / 3:15:00

              At 5-minute bins both policies land in the first bin and
              the measurement cannot separate them. At 1-minute bins the
              severe case separates cleanly (3:00 against 8:00).
              Hypothesis 3 is excluded: the learned coefficients on the
              health features for the atlas arm are
              [+0.2427, -0.1246, +0.0201] — its own health is the single
              largest weight in the model, so the features are plainly
              doing work.
              The stronger finding is one the test never looked at.
              RECOVERY is where the two differ most, and a MILD outage is
              where an unweighted estimator suffers worst: at severity
              0.35 the adaptive policy is back to normal routing in 10
              minutes while the static one takes 3 hours 5 minutes, and
              at severity 0.75 the static policy never recovers within
              the run at all. That is the R2 finding reproduced exactly,
              and it is a better headline than onset.
Fix:          The reaction tests now measure at 1-minute bins, and the
              incident was moved from the 13:00 peak to 08:00 — the rate
              there (~109/min) is still ample for 1-minute bins while
              reaching it costs 30,000 transactions instead of 100,000,
              which cut this file's runtime from 43s to 23s. A new test,
              test_the_static_policy_is_the_one_that_cannot_let_go,
              asserts the recovery gap directly.
Why it worked: The implementation was never at fault; the ruler was too
              coarse. Five-minute bins cannot distinguish "reacted in
              three minutes" from "reacted in eight" because both land in
              the first bin, and a measurement that cannot separate two
              policies is not evidence that they are the same. Adding the
              recovery assertion captures the larger effect, which the
              onset test was never going to see.

### A short half-life was not more unstable on a clean world    2026-10-03 — R4

Symptom:      test_a_shorter_half_life_is_less_stable_when_nothing_is_wrong
              fails: instability 0.0819 for a 1-minute half-life against
              0.0844 for a 60-minute one — indistinguishable, and
              slightly the wrong way round.
Expected:     A short memory should chase noise and produce visibly
              jumpier routing when nothing is actually wrong.
Hypotheses:   1) The metric is dominated by the LEARNING TRANSIENT rather
                 than by jitter. routing_instability takes the standard
                 deviation of each gateway's share across all bins, and a
                 bandit's share moves a long way during its first
                 thousands of transactions as it stops exploring. That
                 drift is the same for both half-lives and swamps the
                 short-term movement the test is after.
              2) On a clean world health genuinely does not move for
                 either setting: traffic is high, the prior is worth ten
                 observations, and R0's background drift may be too mild
                 at this bin size to produce overreaction.
              3) Epsilon-greedy exploration noise dominates the per-bin
                 share variance, so both policies inherit the same floor.
Test that distinguishes them:
              Recompute instability three ways: (a) over the last third
              of the run only, after learning has settled; (b) as the
              standard deviation of FIRST DIFFERENCES between consecutive
              bins, which removes any slow trend; (c) with epsilon = 0,
              removing exploration noise. Whichever of these separates
              the two half-lives identifies which term was drowning the
              signal.

Actual cause: Hypothesis 1 is right about the metric, but hypothesis 2
              turns out to carry most of the weight, and the honest
              answer is that the effect we expected is barely there.

                              whole-run  last-third  first-diff
                hl=1             0.0819      0.0543      0.0681
                hl=60            0.0844      0.0603      0.0584
                hl=1,  eps=0     0.0851          --      0.0644
                hl=60, eps=0     0.0992          --      0.0672

              Taking the standard deviation of consecutive-bin
              DIFFERENCES does separate them in the expected direction
              (0.0681 against 0.0584, about 17% jumpier), confirming that
              the level-based metric was dominated by slow movement
              shared by both. But the separation is small and it flips
              sign with epsilon = 0, so it is not a robust effect.
              The reason is hypothesis 2: with roughly 1,000
              transactions per five-minute bin and a prior worth ten
              observations, health simply does not move much even at a
              one-minute half-life. The prior is doing so much work that
              the half-life choice is nearly risk-free at this traffic
              volume — which is a real and reportable finding, not a
              failure, but it is NOT the clean tradeoff the test assumed.
Fix:          Added `routing_jitter`, the standard deviation of
              bin-to-bin DIFFERENCES, and switched the test to it. The
              comparison is now a quarter-minute half-life against sixty
              minutes rather than one against sixty, and the docstring
              records that at one minute the gap is about 17% and flips
              sign with epsilon = 0.
Why it worked: Differencing removes the slow drift that both half-lives
              share as the bandit stops exploring, which was most of what
              the level-based metric measured. Widening the gap to a
              quarter-minute shows the mechanism exists without pretending
              it is large at sane settings. The honest version of this
              result — that a prior worth ten observations makes the
              half-life choice nearly risk-free at this traffic volume —
              is now written down rather than tuned away.

### The single-transaction guard compared two different transactions   2026-10-03 — R4

Symptom:      test_one_bad_transaction_cannot_flip_the_routing reports
              the preferred gateway moving from atlas to beacon after one
              injected failure.
Expected:     With a prior worth ten observations, one failure must not
              change which gateway the policy prefers.
Hypotheses:   1) The test is comparing the greedy action for TWO
                 DIFFERENT transactions — it reads `before` from
                 txns[3000] and `after` from txns[3001]. Those have
                 different methods, issuers and amounts, so a different
                 preferred gateway is not evidence of overreaction, it is
                 the policy being contextual.
              2) One failure genuinely moves health enough to flip the
                 argmax — the prior is too weak, or the half-life too
                 short, for the evidence standing at that moment.
              3) The synthetic outcome fed to observe() lands at the
                 probe's own timestamp and interacts badly with the decay
                 anchor, producing an outsized jump.
Test that distinguishes them:
              Evaluate greedy_action on the SAME context immediately
              before and after the single observe(), and print the health
              vector and the evidence mass at both points. If the action
              is unchanged on a fixed context, hypothesis 1. If it flips,
              compare the health delta against the evidence mass to
              separate 2 from 3.

Actual cause: Hypothesis 1 was true of the test but is not the whole
              story, and hypothesis 2 is true in a narrower sense than
              the test assumed.
              On a FIXED context the preferred gateway still moved, atlas
              to cirrus, after a single failure. But the health numbers
              explain why that is not overreaction:
                health   atlas 0.8980  beacon 0.9592  cirrus 0.9237
                evidence atlas 35.84   beacon 52.73   cirrus 29.84
              With 35.84 observations of evidence plus a prior worth 10,
              one failure moves atlas's health from 0.8980 to 0.8787 — a
              change of 0.019, which multiplied by the learned
              coefficient of 0.2427 is a score change of about 0.005.
              The argmax flipped because two arms were already within
              0.005 of each other, which is indifference rather than
              instability: switching between two near-identical arms
              costs essentially nothing.
              So the prior IS doing its job — it bounds the movement —
              and the test asserted the wrong invariant. Argmax stability
              under a near-tie is not a property any sane policy has, nor
              one worth wanting.
Fix:          The test now asserts what the prior actually guarantees: a
              single observation moves health by less than 0.03 on a
              store holding more than 20 observations of evidence. It no
              longer asserts that the preferred gateway is unchanged, and
              it is renamed test_one_bad_transaction_barely_moves_health.
Why it worked: Argmax stability under a near-tie is not a property worth
              wanting — when two arms are within 0.005 of each other,
              swapping them costs nothing, and a policy that refused to
              would be ignoring evidence. Bounded movement is the real
              invariant, it is the one the prior provides, and it fails
              loudly if someone weakens the prior or shortens the
              half-life too far.

### An over-budget gateway kept being marked satisfied          2026-10-04 — R5

Symptom:      test_gateways_over_budget_are_excluded fails. beacon,
              whose configured latency p95 is around 790ms, is still
              chosen with constraint_status = "satisfied" well after the
              latency window should have filled. A record from the other
              failing test shows the smoking gun:
              predicted_p95_ms = 250.0, which is exactly the configured
              PRIOR, not a measured value.
Expected:     Once beacon has been sampled, its measured p95 should keep
              it out of the feasible set under a 420ms budget.
Hypotheses:   1) Readmission cycling. Once excluded, beacon is never
                 routed to, so it produces no new latency samples; after
                 ten minutes its old samples age out of the window; with
                 no samples the store returns the optimistic prior of
                 250ms; beacon becomes feasible again, is tried, is
                 measured slow, and is excluded again. The test forbids
                 the very mechanism that lets a recovered gateway back
                 in.
              2) Epsilon exploration is picking excluded gateways.
                 Impossible by construction — excluded gateways have
                 probability exactly zero — but worth excluding
                 explicitly.
              3) The p95 cache (recomputed at most once per 10 simulated
                 seconds per gateway) serves a stale feasible value after
                 the true p95 has crossed the budget.
Test that distinguishes them:
              For every record where beacon was chosen and marked
              satisfied, print predicted_p95_ms and the number of beacon
              samples in the window at that instant.
                - predicted_p95_ms exactly 250.0 with zero samples
                  -> hypothesis 1
                - a real but out-of-date value with samples present
                  -> hypothesis 3
              Then count how often it happens, which is the quantity that
              actually matters: this is the price of the optimistic
              prior, and it should be reported rather than assumed small.

Actual cause: Hypothesis 1 for 38 of the 104 cases, plus a mechanism
              none of the hypotheses named, which turned out to be the
              larger one.

              Of 104 admissions after warm-up, 38 carried
              predicted_p95_ms = 250.0 exactly — the prior, i.e.
              readmission cycling as hypothesised. The other 66 carried
              REAL measured values under budget (388.9, 328.4, 331.4,
              198.6). beacon is rarely routed to, so its window held very
              few samples, and the nearest-rank p95 of five samples is
              just their maximum, which sits near the 83rd percentile in
              expectation. A feasibility filter built on an empirical
              quantile from few samples is systematically biased toward
              admitting slow gateways.

              THE FIRST FIX MADE IT WORSE, and the measurement is the
              interesting part:
                min_samples   admitted   at prior   measured
                          0      0.50%         32         58
                          5      1.07%        163         29
                         25      2.32%        417          0
                         60      2.21%        397          0
              Distrusting small samples eliminated the biased-quantile
              admissions entirely (58 -> 0) and quadrupled the total,
              because beacon never reaches 25 samples and therefore sits
              at the optimistic prior permanently. There is no setting of
              min_samples that works: trust few samples and the quantile
              is optimistic, distrust them and the prior is.

              A last-trusted-value fallback with a TTL was added next and
              changed nothing (2.32% / 2.20% / 2.31% / 2.03% at TTLs of
              0, 10, 30 and 120 minutes), because the trusted value was
              never set — for the same reason.

              The actual root cause is the WINDOW LENGTH. An excluded
              gateway receives only exploration traffic, about 1.7% at
              epsilon 0.05, so at night rates a ten-minute window holds
              roughly eight of its samples:
                window  min_samples   admitted
                   10m           25      2.31%
                   30m           25      0.94%
                   60m           25      0.56%
                   60m           15      0.33%
                  120m           25      0.29%
Fix:          Latency window lengthened from 10 minutes to 60 and
              min_samples set to 15, giving 0.33%. The last-trusted-value
              fallback was kept: it does no harm, it makes the recovery
              interval an explicit TTL rather than an accident of how
              fast samples age out, and it is what stops the problem
              returning if traffic drops further. The test now asserts a
              bounded, reported rate rather than zero.
Why it worked: Sixty minutes is long enough for a gateway living on
              exploration traffic alone to accumulate a trustworthy
              sample, and latency distributions move far more slowly than
              success rates, so a longer window costs little freshness.
              The remaining 0.33% is the real price of an optimistic cold
              start — the mechanism that lets a recovered gateway back in
              — and is now a number in the measurement rather than an
              assumption.

### The lower-success acceptance test caught a cold-start mistake   2026-10-04 — R5

Symptom:      test_it_sometimes_picks_a_lower_success_gateway_and_is_right_to
              finds its transaction and then fails the justification:
              txn-5-000000004 chose cirrus (true p = 0.920) over atlas
              (true p = 0.940), but the arithmetic makes atlas worth more
              — 520.76 against 510.6.
Expected:     Any lower-success choice the policy makes should be
              justified by a larger margin.
Hypotheses:   1) The search starts at the first transaction of the run.
                 txn-5-000000004 is the FIFTH transaction: the model has
                 essentially no information, every arm scores alike, and
                 the choice is arbitrary. The test is auditing the
                 cold-start period and calling it a policy error.
              2) The objective is genuinely wrong — using the UCB upper
                 bound on the probability inflates less-explored arms
                 non-uniformly, so the policy systematically prefers the
                 arm it knows least about rather than the one worth most.
              3) No such transaction exists to be found. The implied
                 margins here are 555 and 554 paise — nearly identical —
                 because cirrus's 100-paise flat fee cancels its 20bps
                 rate advantage at an amount near 50,000 paise. Around
                 that breakeven, every lower-success choice is a mistake,
                 and the test may be sampling a region where the tradeoff
                 does not exist.
Test that distinguishes them:
              Restrict the search to records after the model has warmed
              (say 10,000 transactions) and require the margin gap to
              exceed a threshold so near-ties are excluded. Then count
              how many qualifying transactions exist and what fraction of
              them the arithmetic justifies.
                - plenty of justified cases once warm -> hypothesis 1
                - still systematically unjustified when warm
                  -> hypothesis 2
                - almost no qualifying transactions at all
                  -> hypothesis 3, and the fee structure is the reason

Actual cause: Hypotheses 1 and 3 both wrong. Restricting the search and
              requiring a real margin gap gives 391/400 justified in the
              first 1,000 transactions and 383/400 after 10,000 — the
              policy is right about 96% of the time, warm or cold.
              The test took the FIRST qualifying record with no margin
              threshold, and near-ties are exactly where estimation error
              flips the answer. The margins confirm why: at an amount of
              50,000 paise, atlas and cirrus both have a margin of 550
              paise, because cirrus's 100-paise flat fee exactly cancels
              its 20bps rate advantage there. Around that breakeven every
              choice is a coin flip, and auditing one proves nothing.
Fix:          The test now requires a margin gap of at least 50 paise
              between the chosen gateway and the rival, examines up to
              300 qualifying transactions, and asserts that more than 90%
              of them are justified by the arithmetic.
Why it worked: The acceptance criterion is about whether the tradeoff
              exists and is exercised correctly, which is a statement
              about the population of such decisions rather than about
              any single one. Excluding near-ties removes the cases where
              there is no tradeoff to get right.

### Every chart was gitignored, so the published README was broken   2026-10-09 — delivery

Symptom:      After pushing to GitHub, the README's eight image links all
              resolved to nothing. `git ls-files results/` returned only
              `.gitkeep` — none of the PNGs or CSVs had ever been
              committed, across 22 commits.
Expected:     The charts are the artifacts the project exists to produce
              and the README links to them directly.
Hypotheses:   1) `.gitignore` excludes `results/*` with only `.gitkeep`
                 re-admitted. Written at R0 on the reasoning that
                 generated output does not belong in version control,
                 which is normally right and was wrong here.
              2) The files were added but never committed.
              3) They exceed a size limit and were silently dropped.
Test that distinguishes them:
              `git check-ignore -v results/r0_gateway_outage.png` names
              the rule and line if it is hypothesis 1; `git status` would
              show them as untracked-but-not-ignored for 2; `du -h` rules
              out 3 (the largest is 250KB).
Actual cause: Hypothesis 1, line 5 of .gitignore. The rule was a sensible
              default applied without checking what depended on it — the
              README had been referencing those paths since R0 and
              nothing ever verified the links resolved.
Fix:          .gitignore now excludes only `results/*.log`, with a
              comment explaining why the artifacts are tracked: they are
              regenerable, but a reader should not have to run anything
              to see the result.
Why it worked: The files are 1.3MB in total, which is nothing for git.
              The broader lesson is the one this project keeps relearning
              in new costumes: a thing nobody checks is a thing nobody
              knows is broken. Twenty-two commits and a full read-through
              of the README never caught it, because the README was only
              ever read locally, where the paths resolve.
