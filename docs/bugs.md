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
