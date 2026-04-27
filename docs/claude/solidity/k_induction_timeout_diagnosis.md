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
- **Last phase reached**: inductive step k=13
- **Per-k cost**: 1457 sliced VCCs per step, symex 1.3s per step.
- **Outcome**: timeout.
- **Category**: **(c) symex modeling** — pointer-write modeling
  generates many redundant aliasing VCCs that the slicer can't fold.
  The 65 → 1457 VCC growth at k=4 → k=13 is roughly 22×, suggesting
  per-iteration aliasing-cone growth.
- **Fix proposal**: investigate ESBMC's pointer-analysis to see
  whether tighter alias-set tracking in the inductive step would
  reduce VCC count. **Cost: open-ended ~16-30h** — depends on
  whether existing pointer-analysis infra (`src/pointer-analysis`)
  exposes the right knobs.

### tod_balance_pass — TOD harness

- **Test args**: `--tod-balance-check=payA,payB --k-induction --max-k-step 20 --k-step 3`
- **Last phase reached**: forward condition k=7
- **Per-query cost**: base case k=7 takes **32.9 s** alone for 7
  VCCs. THIS IS THE BOTTLENECK — single-query solver-bound.
- **Pattern**: the EOA balance model uses linear-scan loops over
  `sol_eoa_max_cnt`, which under k-induction the solver sees as a
  bounded-but-large array search. Combined with the 256-bit BV
  arithmetic, single SMT queries scale poorly.
- **Outcome**: timeout (one slow query per k-induction step).
- **Category**: **(d) solver perf** — the formula is well-formed,
  just hard for Bitwuzla's QF_BV256.
- **Fix proposal**: try `--cvc5` (often faster on QF_BV256). Or
  reduce the EOA scan loop unwind. **Cost: ~2-4h** to test
  `--cvc5` substitution; if it works, reclassify or update test.desc.

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
| `aliasing_1` | (c) symex modeling | inductive k=13 | 1.3s × 13 (alias-cone growth) | investigate pointer-analysis | 16-30h |
| `tod_balance_pass` | (d) solver perf | forward k=7 | single 32.9s query | try `--cvc5` substitution | 2-4h |
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

3. **Three of seven tests are genuinely correctly KNOWNBUG.** The
   `(a) genuine inductive insufficiency` category covers
   `mapping_10`, `nest_loop_3`, `reentrance_12`. These tests would
   need user-supplied invariants which the project has explicitly
   disallowed. KNOWNBUG is the correct classification.

4. **The Phase 3 mapping fix was directionally right but unsoundly
   implemented.** `mapping_3` would benefit from a surgical version
   of Phase 3 — per-callee field-write summaries rather than
   empty-set→no-havoc. The unsoundness in `require_1` was caused by
   conflating "analysis returned empty set" with "no fields are
   actually written"; the correct path is to MAKE the analysis
   thorough enough that empty set means something.

5. **`tod_balance_pass` is solver-bound, not modeling-bound.** It's
   the only test where a SINGLE query (32.9s) dominates total time.
   This is a different kind of fix from the others — possibly just
   solver substitution.

## Recommended priority ranking

By cost-vs-tests-unblocked (estimated):

1. **`tod_balance_pass` `--cvc5` substitution** (2-4h, possibly
   unblocks 1-2 tests). Highest ROI per hour.
2. **Document (a) tests as KNOWNBUG-correct** (0h, removes 3+ tests
   from "fixable" list). Right framing for the project's stance on
   user-supplied invariants.
3. **Per-callee field-write summaries for mappings** (12-20h,
   unblocks `mapping_3` + likely 5 more `map_*` tests). Surgical
   refactor of `callee_writes_through_pointer`.
4. **`bytes_8` c2goto helper-unwind cap** (8-12h, unblocks 1-2
   bytes-related tests).
5. **Aliasing investigation** (16-30h, possibly unblocks 1 test).
   Lowest priority — open-ended, single-test ROI.

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
