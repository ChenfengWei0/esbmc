# Stage 2C.1 (v3) — Native-per-field + index/project commutation design

> **STATUS: v3 DESIGN, design-only. Awaiting per-sub-stage authorisation.**
> Supersedes v1 (sym-flattener — wrong path) and v2 (node-flattener but
> the 2C.2c "reuse the `make_free` array_conv idiom" mechanism collides
> with `array_conv.cpp:92` for every K≥2; see `STAGE2C_PAUSE.md`). v1/v2
> full history: `STAGE2C_PAUSE.md` + git. SMT-backend tree is the clean
> pre-2C baseline (2C.2a/2C.2b reverted, rebuilt 100%). No 2C code lands
> without fresh authorisation against *this* document, sub-stage by
> sub-stage, each closing with a gate. Any gate RED → revert that
> sub-stage.

Generated 2026-05-15. Authorised path: user chose "A. rewrite v3
design (native-symbol mechanism)". Investigation (this doc §2) shows the
native-symbol mechanism is only viable as **M3 — per-field solver-native
arrays + an explicit, K≥2-gated index/project commutation in the
tuple-array lowering** — *not* a drop-in symbol swap, because the node
flattener does select-then-project with no commutation and
`tuple_node_smt_ast::select` aborts.

---

## 1. The bug (unchanged from v2, restated)

Default node flattener (`smt_tuple_node_flattener`; bitwuzla/boolector
per `solve.cpp:154-164`; cvc5/z3 use native tuple_api). A symbol of type
`array^K<Struct{f1..fm}>` with **any infinite array level** (Solidity
state-var `mapping(K=>S)`, `S[]`, nested) aborts with
`bare smt_sort (id=4) reached to_solver_smt_sort<>`. Pilot: aqua/Aqua;
cross-language KNOWNBUG `regression/esbmc/nested_inf_array_of_struct_knownbug`.

## 2. Verified architecture (every claim source-cited, 2026-05-15)

| Fact | Source |
|---|---|
| `convert_member` is **select-then-project, no commutation**: `src = convert_ast(member.source_value); return src->project(idx);` | `smt_conv.cpp:3458-3466` |
| `convert_array_index` for an infinite chain: `a = convert_ast(src); a = a->select(this, newidx);` (recurses outer-to-inner) | `smt_conv.cpp:3825-3849` |
| `convert_array_store` symmetric: `src->update(this, update, 0, newidx)` | `smt_conv.cpp:3868-3893` |
| `tuple_node_smt_ast::select` → `log_error("Select operation applied to tuple"); abort();` | `smt_tuple_node_ast.cpp:156-162` |
| `tuple_node_smt_ast::project` → `elements[idx]`; `update` → constant struct-field idx only (`assert(is_nil_expr(idx_expr))`) | `smt_tuple_node_ast.cpp:135-181` |
| `make_free` per member: struct→`tuple_fresh`; tuple-array→`array_conv.mk_array_symbol(newname,newsort,subsort)`; array→`mk_fresh`; scalar→`mk_fresh` | `smt_tuple_node_ast.cpp:16-54` |
| Branch A: infinite nested array w/ array subtype ⇒ `r=convert_sort(subtype); result=mk_array_sort(d,r)`. Native if leaf primitive; **bare** if leaf struct = NW1 | `smt_conv.cpp:2847-2861` (NW1 = :2858) |
| `get_flattened_array_subtype`: infinite-outer + array-subtype ⇒ returns the **immediate** (array) subtype, not the leaf | `smt_conv.cpp:4031-4052` |
| `array_convt::mk_array_symbol`: `assert(subtype->id != SMT_SORT_ARRAY …)`; single-level only | `array_conv.cpp:87-133` (:92) |
| Symbol router: `is_tuple_ast_type(get_flattened_array_subtype(...))` → `mk_tuple_array_symbol` — too shallow for nested (NW0) | `smt_conv.cpp:3050-3057` |
| `mk_tuple_array_symbol` (node): `array_conv.mk_array_symbol(name, convert_sort(flatten_array_type(t)), convert_sort(get_flattened_array_subtype(t)))` | `smt_tuple_node.cpp:68-77` |

