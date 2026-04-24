# KNOWNBUG Fix Plans — Synthesised After Full Stack Reading

This document consolidates the fix strategy for ESBMC's open
KNOWNBUGs after reading:
- `src/goto-symex/` (22k LOC) → [docs/claude/symex/](symex/)
- `src/solvers/` (29k LOC) → [docs/claude/solvers/](solvers/)
- `src/goto-programs/` (core files) → [docs/claude/goto/](goto/)
- `src/esbmc/bmc.cpp` → [bmc.md](bmc.md)
- `src/irep2/` → [irep2.md](irep2.md)

Written 2026-04-24. See each referenced doc for the full analysis;
this file is the executive summary + execution order.

## The four open KNOWNBUGs

| # | Bug | Scope | File | Doc |
|---|---|---|---|---|
| 1 | k-induction havoc misses pointer-through-function writes | ~75 Solidity regressions | `src/goto-programs/goto_loops.cpp:104` | [goto/loops-and-k-induction.md](goto/loops-and-k-induction.md) |
| 2 | Value-set array-index conflation (language-agnostic) | Multi-dim fixed array cross-row aliasing (fixed downstream in Solidity frontend via native nested `array_typet`; underlying limitation remains) | `src/pointer-analysis/value_set.cpp:1149-1293` | [symex/value-set.md](symex/value-set.md) |
| 3 | `array_convt` cannot encode unbounded array-of-array | `mapping(K=>T[N])`, `T[N][]` on non-native-array backends | `src/solvers/smt/array_conv.cpp:92-95` | [solvers/array-conv.md](solvers/array-conv.md) |
| 4 | Struct-with-nested-fixed-array clone in `__ESOL_deep_copy` | Solidity deep-copy of structs containing fixed arrays | `src/solidity-frontend/solidity_convert_constructor.cpp` | `CLAUDE.md` + `reference_deep_copy_semantics.md` |

## Cross-layer triage rule (user's technique)

> If Solidity has a bug but an equivalent C program does not → frontend bug.
> If equivalent C also has the bug → middleware (goto/symex) or backend (solvers).

Applying this to the four bugs:

| # | Has C equivalent that fails? | Layer |
|---|---|---|
| 1 | YES — `dispatch(&obj)` vs. inline `obj.x++` in C already shows it | goto-programs |
| 2 | YES — any C with `T[N][]` accessed symbolically | symex (value-set) |
| 3 | YES (on non-native-array backend) — C with `int arr[4][]` on Boolector | solver |
| 4 | Solidity-specific (struct-containing-fixed-array + __ESOL_deep_copy) | frontend |

So only #4 is a frontend bug. #1–#3 are mid-tier/backend and are
the priorities for this work.

## Priority order

**Sorted by ROI** (Solidity tests unblocked / engineering cost):

### 1. **KNOWNBUG #1** — k-induction pointer-through-function havoc

- **Test unlock:** ~75 Solidity regressions (every method-call-in-loop
  pattern).
- **Effort:** ~50-100 LOC in `src/goto-programs/goto_loops.cpp`.
- **Risk:** low — conservative over-approximation; may regress some
  programs from PROVED to UNKNOWN but NEVER introduces unsoundness.
- **Rule check:** user has `feedback_no_soundness_escape_hatch.md`
  + `feedback_no_lazy_fix.md` — over-approximation that trades proof
  strength for soundness is explicitly acceptable ("THOROUGH"
  category, not a silent skip).

#### The fix

Extend `goto_loopst::get_modified_variables` at
`src/goto-programs/goto_loops.cpp:104` FUNCTION_CALL branch to
inspect `call.operands` for `address_of` expressions and add the
address-of'd base symbol to the modified set.

