# `array_conv.cpp` — Universal Array Flattener

`src/solvers/smt/array_conv.{cpp,h}` (1377 + 454 LOC). Kroening-style
array decision procedure. Used when the backend doesn't advertise
native array theory support, or when `--array-flattener` is set
explicitly.

Instantiated as `class array_convt : public array_iface`. The
factory in `solve.cpp:173` wires it up:

```cpp
if (array_api != nullptr && !array_flat)
  ctx->set_array_iface(array_api);    // backend native
else
  ctx->set_array_iface(new array_convt(ctx));   // universal flattener
```

For the Solidity default (cvc5 / bitwuzla / z3), native array support
is advertised and `array_convt` is **not** used — unless the flattener
is forced on. But for boolector, `array_convt` is the default (the
upstream boolector support predates native arrays).

This doc also covers the **KNOWNBUG #3** — the assertion at
`array_conv.cpp:92-95` blocking array-of-array.

## The bookkeeping — what the flattener remembers

For each root array (each `mk_array_symbol` call), `array_convt`
allocates an integer `base_array_id` and stores five parallel vectors:

| Vector | Per-index | Purpose |
|---|---|---|
| `array_indexes[id]` | `(idx, ctx_level)` | every index ever accessed (for bounded, enumerated explicitly; for unbounded, recorded on select/store) |
| `array_selects[id]` | `(src_array_update_num, idx, val, ctx_level, converted)` | each select that was made, with a placeholder `val` ast and a back-pointer to the update that was current at select time |
| `array_updates[id]` | `array_with` — either a store or an ite | the full history of modifications; each one gets its own `update_level` |
| `expr_index_map[id]` | `(idx, vec_idx, ctx_level)` | index expression → position in the parallel value vectors |
| `array_valuation[id]` | per-update-level × per-index `smt_astt` | the collated "element value at this point in history" grid |

Plus three global maps:

- `array_subtypes` — per base array id, the element sort.
- `array_relations` — per base array id, the set of other base arrays
  that can flow into this one via ite/equality (needed for
  cross-array ite and joins).
- `array_equalities` — recorded equalities between arrays, resolved at
  constraint time.
- `array_of_vals` — for `array_of` initializers, the init value.

## Bounded vs. unbounded

**Bounded** arrays (`domain_width ≤ 10`, i.e. ≤ 1024 elements) are
represented as a plain `std::vector<smt_astt>` of size `2^domain_width`.
`mk_select` and `mk_store` operate on it directly:

- `mk_select(a, idx)` — if idx is const, return the slot; if symbolic,
  emit a chain of `ite(idx == i, a[i], fresh)`.
- `mk_store(a, idx, v)` — if const, overwrite the slot; if symbolic,
  emit `a'[i] = ite(idx == i, v, a[i])` for every i.

**Unbounded** arrays (`domain_width > 10` or `size_is_infinite`) use
the lazy Ackermann procedure:

1. Every select / store / ite / equality is recorded on the per-array
   vectors — not immediately converted to SMT.
2. At `add_array_constraints_for_solving()` time (called from
   `pre_solve`), five passes run:
   - `join_array_indexes` — compute fixedpoint closure of related
     arrays, propagate each array's index set to every array it can
     flow into.
   - `add_new_indexes` — allocate slots in `array_valuation` for each
     new index, re-run initial Ackermann axioms
     (`idx_i == idx_j ⇒ val_i == val_j`).
   - `execute_new_updates` — chain every recorded `with` / `ite`
     operation through `execute_array_trans`, computing the
     per-update-level value for every index.
   - `apply_new_selects` — for each recorded select, pin the
     placeholder `val` to `array_valuation[id][update_num][vec_idx]`.
   - `add_array_equalities` — decompose each array equality into
     per-index value equalities.

This is classic Kroening-style: store the abstract operations during
formula building, run the Ackermann encoding only when we know the
full set of indexes touched.

## Indexes live across push/pop

Every idx_record / array_select / array_update / index_map_rec carries
a `ctx_level`. `pop_array_ctx` erases entries whose `ctx_level ==
popped_level`. The boost multi_index container is indexed on both
(primary key) and ctx_level (secondary), so erasure is a single
`get<1>().erase(level)` call.

## `mk_array_symbol` — the KNOWNBUG assertion

`array_conv.cpp:87-133`. The entry point called from every place that
introduces an array variable (including tuples of structs — the tuple
flattener calls back into `array_convt::mk_array_symbol` for its
struct-array fields).

