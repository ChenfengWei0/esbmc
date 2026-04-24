# Memory Model — Pointers, Address Space, `same_object`

`src/solvers/smt/smt_memspace.cpp` (648 LOC). The solver-side
counterpart to the value-set tracking in `src/goto-symex/`
([docs/claude/symex/value-set.md](../symex/value-set.md)) and
[docs/claude/symex/dereference.md](../symex/dereference.md).

Ops it provides:

- Lower `address_of(expr)` to a constant `(object_id, offset)` tuple.
- Encode pointer arithmetic without going through integers.
- `same_object(a, b)` — do two pointers refer to the same object?
- `pointer_offset(p)` / `pointer_object(p)` — tuple projections.
- Pointer comparisons that don't blow up the solver.
- Disjoint-address-space constraints so every live object has a
  unique byte range.
- `realloc` re-numbering so old pointers can't alias the new block.

## The pointer representation

Every pointer in an ESBMC-generated SMT formula is a **tuple** (not a
bitvector). The tuple is the `pointer_struct` type2tc, built by the
caller during `smt_post_init`. Shape:

```
pointer_struct = struct {
  pointer_object : int/bv      ← object_id (0 = NULL, 1 = INVALID, 2+ = user objs)
  pointer_offset : int/bv      ← byte offset into the object
  [pointer_capability : bv]    ← only under --cheri
};
```

`convert_sort(pointer_id)` and `convert_sort(code_id)` both resolve
to `tuple_api->mk_struct_sort(pointer_struct)`. The address-of of any
symbol is a **constant tuple** with those two (or three) fields.

### Why a tuple, not a plain BV address?

Pointer comparison, `same_object`, and pointer arithmetic are all
trivially computed when `(object_id, offset)` are separate fields.
If pointers were bitvector addresses:

- `same_object(a, b)` would require a lookup into the address-space
  array to find "which object contains address a" and "which contains
  b", which is O(n_objects) for every comparison.
- Pointer arithmetic would require modular arithmetic in the address
  space, handling wrap-around and alignment.
- Casting int to pointer would require solving "which object's range
  contains this integer".

With the tuple, most of these are constant-time field projections.
Casting is still nontrivial (see `convert_typecast_to_ptr` in
`smt_casts.cpp`), but arithmetic is clean.

## Address-space array

Two symbols per object plus an indexed array of their ranges:

- `__ESBMC_ptr_obj_start_<N>` : ptraddr_type — object N's starting address
- `__ESBMC_ptr_obj_end_<N>` : ptraddr_type — object N's ending address
  (inclusive one past the last byte)
- `__ESBMC_ptr_addr_range_<N>` : {start, end} struct
- `__ESBMC_addrspace_arr_<S>` : array of `addr_space_type` — indexed
  by object_id, returns the (start, end) range

The `<S>` on the arr symbol increments per object allocation — it's
an SSA-style version counter, so asking "what are the live ranges at
this point in the formula" means querying
`__ESBMC_addrspace_arr_<cur_S>`.

### Two pre-allocated objects

`init_addr_space_array` (line 560) sets up:

- **Object 0 = NULL** — start = 0, end = 0 (a zero-size object).
- **Object 1 = INVALID** — start = 1, end = 2^ptraddr_width - 1 (spans
  the whole address space; used when we explicitly don't know where
  a pointer points).

All user allocations get object IDs ≥ 2. NULL and INVALID are
pre-asserted; the `NULL` / `INVALID` SMT symbols are bound to those
tuple constants.

### Disjoint ranges

`finalize_pointer_chain(obj_num)` (line 406), called at the end of
`init_pointer_obj`, asserts:

```
for j in [0, obj_num):
  if j == 1: continue                        ← INVALID spans everything, skip
  assert (end_i < start_j) ∨ (start_i > end_j)
  
  (optionally guarded by alive[j] if __ESBMC_alloc is active)
```

This is the O(n²) disjointness encoding. For `n` objects, the
assertion count is `n * (n-1) / 2`. On large programs this is
measurable; but it's also what gives symbolic pointer comparisons
any teeth — without it, two pointers to different objects could
legitimately be equal at the bit level.

The `alive[j]` guarding is important: once `free(obj_j)` is called,
`__ESBMC_alloc[j] = false`, and the disjointness constraint for j is
gated. This lets a fresh `malloc` after a `free` legally reuse that
address range.

### Range end is inclusive

Comment at line 342:
> The accessible object spans addresses `[start, end)`, including
> start, excluding end. The addresses reserved for this object are
> `[start, end]` including `end`.

The extra byte reservation is so that the "one-past-end" pointer is
still uniquely bound to the object — C permits computing (not
dereferencing) `&arr[N]` for an N-element array.

