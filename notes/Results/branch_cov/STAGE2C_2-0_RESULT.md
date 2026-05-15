# Stage 2C.2-0 — M3 feasibility investigation — GATE G2-0: GREEN

Generated 2026-05-15. Investigation only — **no source change**. Closes
design sub-stage 2C.2-0 (`STAGE2C_DESIGN.md` §5). Verdict: **M3 is
confined to the lowering layer; G2-0 GREEN.** No `array_conv` or
`tuple_node` core change is required.

## 1. Exact post-symex shapes the lowering must handle

From `--show-vcc` on `mapping_struct_smtsort_k2_knownbug` (default
bitwuzla, `--contract C --unwind 8 --no-unwinding-assertions`), `m` :
`array<array<S{a,b}>,inf>`:

| # | Constraint | irep2 shape |
|---|---|---|
| `{-1}` | `m#1 == { { { .a = 0 } } }` | `symbol == constant_array` (nested). Infinite-outer ⇒ existing `tuple_array_create` is **modelling-only** (`smt_tuple_node.cpp:104-108`) — no precision concern |
| `{-22}` | `m#2 == (m#1 WITH [i:=m#1[i] WITH [j:=m#1[i][j] WITH [a:=v]]])` | nested `with2t`: **2 array-`with`s (i,j) wrapping 1 struct-field-`with` (.a)** |
| `{1}` | `m#2[i][j].a == v` | `member(index(index(m#2,i),j), .a)` — member-over-index-chain |
| `{-24}` | `m#4 == (cond ? m#2 : m#3)` | tuple-array `if2t` → existing `tuple_node::ite` (R8, below) |

The struct-field `with [a:=v]` is the **existing** constant-field
`tuple_node::update` path (`smt_tuple_node_ast.cpp:135-154`,
`is_nil_expr(idx_expr)` branch) — *not* new. Only the two **array**
`with`s and the index-chain `select`s are the problem.

## 2. The three interception points (all in designated convert_* fns)

K≥2 gate predicate (precise, source-grounded): root symbol type `T` with
`is_tuple_array_ast_type(T)` (tuple leaf, `smt_tuple_sort.h`) **AND**
`is_array_type(to_array_type(T).subtype)` (≥2 dims). For K=1
`array<S,inf>` the immediate subtype is `S` (not array) ⇒ predicate
**false** ⇒ existing K=1 path untouched ⇒ byte-identical (R1/R2).
Matches the verified `get_flattened_array_subtype` infinite-outer
behaviour (`smt_conv.cpp:4038-4041`).

| # | Function (file:line) | Commutation |
|---|---|---|
| I1 | `smt_convt::convert_member` — `smt_conv.cpp:3458` (guard before the generic `src=convert_ast(member.source_value); return src->project(idx);`) | `member(index_chain(root), f)` with K≥2 tuple-array root ⇒ `field=convert_ast(root)->project(idx_f)` (native `array^K<f>`), then native `select` per chain index outer→inner. Field projected **before** any select ⇒ `tuple_node::select` (abort, :160) never reached. |
| I2 | `smt_convt::convert_array_index` — `smt_conv.cpp:3800` | whole-element read (index-chain root K≥2 tuple-array, `expr->type` struct, no enclosing member) ⇒ build fresh `tuple_node`, `elements[i]=project(root,i)` then native K-select. Symmetric to I1; exercised by a gauntlet case (the pinned test only does member access). |
| I3 | `smt_convt::convert_array_store` — `smt_conv.cpp:3852` | with-target K≥2 tuple-array ⇒ descend the with-chain to the leaf struct-field-`with` (index path, field, value); new `tuple_node`: written field = native K-deep `store`-chain on `project(src,f)`; untouched fields = structural share `project(src,g)`. |

Per-field array `array^K<primitive>` ⇒ `convert_sort` routes through
**Branch A** (`smt_conv.cpp:2847-2861`) to a solver-**native** nested
array (no bare sort — verified: `c_nested_inf_primitive.c` 0.118 s,
prior). Its `select`/`update` are solver-native (reached via
`convert_ast` on the projected native array), **not** `array_conv`.

## 3. Confinement proof (the crux of G2-0)

- **`array_conv.cpp`**: not changed, not reached. The K≥2 tuple-array
  path never calls `array_conv.mk_array_symbol` (projection precedes
  every select; per-field arrays are native). The `array_conv.cpp:92`
  `subtype->id != SMT_SORT_ARRAY` assert (the v2 blocker) is structurally
  unreachable on this path.
- **`smt_tuple_node_ast.cpp` (tuple_node core)**: not changed.
  `project` used read-only (returns `elements[i]`). `select`
  (abort, :160) and `update` (constant-field-only, :135-154) are
  **never** invoked on the symbol's tuple_node — the commutation
  projects before selecting and builds result tuple_nodes via the
  constructor + `elements` vector (the existing `tuple_create` idiom,
  `smt_tuple_node.cpp:9-28`). `make_free` does not run: 2C.2c's
  `mk_tuple_array_symbol` pre-populates `elements`, so
  `make_free`'s `if (elements.size()!=0) return;` (:18-19) short-circuits.