**Consequence.** For `((grid[a])[b]).f` the only lowering is
`convert_ast(grid)->select(a)->select(b)->project(f)` (no commutation
exists). The result of the selects must be struct-sorted for `project`.
A native solver array cannot carry a struct leaf (that *is* NW1).
`tuple_node_smt_ast` cannot be selected (aborts). Therefore the only
encodings consistent with the existing convert_ast architecture are:

- **M1 — recursive array_conv**: array_conv elements are themselves
  array_asts, recursing one array level per `select`/`update` down to a
  struct-subtype innermost level. Requires relaxing `array_conv.cpp:92`
  and threading recursion through the SAT-level array decision
  procedure used by **every** node-flattener array. Blast radius =
  whole array_conv; overhead = the SAT array procedure replicated per
  nested level → squarely the "huge overhead makes verification
  impossible / regression-prone" risk the 2C directive forbids.
  **Rejected as primary** (kept as the fallback only if M3's
  investigation gate fails).
- **M3 — per-field native arrays + explicit commutation** (chosen):
  represent the symbol as a `tuple_node` whose `elements[i]` is a
  **solver-native** array of `array^K<fi>` (fi primitive ⇒ Branch A
  native, no bare sort, no array_conv), and add a **new, K≥2-gated
  commutation** to the tuple-array lowering so reads/writes become
  `project`-first then native select/store: `((grid[a])[b]).f` ⇒
  `select(select(project(grid,f), a), b)`. This is the canonical
  struct-of-arrays encoding (= manual `c_perfield_decomposed.c`,
  0.12 s; = z3-internal datatype lowering). Blast radius = the
  tuple-array index/with/member lowering only (gated by
  `is_tuple_array_ast_type` **and** K≥2); array_conv core, tuple_node
  core, Branch A, and the K=1 path are **untouched**. Overhead = native
  nested-array select chains × m fields = **linear in m**, the
  directive's accepted envelope.

## 3. M3 mechanism (the v3 fix)

### 3.1 Representation

`mk_tuple_array_symbol` (node), for a **K≥2** tuple-leaf symbol
`array^K<S{f1..fm}>` (gate: leaf is tuple AND its immediate array
subtype is itself an array — i.e. ≥2 array dims; K=1 unaffected):
build a `tuple_node_smt_ast` of the array-of-struct sort whose
`elements[i]` = `mk_smt_symbol(name.fi, convert_sort(array^K<fi>))`,
where `array^K<fi>` is the symbol's K-dim array_type chain rebuilt with
the struct leaf replaced by field type `fi`. For primitive `fi`,
`convert_sort(array^K<fi>)` is Branch-A solver-native (no bare sort, no
array_conv). Nested-struct fields (`fi` itself a struct) recurse the
same construction. K=1 keeps the **existing** `array_conv.mk_array_symbol`
(struct subtype) route verbatim → byte-identical.

### 3.2 Commutation (the new transformation, gated)

In `convert_member` / `convert_array_index` / `convert_array_store`,
when the access root is a **K≥2 tuple-array symbol**
(`is_tuple_array_ast_type(root->type)` and ≥2 dims):

- **Read** `member(index_chain(grid,[i1..iK]), f)` ⇒
  `S = project(convert_ast(grid), idx_f)` (native per-field array) then
  K native `select`s with `[i1..iK]` outer→inner. Whole-element read
  `grid[i1..iK]` without a field (struct copy/eq/return) ⇒ build a
  fresh `tuple_node`, `elements[i]` = K-select-chain on field array i.
- **Write** `with(grid,[i1..iK] := …)` ⇒ new `tuple_node`, structural
  sharing of unaffected fields; affected field(s) get a K-deep native
  `store`-chain. Field-targeted write ⇒ one field's store-chain;
  whole-struct write ⇒ per-field store-chains from the RHS struct's
  projected fields.
