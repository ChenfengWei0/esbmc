# Dereference — From `*p` to SMT

`src/pointer-analysis/dereference.cpp` (2682 LOC — the largest single
file in the project). Thin shim in
`src/goto-symex/symex_dereference.cpp` (the `goto_symext::dereference`
method) passes an expression and a mode through to a `dereferencet`
member, which does all the real work.

**Read `src/pointer-analysis/README.md` first** — it is the upstream
author's overview of the memory model and gives the core vocabulary
(scalar step lists, stitching bytes, byte_extract).

## The four stages

1. **Guard/short-circuit interpretation** — `dereference_expr` walks
   the input and descends into if/and/or operands with accumulated
   guards. Only when it finds an actual dereference (or an
   index/member on top of one) does stage 2 kick in.
2. **Object collection** — `dereference` asks the callback for the
   value-set of the pointer, producing a list of candidate objects.
3. **Reference building** — per-object, `build_reference_to` →
   `build_reference_rec` produces the expression that reads or writes
   the object at the offset, possibly via byte-stitching.
4. **Assertion emission** — alignment, NULL, bounds, and invalid-free
   checks are emitted along the way via
   `dereference_failure(...)`.

## Entry points

```
goto_symext::dereference(expr, mode)      ← thin wrapper in src/goto-symex
  └── dereferencet::dereference_expr(expr, guard, mode)
        ├── dereference_guard_expr    (peel if/and/or)
        ├── dereference_addrof_expr   (handle &*p and &a[i])
        ├── dereference_expr_nonscalar (collect index/member chain)
        └── dereferencet::dereference (the rebuild entry point)
               ├── dereference_callback.get_value_set(src, points_to_set)
               └── for each target:
                     build_reference_to(target, mode, ptr, type, guard, lex_offset, pointer_guard)
                        ├── check_pointer_alignment
                        ├── valid_check (freed? live?)
                        ├── bounds_check / check_data_obj_access / check_code_access
                        └── build_reference_rec(value, offset, type, guard, mode, align)
                              └── dispatch on (src_kind, dst_kind, offs_const?)
                                    → construct_from_array
                                    → construct_from_multidir_array
                                    → stitch_together_from_byte_array
                                    → scalar direct extract
```

At the top, `dereference_expr` recursively descends — it doesn't
know in advance where the `*` or index-on-pointer lives, so it walks
everything.

## `dereference` — the case-split builder

`dereference.cpp:450`. Given a pointer expression `src`, target type
`to_type`, guard, mode, and a lexical offset (from an earlier
index/member access), this is:

```cpp
value_setst::valuest points_to_set;
dereference_callback.get_value_set(src, points_to_set);

bool known_exhaustive = /* true iff no unknown/invalid in points_to_set */;
expr2tc value;
if (!known_exhaustive) value = make_failed_symbol(type);

for (target in points_to_set) {
  expr2tc ptr_guard;
  expr2tc new_value = build_reference_to(target, mode, src, type,
                                         guard, lexical_offset, ptr_guard);
  if (is_nil(new_value)) continue;           // handler rejected
  if (!dereference_type_compare(new_value, type)) {
    bad_base_type_failure(...);
    continue;
  }
  value = is_nil(value) ? new_value
                        : if2tc(type, ptr_guard, new_value, value);
}
```

The result is a (potentially nested) `if(ptr_guard_1, val_1,
if(ptr_guard_2, val_2, ...))` — one arm per target, with each
`ptr_guard_i = same_object2t(src, &target_i)`. If the value-set
wasn't known-exhaustive, the innermost else is a fresh
`symex::invalid_objectN` symbol so the formula stays well-typed even
if no target matches.

### Dead-letter arm: failed symbols

`make_failed_symbol(type)` creates a fresh nondet symbol with a
numbered name (`symex::invalid_object{N}`). These symbols appear in
the target equation but are never constrained beyond their type. In
counter-examples they manifest as "this value was unconstrained",
which is one of the signals that a dereference failed.

## `build_reference_to` — per-object machinery

`dereference.cpp:655`. Given a single target from the value-set:

1. `check_pointer_alignment(mode, type, deref_expr, guard)` — if the
   access is a scalar read/write and the known/computed offset is not
   aligned to the target type's width, emit an alignment-failure
   assertion.
2. If target is `unknown` / `invalid` → `deref_invalid_ptr` emits an
   invalid-deref assertion, returns nil.
3. If target root is `null_object2t` → emit "NULL pointer" failure
   (unless mode is free / internal).
4. Build the per-object guard: `pointer_guard = same_object2t(ptr,
   &target)`.
5. `valid_check(object, tmp_guard, mode)` — asserts the storage is
   still live (not freed, activation record still on stack, etc.)
6. Free-mode short-circuit: return nil after check.
7. Compute `final_offset`:
   - Start from `o.offset` (the value-set-recorded offset).
   - If it's unknown, replace with `pointer_offset2tc(deref_expr)` —
     the SMT-level pointer offset expression.
   - If `deref_expr` itself isn't a symbol, assume alignment = 1
     (can't assume anything about a compound pointer expression's
     alignment).
   - Add `lexical_offset` (any member/index the dereference site
     applied on top).
   - Convert to bits.
8. If mode is `INTERNAL`, collect `{object, offset, guard}` into
   `internal_items` and return nil — the caller wanted metadata only.
9. Per shape:
   - **Code type** → `check_code_access` + return.
   - **Array** → `bounds_check` on the offset.
   - **Scalar/struct** → `check_data_obj_access`.
10. `build_reference_rec(value, final_offset, type, guard, mode,
    alignment * 8)` — the actual value extraction.

## `build_reference_rec` — the shape cross-product