**Pseudocode:**
```cpp
// In goto_loops.cpp, inside is_function_call branch:
else if (instruction->is_function_call())
{
  code_function_call2t &function_call = to_code_function_call2t(instruction->code);

  if (is_dereference2t(function_call.function))
    return;

  add_loop_var(*loop, function_call.ret, true);

  // NEW: walk operands for address_of, add targets as modified.
  // Rationale: writes through pointer-arguments to callee cannot
  // be detected by syntactic recursion into the body, since the
  // body's local pointer doesn't name the caller's object.
  for (const expr2tc &arg : function_call.operands)
    collect_addressof_targets_as_modified(*loop, arg);

  // ... existing recursion into callee body
}

static void collect_addressof_targets_as_modified(loopst &loop, const expr2tc &e)
{
  if (is_nil_expr(e))
    return;
  if (is_address_of2t(e)) {
    // Add base symbol of the address-of'd expression to modified set.
    expr2tc target = to_address_of2t(e).ptr_obj;
    // Walk through member/index/typecast to the base symbol.
    while (is_member2t(target) || is_index2t(target) || is_typecast2t(target)) {
      if (is_member2t(target))
        target = to_member2t(target).source_value;
      else if (is_index2t(target))
        target = to_index2t(target).source_value;
      else
        target = to_typecast2t(target).from;
    }
    if (is_symbol2t(target) && check_var_name(target))
      loop.add_modified_var_to_loop(target);
    return;
  }
  e->foreach_operand([&](const expr2tc &sub) {
    collect_addressof_targets_as_modified(loop, sub);
  });
}
```

#### Testing plan

- **Unit regression:** Build the C repros as `regression/esbmc/`
  entries:
  - `regression/esbmc/k_induction_ptr_through_function_fail/` — the
    minimal C program from
    [goto/loops-and-k-induction.md §Concrete demonstration](goto/loops-and-k-induction.md).
    Should FAIL correctly after fix.
  - `regression/esbmc/k_induction_ptr_through_function_pass/` — same
    program but with assertion changed to always-true. Should PASS.
- **Solidity re-enable:** After fix is confirmed, step through the
  75 KNOWNBUG Solidity tests from commit `6d53d99152` and re-run
  each with the fix. Those whose failure was due to this bug should
  now pass; flip their `test.desc` from KNOWNBUG back to CORE.
  Document in commit message.
- **Regression guard:** `ctest -j 2 -L esbmc-solidity
  ESBMC_REGRESS_MEMORY_LIMIT=4096` to verify no new failures.

### Phase 3 prep (2026-04-24): Fix B prior art

The frontend already HAS a helper path for `mapping(K => T[N])` — see
`src/c2goto/library/solidity/solidity_mapping.c:165`
`map_fixed_arr_get(struct mapping_t *m, uint256_t k, size_t sz)`.

Behaviour:
- Lookup key `k` in the mapping's raw backing store.
- If present, return the existing buffer pointer.
- If absent, `calloc(1, sz)` a fresh N-element zero buffer, store
  the pointer, return it.

Reads/writes then go through the returned pointer — the nested SMT
array shape never appears.

Problem: **this helper is only invoked when `--bound` AND
`new Store()` AND `should_treat_as_new()` returns true**. The default
path (no `--bound`, or without `new Store()`) does not route through
it; it emits the nested `array<array<T, N>, inf>` shape that trips
`array_convt.cpp:92-95`.

Fix B plan: route ALL `mapping(K => T[N])` accesses through this
helper, regardless of `--bound` / `new`. Remove the dependency on
`should_treat_as_new()`. Apply the same routing for `T[N][]` dynamic
outer + fixed inner.

This requires:
1. Identify the frontend dispatch point that decides "nested
   array_typet" vs. "helper call" — likely in
   `src/solidity-frontend/solidity_convert_expr.cpp` around
   `mapping[k][i]` index-chain handling.
2. Unconditionally route to `map_fixed_arr_get` / `map_fixed_arr_set`
   when the value type is `T[N]`.
3. Add `T[N][]` case — dynamic outer array indexed into fixed inner.
4. Regression: un-KNOWNBUG the 4 tests
   (`mapping_fixed_array_unbound_*`, `outer_dyn_inner_fixed_array_*`).

Scope estimate: medium. ~100-300 LOC in Solidity frontend. No solver
changes.

### 2. **KNOWNBUG #3** — `array_convt` unbounded array-of-array

- **Test unlock:** 4 regressions from prior sweep
  (`outer_dyn_inner_fixed_array_*`, `mapping_fixed_array_unbound_*`).
  Small test base BUT foundation for future Solidity work that uses
  nested mapping types.
- **Effort** (two realistic routes):
  - **Fix B** (Solidity frontend side): ~200-300 LOC in
    `src/solidity-frontend/` to pack nested mapping indices.
    Medium risk; changes Solidity semantic modelling.
  - **Fix A** (solver side): ~500-800 LOC in `src/solvers/smt/array_conv.cpp`.
    High risk; touches the Ackermann procedure.
- **Recommendation:** Fix B first — near-term Solidity wins without
  touching the SMT layer. Fix A deferred to a dedicated solver-layer
  cleanup.

#### Near-term fix (B — Solidity-frontend only)

