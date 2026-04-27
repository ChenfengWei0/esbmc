# mapping_3 / bytes_8 hot-loop diagnosis (Stage 1, 2026-04-27)

This doc reports on **why mapping_3, mapping_4, mapping_10 stay
KNOWNBUG even after the per-callee field-write summary refactor**
(Task B, attempted and reverted same session) and **what the actual
hot path is for both mapping_3 and bytes_8**. It also explains why
Stage 2 (Phase 4 priority 3, "bytes_8 c2goto helper-unwind cap")
turns out to be a soundness-trade-off question that needs user
direction before coding.

## Method

Temporarily added `log_status` instrumentation to
`make_nondet_assign` (start of struct-field branch +
loop-vars enumeration) to capture, for each loop processed by
k-induction:

- function name containing the loop
- loop head location number
- modified-vars count and per-lhs (name + kind classification)
- struct-field decision (RESTRICT_TO {…} vs FALLBACK_HAVOC_ALL)

Captured on mapping_3 and (for comparison) the CORE-passing
default_value_1.

Files:

- log: `/tmp/mapping3_hotloop.log`, `/tmp/default_value_1_hotloop.log`
- instrumentation: temporary; reverted in same session.

## Empirical findings

### Loop count is approximately the same between passing and failing tests

| Test | Unique functions with k-inductised loops | Status |
|---|---|---|
| `mapping_3` | 36 | KNOWNBUG (timeout) |
| `default_value_1` | 38 | CORE pass |

Both tests pull in nearly the same set of c2goto library helper
loops: `__memcpy_impl`, `__memset_impl`, `atoi`, `strlen`, `strcmp`,
`strtof`, `strtol`, `bytes_static_*`, `bytes_dynamic_ensure_capacity`,
etc. So **loop count is not the discriminator**.

### No struct-typed lhs in modified-vars matches the contract object

`make_nondet_assign`'s struct-field path (the target of Task B's
refactor) fires only for these lhs in mapping_3:

```
fn=c:@F@bytes_static_from_hex   lhs=...@b  decision=RESTRICT_TO {data,}
fn=c:@F@bytes_static_xor        lhs=...@r  decision=RESTRICT_TO {data,}
fn=c:@F@bytes_static_and        lhs=...@r  decision=RESTRICT_TO {data,}
fn=c:@F@bytes_static_or         lhs=...@r  decision=RESTRICT_TO {data,}
fn=c:@F@bytes_static_from_uint  lhs=...@b  decision=RESTRICT_TO {data,}
```

**All five struct-typed lhs are BytesStatic locals inside the
helpers. Every one is correctly RESTRICTED to {data}.** None of
them is the contract object, the mapping_t struct, or any state
variable. Phase 4's claim that `mapping_t.mid/.addr` identity
fields get over-havoc'd is **empirically not what is happening**
for mapping_3.

This means Task B's per-callee field-write summary refactor would
never have flipped mapping_3 — there is no havoc-all decision to
refine.

### Slow query at k=4 inductive step is solver-bound on existing formula

mapping_3 phase-by-phase timing (Bitwuzla, current build):

```
Checking base case, k=1:        Symex 0.001s, 0 VCCs
Checking base case, k=4:        Symex 0.043s, 9 VCCs, solver 0.008s
Checking inductive step, k=4:   Symex 0.800s, 184 VCCs gen → 81 sliced,
                                solver **12.749s**
Checking base case, k=7:        Symex 0.086s, 9 VCCs, solver 0.014s
Checking inductive step, k=7:   Symex 6.922s, 304 VCCs gen → 153 sliced
                                — runs over budget
```

The bottleneck is the **inductive step's SMT formula**, not symex.
Even at k=4 (small k), 81 sliced VCCs encode the full program
state under the havoc preamble of every loop in every helper
function reached. As k grows, the program state path lengthens
and the formula scales accordingly.

### bytes_8 has the same pattern, sharper

