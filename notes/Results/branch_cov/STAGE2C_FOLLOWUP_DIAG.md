# Diagnosis — deep nested-mapping WRITE wall (post-Stage-2C aqua blocker)

Generated 2026-05-15. **Diagnosis only — no fix** (fix is a separate,
separately-authorised stage, per `feedback_strict_stage_authorization`).
Companion to `STAGE2C_FOLLOWUP_REPIN.md`.

## Symptom

After Stage 2C removed the `bare smt_sort` SMT-backend wall, the 7
write-path aqua pilots abort during goto-symex (before SMT), with one
of two assertions depending on nesting depth / value kind:

- depth 4–5 (and aqua/Aqua, 4-level): `src/irep2/irep2_expr.cpp:366`
  `assert_type_compat_for_with` — `assert(is_array_type(b))`.
- depth 3 + struct value: `src/pointer-analysis/value_set.cpp:1258`
  `value_sett::assign` — `assert(base_type_eq(rhs->type, lhs_type, ns))`.

Reproduces with a **non-struct `uint256`** value
(`cov_pilot_aqua2A_4lvl_..._uint256`) ⇒ unrelated to Stage 2C's
struct-of-arrays; it is the deep nested-mapping **write** itself.
Threshold: 2-level mapping write is fine (Stage 2C
`mapping_struct_smtsort_k2_pass` is SUCCESSFUL); ≥3-level aborts.

## Backtrace (uint256 repro, `--contract C --unwind 3`)

```
irep2_expr.cpp:366  assert(is_array_type(b))            <- fails
irep2_expr.cpp:369  assert_type_compat_for_with(recurse on subtype)
irep2_expr.cpp:406  with2t::assert_consistency
irep2_expr.h:2964   with2t::with2t
symex_assign.cpp:680 with2tc(index.source_value->type, …, new_rhs)
symex_assign.cpp:682 goto_symext::symex_assign_array  (×N, recursing)
symex_assign.cpp:387 goto_symext::symex_assign
```

`symex_assign_array` (symex_assign.cpp:650-684) lowers `a[i]=e` to
`a' = a WITH [i:=e]` and recurses up the LHS `index` chain. At each
level it builds `with2tc(index.source_value->type, index.source_value,
index.index, new_rhs)` (line 680). `with2t::assert_consistency`
(irep2_expr.cpp:399-425) requires, when the array's element type `a`
is itself an array, that the stored value type `b` is also an array
(line 366). It is not ⇒ abort.

## Root cause (frontend, source-grounded)

`src/solidity-frontend/solidity_convert_expr.cpp`, the mapping
IndexAccess lowering. `t` is the index-access expression's type from
`get_type_description(expr["typeDescriptions"], t)` (~line 4105).

Two branches with an **asymmetry**:

- **Direct** access `m[k]` (`referencedDeclaration` present),
  line 4169-4174:
  ```cpp
  if (!is_new_expr && array.type().is_array()) {
    xor_fold_key_to_64bit(pos);
    // Use the array's declared subtype rather than `t` from
    // get_type_description, which may lack subtypes for nested mappings.
    new_expr = index_exprt(array, pos, array.type().subtype());   // CORRECT
  }
  ```
- **Nested** access `m[k1][k2]` (base is itself an IndexAccess, no
  `referencedDeclaration`), line 4190-4204:
  ```cpp
  gen_mapping_key_typecast(current_contractName, pos, location, pos.type());
  xor_fold_key_to_64bit(pos);
  new_expr = index_exprt(array, pos, t);                          // BUG: uses `t`
  ```

The nested branch types the intermediate `index_exprt` with `t`
(from `get_type_description`), which — by the code's own comment at
lines 4172-4173 — **"may lack subtypes for nested mappings"**. For
`mapping(a=>…=>uint256)` the AST `typeDescriptions` of an
intermediate `m[k1][k2]` is the mapping *value* type and is **not**
recursively expanded into the properly nested
`array<array<…<uint256,inf>…,inf>,inf>`; it drops the remaining array
dimensions. The direct-access fast path deliberately avoids exactly
this by using `array.type().subtype()` instead — the nested branch
does not.

### Why the ≥3 depth threshold

- depth 2 (`m[k1][k2]`, e.g. Stage 2C's `mapping(uint=>mapping(
  uint=>S))`): the nested branch fires once for `m[k1][k2]`; there the
  correct element type *is* the leaf value (`S`/`uint256`) — `t` from
  get_type_description matches, no nesting is lost. Works.
- depth ≥3: the nested branch fires for `m[k1][k2]` (and deeper) where
  the correct type is still a **nested array**
  (`array<array<…>,inf>`), but `t` is the scalar/leaf value type ⇒
  intermediate `index` node is under-nested. `symex_assign_array`
  then propagates a non-array `new_rhs` into a `with2tc` whose
  `index.source_value->type` subtype is an array ⇒
  `with2t::assert_consistency` `is_array_type(b)` fails (depth 4–5),
  or `value_sett::assign` `base_type_eq` fails first (depth 3 + struct
  — value-set's stricter base-type check trips before the with2t one).

Both downstream assertions (`irep2_expr.cpp:366`,
`value_set.cpp:1258`) are the **same** root cause: the under-nested
intermediate-index type emitted at `solidity_convert_expr.cpp:4203`.

### Read vs write

The READ path (`v = _b[k1][k2][k3][k4]`,
`convert_array_index`/`index2t` chain) tolerates the under-nested
intermediate type and does not crash. Only the WRITE enforces
per-level `with2t::assert_consistency` / `value_sett::assign`
base-type equality, so the latent mis-typing is write-only-fatal.

## Fix location (sketch — NOT implemented; separate stage)

In the nested-mapping branch (solidity_convert_expr.cpp:4190-4204),
when `array.type().is_array()`, type the `index_exprt` with
`array.type().subtype()` (mirroring the direct-access fast path at
line 4174) instead of `t`, with the key typecast / `xor_fold` routed
consistently for the nested case. Risk surface: must not regress the
depth-2 path (where `t` currently coincides with the correct element
type) nor the `mapping_t` / `#sol_mapping_fixed_arr_value` /
fixed-array-value paths that intentionally flow through the helper
(lines 4176-4188). Requires its own gated stage:
KNOWNBUG→CORE flip targets = the 7 re-pinned aqua pilots
(`STAGE2C_FOLLOWUP_REPIN.md`) + a new 3/4/5-level minimal pass/fail
pair; full Solidity regression gauntlet; soundness probes
(read-back of the deep-nested write round-trips).

## Scope

This document is **diagnosis only**. No source change, no test
change, no commit. The fix is a distinct, separately-authorised stage.
