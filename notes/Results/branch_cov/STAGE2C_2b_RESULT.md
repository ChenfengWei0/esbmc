# Stage 2C.2b — broaden the symbol router (NW0) — GATE G2b: GREEN

Generated 2026-05-15. Implements design sub-stage 2C.2b from
`STAGE2C_DESIGN.md` (v2, node-flattener-correct). Scope: symbol-router
discriminator only; no constraint emitted; no shape gains a verdict.

## Change

`src/solvers/smt/smt_conv.cpp` (symbol_id router, was lines 3050-3058).

Replaced the shallow discriminator

```cpp
if (is_array_type(expr))
{
  type2tc range = get_flattened_array_subtype(expr->type);
  if (is_tuple_ast_type(range))
    return tuple_api->mk_tuple_array_symbol(expr);
}
```

with

```cpp
if (is_tuple_array_ast_type(expr->type))
  return tuple_api->mk_tuple_array_symbol(expr);
```

**Deviation from the design's literal snippet (declared, not silent):**
the design kept the outer `if (is_array_type(expr))` wrapper. I dropped
it because `is_tuple_array_ast_type` *opens* with
`if (!is_array_type(t)) return false;` (`smt_tuple_sort.h`), so the
outer guard is provably redundant — keeping it would be a dead
conditional the post-implementation pass mandates removing. Semantics
are identical for every input (array and non-array). This is a strict
simplification of the specified form, not a substitution of behaviour.

`get_flattened_array_subtype` is no longer called on this path (it
returned the *immediate* subtype `array<Struct>` for an infinite-outer
nested array, which `is_tuple_ast_type` cannot see through — that was
NW0, the root cause that led to the NW1 bare-sort abort).

## K=1 byte-identical proof (R1/R2 mitigation, static arm)

The routing predicate is a pure boolean identity for every K=1 input:

- **`array<Struct,inf>` (single-level array-of-struct)**: old check =
  `is_tuple_ast_type(get_flattened_array_subtype(array<Struct>))` =
  `is_tuple_ast_type(Struct)` = **true**. New check =
  `is_tuple_array_ast_type(array<Struct>)`: recurses array dims to the
  leaf `Struct`, returns `is_tuple_ast_type(Struct)` = **true**. Same
  branch → same `mk_tuple_array_symbol(expr)` → identical SMT.
- **non-array symbol**: old code never entered the
  `if (is_array_type(expr))` block; new `is_tuple_array_ast_type(t)`
  returns false at its own `!is_array_type` guard. Both fall through to
  the identical normal-symbol path.
- **`array<primitive>` (non-tuple array)**: old `is_tuple_ast_type`
  (primitive) = false; new `is_tuple_array_ast_type` recurses to the
  primitive leaf, `is_tuple_ast_type(primitive)` = false. Both fall
  through identically.

⇒ Provably zero routing change for *all* K=1 / non-nested inputs (a
proof over the predicate, strictly stronger than a sampled SMT2 diff —
same basis G2a was accepted on). The K≥2 nested case is the *only* input
whose routing changes (it now reaches `mk_tuple_array_symbol` instead of
bypassing it to Branch A).

## Gate G2b results

| Check | Result |
|---|---|
| Build (`make -j4 esbmc`) | ✅ clean, 100% |
| K=1 byte-identical (static) | ✅ predicate-identity proof above |
| K=1 byte-identical (empirical) | ✅ `mapping_11`, `nested_array_2d_1`, `nested_array_2d_2`, `napp_map_2d_struct_pass`, `napp_map_2d_struct_fail` — all PASS, verdict unchanged |
| Cross-language KNOWNBUG `regression/esbmc/nested_inf_array_of_struct_knownbug` | ✅ KNOWNBUG-PASS (0.38 s) — still aborts at NW1 (now *inside* `mk_tuple_array_symbol`'s `convert_sort`, not bypassed to Branch A); no new failure mode — exactly as designed (2C.2c not landed) |
| Solidity KNOWNBUG `regression/esbmc-solidity/cov_pilot_aqua_Aqua` | ✅ KNOWNBUG-PASS (0.97 s) — `bare smt_sort` regex still matches; entry point moved, wall unchanged |
| Focused representative subset | ✅ 7/7 PASS; documented pre-existing `napp_map_fixdyn_uint8_{fail,pass}` Stage-2B timeouts are orthogonal (not on this router path) |
| Revertability | ✅ one-line discriminator swap; independently revertable |

## Soundness / completeness (per `feedback_completeness_soundness_report`)

- **Soundness**: neutral. Routing decision only — no constraint emitted,
  no `mk_array_sort`/`assert_ast` reached differently for any input that
  produced a verdict before. K=1 byte-identical ⇒ zero change on the
  720+ Solidity / 2000+ C/C++ tests.
- **Completeness**: neutral at this sub-stage. The nested-of-struct
  shape still aborts at NW1 (`smt_conv.cpp:2858`) — it has merely
  changed which function call-stack reaches NW1. No shape gains a
  verdict yet (by design — 2C.2c is the semantic stage).
- **Overhead**: neutral/negative. One predicate (`is_tuple_array_ast_type`,
  O(array depth)) replaces one predicate + a `get_flattened_array_subtype`
  call; net slightly cheaper on the array path.

## Verdict

**G2b GREEN.** 2C.2b is a strict, K=1-predicate-identical generalisation
with no regression and a declared simplification of the specified form
(redundant outer guard removed). Per the per-substage authorisation
contract, **stop here and await explicit authorisation for 2C.2c**
(`mk_tuple_array_symbol` K≥2 per-field decomposition + NW1 defensive
tripwire — the critical semantic stage, gate G2c).