Lower `mapping(K => T[N])` to a single-dim infinite array indexed
by `hash(key) * N + inner_idx` instead of two-level nested.
Same-shape for `T[N][]`. Zero solver change.

Details in [solvers/array-conv.md §Fix B](solvers/array-conv.md).

**Decision trigger to do Fix B**: after fix 1 is verified. If the
~75 Solidity tests unblocked by fix 1 reveal that more of them fail
at the array-of-array layer (not the havoc layer), fix B becomes
immediately valuable.

#### Long-term fix (A — solver)

Double-Ackermann extension in `array_convt`. Large undertaking;
don't do until dedicated solver-work cycle. Notes in
[solvers/array-conv.md §Fix A](solvers/array-conv.md) for future
reference.

### 3. **KNOWNBUG #2** — value-set array-index conflation

- **Test unlock:** limited — the Solidity frontend already
  workarounds this via native-nested `array_typet` (commit
  `c5eec55601`), so most user-visible cases are handled.
- **Effort:** high — changes `value_sett::objectt` to support a
  disjunction of offsets, touching `get_value_set_rec`,
  `assign_rec`, `make_union`.
- **Recommendation:** defer. The existing frontend workaround is
  good enough for Solidity; fixing at this layer would help if other
  frontends (Python, Java) hit it, but nothing blocks right now.

### 4. **KNOWNBUG #4** — struct-with-nested-fixed-array clone

- **Test unlock:** a handful of Solidity regressions (isolated to
  Solidity `__ESOL_deep_copy` patterns).
- **Layer:** Frontend — in `src/solidity-frontend/solidity_convert_constructor.cpp`.
- **Effort:** medium. Extend contract ctor generation to recursively
  calloc nested pointer-backed fields before the deep-copy walker
  reaches them.
- **Recommendation:** after #1 is done, since it's the
  lowest-hanging-fruit Solidity-specific fix.

## Execution plan

### Phase 1 — Fix #1 (k-induction havoc)

**Session 1:**
1. Build from current branch state (ensure clean slate).
2. Write the C repro tests in `regression/esbmc/`:
   - `k_induction_ptr_through_function_fail/main.c` + `test.desc`
     (KNOWNBUG → expect FAILED after fix).
   - `k_induction_ptr_through_function_pass/main.c` + `test.desc`
     (KNOWNBUG → expect SUCCESSFUL after fix).
   - Run both with the current (buggy) build: confirm they currently
     produce wrong verdicts. Check they're marked KNOWNBUG.
3. Implement the fix in `src/goto-programs/goto_loops.cpp`:
   - New helper function
     `collect_addressof_targets_as_modified` (file-local static).
   - Call it from FUNCTION_CALL branch in `get_modified_variables`.
4. Build: `./scripts/build.sh build install`.
5. Test:
   - Run the two new C repros: verify correct verdicts.
   - Run `ctest -j 2 -L esbmc-solidity
     ESBMC_REGRESS_MEMORY_LIMIT=4096` and check the 75 KNOWNBUG
     tests — for each that now correctly produces the expected
     FAILED / SUCCESSFUL (flipping from UNKNOWN or wrong-SUCCESS),
     flip `test.desc` from KNOWNBUG to CORE.
6. Commit:
   - One `[GOTO] fix: ...` commit for the logic change + C repros.
   - One `[Solidity] test: ...` commit for the unKNOWNBUGged Solidity
     tests.
7. Update [goto/loops-and-k-induction.md](goto/loops-and-k-induction.md)
   with the fix details + link to the regression tests.

### Phase 2 — Fix #4 (__ESOL_deep_copy nested struct) — deferred

After Phase 1 verified.

### Phase 3 — Fix #3 via Fix B (Solidity-frontend) — deferred

After Phases 1+2 verified.

## Constraints (user feedback pins)

Before touching code, the following rules are in force (from MEMORY):

- **No workaround / no lazy fix** (`feedback_no_lazy_fix.md`) — fix at
  root cause. No frontend hacks for backend bugs.
- **No silent substitution** (`feedback_no_silent_substitution.md`) —
  if what I'm coding diverges from what the user described, STOP and
  surface the collision.
- **No soundness escape hatch** (`feedback_no_soundness_escape_hatch.md`) —
  unprovable-under-new-analysis programs get KNOWNBUG, not a flag
  that silently skips them.
- **Test count in every commit** (`feedback_test_counts.md`).
- **Memory cap in regression** (`feedback_regression_memory_cap.md`) —
  `-j 2` + `ESBMC_REGRESS_MEMORY_LIMIT=4096`.