```cpp
smt_astt array_convt::mk_array_symbol(
  const std::string &name,
  smt_sortt ms,
  smt_sortt subtype)
{
  assert(
    subtype->id != SMT_SORT_ARRAY &&
    "Can't create array of arrays with "
    "array flattener. Should be flattened elsewhere");          // [1]

  size_t domain_width = ms->get_domain_width();
  size_t array_size = 1UL << domain_width;

  array_ast *mast = new_ast(ms);
  mast->symname = name;

  if (is_unbounded_array(mast->sort)) {                         // [2]
    mast->base_array_id = new_array_id();
    mast->array_update_num = 0;
    array_subtypes.push_back(subtype);
    return mast;
  }

  // Bounded: allocate 2^domain_width fresh elements
  mast->array_fields.reserve(array_size);
  for (unsigned long i = 0; i < array_size; i++) {              // [3]
    smt_astt a = ctx->mk_fresh(subtype, "array_fresh_array::");
    mast->array_fields.push_back(a);
  }

  return mast;
}
```

**[1]** is the KNOWNBUG-triggering assertion. Reads: "if you're
trying to register an array whose element sort is *itself* an array
sort, the flatten-type pass should have collapsed it into a
single-domain array by now. If you reach me with array-of-array, I
don't know how to encode you."

## Why this assertion exists

The Ackermann procedure maintains a per-element value slot keyed on
a flat index. An element that is itself an array would require a
**second level of Ackermann** — a whole nested set of
`array_indexes` / `array_selects` / `array_updates` per outer
element — and the infrastructure here has no machinery for that.

**`smt_conv.cpp:2800` is where flattening should have handled this**:

```cpp
case array_id:
{
  const array_type2t &arrtype = to_array_type(type);

  // Path A: nested infinite arrays (e.g. Solidity nested mappings)
  if (arrtype.size_is_infinite && is_array_type(arrtype.subtype)) {
    type2tc t = make_array_domain_type(arrtype);
    smt_sortt d = mk_int_bv_sort(t->get_width());
    smt_sortt r = convert_sort(arrtype.subtype);       // RECURSIVE — stays nested
    result = mk_array_sort(d, r);
    break;
  }

  // Path B: nested finite arrays — flatten with flatten_array_type
  type2tc t = make_array_domain_type(to_array_type(flatten_array_type(type)));
  ...
}
```

Path A produces an `Array(BV, Array(BV, V))` sort — a nested array
sort. When the backend has **native** array theory (bitwuzla, cvc5,
z3), the native implementation handles nested sorts directly.
**`array_convt` does not handle them** — hence the assertion.

Path B flattens `T[M][N]` finite to `T[M*N]` single-dim, so
`array_convt` only ever sees the flat form. This path is sound.

## Exactly when the assertion fires

The assertion fires when **both** of:
1. The backend does not advertise native array theory (so
   `array_api == array_convt`), OR `--array-flattener` is set.
2. A user-level expression has type `T[N][]` or
   `mapping(K => T[N])` — an **unbounded outer** array with a
   **bounded inner** array, i.e. nested array where the **outer**
   is infinite.

In that case:
- `convert_sort` hits Path A (outer infinite, subtype is array).
- Recurses into `convert_sort(subtype)` which produces a bounded
  Array sort (Path B applies at recursion but is a no-op since the
  inner array is already single-dim).
- Result is `Array(BV, Array(BV, V))` — the outer nested array sort.
- The user then does `sol_mapping[key] = value` or allocates a
  symbol of that type. `mk_array_symbol` is called with
  `subtype = Array(BV, V)` — an array sort.
- **Assertion fires.**

## The two affected patterns

### Pattern 1 — `mapping(K => T[N])`

Solidity:
```solidity
contract C { mapping(uint => uint[4]) m; }
```

The frontend models `m` as `array_typet(array_type(uint, 4), inf)` —
infinite outer, fixed inner. This is **unavoidable** at the
frontend level because mappings inherently have unbounded key
domains.

Under a non-native-array backend (or forced flattener), this hits
the assertion.

### Pattern 2 — `T[N][]` (dynamic outer, fixed inner)

Solidity:
```solidity
contract C { uint[4][] arr; }
```

`arr` is a dynamic array of fixed-size arrays — same
`array_typet(array_type(uint, 4), inf)` shape, same assertion
trigger.

## Why the Solidity regressions currently KNOWNBUG

