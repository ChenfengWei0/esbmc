# Value Set — Points-to Tracking

`src/pointer-analysis/value_set.{cpp,h}` (2.5k LOC). The per-state map
from L1 pointer names to the set of data objects they might refer to.
Updated on every pointer assignment, merged at phi points, queried by
`dereference.cpp`.

## What it is, in one sentence

A `value_sett` is an associative container
`l1_variable_name → { (object_id, offset) }`, where `offset` may be a
concrete `BigInt` or "nondet with alignment N".

Two mental models coexist:

1. **Per-state** (the main use) — each `goto_symex_statet` owns one.
   Flow-sensitive: it reflects the set of things pointers point at
   along the currently-explored path. Updated on `ASSIGN`, `DECL`,
   `DEAD`, `FUNCTION_CALL`, `free`. Copied on branch, merged on phi.
2. **Flow-insensitive (static)** — `value_set_analysis` runs the same
   interpretation over the goto-program as a pre-symex dataflow
   fixedpoint, computing a conservative over-approximation used by
   some optimisation passes (not by the main symex loop; symex rolls
   its own flow-sensitive version).

The static variant is in `value_set_analysis.{cpp,h}` (240 LOC) and
wraps `value_sett` in the abstract-interpretation domain framework at
`value_set_domain.{cpp,h}`.

## Internal shape

```
class value_sett {
  object_numberingt object_numbering;      // global: object ↔ integer id
  valuest values;                          // map<string, entryt>
};

struct entryt {
  string identifier;                       // l1 name
  string suffix;                           // tag (scalar / member_x / index)
  object_mapt object_map;                  // map<int, objectt>
};

struct objectt {
  BigInt offset;
  bool offset_is_set;                      // false → offset nondet
  unsigned offset_alignment;               // guaranteed alignment in bytes
};
```

- **Object numbering** is global and shared across all `value_sett`s
  (the comment in the header calls this out as a future
  multithreaded-death-cake). Each pointed-to expression (symbol,
  dynamic_object, string literal, etc.) is given an integer id on first
  insertion; the id is stable across all value-sets in the run.
- **Suffix** is a string tag distinguishing sub-targets when the name
  alone is ambiguous. Struct fields use `"." + field_name`; array
  accesses use a single umbrella entry with an unknown index.
- The `objectt` records **either** an exact byte offset **or** an
  alignment guarantee. Alignment is tracked so that the dereference
  machinery can skip some unaligned-access support when it's known
  impossible.

## How reads work — `get_value_set`

Two public entry points:

```cpp
void get_value_set(const expr2tc &expr, value_setst::valuest &dest);
void get_value_set(const expr2tc &expr, object_mapt &dest);
```

The recursive workhorse is `get_value_set_rec` (`value_set.cpp:233`).
It walks the input expression and, for each syntactic form:

- **`symbol`** → look up `values[l1_name]`, emit every entry.
- **`constant_pointer`** → emit `invalid` or `null` directly.
- **`address_of(x)`** → emit object-descriptor `{x, offset=0}`.
- **`if(c, a, b)`** → union the results from `a` and `b`.
- **`typecast(p)`** → recurse on `p`, adjust type when meaningful.
- **`add(p, n)` / `add(n, p)`** — pointer arithmetic:
  - If `n` is a constant and the object's offset is known, bump the
    offset.
  - If either side is unknown, mark offset nondet with the alignment
    guaranteed by the add operands (e.g. `p + i * sizeof(int)` → 4-byte
    alignment even if `i` is nondet).
- **`index(a, i)`** → dereference `a` as a pointer, then treat `i` as
  adding `i * sizeof(element)` to the offset.
- **`dereference(p)`** → two-step: get the value-set of `p`, then for
  each pointed-to object, treat it itself as a pointer and recurse.
  This is how `**pp` gets resolved.
- **`member(s, f)`** → for each object `s` points at, find its field
  `f` at its known offset.

When a recursion hits something the interpreter doesn't understand
(e.g. arbitrary arithmetic), it inserts `unknown` into the result —
which downstream dereference handles as "fall back to a failed
symbol".

## How writes work — `assign`

`value_set.cpp:1149`. Interpret `lhs = rhs`:

1. **`rhs` is `if(c, t, f)`** — assign both through an `xchg_sym`
   temporary, then copy to `lhs`. The temp prevents the self-reference
   case where the if branches back to the lhs.
2. **Struct/union lhs** — iterate members, recurse member-wise.
3. **Array lhs** — collapse all indices into one unknown, recurse on
   `a[unknown]`. If rhs is `with(a, i, v)`, treat it as a write to that
   index plus a carry-over from the base. If rhs is
   `constant_array_of(init)` or `constant_array(values...)`, iterate
   initialisers. Otherwise (`rhs` is a full-array value), copy each
   element under the unknown index.