## Object tracking — `pointer_logict`

`src/solvers/prop/pointer_logic.{cpp,h}` (not in smt/). Simple
table keyed on the `expr2tc` of what was taken the address of:

```
pointer_logict::add_object(expr)        → unsigned int obj_num
pointer_logict::get_free_obj_num()      → fresh obj_num for realloc
```

A `list<pointer_logict> smt_convt::pointer_logic` records a stack —
`push_ctx` duplicates the back; `pop_ctx` pops it.

When `convert_identifier_pointer(expr, name, type)` fires for a
symbol that's never been address-of'd before:

1. `pointer_logic.back().add_object(expr)` allocates a fresh obj_num.
2. `mk_tuple_symbol(name, ...)` creates the SMT tuple symbol.
3. `init_pointer_obj(obj_num, size, type)`:
   - Computes `end_sym = start_sym + size`.
   - Asserts `start ≤ end` (no wraparound).
   - Honours the type's `alignment` attribute — asserts
     `start_sym mod alignment == 0`.
   - Runs `finalize_pointer_chain(obj_num)` for disjointness.
   - Bumps the addrspace array.
4. Asserts the pointer tuple equals `(obj_num, 0)`.
5. Caches by canonical `address_of(expr)` expr2tc.

Subsequent `address_of` on the same expr returns the cached tuple —
the object_id and all its constraints are asserted only once.

## `convert_addr_of` — the 7 cases

`smt_memspace.cpp:475`. Dispatch on the operand of `&`:

| Case | Handler |
|---|---|
| `&s.f` / `&a[i]` | Compute `offs = compute_pointer_offset(inner)`; recurse on `&base`; `update(this, offs, 1)` → new tuple with bumped offset |
| `&symbol` | `convert_identifier_pointer(expr, name, type)` |
| `&"literal"` | Treat as unique per-literal object, canonical name `address_of_str_const(...)` |
| `&constant_array` | Anonymous constant array — synthesize a name `address_of_arr_const(N++)` |
| `&(c ? a : b)` | Rewrite to `c ? &a : &b`, recurse |
| `&(cast)x` | Strip cast, reapply type — pointer type fits regardless |
| else | `log_error + abort` |

Note the member/index case bumps the offset by
`compute_pointer_offset(inner_expr)` — a compile-time walk that
resolves the byte offset of the accessed field or index.

## `convert_pointer_arith` — 8-way matrix

`smt_memspace.cpp:62`. `add`/`sub` where some operand is
pointer-typed. Three bits of state:

- `ret_is_ptr` (4): return type is pointer?
- `op1_is_ptr` (2): lhs is pointer?
- `op2_is_ptr` (1): rhs is pointer?

Combined → 3-bit key, 8 cases. The handlers:

| bitset | Meaning | Handler |
|---|---|---|
| 000 | No pointers anywhere | Never fed here (assertion) |
| 001, 010 | `ptr - int` / `int + ptr` returning int | Use `add2tc(ptr, non_ptr)` + typecast → not a permitted C op, actually this is "pointer casted down to int then arith-ed" |
| 011 | `ptr - ptr` returning int | Compute `(offs(p1) - offs(p2)) / sizeof(pointee)` — the pointer difference |
| 100 | `int + int` returning ptr | Error ("should have been handled at a higher level") |
| 101, 110 | `int + ptr` / `int - ptr` returning ptr | Real pointer arithmetic: `new_offs = offs(ptr) ± (int * sizeof(pointee))`, update field 1 of tuple |
| 111 | `ptr - ptr` returning ptr | Same as 011 but update the tuple's offset instead of returning int |

Comment at line 79 flags case 001/010 (NPP) as "most dangerous":
the integer arith might produce a value that maps to no live object,
and dereferencing it later has to be caught by downstream check
assertions.

## `convert_ptr_cmp` — optimisation

`smt_memspace.cpp:32`. Dispatched for `<`, `<=`, `>`, `>=` between
pointers (when `--no-pointer-relation-check` hasn't disabled the
"same-object" precondition). Body:

```cpp
type2tc type = get_uint_type(config.ansi_c.address_width);
type2tc stype = get_int_type(config.ansi_c.address_width);

expr2tc op = templ_expr;
rel.side_1 = typecast(type, pointer_offset(stype, side1));
rel.side_2 = typecast(type, pointer_offset(stype, side2));
return convert_ast(op);
```

In other words: extract just the offsets, compare unsigned. This
presumes the "same-object" test passed earlier (otherwise the
comparison is undefined behaviour and the `--pointer-relation-check`
already asserted it).