- The commutation is the **only** new behaviour; `tuple_node::select`
  (abort) and `array_conv` are never reached for the K≥2 tuple-array
  path because projection happens before any `select`.

### 3.3 Untouched (K=1 byte-identical, R1)

array_conv core; `tuple_node` core select/update/project; Branch A;
the symbol router after 2C.2b's K=1-equivalent broadening; every
single-level array-of-struct path (`mapping_*` struct value,
`napp_map_*`, `nested_array_*`). The K=1 gate ensures the commutation
never fires for single-level → before/after SMT2 byte-identical.

## 4. Walls

| Wall | Loc | Nature |
|---|---|---|
| NW0 | `smt_conv.cpp:3056` | router discriminator too shallow for nested (2C.2b) |
| NW1 | `smt_conv.cpp:2858` | Branch A `mk_array_sort(d, bare-struct-sort)` — the user-visible abort; defused once M3 routes every tuple leaf through per-field native arrays. Defensive tripwire assert added here in the final sub-stage. |

## 5. Sub-stages (each gated, each separately authorised)

```
2C.2-0  M3 feasibility investigation (NO source change)      → G2-0 ✅ GREEN (2026-05-15) →
2C.2a   mk_struct_sort recursion (re-apply, proven clean)     → G2a ✅ GREEN (re-applied 2026-05-15) →
2C.2b   router discriminator → is_tuple_array_ast_type        → G2b ✅ GREEN (2026-05-15) →
2C.2c   mk_tuple_array_symbol K≥2 per-field NATIVE symbols     → G2c ✅ GREEN (2026-05-15) →
2C.2d   commutation (OO: select/update/assign distribute)      → G2d ✅ GREEN (2026-05-15) →   ← semantic core
2C.2e   full regression gauntlet (+ --cvc5 slice)              → G2e ✅ GREEN (2026-05-15) →
2C.2f   KNOWNBUG→CORE flips + new CORE tests                   → G2f ✅ GREEN (2026-05-15)

> **STAGE 2C CLOSED 2026-05-15.** Mechanism refinement vs design §3.2:
> the commutation was implemented as OO dispatch on the tuple_node
> (`tuple_node_smt_ast::select` distribute, `::update` per-field native
> store, `::assign` fall back to field-wise eq for the eager SoA symbol
> target) rather than as explicit branches in convert_member/index/
> store — those three call select/update/project unchanged and need
> ZERO edits (cleaner, smaller blast radius, same M3 representation,
> identical soundness/completeness). Four code sites total:
> (a) `smt_conv.cpp` NW0 router → is_tuple_array_ast_type (2C.2b);
> (b) `smt_conv.cpp` NW1 array_id case → tuple-array carve-out above
> the nested-backend branch (subsumes the K=1/finite mk_struct_sort
> path → byte-identical there);
> (c) `smt_tuple_node.cpp` mk_tuple_array_symbol K≥2 SoA build (2C.2c)
> + `convert_array_of_prep` K≥2 constant → fresh per-field SoA (parity
> with the existing K=1 infinite-mapping modelling-only nondet init —
> NOT a new approximation; avoids bitwuzla's unsupported const-array
> eq); rebuild_array_leaf propagates index_width (ledger #22 480-bit);
> (d) `smt_tuple_node_ast.cpp` select/update/assign distribute;
> tuple_get_array_elem + `smt_conv.cpp` get_array return empty for the
> SoA tuple_node (model-readback unimplemented for nested tuple-array,
> same convention as tuple_get_rec — verdict from solver, unaffected).
> Soundness: STRENGTHENED (NW1 abort removed, no new approximation; 7
> manual probes — round-trip, dual, sibling-independence, dim-order
> non-aliasing, cross-slot — all correct; dual/sibling/dimorder landed
> as CORE FAILED regressions). Completeness: STRENGTHENED (K≥2
> nested-mapping/array-of-struct gains a verdict; K=1/finite/non-tuple
> byte-identical). Overhead: linear in m (one native nested array per
> field); pin family <1 s. Flips: Solidity
> `mapping_struct_smtsort_k2_knownbug`→`_k2_pass` (CORE SUCCESSFUL) and
> C `regression/esbmc/nested_inf_array_of_struct_knownbug`→
> `nested_inf_array_of_struct` (CORE, Branch Coverage 75%). Gauntlets:
> 29/29 then final 21/21 (Solidity+C+C++ struct·array·mapping slices,
> zero regressions); --cvc5 inert (native tuple_api path untouched).
```