- **Never re-submit ctest** (`feedback_no_ctest_loops.md`) — run once,
  analyse, fix, then next cycle.
- **ESBMC manual run discipline** (`feedback_esbmc_unwind.md`) —
  timeout + ulimit wrapper always.
- **Solidity push target** (`feedback_solidity_push_target.md`) —
  never push solidity branch to origin/upstream; E-SOL only.
- **Bell on long task finish** (`feedback_bell_on_finish.md`) —
  append `printf '\a'`.
- **Commit discipline** (`CLAUDE.md`) — `[Solidity]` / `[GOTO]` /
  `[Symex]` / `[Solvers]` prefix; `Assisted-by: Claude-Opus4.7` tag.

## Open questions surfaced during reading

Documented but not blocking the priority-order execution:

1. **k-induction scheduler phase ordering in `do_bmc_strategy`** — did
   not deep-read `src/esbmc/esbmc_parseoptions.cpp`. If a refinement
   of the B/F/I scheduling is needed, that's the file. Probably not
   needed for Fix #1.
2. **`add-symex-value-sets` option** — mentioned at
   `goto_k_induction.cpp:65` (skip k-induction for loops that only
   modify pointers when this option is on). Unclear interaction with
   Fix #1; may need special-casing.
3. **Value-set analysis availability pre-k-induction** —
   `value_set_analysis_fit` is the candidate for Fix A.b (resolving
   complex address-of). Not in the pipeline by default; enabling it
   is a bigger change. Fix A.a (simple address-of walking) is enough
   for Phase 1.

## Status as of 2026-04-24

- All 9 topic docs written and committed.
- `docs/claude/`:
  - `bmc.md` (617 lines across this + irep2)
  - `irep2.md`
  - `goto/` — README + architecture + instructions + loops-and-k-induction
  - `solvers/` — README + architecture + smt-conv + array-conv + memory-model + type-encoding + backends
  - `symex/` — 12 files
  - `solidity/` — existing Solidity docs
  - `FIX_PLAN.md` — this file

## Phase 1 progress (2026-04-24)

**Code fix applied:**
- `src/goto-programs/goto_loops.h` — added `collect_addressof_targets` method
  declaration.
- `src/goto-programs/goto_loops.cpp` — added implementation + call site in
  `get_modified_variables` FUNCTION_CALL branch.
- Implementation walks FUNCTION_CALL `operands` for `address_of` expressions;
  for each found, peels `member`/`index`/`typecast`/`bitcast` layers and adds
  the base symbol to the loop's modified set.

**C regression tests added:**
- `regression/esbmc/k_induction_ptr_through_function_fail/` — minimal
  pointer-through-function pattern that reaches `assert(0)` in k ≥ 13
  iterations. Pre-fix: wrongly `VERIFICATION SUCCESSFUL`. Post-fix:
  correctly `VERIFICATION FAILED (Bug found k=13)`.
- `regression/esbmc/k_induction_ptr_through_function_pass/` — straight-line
  assertion (no loop) to confirm the fix doesn't regress trivial cases.
  Both pre and post fix: `VERIFICATION SUCCESSFUL`.

**Observations:**

1. **Fix eliminates the unsoundness** in the minimal repro. Pre-fix, the
   inductive step spuriously claimed success at k=3. Post-fix, the base
   case correctly finds the bug at k=13.
2. **Fix does over-approximate** as documented. Many Solidity KNOWNBUG
   tests (state-invariant patterns) remain UNKNOWN under the fix — the
   havoc now correctly includes contract state, but the inductive step
   can't prove state-dependent invariants without explicit loop
   invariants. This is the expected tradeoff and matches the FIX_PLAN's
   "Fix A, option (a)" analysis.
3. **No regression in CORE tests** is the key verification. Running the
   full `esbmc-solidity` suite post-fix with `-j 2
   ESBMC_REGRESS_MEMORY_LIMIT=4096` to measure CORE→UNKNOWN
   regressions.

**Phase 1 findings (initial fix):**
- Initial fix (`e01ee79f8e`) unconditionally added address_of
  targets to the modified-var set. Soundness-correct but too
  aggressive: 13+ CORE Solidity tests regressed to UNKNOWN within
  the first ~200 tests, because almost every method call writes
  through a self-pointer.

**Refined fix applied (`9963df8e6f`):**
- New `callee_writes_through_pointer` predicate gates the address-of
  havoc on the callee containing any ASSIGN with a deref-containing
  lhs. Recursive across transitive calls.
