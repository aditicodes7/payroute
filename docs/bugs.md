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