bytes_8 has the same shape as mapping_3 but with a state variable
that exercises bytes_dynamic_ensure_capacity and __memcpy_impl
directly through `data.push(...)` and `data.pop()` calls. Phase 4
reported "memcpy + dynamic-bytes capacity loops are unwound 21×
each" and "SSA size grows with k³". The SSA cost is the
sub-quadratic explosion of having ~40 helper-function loops × k
sub-iterations × k k-induction steps.

## Root cause (corrected vs Phase 4)

Phase 4's diagnosis attributed `mapping_3` to "(b) over-havoc on
mapping_t identity fields" and `bytes_8` to "(c) symex modeling".
Empirical instrumentation says:

- **mapping_3 (and mapping_4/10 by extrapolation)**: NOT over-havoc
  — the struct-field path correctly restricts. Cumulative cost
  across ~40 helper-function loops processed under k-induction.
  Each helper's loop adds a havoc preamble + body-once + post-
  condition check to the inductive-step formula.
- **bytes_8**: SAME pattern, sharper because the user code directly
  exercises the heavy helpers.

This is a refinement, not a replacement, of Phase 4's "(c) symex
modeling": the bottleneck is the **k-induction transform applied
to too many helper functions**, not the modeling of any one
specific loop.

## Why Task B's refactor doesn't help

Task B targeted `collect_modified_struct_fields` → refine the
fields-set when the loop body invokes a callee writing through
pointer to a struct-typed lhs. The lhs in question would be a
struct symbol in modified_loop_vars. But mapping_3's modified_vars
sets contain only:

- BytesStatic locals (already correctly RESTRICTED)
- Scalar locals (i, count, len, …) — not struct-typed, not affected
- Pointer locals — skipped under value-set analysis
- Arrays (e.g., ATOI_MAP) — different code path

There is no contract-object-as-struct entry in any modified_loop_vars
set for mapping_3. So the refactor's consumer path never fires
for the failing test.

## Stage 2 — bytes_8 c2goto helper-unwind cap: deferred

Two paths investigated, both blocked by hard constraints:

### Path A — Skip k-induction transform on hidden helpers

Skip k-induction on loops in `__ESBMC_HIDE`-labeled c2goto library
helpers (memcpy, memset, bytes_dynamic_ensure_capacity, …),
relying on BMC unrolling at `max_k_step` to cover them precisely
instead. This would remove ~30-40 helper loops from the
inductive-step formula, dramatically cutting SSA size.

**Trade-off**:
- **Sound when** the helper's true trip count is ≤ `max_k_step`.
  Typical Solidity bytes ops are bounded by 32 or small dynamic
  lengths.
- **Unsound when** trip count exceeds `max_k_step`. BMC silently
  truncates writes (e.g., a 100-byte memcpy with `--max-k-step 20`
  copies only 20 bytes).