`dereference.cpp:939`. Dispatches based on the source-object kind and
the destination type. Conceptually the matrix is
`src_kind × dst_kind × offset_is_constant`, but the handler names
come from a set of dedicated functions:

- `construct_from_array(value, offset, type, g, mode, align)` — read
  a single element from an array. Rewrites the offset to an index and
  (re-)enters `build_reference_rec` if the element is itself a
  struct/array.
- `construct_from_multidir_array(...)` — same, but for multi-dim
  native arrays.
- `construct_from_struct(...)` — constant-offset: index into
  members; nondet-offset: fall back to byte stitching.
- `construct_from_dyn_struct(...)` / `construct_from_dyn_array(...)`
  — for byte-backed ("`dynamic_object`") objects, where the SMT
  primitive is an `array<byte, inf>` and each access is a slice.
- `stitch_together_from_byte_array(value, offset, type, guard)` — the
  fallback when the target type doesn't cleanly align with the source
  type. Reads N bytes at `offset..offset+N-1`, concatenates them, and
  typecasts to the destination scalar type. N comes from
  `type_byte_size`.

The dispatch uses a flags bitmask (`flag_src_* | flag_dst_* |
flag_is_const_offs`) — see README.md §"switch statement" for the
full enumeration.

### Writes

For writes, `build_reference_rec` emits a `WITH` (or `byte_update`)
expression rather than a read. The calling path in symex is:

```
symex_assign on lhs = rhs
  └── dereference(lhs, WRITE)
        └── build_reference_to returns: an lvalue with a WITH expression at the right spot
  └── symex_assign_rec walks the lvalue chain
        └── eventually reaches a symbol; bumps its SSA version with the new rhs
```

So the `*p = v` write never directly touches `p`'s SSA — it touches
each candidate target's SSA, each guarded by `same_object2t(p, &t)`.

## Alignment checking

`check_pointer_alignment` (`dereference.cpp:615`). Gated by
`--no-align-check` — without it, misaligned scalar reads trigger
`dereference_failure("access to object of incompatible size",
...)`. The access is still performed under the failure guard, so
over-approximating remains safe.

`check_alignment` compares the known pointer offset (in bits) modulo
the type-alignment requirement, and emits a failure when they don't
match.

## Bounds checking

`bounds_check` (for arrays) and `check_data_obj_access` (for scalars
/ structs) emit out-of-bounds failure assertions. Disabled by
`--no-bounds-check` / `--no-pointer-check`.

## Failure categories

All go through `dereference_failure(property, msg, guard)`:

| Property | Typical message | When |
|---|---|---|
| `"pointer dereference"` | `"NULL pointer"` | value-set contained null, mode is read/write |
| `"pointer dereference"` | `"dereference failure: invalid pointer"` | target is `unknown`/`invalid` |
| `"pointer dereference"` | `"dereference failure: object bounds"` | access beyond array bounds |
| `"pointer dereference"` | `"dereference failure: incompatible size"` | scalar access with wrong alignment |
| `"invalid pointer"` | `"live pointer to freed storage"` | target was freed |
| `"pointer alignment"` | misalignment message | alignment violation |
| `"code separation"` | `"dereferencing function pointer"` | access on a code-type object from data mode |

## `goto_program_dereference.cpp`

`src/pointer-analysis/goto_program_dereference.cpp` (250 LOC) is the
*offline* version: it rewrites dereferences in the goto-program
statically, before symex. Used by a few optimisation passes. It
shares the `dereferencet` class but supplies a different
`dereference_callbackt` implementation that doesn't have access to the
per-state value-set (uses the static analysis result instead).

## Modes

- `READ` — value is only consumed. Emit alignment/bounds checks on
  reads.
- `WRITE` — value is stored. Emit the WITH/byte_update; alignment
  checks also apply.
- `FREE` — invoked for `free(p)`. Emits checks about alive/freed
  storage; does not build a reference value.
- `INTERNAL` — caller wants the (object, offset, guard) tuples
  without a materialised reference expression. Used by some callers
  that need to reason about the points-to set without generating
  extra SMT.

Each has an `unaligned` sibling for packed-struct access.

## Debugging

- `--no-pointer-check` / `--no-align-check` / `--no-bounds-check` —
  disable the respective failure emission. A verdict that changes
  from failed to passed when you flip these on/off tells you which
  category of failure is active.
- `--program-only` shows the SSA with dereferences lowered to the
  if-chain. Grep for `same_object` to find the guard sentinels.
- `dereference.cpp`'s `stitch_together_from_byte_array` path is the
  worst-case "I couldn't build a clean reference" branch — if your
  SSA has a lot of byte-concatenate expressions, the frontend likely
  needs a cleaner type for the target.
- `make_failed_symbol` emissions are visible as
  `symex::invalid_objectN` in traces. Their presence in a witness
  usually means the value-set lost a target.

## Common pitfalls

- **"Why is my dereference producing a failed symbol?"** — the
  value-set returned `unknown` or `invalid`, because the pointer was
  either unconstrained or the tracking lost it. Check the pre-deref
  value-set dump.
- **"Why does `*p = v` change the value of unrelated variables?"** —
  the value-set overapproximates and includes objects the pointer
  can't actually point at. The WITH-chain therefore writes to all of
  them (guarded by the same_object). Tighten the program or fix the
  value-set tracking upstream.
- **"The bounds check fires on what looks like a valid access."** —
  the value-set's offset for this record is nondet, and
  `bounds_check` can't rule out OOB. Often happens in byte-backed
  storage; consider whether the frontend should hand you a native
  array type instead.
- **"I want to know which objects a pointer can touch without
  generating SMT."** — use `INTERNAL` mode; the `internal_items` are
  returned via the callback's `dump_internal_state`.