- **2C.2-0 (investigation, no code)**: prove M3's commutation is
  expressible purely in `convert_member`/`convert_array_index`/
  `convert_array_store` (tuple-array branch) **without** touching
  array_conv or `tuple_node` cores; identify the exact insertion points
  and the K≥2 gate predicate; build a pure-C / Solidity IR-near-identical
  pair (`grid[a][b].f` round-trip) and dump its GOTO + the *intended*
  per-field native select/store SSA to confirm the rewrite target is
  well-formed. **Gate G2-0**: a written feasibility note with
  file:line insertion points + the IR-near pair + a hand-traced lowering
  of one read and one write. If M3 cannot be confined to the lowering
  layer → STOP, escalate M1-vs-defer decision (do not silently fall to
  M1).
- **2C.2a / 2C.2b**: re-apply the proven-clean reverted edits (static
  K=1-identity proofs in `STAGE2C_2a_RESULT.md` / `STAGE2C_2b_RESULT.md`
  still hold verbatim). Gates G2a/G2b as before (build clean, K=1
  byte-identical, KNOWNBUGs still pinned at NW1, focused subset ≥
  Stage-2B baseline 89/91).
- **2C.2c**: extend `mk_tuple_array_symbol` for the K≥2 native per-field
  representation only (no read/write change yet). **Gate G2c**: build
  clean; K=1 byte-identical (gate ensures untouched); the KNOWNBUG now
  aborts *later* (inside the commutation gap, not NW1) or still NW1 —
  documented, no new failure mode; no verdict change.
- **2C.2d (semantic core)**: the commutation. **Gate G2d (strictest)**:
  cross-language KNOWNBUG `nested_inf_array_of_struct_knownbug` emits
  `Branch Coverage:` (no abort/timeout; ≤ ~1 s vs `c_perfield_decomposed.c`
  0.12 s). Pure-C, no dispatcher: `grid[a][b].f=V; assert(grid[a][b].f==V)`
  → SUCCESSFUL **and** dual `…!=V` → FAILED (no false either way).
  **Asymmetric dims 2×3 vs 3×2** (dim-order soundness). Multi-field
  struct: writing `.f` leaves `.g` unchanged (structural-sharing
  soundness). 3 single-level regressions byte-identical SMT2. Focused
  subset ≥ 89/91.
- **2C.2e**: `ctest -L esbmc-solidity` focused then capped full; C/C++
  slice `esbmc/.*(array|struct|tuple)` + `esbmc-cpp/.*(array|struct|map)`;
  `--cvc5` slice (native tuple_api, must stay green — M3 is
  node-flattener-only). **Gate G2e**: PASS ≥ Stage-2B baseline, no
  pre-CORE C/C++ flip to FAILED.

## 6. Risk register (v3)