Why unsigned? Comment at line 49:
> Objects could be larger than half the address space, in which case
> offsets could flip sign.

So we go through signed-offset extraction (to preserve value if
pointer_offset is stored as signed) then cast to unsigned for the
comparison itself.

## `same_object` — the simplest of the lot

Dispatched in `smt_conv.cpp` case `same_object_id`:

```cpp
args[0] = args[0]->project(this, 0);    // object_id field
args[1] = args[1]->project(this, 0);
a = mk_eq(args[0], args[1]);
```

Two integer equality. No address-space lookup. This is why the
tuple representation is worth the cost — `same_object` is a
one-select, one-eq operation.

## `renumber_symbol_address` — realloc

`smt_memspace.cpp:191`. Called from the SSA step type `renumber`
(inserted by `symex_target_equationt::renumber` in response to
`realloc(p, new_size)`).

Two subcases:

1. **Pointer was already address-of'd** (in `renumber_map.back()`):
   - Allocate a fresh object number.
   - `init_pointer_obj` for the new size → new tuple.
   - The old entry is overwritten with `ite(guard, new_tuple,
     old_tuple)` — so at this point in the SSA, the SMT sees either
     the new pointer or the pre-renumber version, guarded on whether
     the realloc succeeded.
2. **First time** — just allocate and store.

`renumber_map` is per-push-level; `push_ctx` duplicates the back,
`pop_ctx` pops. This is how re-entering loops get fresh realloc
numbers.

## Interaction with value-set tracking

The symex value-set
([docs/claude/symex/value-set.md](../symex/value-set.md)) tracks
which **L1 names** a pointer can refer to. At SSA-step emission
time, the value-set is consulted to produce the if-chain that
`dereference.cpp` emits: `if (same_object(p, &x)) val_of_x else ...`.

The solver side (this file) doesn't track any of that. It only
knows:

- Each `address_of(x)` maps to object_id N.
- Object N has disjoint byte range from all other objects.
- Offsets within object N are a separate integer field.

So the value-set → dereference → SMT pipeline is:

```
value-set says: p might point at {x, y, z}
dereference emits:
  if (same_object(p, &x)) → <read from x at pointer_offset(p)>
  else if (same_object(p, &y)) → <read from y at ...>
  else if (same_object(p, &z)) → <read from z at ...>
  else → failed_symbol
```

and the solver checks feasibility of each same_object via trivial
obj_id equality.

## CHERI extension

Under `config.ansi_c.cheri`, pointer_struct has a third field
`pointer_capability`. It's a CHERI capability value — typically a
128-bit bitvector encoding permissions and bounds. Operations that
interact: `convert_addr_of` stores 0 (default capability);
`pointer_capability2t` extracts field 2. The CHERI-aware
same-object / bounds checks live elsewhere (`src/cheri-c/` and
check code in the C frontend).

## Debug inspection

- `--show-smt` after solving — dump the model. You'll see
  `__ESBMC_ptr_obj_start_N` / `end_N` concrete values — the
  solver-picked address-space layout.
- `--no-pointer-check` — skip out-of-bounds / alignment
  assertions (but keep same-object for pointer comparisons).
- `--no-pointer-relation-check` — skip the "comparing different
  objects is UB" assertion. Dangerous; legitimate when hand-checking
  a low-level memory manipulation.
- Trace has `__ESBMC_addrspace_arr_<N>` entries whose concrete
  values tell you the live-object set at each SSA step.

## Common pitfalls

- **"Two pointers to different objects compare equal"** — the
  disjointness constraint requires `finalize_pointer_chain` to have
  fired for both. If you synthesize a pointer AST by hand (don't),
  `init_pointer_obj` is what you need to call.
- **"Pointer arithmetic produced an unexpected offset"** — the
  `pointee_size` comes from `type_byte_size_expr(pointer_subtype)`.
  Incomplete types (`void *`, `struct Foo *` with no `Foo`
  definition in scope) default to 8 bits — a common cause of
  "arithmetic on `void *` doesn't advance by 1".
- **"Model shows the solver picked an address range outside the
  machine word"** — the no-wraparound assertion is
  `end ≥ start`, not `end ≤ INT_MAX`. Arguably a limit we should
  add; currently it relies on the ptraddr_type width being
  implicitly bounded.
- **"Freed pointer still aliases the new allocation"** — the
  `alive[j]` gating requires `__ESBMC_alloc` to be tracked. Under
  `--no-malloc-tracking` or similar, the gating is absent.
- **"Unrecognized address_of operand"** abort — the 7 handled
  shapes don't cover something. Most often
  `&(func_call())`-style expressions that should have been
  lowered to a temporary at the frontend.