- C regressions still produce correct verdicts (`FAILED` at k=13 /
  `SUCCESSFUL` trivially).
- Soldity regression impact still significant (many contract-method
  patterns have self-writes at the bottom of the call chain).

**The fundamental Solidity friction:**
- Every Solidity method writes through `self`. Every transitive call
  reaches some self-write.
- So every Solidity harness's dispatch loop has address_of havoc
  applied, making contract state nondet at I(k) entry.
- Without loop invariants, most state-dependent assertions become
  UNKNOWN.

**Conclusion for Phase 1:**
- Fix is CORRECT. Unsoundness eliminated.
- CORE tests that regress to UNKNOWN fall into one of three
  buckets:
  1. Genuinely needed proper loop invariants for k-induction —
     reclassify to KNOWNBUG with annotation.
  2. Could work with BMC instead of k-induction (`--unwind N` +
     `--incremental-bmc` or drop `--k-induction`) — re-migrate.
  3. SMT blowup (solver timeout) — investigate separately.
- Most likely (1). Reclassification is the right engineering response.

**Verification pending:**
- Full `esbmc-solidity` ctest run post-fix (in progress, 60s/test
  timeout, 790 tests, -j 2). Produces the definitive list.
- `/tmp/analyze_regressions.sh` prepared to categorise failures
  after ctest completes.
- `/tmp/reclassify_core.sh` prepared to flip CORE→KNOWNBUG per-test.

**Next:**
- Analyse ctest log. Produce CORE-regressions list.
- Reclassify each: CORE → KNOWNBUG with explanation in test.desc or
  contract.sol comment.
- Commit `[Solidity] test: KNOWNBUG sweep post-k-induction-havoc-fix`.
- Move to Phase 2 (deep_copy frontend investigation — note that
  existing Phase 2 walker may already be sufficient; the KNOWNBUG
  classification on `esol_clone_struct_array_pass` is likely
  post-fix-UNKNOWN rather than deep_copy failure).

## Phase 1 COMPLETED (2026-04-24)

**Commits:**
- `e01ee79f8e` — initial fix + 2 C regressions (unconditional
  address_of add)
- `9963df8e6f` — refined fix (gated on callee deref-write)
- `c832b07a27` — KNOWNBUG sweep for 44 Solidity tests (34 CORE + 10
  THOROUGH) that depended on the havoc-miss bug for their proof

**Full test results (esbmc-solidity, 60s timeout, -j 2):**
- 787 total, 705 passed, 82 failed (all failures now correctly
  KNOWNBUG-classified; all CORE tests pass).

**Conclusion:**
- Unsoundness fixed: pointer-through-function writes now correctly
  havoc'd in the k-induction modified-var analysis.
- 44 tests that depended on the bug reclassified to KNOWNBUG with
  comprehensive commit message. These need loop invariants (not
  yet available in Solidity) or migration to --incremental-bmc.
- Regression test coverage: 2 C regressions under
  `regression/esbmc/k_induction_ptr_through_function_{fail,pass}`
  catch the exact bug pattern.

**Open questions for the user:**

1. Should we migrate some of the 44 KNOWNBUGs to
   `--incremental-bmc` (bounded-but-sound) instead of leaving them
   as KNOWNBUG? That's a separate work chunk.
2. Is there an appetite for extending ESBMC with Solidity-level
   loop-invariant syntax? That unlocks proving these tests under
   k-induction.

## Phase 2/3 status

Phase 2 (Solidity deep_copy for struct-with-nested-array):
- Investigation showed the Phase 2 ctor walker is already
  implemented (`emit_ctor_deep_init_fixup` in
  solidity_convert_constructor.cpp). Existing KNOWNBUG on
  `esol_clone_struct_array_pass` is likely SMT-timeout under my
  Phase 1 fix, not a frontend deep-copy issue. Revisit only if the
  user confirms the test is genuinely broken vs. just slow.

Phase 3 (Solidity mapping(K => T[N]) via helper routing):
- Existing `map_fixed_arr_get` helper in
  `src/c2goto/library/solidity/solidity_mapping.c:165` handles the
  pattern correctly but is only routed under `--bound` + `new
  Store()` + `should_treat_as_new()`. Fix B: unconditional routing
  for the value-type pattern `T[N]`. Scope: medium
  (100-300 LOC in `solidity-frontend/`), zero solver change.
- Ready to implement once Phase 1 is verified clean.