- **New code lives only in**: I1/I2/I3 (the three designated lowering
  functions) + `mk_tuple_array_symbol` (`smt_tuple_node.cpp`, the
  designated 2C.2c file, building the native per-field tuple_node — same
  shape as `tuple_create`).
- **Branch A**: unchanged, reused as-is.

⇒ M3 touches exactly the lowering layer + the designated 2C.2c symbol
builder. **No array_conv / tuple_node core change. G2-0 GREEN.**

## 4. Hand-trace

**Read** `m#2[i][j].a` (`{1}`), `m#2`:`array<array<S>,inf>` (K=2):
1. `convert_member(member(index(index(m#2,i),j), .a))`. I1 gate: source
   is index-chain, root `m#2` is K≥2 tuple-array → commute.
2. `t = convert_ast(m#2)` → 2C.2c tuple_node `{a: A_a, b: A_b}`,
   `A_f`:native `array<array<uint256>,inf>`.
3. `fa = t->project(idx_a)` = `A_a` (existing project, elements[0]).
4. `r = fa->select(fix_array_idx(i))->select(fix_array_idx(j))` —
   solver-native selects. Result: `uint256` ast.
5. assertion `r == convert_ast(v)`. No tuple_node::select, no array_conv.

**Write** `{-22}` `m#2 == (m#1 WITH [i:=…WITH [j:=…WITH [a:=v]]])`:
1. `convert_array_store(with(m#1,i,INNER))`. with-type K≥2 tuple-array →
   I3 commute. Descend: outer idx `i`, mid idx `j`, leaf struct-field
   `a`, value `v`.
2. `s = convert_ast(m#1)` → tuple_node `{a:A_a, b:A_b}`.
3. written field a: `A_a' = store(store(A_a, key_i_subarray…), key_j, v)`
   — native K-deep store on `project(s,a)`.
4. untouched field b: share `project(s,b)` = `A_b`.
5. result = new tuple_node `{a:A_a', b:A_b}` (constructor+elements, the
   `tuple_create` idiom). `m#2 == result` via `tuple_node::eq`
   (existing, :106-133 — per-field eq). No array_conv, no
   tuple_node-core change.

## 5. IR-near pair (rewrite target well-formed)

`notes/Results/branch_cov/stage2c_2-0_ir_near/` (checked-in):

| Program | Shape | Result |
|---|---|---|
| `arr_of_struct.c` | **finite** `S g[4][4]` array-of-struct | SUCCESSFUL 0.010 s — *does not abort*: finite chains flatten via `decompose_select_chain` (different path). Confirms the bug is **infinite-outer-specific**. |
| `struct_of_arr.c` | `struct{uint a[4][4]; uint b[4][4];}` = **M3's lowering target** | SUCCESSFUL 0.001 s — the per-field decomposition is trivially well-formed & fast. |

Faithful **infinite-outer** analogs (the actual bug shape) already
exist and are pinned: C `regression/esbmc/nested_inf_array_of_struct_knownbug`
(infinite nested-of-struct → same `bare smt_sort` abort ⇒ bug is
language-agnostic infinite-outer routing) and prior
`c_nested_inf_primitive.c` (infinite nested-of-**primitive** →
SUCCESSFUL 0.118 s ⇒ M3's per-field native target is well-formed & fast
for the infinite case too). Evidence chain is complete.

## 6. Risks surfaced (for later gates, none a G2-0 blocker)

- **R8 (new)**: tuple-array `if2t` (`{-24}` `cond ? m#2 : m#3`) →
  `tuple_node::ite` (`smt_tuple_node_ast.cpp:56+`): per-member `project`
  then `ite`. For M3 repr the members are native arrays → native-array
  `ite` (solver-supported). Existing-machinery reuse, no core change;
  **verify at G2c/G2d** (round-trip through the `cond?:` guard the
  dispatcher emits — already present in the pinned test's VCC).
- **R7 restated**: per-field native nested select/store chain depth = K;
  cost linear per field. `struct_of_arr.c` 0.001 s, `c_nested_inf_primitive.c`
  0.118 s bound it; hard wall-clock gate at G2d (≤ ~1 s).

## 7. Soundness / completeness of this sub-stage

Investigation only — **zero source delta**. Soundness/completeness/
overhead of the tree unchanged (clean pre-2C baseline). The
mapping_struct_smtsort pin pair (k1 CORE / k2 KNOWNBUG) still 2/2.

## 8. Verdict

**G2-0 GREEN.** M3 is fully expressible in `convert_member` /
`convert_array_index` / `convert_array_store` + the designated 2C.2c
`mk_tuple_array_symbol`, with the K≥2 gate keeping K=1 byte-identical
and array_conv / tuple_node cores untouched (proven §3, hand-traced §4,
target well-formed §5). Per the per-substage authorisation contract,
**stop here and await explicit authorisation for 2C.2a** (re-apply the
proven-clean `mk_struct_sort` recursion, gate G2a).