This conflicts with memory rule
`feedback_no_soundness_escape_hatch.md` ("slow tests get THOROUGH,
not a CLI flag that silently skips correctness"). The existing
"counted-loop skip" makes a similar soundness call but only for
pure-local-writers — the helpers in question write through pointer
params, so they're NOT pure-local-writers.

### Path B — Rewrite __memcpy_impl / __memset_impl to avoid loops

Initially looked attractive because `memcpy()` routes to the
`__ESBMC_memcpy` intrinsic (`symex_main.cpp:652`) and one might
think the `__memcpy_impl` body is dead. **It isn't.**
`intrinsic_memcpy` (`builtin_functions.cpp:1994-2160`) bumps to
`c:@F@__memcpy_impl` whenever the size is symbolic, the operands
have non-constant offsets, types are dynamic-sized,
`enable-unreachability-intrinsic` is set, and several other
fallback conditions. Removing the loop body would silently
short-circuit those fallback paths — an unsound encoding change.

A clean fix would refactor symex's memcpy intrinsic itself to
handle the symbolic-size case without falling back to the C-level
loop (e.g., emit array-comprehension constraints directly). This
is research-scale (>30h) and orthogonal to the bytes_8 KNOWNBUG.

### 2026-04-27 update (B0 empirical pass on Path B)

After more careful exploration of `intrinsic_memcpy`, Path B as
scoped in the prior plan (~9-15 days for symbolic-`n` bounded
unroll) does NOT close the gap. Empirical findings:

- **bytes_8 alone hits the symbolic-`n` bail 1030 times per run.**
  Confirmed: every `intrinsic_memcpy` invocation bumps to
  `__memcpy_impl`. The constant-size inline path NEVER fires for
  this test under k-induction's inductive step (where parameters
  are havoc'd to nondet).

- **Bypassing only the symbolic-`n` bail is insufficient.** The
  downstream constant-size logic at lines 2073-2077 and 2157-2161
  also bails on symbolic offsets. For bytes_8's
  `memcpy(&pool->pool[new_offset], &pool->pool[b->offset],
  b->length)`, all three of `new_offset`, `b->offset`, `b->length`
  are symbolic at the inductive step. Bounded-unroll for `n`
  alone leaves the offset-bail still firing.

- **Even with concrete offsets, `do_memcpy_expression` rejects
  array types** (`builtin_functions.cpp:1960-1967`: returns nil for
  array/struct/union, triggering `bump_call` at line 2213-2217).
  Solidity bytes use array dst/src (`pool[POOL_MAX]`); the
  constant-size path can't handle them directly today.

The full Path B scope needed to flip `bytes_8` is:
1. Symbolic `n` → bounded unroll
2. Symbolic offsets → emit per-byte conditional `with2tc` array
   stores indexed by `off+i`
3. Array-typed dst/src → use array theory directly instead of the
   bit-mask approach

This is a fundamentally different encoding (array theory at SMT
level, possibly via quantifiers) and is **research-scale (>30d)**,
not the 9-15d the prior plan estimated.

### 2026-04-27 (later) — Path B-Light prototyped twice, both -2/-3 net

After the deeper investigation found `byte_update2t`, `with2t` etc
already support symbolic indices, the plan was rescoped to two
paths (see `/home/samson/.claude/plans/foamy-sniffing-toucan.md`):

- **Path B-Light** (~4-6h): skip k-induction transform on
  `__ESBMC_HIDE`-labeled helpers when their loops fit the
  counted-for-loop+symbolic-bound pattern. Soundness from BMC's
  built-in unwinding-assertion (default ON).
- **Path B-Full** (~15-19d): refactor `intrinsic_memcpy` to use
  `byte_update2t` / `with2t` directly for symbolic-everything case.

**Two B-Light variants tried, both regress**:

| Variant | Skipped functions | Flips | Regressions | Net |
|---|---|---|---|---|
| Broad: all `__ESBMC_HIDE` helpers | ~all string/bytes helpers | +1 (`bytes_15`) | -3 (`library_eoa_credit_unbound_fail`, `reentrance_12`, `stress_libsol_calldata_string_array`) | **-2** |
| Conservative: only `__memcpy_impl` + `__memset_impl` by name | 2 functions | 0 | -3 (`reentrance_12`, `require_3`, `stress_libsol_calldata_string_array`) | **-3** |

Both reverted. Tree at post-Stage-1 baseline.

**Critical insight from the failed prototypes**:

K-induction's havoc-step-once on `__memcpy_impl` is LOAD-BEARING for
a non-trivial number of CORE-passing tests. Skipping it (even just
for memcpy/memset by name, no other helpers) causes 3 CORE-pass
regressions: `reentrance_12`, `require_3`,
`stress_libsol_calldata_string_array`. These tests rely on
k-induction's coarse over-approximation of memcpy's effects — the
EXACT BMC unrolling forces them into a more precise but harder-to-
prove formula, which doesn't converge within the 60s budget.

In other words: the existing `function_is_pure_local_writer` gate
that excludes memcpy/memset is correct under the project's typical
configuration. Tests targeting memcpy-heavy code (`bytes_8`)
WANT the over-approximation; tests targeting other invariants
(`reentrance_12`, `require_3`) ALSO benefit from it. There is no
clean middle ground.

**Implication for Path B-Full**:

Path B-Full would replace memcpy's symex with a per-call SMT
constraint chain (using `byte_update2t` etc.) instead of relying
on the C-loop fallback. This avoids the k-induction transform on
the C-loop entirely (no loop, no havoc to apply). Whether this
preserves the "coarse over-approximation that helps reentrance_12"
property depends on how the replacement encoding compares — a
PER-CALL SMT constraint is precise (good for bytes_8) but might
be too complex for tests that don't actually constrain memcpy
results (bad for reentrance_12 if the formula bloats with full
byte-level precision).

This is no longer a clear win and the 15-19d investment risks
similar regression patterns. Path B-Full ROI is now uncertain.

### Decision

Neither path ships in this session. **The bytes_8 / mapping_3
KNOWNBUG cohort needs explicit user direction before further
work**:

- **More conservative Path B-Light** (~4-6h follow-up): instead of
  skipping ALL `__ESBMC_HIDE` helpers, gate on a NAMED ALLOWLIST
  (just `__memcpy_impl` and `__memset_impl`). Avoids regressing
  `library_eoa_credit_unbound_fail` etc. Higher chance of clean
  flip for bytes_8 / mapping_3 / mapping_4 / mapping_10.
- **Pursue Path B-Full** (`intrinsic_memcpy` refactor using
  `byte_update2t` / `with2t` for symbolic-everything case):
  ~15-19d (corrected estimate after deeper investigation —
  supersedes the earlier >30d claim from 619be754f6). The
  necessary IR primitives are present in ESBMC's SMT layer.
- **Reclassify bytes_8 / mapping_3 / mapping_4 / mapping_10
  → THOROUGH** per memory rule: clean, preserves correctness,
  but doesn't actually verify the tests faster. ~30min.
- **Stop and document** these tests as KNOWNBUG-correct given
  current k-induction cost model (analogous to the (a) genuine
  inductive insufficiency category in Phase 4).

## Alternatives considered

1. **Per-callee field summaries (Task B)** — implemented and
   reverted same session. Sound, no regressions, but zero
   KNOWNBUG flips. Reason: the targeted havoc path doesn't fire
   for mapping_3-class tests.

2. **Solver substitution (Phase 4 priority 1)** — empirically
   invalid for mapping_3-style tests; CVC5/Z3 don't beat Bitwuzla
   on this shape (similar to tod_balance_pass per
   `reference_tod_balance_pass_solver_hard.md`).

3. **Skip k-induction transform on hidden helpers (Stage 2)** —
   biggest potential payoff but soundness trade-off; needs user
   signoff.

4. **Reduce max_k_step** — already at 20. Lower would lose proof
   for tests that need higher k.

5. **Mark slow tests THOROUGH instead of KNOWNBUG** — preserves
   correctness, just removes them from the default 60s ctest
   budget. Safest "fix" but doesn't actually verify them either.

## Recommended next-action priority

The Phase 4 cohort of remaining KNOWNBUG-timeout tests reduces to
two clean buckets given this analysis:

1. **(a) genuine inductive-completeness gap** — covers `mapping_10`,
   `nest_loop_3`, `reentrance_12`, etc. KNOWNBUG-correct.
2. **(c+d) cumulative k-induction cost across helper loops** —
   covers `mapping_3`, `mapping_4`, `bytes_8`, plus probably
   `bytes_15`, `dangling_ref_1`, and other helper-heavy tests.
   The fix is path A (CLI-gated soundness trade-off) or path B
   (symex memcpy refactor).

Phase 4 priorities 4-5 (aliasing investigation, deeper symex
work) remain open and orthogonal.

This session's deliverable: Stage 1 diagnosis doc only. Stage 2
deferred pending user direction on the soundness/depth trade-off.