The default Solidity solver is CVC5 or Bitwuzla (see `CLAUDE.md` —
Z3 struggles with 256-bit BV). Both advertise **native array
support**, so `array_api != nullptr` and the flattener is skipped.

But Path A's nested Array sort depends on the **tuple flattener**
behaviour for the range type. For inner arrays of structs, the
tuple flattener in `smt_tuple_node.cpp:40` calls back into
`array_conv.mk_array_symbol` — even when the top-level backend
supports arrays natively:

```cpp
// smt_tuple_node.cpp
smt_astt smt_tuple_node_flattener::mk_tuple_array_symbol(...) {
  ...
  return array_conv.mk_array_symbol(name, s, subtype);    // this fires the assert
}
```

So even with native-array backends, the **moment a struct
enters an unbounded array-of-array**, the tuple flattener's fallback
to `array_convt` triggers the assert.

(For non-struct-element types, the assertion doesn't fire on
CVC5/Bitwuzla — but other issues further downstream in the native
SMT array handling can still produce "array theory cannot handle
this" errors. Both paths are conservative.)

## Fix landscape

Three realistic directions; all require real work in `array_convt`.

### Fix A — Double-Ackermann for nested arrays

**Scope:** bigger. Extend `array_convt` to track a per-outer-element
nested Ackermann state. Each outer-level select of an array-valued
element returns a *sub-array handle* backed by its own
`array_indexes` / `array_selects` / `array_updates`.

**Cost:**
- `mk_array_symbol` takes a nested `subtype`; if `subtype->id ==
  SMT_SORT_ARRAY`, allocate a family of sub-arrays keyed on the
  outer index.
- `mk_unbounded_select` / `mk_unbounded_store`: when the result is
  array-typed, need to return a fresh `array_ast` that points into
  the sub-array family.
- Ackermann axioms multiply: for every pair of outer indexes
  (i, j), and every pair of inner indexes (m, n) that touched
  either sub-array, the standard index-equality-implies-value-
  equality plus a new outer-equality-implies-sub-array-equality.
- `push_ctx` / `pop_ctx` have to recurse into sub-arrays.

**Complexity:** probably 500-800 LOC of `array_conv.cpp` churn;
non-trivial test matrix.

**Benefit:** The universal flattener becomes sound for
`mapping(K => T[N])` on *every* backend, including Boolector
(which currently gets killed by this).

### Fix B — Frontend-side representation change

**Scope:** shift the representation. Make the frontend lower
`mapping(K => T[N])` to **single-dim** infinite-array with a
computed index `hash(key) * N + inner_idx`, as if the user wrote
`mapping(uint => uint)` with a compound key.

**Cost:**
- `src/solidity-frontend/` produces a different `array_typet` for
  nested mappings — one dimension only, with packed index.
- Every access `m[k][i]` at source level becomes a single
  `m[pack(k,i)]` at the irep2 level.
- Needs `pack` to be collision-free when `k` is mapping-key
  (address, uint, bytes) and `i` is small-bounded — usually done
  by `key * N + i` with `N` the inner-array fixed size.
- Requires re-auditing every place in the Solidity frontend that
  assumes a two-dim mapping → two-level access pattern.

**Complexity:** smaller in `src/solvers/` (zero), medium in
`src/solidity-frontend/`. Probably similar total LOC to Fix A.

**Benefit:** also fixes `mapping(K => T[N])` on all backends, plus
produces more efficient SMT (one select vs. two).

**Downside:** a Solidity-specific hack; other language frontends
producing nested infinite arrays (Python / Java via
`list of list of X` at arbitrary size) would still hit the bug.

### Fix C — Refuse path A in non-native-array backends

**Scope:** emit a cleaner error. In `convert_sort`, when Path A
triggers and the backend is `array_convt`, log a descriptive error
and abort with a "this solver doesn't support nested unbounded
arrays — use cvc5 / bitwuzla" message instead of an assertion deep
in the flattener.

**Cost:** ~20 LOC. Not a fix, just better ergonomics.

**Complexity:** trivial.

**Benefit:** zero. The user still can't verify the program.

### Recommended direction

For the near-term Solidity KNOWNBUGs:

- **Fix B** has the best ROI. The current flattened approach
  (frontend converts `T[N][]` to `T[M*N]` via packed indexing) works
  and is already done for some Solidity patterns. Extending it to
  cover `mapping(K => T[N])` requires a small Solidity-frontend
  change, zero solver work, and avoids the Ackermann blowup of
  Fix A.
- **Fix A** is the right thing for the SMT layer in the long run.
  `array_convt` claiming to be "universal" and then asserting on
  nested-unbounded is a capability gap that will bite other
  frontends too. But it's 2-3 weeks of careful SMT work, not
  something to tack onto the current work.
- **Fix C** is a one-afternoon ergonomic improvement regardless.

## Entry points worth knowing

| Method | Line | When called |
|---|---|---|
| `mk_array_symbol` | 87 | Every fresh array variable; every array-of-struct via tuple flattener |
| `mk_select` | 135 | Dispatched from `array_ast::select` → `smt_convt::convert_array_index` |
| `mk_store` | 177 | Dispatched from `array_ast::update` → `smt_convt::convert_array_store` |
| `mk_unbounded_select` | 223 | Unbounded path of `mk_select` |
| `mk_unbounded_store` | 283 | Unbounded path of `mk_store` |
| `array_ite` | 326 | `array_ast::ite` — iterates fields for bounded; delegates for unbounded |
| `unbounded_array_ite` | 353 | Unbounded path of `array_ite`, records as `array_with.is_ite` |
| `convert_array_of` | 392 | `array_iface::convert_array_of` — initializer support |
| `encode_array_equality` | 435 | `array_ast::eq` for unbounded; records for later solve |
| `mk_bounded_array_equality` | 457 | `array_ast::eq` for bounded — per-field conjunction |
| `get_array_elem` | 471 | Model readback — linear scan over `array_indexes`, match on concrete value, return `array_valuation[id][update][vec_idx]` |
| `add_array_constraints_for_solving` | 521 | Called from `pre_solve` — runs the five passes |
| `push_array_ctx` / `pop_array_ctx` | 530 / 559 | Context stack, with the resize-everything pop logic |

## Context-stack discipline

`push_array_ctx`:
1. `join_array_indexes` — re-close relations at the new level.
2. `add_new_indexes` — allocate valuation slots.
3. `execute_new_updates` — chain new with/ite.
4. `apply_new_selects` — bind select placeholders.
5. Record `array_valuation.size()` in `num_arrays_history`.

`pop_array_ctx`:
1. Restore the array count from `num_arrays_history`.
2. Resize every parallel vector.
3. For each multi_index container, `get<1>().erase(target_ctx)` —
   drop all entries recorded at the popped level.
4. Resize `array_valuation[arrid]` vectors to match the new
   `array_indexes[arrid].size()`.

The comment at line 634 admits "this is an intensely expensive
operation" — the resize per array value vector iterates every
update level. For deep push/pop loops, this is O(updates × indexes).

## Soundness touch-points

**Bounded path** is straightforward — every operation is explicit.

**Unbounded path** relies on:

1. Every index ever read or written is in `array_indexes[id]`.
2. The Ackermann constraints
   `∀i,j. idx_i == idx_j ⇒ val_i == val_j` are added for every
   array at every index introduction.
3. `array_valuation[id][update][i]` is the correct value for `idx_i`
   at the point after `update` applications — enforced by
   `execute_array_trans`.

Holes in these invariants show up as "the solver gave an impossible
model" (bad value leak) or "the solver timed out on an easy
constraint" (missing constraint). If you're chasing one of those,
check whether your new index made it into `array_indexes` via
`mk_unbounded_select` / `mk_unbounded_store`.

## Debugging

- `--array-flattener` — force `array_convt` on a backend that has
  native support. Use to isolate whether a bug is in `array_convt`
  or the backend.
- `--smt-formula-only --show-smt` — dump the post-Ackermann formula.
  For non-trivial programs this is huge but necessary when a
  quadratic Ackermann explosion is suspected.
- `--add-symex-value-sets` — rare interaction between the value-set
  side and array_convt; turning it off has helped in past bugs.

## Common pitfalls

- **"Assert fires on mk_array_symbol"** — see KNOWNBUG analysis
  above. Either use a native-array backend (cvc5/bitwuzla) or
  re-lower the nested array at the frontend level.
- **"Got weird value from an array"** — the index equality was
  solved by the SMT layer but the Ackermann wasn't propagated.
  Often caused by pushing/popping around array ops in a way that
  orphans an `array_select` record. Check the context-level on the
  offending select.
- **"Push/pop resize is slow"** — inherent to this design. If
  you're pushing/popping inside tight loops, consider whether the
  incremental path is right for your case.
- **"Array equality between different-size arrays"** — guarded by
  assertion at `mk_bounded_array_equality:460`. If the assertion
  fires, your frontend has type-mismatched arrays — symptom, not
  cause.