| # | Risk | Mitigation |
|---|---|---|
| R1 | M3 shares `mk_tuple_array_symbol`/lowering with the WORKING K=1 path | K≥2 gate (≥2 array dims) — K=1 takes the unchanged branch; before/after SMT2 diff on 3 single-level tests at G2c & G2d |
| R2 | 2C.2b discriminator swap changes K=1 routing | `is_tuple_array_ast_type(array<Struct,inf>)` ≡ old check for K=1 (proven, `STAGE2C_2b_RESULT.md`); G2b diff probe; one-line revert |
| R3 | Commutation dim-order / index-chain bug ⇒ wrong slot (soundness) | G2d round-trip + dual + **asymmetric 2×3 vs 3×2** are hard soundness gates |
| R4 | `with` on one field corrupts/aliases sibling fields (soundness) | G2d multi-field "write `.f`, assert `.g` unchanged" gate |
| R5 | cvc5/z3 native tuple_api diverges | M3 is node-flattener-only files; G2e `--cvc5` slice; expected inert, verified |
| R6 | M3 cannot be confined to the lowering layer (forces array_conv/tuple_node core change) | **2C.2-0 G2-0 gate fails ⇒ STOP & escalate** (no silent fall to M1); M1 is documented & rejected, not a hidden default |
| R7 | Native nested-array select-chain overhead at K≥2 super-linear | G2-0 IR-near pure-C/Solidity baseline measures the *select/store chain* (not just sort construction, v2's gap); G2d wall-clock ≤ ~1 s gate |

## 7. Soundness / completeness

- **Soundness**: strengthen. M3 = canonical per-field
  parallel-native-array decomposition (= `c_perfield_decomposed.c`, =
  z3 datatype lowering). Removes the NW1 abort with **no new
  approximation**; per-field equalities are exactly what the K=1 path
  emits, lifted to K dims. K≥2-gated ⇒ K=1 byte-identical ⇒ zero change
  on 720+ Solidity / 2000+ C/C++ tests. Soundness gates: G2d
  round-trip + dual + asymmetric-dim + sibling-field.
- **Completeness**: strengthen. Nested-array/mapping-of-struct shapes
  that abort today gain a verdict; no new false-FAILED (no
  over-approximation introduced; commutation is an exact rewrite of the
  same read/write semantics).
- **Overhead**: linear in struct field count m (one native nested array
  per field) + native select/store chain depth K. Bounded by G2-0
  baseline + G2d ≤ ~1 s wall gate; M1 (super-linear, rejected) avoided.

## 7a. Non-coverage pin (landed 2026-05-15, the CORE-flip target)

Before any v3 code, the bug is now pinned in **plain non-coverage
verification** (the prior pins — `cov_pilot_aqua_Aqua`,
`regression/esbmc/nested_inf_array_of_struct_knownbug` — are both
coverage-mode and indirect). Minimal dual-sentinel, default solver
(bitwuzla = the node flattener; **not** `--cvc5`, which uses native
tuple_api and would not reproduce the node-flattener abort), flags
`--contract C --unwind 8 --no-unwinding-assertions`, only the K
dimension differs:

| Test | Mode | Shape | Today |
|---|---|---|---|
| `regression/esbmc-solidity/mapping_struct_smtsort_k1_pass` | CORE | `mapping(uint=>S)` (K=1) | `VERIFICATION SUCCESSFUL` — working-boundary guard (R1) |
| `regression/esbmc-solidity/mapping_struct_smtsort_k2_knownbug` | KNOWNBUG | `mapping(uint=>mapping(uint=>S))` (K=2) | `bare smt_sort` abort; regex `^VERIFICATION SUCCESSFUL$` NO-MATCH ⇒ KNOWNBUG held |

Empirically verified (manual run captured the `bare smt_sort (id=4)
reached to_solver_smt_sort<>` abort) and via ctest: both PASS
(2/2, k1 0.49 s, k2 0.72 s). The v3 fix's success criterion at
**G2d / 2C.2f** is the `mapping_struct_smtsort_k2_knownbug`
KNOWNBUG→CORE flip (regex starts matching `^VERIFICATION SUCCESSFUL$`)
**with `mapping_struct_smtsort_k1_pass` still CORE-green** (K=1
byte-identical). This replaces the earlier "should add a non-coverage
pin" note — it now exists.

## 8. Out of scope

- 3D+ state-var dyn-array (separate open item; see
  `reference_under_approx_3d_dynarr_statevar_havoc`).
- sym flattener / `--cvc5-native-tuples` rework unless G2e demands.
- The other 4 pilot findings (`Reached: 0` / NO-TARGETS) — orthogonal to
  2C, KNOWNBUG-pinned in Stage 0.
