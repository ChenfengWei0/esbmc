# Stage 2C — PAUSED for scope reassessment (2026-05-15)

User decision: **暂停 2C 重新评估范围** (pause 2C, reassess scope),
taken when the 2C.2c specified mechanism was found to collide with
verified source *before any 2C.2c code was written* (per
`feedback_no_silent_substitution` — surface collisions BEFORE coding).

## Tree state after pause

| Sub-stage | Gate | Action taken |
|---|---|---|
| 2C.2a (`mk_struct_sort` recursion, node+sym) | G2a GREEN | **REVERTED** — inert scaffolding for paused fix |
| 2C.2b (router discriminator `is_tuple_array_ast_type`) | G2b GREEN | **REVERTED** — inert scaffolding for paused fix |
| 2C.2c (`mk_tuple_array_symbol` K≥2 decomposition) | — | **NOT WRITTEN** — design collision found first |

`git checkout --` on `smt_tuple_node.cpp`, `smt_tuple_sym.cpp`,
`smt_conv.cpp` restored the exact pre-2C baseline (W1 assert back at
`smt_tuple_node.cpp:238`; `src/solvers/smt/` working tree clean; baseline
rebuilds 100% clean). 2C.2a/2C.2b were K=1 byte-identical with zero
regression but produced **no verdict change for any test** (the target
nested shape still aborted at NW1 with both applied) — pure scaffolding.
Keeping dead scaffolding in the soundness-critical SMT backend for an
indefinitely-paused fix is rejected by the post-implementation pass; the
diffs are fully recoverable from `STAGE2C_2a_RESULT.md` /
`STAGE2C_2b_RESULT.md` if 2C resumes.

## The 2C.2c blocker (source-verified, the reason for the pause)

2C.2c specified: build per-field arrays by reusing the `make_free`
idiom (`smt_tuple_node_ast.cpp:35-41`) →
`array_conv.mk_array_symbol(name, convert_sort(array^K<fi>),
convert_sort(get_flattened_array_subtype(array^K<fi>)))`.

For the target shape (state-var mapping / dyn-array of struct = the
aqua / `nested_inf_array_of_struct_knownbug` case) the symbol is
`array^K<Struct>` with an **infinite outer** dim, so each per-field
array is `array^K<fi>`, also infinite-outer. Then:

- `get_flattened_array_subtype` (`smt_conv.cpp:4038-4041`): infinite-outer
  **and** subtype-is-array ⇒ returns the **immediate** subtype
  `array^{K-1}<fi>` — *an array*.
- `array_convt::mk_array_symbol` (`array_conv.cpp:92-95`):
  `assert(subtype->id != SMT_SORT_ARRAY && …)`.
- A solver-native nested-array sort from Branch A (`smt_conv.cpp:2858`)
  **still has `id == SMT_SORT_ARRAY`** — native-ness does not change the
  sort id.

⇒ For **every K≥2** per-field array the `array_conv.cpp:92` assert
fires. **R6's mitigation** ("array_conv gets the already-native
composite, not a bare nested sort") **is contradicted**: array_conv's
guard keys on `subtype->id`, not on bare-vs-native. The `make_free`
idiom is only safe for a **K=1** tuple-array member (subtype =
`Struct`, not array). It does not generalise to K≥2 as 2C.2c assumed.

The genuinely correct encoding (source-grounded): per-field
`array^K<primitive>` infinite-outer is natively supported by the
backend via Branch A (`mk_array_sort` all the way down — no bare sort,
no array_conv). So each per-field element should be a **solver-native
array symbol** (Branch-A sort + `mk_smt_symbol`), with select/`with`
dispatched to native array ops — *not* an `array_conv` symbol. The
struct-of-arrays `tuple_node_smt_ast` wrapper is still correct; only the
per-field leaf construction changes from the array_conv idiom to native
symbols. **This is a different mechanism than the one in the v2 design**
— hence the design must be revised before any further code, and the
user paused 2C to reassess scope first.

## Scope reassessment input (for the next decision)

What 2C is, restated against the governing 2C directive ("large change,
regression-prone, careful investigation FIRST; fixes may introduce huge
overhead making verification impossible; soundness/completeness must NOT
weaken; use IR-near-identical C++/Solidity comparison"):

1. **Impact if fixed.** Removes the `bare smt_sort` SIGABRT for
   nested-array(≥1 inf level)-of-tuple. Unblocks: aqua/Aqua pilot +
   `nested_inf_array_of_struct_knownbug` + any Solidity nested
   mapping/dyn-array whose value is a struct. This is 1 of the 5 pilot
   findings (the SIGABRT); the other 4 are `Reached: 0` / NO-TARGETS —
   *distinct symptoms, not unblocked by 2C*.

2. **True cost.** The fix is now confirmed to require a **new
   per-field-native-array select/with/project path** in the node
   flattener (not the cheap "reuse make_free idiom" the v2 design
   assumed). That is materially larger: it touches
   `tuple_node_smt_ast`'s array-member select/update/project for the
   K≥2 case, which is exactly the regression-prone, overhead-sensitive
   surface the 2C directive warned about. Overhead probes
   (`c_perfield_decomposed.c` 0.12 s) covered only *sort construction*
   cost, not the per-field native-array select/store chain at K≥2,
   which is unmeasured.

3. **Soundness/completeness.** The native-symbol mechanism is still the
   canonical sound encoding *if implemented correctly*, but the
   correctness burden (dim-order, index-chain over K native dims,
   round-trip + asymmetric-dim) now sits in select/with code, not just
   sort construction — a strictly larger verification obligation than
   the v2 G2c gate budgeted for.

4. **Options for the reassessment** (no code until chosen + authorised):
   - **A. Rewrite 2C.1 design (v3) to the native-symbol mechanism**,
     re-scope 2C.2c's gate (G2c) to cover the K≥2 native select/with
     path explicitly (round-trip + dual + asymmetric-dim + a pure-C
     IR-near-identical baseline for the select/store chain, not just
     sort construction), then re-authorise sub-stage by sub-stage.
   - **B. Narrow 2C scope** to only the shapes the backend *already*
     encodes natively without a new select/with path (if any subset of
     the pilot's nested shapes has a *finite* outer dim →
     `get_flattened_array_subtype` returns the scalar, array_conv assert
     is satisfied, make_free idiom works) and KNOWNBUG-pin the rest.
   - **C. Defer 2C entirely**, keep all 5 pilot findings KNOWNBUG-pinned
     (already done in Stage 0), and reallocate effort to the `Reached:
     0` cluster (4 of 5 findings — the larger comparison-blocker per the
     original plan's Stage 3).

## Soundness / completeness of the pause itself

- **Soundness**: unchanged — baseline fully reverted and rebuilt; zero
  delta vs pre-2C. The 5 pilot KNOWNBUG pins still hold (regex still
  matches `bare smt_sort` at the original NW1).
- **Completeness**: unchanged — no shape gained or lost a verdict.
- **Overhead**: unchanged — baseline binary.
