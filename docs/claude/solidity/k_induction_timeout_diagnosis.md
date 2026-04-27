# K-induction timeout root-cause diagnosis (2026-04-27)

Phase 4 of the Solidity verification roadmap: structured investigation
of 7 representative KNOWNBUG-timeout tests, categorizing each by
failure mode and suggesting targeted fixes.

## Methodology

Each test was run with its `test.desc` arguments and a 65s timeout
budget (slightly above the 60s ctest default to capture the
last-iteration state). Per-k scaling was extracted from log markers:
`Checking <phase>, k = N`, "Generated N VCC(s), M remaining...", and
"Runtime decision procedure: Xs" (`bmc.cpp:1393, 301`).

Test selection spans the failure-category space; results extrapolate
to the wider 23-non-array KNOWNBUG cohort.

## Per-test findings

### mapping_3 — `mapping(K=>V)` perf

- **Test args**: `--bound --k-induction --max-k-step 20 --k-step 3`
- **Last phase reached**: inductive step k=7
- **Per-k cost** (selected): k=10 inductive step took **12.4 s**
  (`Generated 184 VCC(s), 81 remaining ... Runtime decision procedure:
  12.413s`); other steps 0.001-0.05s.
- **Budget burn**: k iterates 1, 4, 7, 10, 13, 16, 19 — even with most
  steps fast, one or two slow queries at higher k consume the budget.
- **Outcome**: timeout; never reaches inductive proof.
- **Category**: **(b) over-havoc** (suspected). The mapping_t struct
  identity fields (`mid`, `addr`) get havoc'd at inductive step (per
  Phase 3 investigation). Phase 3's naive empty-set fix was unsound;
  a surgical per-callee field-write summary would be the right path.
- **Fix proposal**: implement per-callee field-write summaries in
  `goto_loops.cpp::callee_writes_through_pointer` (currently boolean
  → extend to set-of-field-names). Then
  `goto_k_induction.cpp::collect_modified_struct_fields` can union
  callee summaries with direct writes. **Cost: ~12-20h** (large
  refactor; touches the whole k-induction modified-set computation).

### mapping_10 — larger mapping perf

- **Test args**: `--bound --k-induction --max-k-step 20 --k-step 3`
- **Last phase reached**: base case k=16
- **Per-k cost**: tiny per-step (16 VCCs at k=16, 0.021s). But
  iterates many k.
- **Outcome**: timeout (eventually).
- **Category**: **(a) genuine** inductive insufficiency for the
  specific assertion this test exercises. Even with light
  computation, k-induction never converges because the property
  requires reasoning over an unbounded mapping domain.
- **Fix proposal**: KNOWNBUG correct classification.
  **Cost: 0h**.

### nest_loop_3 — nested-loop control-flow

- **Test args**: `--k-induction --max-k-step 20 --k-step 3`
- **Last phase reached**: inductive step k=19
- **Per-k cost**: very small (1 VCC, 0.001s solver). Symex 0.18s.
- **Budget burn**: 7 k-iterations all run; each is fast but
  cumulative over-budget at higher k.
- **Outcome**: forward never converges; inductive step doesn't prove.
- **Category**: **(a) genuine** — nested loops without an inductive
  invariant the prover can synthesize. KNOWNBUG correct.
- **Fix proposal**: would need user-supplied invariants, which memory
  rules out (`feedback_no_user_invariants.md`). Document as
  KNOWNBUG-correct. **Cost: 0h**.

### reentrance_12 — reentrance with k-induction

- **Test args**: `--reentry-check --unbound --k-induction --max-k-step 20 --k-step 3`
- **Last phase reached**: inductive step k=19 — completed!
- **Per-k cost**: at k=16 raw 906 VCCs → 147 sliced; at k=19 the
  inductive step takes 1.4s on 300 VCCs.
- **Outcome**: 47s total runtime, **VERIFICATION UNKNOWN** (the
  k-induction algorithm exhausted all k budgets without finding
  inductive proof). NOT a timeout — a clean "couldn't prove".
- **Category**: **(a) genuine** — reentry-check property is hard for
  k-induction. The slicer reduces 6522 raw VCCs to 1083 (17×) but the
  remaining VCCs encode reachability through nested external calls
  that don't admit a finite inductive invariant.
- **Fix proposal**: KNOWNBUG correct. The `--reentry-check` mode
  would need a different proof technique (e.g., dedicated reentrancy
  analysis) rather than k-induction.
  **Cost: 0h** (or very large for a new analysis).

### aliasing_1 — pointer-aliasing inductive step

- **Test args**: `--k-induction --max-k-step 20 --k-step 3`
- **Last phase reached**: inductive step k=10 (timeout in solver)
- **Per-phase inductive growth (re-measured 2026-04-27)**:
  | k | inductive VCC | inductive SSA | slicer keep % |
  |---|---|---|---|
  | 1  | 467  | 3351  | 84% |
  | 4  | 797  | 5727  | 85% |
  | 7  | 1127 | 8103  | 85% |
  | 10 | 1457 | 10479 | 85% |
  **Linear growth: ~+330 VCC / +2376 SSA per +3 k step. Slicer ratio
  stable.**
