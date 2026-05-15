# Stage 2C.2d–2f — M3 struct-of-arrays — GATES G2d/G2e/G2f: GREEN

Generated 2026-05-15. Closes Stage 2C (`STAGE2C_DESIGN.md`).
**The `bare smt_sort (id=4)` abort on nested (K≥2) array/mapping-of-struct
is fixed; soundness & completeness strengthened, K=1 byte-identical.**

## Mechanism (4 code sites; refinement vs design §3.2)

The commutation is **OO dispatch on the tuple_node**, not explicit
branches in convert_member/index/store (those call select/update/
project unchanged → zero edits there; smaller blast radius, same M3
representation):

1. **NW0** `smt_conv.cpp` symbol router → `is_tuple_array_ast_type(expr->type)`
   (walks all dims to leaf). K=1 identical (old check also true for
   `array<S,inf>`); K≥2 now routed (2C.2b).
2. **NW1** `smt_conv.cpp` `array_id` case → a tuple-array carve-out
   *above* the nested-backend branch: `is_tuple_array_ast_type(type)`
   ⇒ `tuple_api->mk_struct_sort(flatten_array_type)` (bare recursive
   nested sort, never backend `mk_array_sort` with a bare struct
   range). Subsumes the pre-existing K=1/finite `mk_struct_sort` path
   (2876) → those byte-identical; only the K≥2-infinite case (which
   aborted) changes.
3. `smt_tuple_node.cpp` `mk_tuple_array_symbol` K≥2 struct leaf →
   struct-of-arrays `tuple_node` (sort = leaf STRUCT; `elements[i]` =
   solver-native `array^K<fi>` via `convert_ast(symbol2tc(...))`,
   primitive ⇒ Branch-A native, struct ⇒ recurse) (2C.2c).
   `convert_array_of_prep` K≥2 struct-leaf constant ⇒ fresh per-field
   SoA `tuple_node` (parity with the existing K=1 infinite-mapping
   modelling-only nondet init — **not** a new approximation; sidesteps
   bitwuzla's unsupported const-array equality). `rebuild_array_leaf`
   propagates `index_width` (ledger #22 480-bit Solidity key) so the
   per-field native domain matches the struct-array domain.
4. `smt_tuple_node_ast.cpp`: `select` distributes the index over each
   per-field native array (returns a same-leaf tuple_node, one dim
   lower); `update` (non-nil idx, array-sorted elements) distributes
   the store per field from the field-aligned value tuple_node;
   `assign` to an eager SoA symbol target falls back to field-wise
   `eq` (== default `smt_ast::assign`; the copy-elements fast path
   needs a free target). Model readback (`tuple_get_array_elem`,
   `smt_conv.cpp get_array`) returns an empty entry for the SoA
   tuple_node — counterexample extraction for nested tuple-arrays is
   unimplemented (same convention as `tuple_get_rec`'s
   `is_tuple_array_ast_type` member); the **verdict is the solver's
   and is unaffected**.

`tuple_node::eq` / `::ite` (per-member project) already work on the
native-array fields — no change. array_conv core, tuple_node project,
Branch A, K=1 path: untouched.

## Soundness (STRENGTHENED) — 7 manual probes, all correct

| Probe | Expected | Got |
|---|---|---|
| K=2 write→read same slot `m[i][j].a` | SUCCESSFUL | ✅ |
| dual `assert a==v+1` | FAILED | ✅ (not vacuous) |
| sibling `write .a, assert .b==v` | FAILED | ✅ (no field bleed) |
| both fields `write .a,.b assert both` | SUCCESSFUL | ✅ |
| dim-order `write [i][j], assert [j][i]==v, i≠j` | FAILED | ✅ (no i↔j alias) |
| cross-slot `assert [i][j]==v && [i][j+1]==v` | FAILED | ✅ (slot independent) |
| --cvc5 (native tuple_api) | SUCCESSFUL | ✅ (M3 inert there) |

No new approximation: per-field equalities are exactly the K=1
emissions lifted to K dims; init nondet = K=1 parity.

## Completeness (STRENGTHENED)

K≥2 nested mapping/array-of-struct gains a verdict (was abort).
K=1 / finite array-of-struct / non-struct-leaf nested arrays:
byte-identical (gate = leaf struct ∧ immediate subtype is array).

## Overhead

Linear in struct field count m (one native nested array per field) ×
native select/store chain depth K. Pin family wall-clock <1 s each.

## Flips (G2f) + regressions (G2e)

- Solidity `mapping_struct_smtsort_k2_knownbug` → `_k2_pass` (CORE
  `VERIFICATION SUCCESSFUL`). New CORE FAILED soundness companions:
  `_k2_{dual,sibling,dimorder}_fail`. `_k1_pass` CORE green
  (byte-identical guard).
- C `regression/esbmc/nested_inf_array_of_struct_knownbug` →
  `nested_inf_array_of_struct` (CORE `^Branch Coverage: 75%$`).
- Gauntlets: 29/29 (pre-readback-fix) then **final 21/21** on the
  shipped binary — Solidity + C + C++ struct·array·mapping·tuple
  slices, zero regressions. clang-format clean.

## Out of scope (unchanged)

3D+ state-var dyn-array; sym flattener / `--cvc5-native-tuples`;
non-struct (pointer/code/complex) nested-array leaves (fall through to
the historical path = unchanged); rich counterexample model display
for nested tuple-arrays (readback returns empty — verdict unaffected).

**G2d/G2e/G2f GREEN. Stage 2C CLOSED.**
