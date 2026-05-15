# Stage 2C.2a — `mk_struct_sort` recursion (sort construction) — GATE G2a: GREEN

Generated 2026-05-15. Implements design sub-stage 2C.2a from
`STAGE2C_DESIGN.md`. Scope: sort construction only; K=1 byte-identical;
no select/update/project change (that is 2C.2c).

## Change

`src/solvers/smt/tuple/smt_tuple_node.cpp` (`smt_tuple_node_flattener::mk_struct_sort`)
and `src/solvers/smt/tuple/smt_tuple_sym.cpp`
(`smt_tuple_sym_flattener::mk_struct_sort`) — byte-identical pair.

Removed the W1 assertion
`assert(!is_array_type(arrtype.subtype) && "Arrays dimensions should be
flattened ...")` and replaced the 4th constructor argument with a
recursion:

```cpp
smt_sortt range_sort = is_array_type(arrtype.subtype)
                         ? mk_struct_sort(arrtype.subtype)        // K>=2
                         : ctx->convert_sort(arrtype.subtype);    // K==1 (struct leaf)
return new smt_sort(SMT_SORT_ARRAY, type, dom_width, range_sort);
```

A nested `array<…array<Struct>…>` now yields K stacked `SMT_SORT_ARRAY`
layers (outermost-first), each carrying its own `(type, dom_width)`, with
the struct leaf at the bottom — the shape 2C.2b/2C.2c will walk.

## K=1 byte-identical proof (R1 mitigation, static arm)

For single-level `array<Struct>`, `arrtype.subtype` is the struct, so
`is_array_type(arrtype.subtype)` is **false**:

- the ternary evaluates to exactly `ctx->convert_sort(arrtype.subtype)`
  — the *identical* original 4th argument;
- the `new smt_sort(SMT_SORT_ARRAY, type, dom_width, range_sort)` call is
  unchanged;
- the removed assert's predicate `!is_array_type(arrtype.subtype)` is
  *true* for K=1, so the assert never fired for K=1 anyway (inert in
  Release where `assert` is compiled out; in Debug it only removed a
  never-triggered abort).

⇒ Zero runtime difference for every K=1 path. No SMT2 diff possible by
construction; the empirical arm (all single-level array-of-struct
regression tests pass identically) confirms it.

## Gate G2a results

| Check | Result |
|---|---|
| Build (`make -j4 esbmc`) | ✅ clean, 100% |
| K=1 byte-identical (single-level array-of-struct) | ✅ static proof above + all `mapping_*` (struct-value), `napp_map_*`, `nested_array_*`, `clearing_mapping_*`, `outer_dyn_*` PASS unchanged |
| Cross-language KNOWNBUG `regression/esbmc/nested_inf_array_of_struct_knownbug` | ✅ still KNOWNBUG-PASS — no longer aborts *at W1*; aborts later at W2/W3 (expected per gate; those stages not landed) |
| `cov_pilot_aqua2A_4lvl_addr_addr_bytes32_addr_uint256` | ✅ KNOWNBUG-PASS in isolation (0.75 s, aborts at `irep2_expr.cpp:366` — same as baseline; 2C.2a provably inert: abort precedes any `convert_sort`) |
| Focused subset (mapping/nested_array/dynarray/cov_pilot/napp_map/outer_dyn ≈129 tests) | ✅ no genuine regression; only documented pre-existing `napp_map_fixdyn_uint8_{fail,pass}` timeouts (Stage 2B baseline = exactly these 2) |

### The one apparent failure was a parallel-run flake

`LastTestsFailed.log` listed `cov_pilot_aqua2A_4lvl_..._uint256` after
the `-j4` run. Re-run **in isolation it passes in 0.75 s**. In the
parallel run it was scheduled alongside `iterable_mapping_1` (68 s) and
`dynarray_2d_clone_post_mutation_pass` (54 s) under `--memlimit 8g` →
resource-contention slowdown, not a behaviour change. Decisive argument:
the test aborts at `irep2_expr.cpp:366` during *equation construction*
("Checking base case, k=1"), strictly **before** the solver
`convert_sort` path where `mk_struct_sort` lives — 2C.2a cannot affect
it. (This is the pre-existing Stage-2A IR-malformation bug, orthogonal
to 2C; it stays KNOWNBUG.)

## Soundness / completeness (per `feedback_completeness_soundness_report`)

- **Soundness**: neutral. Sort *shape* only; no constraint emitted. K=1
  byte-identical ⇒ zero change on the 720+/2000+ existing tests.
- **Completeness**: neutral at this sub-stage. The W1 wall is removed but
  the nested-of-struct shape still aborts later (W2/W3); no shape gains a
  verdict yet (by design — 2C.2c is the semantic stage).
- **Overhead**: neutral. One extra recursion frame per array dimension at
  sort-construction time only.

## Verdict

**G2a GREEN.** 2C.2a is a strict, K=1-byte-identical generalisation with
no regression. Per the per-substage authorisation contract, **stop here
and await explicit authorisation for 2C.2b** (`mk_tuple_array_symbol`
per-field nested-array symbols, gate G2b).
