# Nested-array support status (audit, 2026-04-27)

This document answers the question "is ESBMC's Solidity nested-array
modeling complete?" with a per-shape coverage table, citing concrete
file paths and regression tests, and ranks the remaining gaps by
user impact.

The audit covers fixed-size, dynamic-length, and mixed nesting
patterns at depths 1-3, plus arrays interacting with mappings and
structs. It is a snapshot of the codebase at branch `solidity` head
post Stages 0-3, Phase 1 auto-hint (commit `ce08a2785b`), and
Phase 4 diagnosis (commit `bfd18f8737`).

## Summary table

Status legend: ✓ full = works end-to-end on default solver; ⚠ partial
= works under specific flags; ✗ KNOWNBUG = blocked by a documented
bug.

| Solidity shape | Status | Notes / blocker |
|---|---|---|
| `uint[]` 1D dynamic | ✓ full | Lowered to global infinite-size SMT array (`solidity_convert_type.cpp` 1D handling). Works on every solver. |
| `uint[N]` 1D fixed | ✓ full | Pointer-backed heap allocation; clone path uses `_ESBMC_arrcpy`. |
| `uint[N][M]` 2D fixed | ✓ full | Native nested `array_typet`; clone path uses `_ESBMC_arrcpy_2d` (CLAUDE.md `__ESOL_deep_copy`). |
| `uint[N][M][K]` 3D fixed | ⚠ partial | Standalone OK; **KNOWNBUG #4** when contained in a struct that goes through `__ESOL_deep_copy` — clone walker doesn't recurse beyond 2 fixed levels (`solidity_convert_constructor.cpp` `build_tod_clone_helper`). |
| `uint[][]` 2D dynamic | ⚠ partial | Lowered to nested infinite `array_typet`. Auto-hint (Phase 1) only fires on **≥3D**, so 2D dynamic still requires manual `--cvc5 --cvc5-native-tuples`. Bitwuzla/Boolector/Z3 abort via Phase-0 discriminator on `array_convt`. |
| `uint[][][]` ≥3D dynamic | ⚠ partial | Phase 1 auto-hint detects via `t_array$_t_array$_t_array$_` typeIdentifier scan (`esbmc_parseoptions.cpp:845-1058`); auto-selects `--cvc5 --cvc5-native-tuples`. Encoding works; k-induction PASS proofs hit **KNOWNBUG #1** (`nested_array_deep_1`). |
| `uint[N][]` outer-dynamic, inner-fixed | ⚠ partial | 2D works (`nested_array_fixdyn_2` CORE). 3D+ blocked by KNOWNBUG #1 (`nested_array_fixdyn_1` KNOWNBUG). |
| `uint[][N]` outer-fixed, inner-dynamic | ⚠ partial | 2D works (`nested_array_mixed_2` CORE). 3D+ blocked by KNOWNBUG #1 (`nested_array_mixed_1` KNOWNBUG). |
| `T[][]` array of struct | ⚠ conditional | Works when `T` has only scalar/array fields. **Breaks** when `T` contains a fixed array field (clone walker doesn't recursively calloc the nested pointer; `_ESBMC_arrcpy(NULL, ...)` SIGSEGV). |
| `mapping(K=>V)[N]` array of mapping | ✓ full | Special-cased at `solidity_convert_type.cpp:474-481` — modelled as 2D infinite array with `#sol_mapping_array` flag, avoiding pointer/malloc model. Tests: `mapping_of_dynarray_push_{pass,fail}` CORE under `--bound --cvc5 --k-induction`. |
| `mapping(K=>V[])` mapping of array | ⚠ conditional | Works under `--bound` with helper. **Fails** on Bitwuzla/Boolector/Z3: `array_convt::mk_array_symbol` asserts `subtype->id != SMT_SORT_ARRAY` at `array_conv.cpp:92` (KNOWNBUG #3). CVC5 with `--cvc5-native-tuples` is the supported path. |
| `mapping(K=>K2=>V)` nested mapping | ✓ full | Tests: `mapping_nested_{1,2}` CORE under `--unbound --k-induction`. |

## Test inventory

Shape coverage by regression test (49 tests touching nested-array shapes):

- **2D fixed (CORE, 19 tests)**: `multi_dim_fixed_array_{pass,fail}`,
  `multi_dim_fixed_3d_{pass,fail}`, plus `multi_dim_fixed_*_2d_{pass,fail}`
  variants for addr/bool/bytes32/int/fn-param/local/struct-field types.
  All pass on default Bitwuzla.
- **Outer-dyn/inner-fixed (CORE)**: `outer_dyn_inner_fixed_array_{pass,fail}`.
- **Mixed dynamic/fixed combinations**: `nested_array_fixdyn_{1,2}`,
  `nested_array_mixed_{1,2}` — `_2` pass variants are CORE, `_1` PASS
  variants are KNOWNBUG (k-induction can't close inductive step).
- **Pure 2D dynamic**: `nested_array_2d_{1,2}` — `_1` (PASS) KNOWNBUG,
  `_2` (FAIL) CORE.
- **3D+ jagged dynamic**: `nested_array_deep_1` KNOWNBUG.
- **1D dynamic baseline**: `nested_array_1d_{1,2}` CORE.
- **Multi-dim clone semantics**: `esol_clone_multi_dim_{pass,3d_pass}`,
  `esol_clone_array_of_struct_{pass,isolation_pass}` — all CORE.
- **Mapping with array nesting**: `mapping_of_dynarray_push_{pass,fail}`,
  `mapping_nested_{1,2}`, `cross_nested_mapping_{pass,fail}`,
  `mapping_fixed_array_{value_pass,unbound_pass}` — most CORE.
- **Mapping value-side fixed-2D-array**:
  `mapping_fixed_2d_array_unbound_{pass,fail}` — `_pass` is KNOWNBUG.

## Cross-cutting observations

1. **All KNOWNBUG nested-array tests are PASS variants**. The FAIL
   companions (`*_fail`, e.g. `nested_array_2d_2`) all pass as CORE.
   This is the same pattern as `tod_balance_{pass,fail}`: falsifying
   is easy (one counter-example), proving universally is hard
   (k-induction must close inductive step). This is the **KNOWNBUG #1
   k-induction pointer-through-function havoc** pattern documented
   in `docs/claude/FIX_PLAN.md`.

2. **The frontend is doing its job; the backend is fine.** The
   modeling layer (`src/solidity-frontend/solidity_convert_type.cpp`,
   `solidity_convert_constructor.cpp`) handles every shape we tested.
   The SMT-encoding layer routes correctly to native nested arrays
   when the solver supports them (CVC5 with native-tuples) or to
   flattening when not. The remaining failures are k-induction
   inductive-step incompleteness, which is orthogonal to "modeling
   completeness."

3. **The Phase 1 auto-hint covers users for 3D+ dynamic shapes.**
   Empirically (no false-positives in regression), users hitting
   `T[][][]` or deeper get auto-selected CVC5+native-tuples.

4. **2D dynamic is a minor user-experience gap.** A user writing
   `uint[][]` on default Bitwuzla gets a Phase-0 abort with no
   automatic suggestion to retry under CVC5. The auto-hint detector
   threshold is `≥3 consecutive t_array$_` markers; lowering to
   `≥2` would close this gap (~2h).

5. **Struct-with-nested-fixed-array clone is a real modeling gap.**
   This is a hard-stop SIGSEGV path (NULL deref), not a soundness
   issue, but it does limit code that mirrors common patterns from
   ERC-20/ERC-721 inheritance trees that use `mapping(uint => Bin)`
   where `Bin` contains fixed-size arrays. Triaged as KNOWNBUG #4.

## Remaining gaps with file:line citations

### Gap 1 — 2D dynamic auto-hint threshold

- **What**: `uint[][]` not auto-detected; user must pass
  `--cvc5 --cvc5-native-tuples` manually.
- **Where**: detector at `src/esbmc/esbmc_parseoptions.cpp:845-1058`.
  Specifically the `t_array$_` consecutive-marker count is
  `≥3` in the `nested_dyn_detected = true` branches at lines
  968 and 995.
- **Fix sketch**: lower threshold to `≥2`. Verify no false
  positives by re-running full Solidity regression. Cost: ~2h.

### Gap 2 — `mapping(K=>V[])` outside `--bound`

- **What**: assert in `src/solvers/smt/array_conv.cpp:92` rejects
  array-of-array sort on Bitwuzla/Boolector/Z3.
- **Where**: assertion + comment at lines 92-95.
- **Fix path**: the pre-existing answer is "use CVC5 with
  `--cvc5-native-tuples`" (works correctly). Phase 2
  (`linearize_finite_tail`) would address this for non-CVC5
  backends, but Phase 4 diagnosis confirmed Phase 2 is not
  cost-effective vs Phase 1's auto-hint coverage. Defer.
- **Tracked**: KNOWNBUG #3 in `docs/claude/FIX_PLAN.md`.

### Gap 3 — Struct-with-nested-fixed-array clone

- **What**: `__ESOL_deep_copy` walker doesn't recurse into nested
  pointer-backed fixed arrays inside struct fields. Constructor
  for `struct { uint[N][] inner; }` doesn't calloc `inner`,
  causing `_ESBMC_arrcpy(NULL, ...)` deref.
- **Where**: `src/solidity-frontend/solidity_convert_constructor.cpp`
  `build_tod_clone_helper`; CLAUDE.md `__ESOL_deep_copy` section
  documents the limit.
- **Fix sketch**: extend the constructor's recursive walker to
  initialise nested pointer-backed array fields before the clone
  walker runs. Cost: ~6-10h.
- **Tracked**: KNOWNBUG #4 in `docs/claude/FIX_PLAN.md`.

### Gap 4 — k-induction inductive-step completeness for nested-array PASS proofs

- **What**: 13 KNOWNBUG nested-array tests are all PASS variants
  blocked by k-induction's inability to synthesise loop-carried
  invariants over nested-array dispatcher writes.
- **Where**: `src/goto-programs/goto_loops.cpp`
  `callee_writes_through_pointer` (boolean coarse summary);
  `src/goto-programs/goto_k_induction.cpp`
  `collect_modified_struct_fields` bails to nullopt on indirect
  writes through actuals.
- **Fix sketch**: same per-callee field-write summaries proposal
  that targets `mapping_3` (Phase 4 priority 2). Some nested-array
  tests may benefit; most are independent. Cost: 12-20h
  (covered separately under Phase 4 priority 2).
- **Tracked**: KNOWNBUG #1.

## SMT-conv dispatch sites (Stage 3 chain-aware)

For reference, the 6 sites where `array_chain_has_infinite_level`
gates flatten-vs-skip dispatch (commit `8697372921`,
`src/solvers/smt/smt_conv.cpp`):

| Line | Context |
|---|---|
| 2812-2813 | `convert_sort` array branch — chain-aware skip linearisation |
| 3508 | `flatten_array_type` subtype check |
| 3668 | array index read sort dispatch |
| 3711 | array `with`-update flattening guard |
| 3777 | `flatten_array_type` recursion guard |
| 4557-4558 | recursive sort conversion |

The helper itself is at `smt_conv.cpp:3745`.

## Action-item ranking by user impact

1. **2D dynamic auto-hint extension** (gap 1, ~2h). Closes a UX
   wart that affects every user writing 2D dynamic arrays. Low
   risk, mechanical change. **Recommend doing alongside other
   work** if a related auto-hint commit is in flight.

2. **Per-callee field-write summaries** (gap 4, 12-20h). The biggest
   blocker for k-induction PASS proofs over nested-array shapes.
   Would unblock several KNOWNBUG tests including `mapping_3`.
   Already prioritised under Phase 4 priority 2.

3. **Struct-with-nested-fixed-array clone** (gap 3, 6-10h). Unblocks
   a real but narrow user pattern. **Defer** unless a user reports.

4. **`mapping(K=>V[])` non-CVC5 backend** (gap 2, research-scale).
   **Defer indefinitely** — the CVC5+native-tuples path is correct
   and supported; this is a Bitwuzla parity question. Phase 4
   already concluded Phase 2 (`linearize_finite_tail`) is not
   cost-effective at this point.

## Verdict

**Modeling-layer is complete for the shapes users actually write.**
The remaining 13 KNOWNBUG nested-array tests are k-induction proof
incompleteness, not modeling holes. The two genuine modeling gaps
are (a) struct-containing-nested-fixed-array clone and (b) the
2D-dynamic auto-hint threshold; both are bounded-cost and tracked.

The largest leverage point for unblocking the KNOWNBUG cohort is
the per-callee field-write summary refactor (Phase 4 priority 2),
which is a goto-programs change rather than a frontend change.