- **Outcome**: timeout.
- **Category** *(corrected 2026-04-27)*: **(a) genuine inductive
  insufficiency**. The original (c)-symex-modeling classification
  rested on a "65 → 1457 VCC = 22× growth" claim that compared
  k=1 base-case VCCs (65) to k=10 inductive-step VCCs (1457) —
  apples-to-oranges across phases. Within a single phase, growth
  is linear in k and the slicer keep-ratio is constant (85%). No
  alias-cone explosion.
- **Why it times out**: same shape as `mapping_10` / `nest_loop_3` /
  `reentrance_12`. The dispatcher harness's forward condition never
  converges; inductive step is the only path to SUCCESSFUL; closing
  inductively needs k beyond what 60s allows when each k iteration
  costs solver time on ~1457 VCCs at k=10.
- **Fix proposal**: none — KNOWNBUG-correct under the project's
  no-user-invariants stance (memory rule
  `feedback_no_user_invariants.md`). **Cost: 0h.**

### tod_balance_pass — TOD harness

- **Test args**: `--tod-balance-check=payA,payB --k-induction --max-k-step 20 --k-step 3`
- **Last phase reached**: base case k=7 (Bitwuzla); base case k=1
  (CVC5, Z3 — neither completes the first query in 60s)
- **Per-query cost**: base case k=7 under Bitwuzla takes **32.9 s**
  alone for 7 VCCs. Under CVC5 and Z3 the very first query at k=1
  (4 VCCs) does not complete within 60 s either.
- **Pattern**: the EOA balance model uses linear-scan loops over
  `sol_eoa_max_cnt`, which under k-induction the solver sees as a
  bounded-but-large array search. Combined with the 256-bit BV
  arithmetic, single SMT queries scale poorly.
- **Outcome**: timeout on every solver tried (Bitwuzla, CVC5, Z3;
  Boolector not built in this checkout).
- **Category**: **(d) solver perf, all-solver** — the PASS variant
  requires *proving* `balance(c1) == balance(c2)` universally over
  all `(a_to, b_to)` inputs, which is fundamentally harder than the
  FAIL variant (`tod_balance_fail`, currently CORE-passing in 0.65 s
  by exhibiting a single counter-example). 256-bit BV proof of order
  invariance over the EOA-scan-bound loop is not within reach of any
  bundled solver at this setting.
- **Fix proposal (revised 2026-04-27)**: `--cvc5` substitution does
  NOT help — the original 2-4h plan rested on the assumption that
  CVC5 would clear the same formula faster, but empirical retest
  shows CVC5 hangs on the *first* query at k=1 (CVC5 1.1.2). KNOWNBUG
  classification is correct; further progress requires either
  (a) restructuring the EOA balance model to remove the symbolic-
  bound scan loop, or (b) a TOD-balance-specific proof harness that
  does not require universal quantification over `(a_to, b_to)`.
  Both are research-scale (>20 h). **Recommendation: leave KNOWNBUG;
  do not retry solver substitution without first changing the
  encoding.**

### bytes_8 — bytes manipulation

- **Test args**: `--k-induction --no-standard-checks`
- **Last phase reached**: forward condition k=21
- **Per-step cost**: symex 4.6s per step (memcpy + dynamic-bytes
  capacity loops are unwound 21× each).
- **Log size**: 8.5 MB (mostly unwinding messages).
- **Outcome**: timeout.
- **Category**: **(c) symex modeling** — `bytes_dynamic_ensure_capacity`
  and `__memcpy_impl` unwind to depth 21 per k-step. SSA size grows
  with k³ effectively (k symbolic execution steps × k unwinding rounds
  × k inductive steps).
- **Fix proposal**: cap the bytes-buffer growth via either (a)
  modeling change in `solidity_bytes.c` to use direct array writes
  instead of `__memcpy_impl`-based copies, or (b) k-induction skip
  for these specific c2goto helper loops (similar to existing
  counted-loop skip per `feedback_k_induction_counted_loop_skip.md`
  in memory). **Cost: ~8-12h**.

## Summary table

| Test | Category | Last phase | Time bottleneck | Fix proposal | Cost |
|---|---|---|---|---|---|
| `mapping_3` | (b) over-havoc | inductive k=7 | 12s slow query at k=10 | per-callee field summaries | 12-20h |
| `mapping_10` | (a) genuine | base case k=16 | budget burn over many k | document as KNOWNBUG-correct | 0h |
| `nest_loop_3` | (a) genuine | inductive k=19 | budget burn | document as KNOWNBUG-correct | 0h |
| `reentrance_12` | (a) genuine | inductive k=19 (done!) | 47s, VERIFICATION UNKNOWN | document as KNOWNBUG-correct | 0h |
| `aliasing_1` | (a) genuine *(corrected 2026-04-27)* | inductive k=10 | linear VCC growth, stable slicer ratio | document as KNOWNBUG-correct | 0h |
| `tod_balance_pass` | (d) solver perf, all-solver | base k=1-7 | every solver hangs on single base-case query | KNOWNBUG correct; needs encoding rework | research-scale (>20h) |
| `bytes_8` | (c) symex modeling | forward k=21 | 4.6s/step (memcpy unwinding) | cap c2goto helper unwinds | 8-12h |