4. **Basic pointer type lhs** — `get_value_set(rhs)` into a fresh
   `object_mapt`, then `assign_rec(lhs, values_rhs, "", add_to_sets)`
   actually mutates the map.

`assign_rec` writes through to the `values` map. When `add_to_sets` is
true (the default after a phi or if-merge), it unions instead of
overwriting.

## Phi and merge — `make_union`

`value_set.cpp:155`. Called from `merge_value_sets` in
`symex_goto.cpp:356` at join points. For every entry in the incoming
`new_values`:

- If not already in `values`, inserted as-is.
- If present, the `object_map`s are unioned via
  `make_union(dest_om, src_om)`. The per-object `insert(n, object)`
  call (line 350 of the header) is where the alignment-merging logic
  lives:
  - Both offsets known and equal → no change.
  - Both known but different → downgrade to "unknown offset with
    alignment = min(align(offs1), align(offs2))".
  - One set, one unset → mix the alignments.
  - Both unset → min of alignments.

The net effect: merging two paths where the pointer came from two
different writes downgrades the offset to nondet with the best
alignment we can prove.

## `do_function_call` / `do_end_function`

`value_set.cpp:1510` and `:1582`. Called from symex to propagate
points-to across frame boundaries:

- On `function_call(id, args)` — bind the arguments' value-sets to
  the callee's formals under the callee's L1 numbers.
- On `end_function(lhs)` — propagate the callee's return value's
  value-set into the caller's lvalue.

These are the **only** channels through which points-to information
crosses a frame. In particular, pointers passed via `&obj` are
interpreted correctly — the formal inherits the actual's object-map.

## `apply_code` / `apply_assume`

`value_set.cpp:1658` and `:1592`. Used by the static analyser side
(not the symex side) to interpret entire code expressions and
`__ESBMC_assume` conditions on the value-set. The symex side doesn't
need these — it interprets each ASSIGN individually.

## Known limits and gotchas

### 1. Object-numbering is global and thread-unsafe

The `object_numbering` static member is shared across value-sets. A
future multi-threaded symex would need to guard this.

### 2. Array access loses per-index precision

All array writes fold into a single "unknown index" entry. An
`a[0] = &x; a[1] = &y;` sequence results in the value-set for `a[?]`
containing both `x` and `y` — reading `a[0]` can't rule out `y`.

This is why the `array_convt` limit on array-of-array is load-bearing
for multi-dim fixed arrays: when the frontend can flatten the structure
into a genuine nested array type, the SMT side handles it natively; if
the frontend falls back to pointer-to-pointer (`T**`), the value-set
conflation on the outer array makes every cross-row write alias every
row. See `docs/claude/solidity/language-support.md` §B and the
`multi_dim_fixed_*` KNOWNBUG regressions.

### 3. Byte-array backing loses field-level granularity

When a pointer is stored inside a byte-backed struct field (the
`solidity_bytes` representation, dynamic string contents, etc.), the
value-set treats the whole backing array as one object. All pointer
writes to different fields inside it conflate. This is the
"value-set offset-granular points-to loss on byte-array-backed struct
fields" trip-wire called out in `README.md` §Bugs.

### 4. `make_union` is lossy on offset conflicts

Two concrete-but-different offsets produce an offset-unknown record.
There is no mechanism to preserve the disjunction
`(offs==A) ∨ (offs==B)`; it's either a singleton known offset or
nondet. For most C programs this is fine; for heavily-symbolic-indexed
Solidity storage patterns it over-approximates.

### 5. Static analyser vs. flow-sensitive symex drift

The static value-set and the flow-sensitive one use the same class but
different callers. If you add a new assignment rule, do it in
`value_sett::assign` and it applies to both. If you add a new lookup
rule in the callers, be sure to check both `symex_assign.cpp`
(flow-sensitive) and `value_set_analysis.cpp` (static).

## Inspecting the value-set at runtime

- `--show-symex-value-sets` — dump the value-set at every symex step.
  Very verbose; grep for `lhs_name` to find where it gets written.
- `--show-value-sets` (static variant) — dump the pre-symex static
  analysis result.
- `value_sett::dump()` — writes `output(std::cerr)` for ad-hoc debugger
  breakpoints. Prints each entry as
  `identifier.suffix: { object_num@offset(align), ... }`.

## Relationship to dereference

`dereferencet::dereference` (`dereference.cpp:450`) calls
`dereference_callback.get_value_set(src, points_to_set)` to fetch the
set of candidate objects for the pointer being dereferenced. It then
builds one reference per object via `build_reference_to`, guarded by
`same_object2t(deref_expr, &obj_i)`, and chains them into an if-chain.

So `value_sett` answers **which objects** the pointer can refer to;
`dereference.cpp` answers **what value** each reference materialises
to. Both are needed to lower `*p` into an SMT formula.