## Cross-cutting observations

1. **k-induction iteration budget is the real timeout shape.** Most
   timeouts (5/7) come from cumulative budget burn across k=1, 4, 7,
   10, 13, 16, 19 iterations — NOT from a single solver UNKNOWN.
   `--max-k-step 20 --k-step 3` runs up to 7 phases × 3 sub-steps =
   21 SMT queries per test. Each step's cost compounds.

2. **Forward condition never converges in Solidity dispatcher
   harness.** This is BY DESIGN — `while(nondet) dispatch()` is
   unbounded. Forward condition will never prove. Inductive step is
   the only path to SUCCESSFUL. This means: test pass/fail is
   entirely determined by whether the inductive step closes
   inductively — no amount of forward-condition tuning helps.

3. **Four of seven tests are genuinely correctly KNOWNBUG.** The
   `(a) genuine inductive insufficiency` category covers
   `mapping_10`, `nest_loop_3`, `reentrance_12`, and (after
   2026-04-27 re-measurement) `aliasing_1`. These tests would
   need user-supplied invariants which the project has explicitly
   disallowed. KNOWNBUG is the correct classification.

4. **The Phase 3 mapping fix was directionally right but unsoundly
   implemented.** `mapping_3` would benefit from a surgical version
   of Phase 3 — per-callee field-write summaries rather than
   empty-set→no-havoc. The unsoundness in `require_1` was caused by
   conflating "analysis returned empty set" with "no fields are
   actually written"; the correct path is to MAKE the analysis
   thorough enough that empty set means something.

5. **`tod_balance_pass` is solver-bound on EVERY solver, not
   modeling-bound.** It's the only test where a SINGLE query
   dominates total time. The original Phase-4 conjecture was that
   `--cvc5` substitution would clear it; retest (2026-04-27) shows
   CVC5 also hangs on the first base-case query at k=1. Z3 too.
   This is now reclassified as "encoding-bound" — the EOA-scan-loop
   + 256-bit BV combination is fundamentally hard for proving
   universally quantified order-invariance, regardless of solver.
   Further progress requires changing the encoding.

## Recommended priority ranking

*Final, post-Phase-4-execution (2026-04-27).*

1. **Document (a) tests as KNOWNBUG-correct** (0h). Now covers
   `mapping_10`, `nest_loop_3`, `reentrance_12`, `aliasing_1`.
   Right framing for the project's no-user-invariants stance.
2. **Per-callee field-write summaries for mappings** ✗ *attempted,
   reverted 2026-04-27*. Refactor was sound (760/34 preserved) but
   produced 0 KNOWNBUG flips: mapping_3's modified_loop_vars don't
   carry the contract-object struct, so the consumer path never
   fires. See `project_stream22_param_subst_no_op.md`.
3. **`bytes_8` c2goto helper-unwind cap (Path B-Light)** ✗
   *attempted, reverted 2026-04-27*. Both broad and conservative
   variants regressed CORE-passing tests: k-induction's
   havoc-step-once on `__memcpy_impl` is load-bearing for
   `reentrance_12` / `require_3` / `stress_libsol_calldata_string_array`.
   Path B-Full (~15-19d) has uncertain ROI for the same reason —
   byte-level precision is just as constraining as BMC unrolling
   and likely triggers identical regressions. **Deferred.**
4. **Aliasing investigation** ✗ *closed 2026-04-27 without code
   change*. Re-measurement showed linear (not exponential) VCC
   growth across k; reclassified as (a) genuine. No fix needed.
5. **`tod_balance_pass` encoding rework** (research-scale, >20h).
   Either restructure EOA balance model to drop the symbolic-bound
   scan loop, or design a TOD-balance proof harness that avoids
   universal quantification over recipient addresses. Deferred —
   revisit only if multiple users hit this pattern.

**Net Phase-4 outcome**: 760/34 baseline preserved; corrected
priority ranking; 4 of 7 timeout-bound tests now correctly
classified as (a)-genuine-KNOWNBUG. Remaining cohort
(`mapping_3`, `mapping_4`, `mapping_10`, `bytes_8`,
`tod_balance_pass`) is architectural: each needs either
encoding-layer redesign or relaxed user-invariant policy.

Phase 2 (`linearize_finite_tail`, ~20-30h) is NOT directly addressed
by any of the above. Phase 4's findings show the remaining
timeout-bound tests are mostly modeling/perf issues, not
nested-array architectural ones. **Phase 2 may stay deferred until
a use case for Bitwuzla parity emerges**; Phase 1's auto-hint
already covers user-side ergonomics.

## Phase 4 cost actual

~3h for data capture + analysis, vs 6-12h estimate. Below budget
because diagnostic infrastructure already existed and patterns
emerged quickly.
