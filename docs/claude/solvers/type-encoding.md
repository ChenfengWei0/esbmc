# Type Encoding — Casts, Bytes, Overflow, Tuples

Five files (and one subdir) that encode ESBMC's non-trivial
type machinery into SMT:

- `src/solvers/smt/smt_casts.cpp` (776 LOC) — `typecast2t` dispatch.
- `src/solvers/smt/smt_bitcast.cpp` (260 LOC) — `bitcast2t` (reinterpret).
- `src/solvers/smt/smt_byteops.cpp` (533 LOC) — `byte_extract` / `byte_update`.
- `src/solvers/smt/smt_overflow.cpp` (370 LOC) — overflow_arith / overflow_cast / overflow_neg.
- `src/solvers/smt/tuple/` (1423 LOC across 6 files) — struct (tuple) flattening.
- `src/solvers/smt/fp/fp_conv.cpp` (2228 LOC) — floating-point. Touched only briefly here.

## Typecast — `convert_typecast` dispatch

`smt_casts.cpp:681`. Entry from `convert_ast` case `typecast_id`.
Decides based on **result type**:

```
Special cases first:
  int_encoding + fp → fp                  → no-op (real is exact)
  int_encoding + fp → bv/fixedbv          → round_real_to_int; clamp if unsigned
  int_encoding + bv/fixedbv → fp          → mk_int2real
  cast.type == cast.from->type            → no-op
  result is pointer                       → convert_typecast_to_ptr
  from is add2t, result is array          → FAM init quirk, return zero array
  from is pointer                         → convert_typecast_from_ptr

Then by result type:
  bool             → convert_typecast_to_bool
  fixedbv (bv mode) → convert_typecast_to_fixedbv_nonint
  bv/fixedbv       → convert_typecast_to_ints
  floatbv          → convert_typecast_to_fpbv
  struct           → convert_typecast_to_struct (handles upcast/downcast)
  union            → no-op if types match; else abort
  else             → abort
```

Each specialised handler covers a multi-way matrix on `from` type.
The whole file exists because in SMT-land, casting an integer to a
fixedbv or a pointer to an int requires careful reconstruction —
there's no built-in "cast" operator across sorts.

### `convert_typecast_to_ptr`

The most involved cast. Takes an integer, walks the address_space
array, and builds an if-chain:

```
result = if (int_val ∈ [start_0, end_0]) then {obj=0, offs=int_val-start_0}
         else if (int_val ∈ [start_1, end_1]) then {obj=1, offs=int_val-start_1}
         ...
         else INVALID
```

For N live objects, produces N conditionals. The `[start, end]`
inclusivity was set up in `smt_memspace.cpp:342` to make this
if-chain unique even for one-past-end pointers.

### `convert_typecast_from_ptr`

Reverse — computes `start_sym + offs` for the single object the
pointer points at. Uses `project(0)` to get obj_num and look up
`__ESBMC_ptr_obj_start_<N>`.

### Fixedbv-to-fixedbv

`convert_typecast_to_fixedbv_nonint` and variants in `smt_casts.cpp:22-153`
handle the width adjustments: extract / sign_extend / concat the
integer and fraction parts to the new format. Done per-part because
fixedbv is represented as a single bitvector with a split
convention, not as a struct — shifting the fraction width requires
re-splitting.

## Bitcast — `convert_bitcast`

`smt_bitcast.cpp:122`. Unlike typecast, bitcast preserves the bit
representation — so the SMT operation is either a no-op (same width)
or a reshape:

- Scalar ↔ scalar of same width → no-op.
- Scalar ↔ scalar of different width → reinterpret via BV layout.
- Struct → BV → pack fields byte-aligned.
- BV → struct → slice BV into field-widths.
- Array ↔ BV → concat / split.
- Float ↔ BV → fp_api→mk_from_bv_to_fp / mk_from_fp_to_bv.

The struct-to-BV path respects member alignment; padding bytes
become fresh nondet bits. Under `--fp2bv` float↔BV is trivial
because both are already BV internally.

## Byte operations — `convert_byte_extract` / `convert_byte_update`

`smt_byteops.cpp`. The lowering for:

- `byte_extract(source, offs, target_type)` — read one byte from a
  wider value at byte offset `offs`, interpret as `target_type`.
- `byte_update(source, offs, update_value)` — write `update_value`
  into byte `offs`, return the updated wider value.

Two encoding paths (branch on `int_encoding`):

### Int encoding (`convert_byte_extract_int_mode` at line 20)

Treats the source as a mathematical integer. Byte offset
`i` corresponds to bits `[i*8, i*8+7]`. Extract via:

```
shifted = source / (2^(i*8))   if not big-endian
shifted = source / (2^((N-1-i)*8))  if big-endian
result  = shifted mod 256
```

For non-constant offsets, `create_int_right_shift(source,
bit_offset)` synthesises a symbolic div-by-power-of-2 using
lookup into the `int_shift_op_array` precomputed in
`smt_post_init`.

### BV encoding (`convert_byte_extract_bv_mode` at line 123)

Treats source as a bitvector. Extract via `mk_extract(source,
high, low)` where `high = (offs+1)*8 - 1`, `low = offs*8`. For
non-constant offsets, iterate over all possible byte positions
and emit an if-chain.

### `byte_update`

Similar two-path structure. For constant offset, compose
`(source & ~mask) | (update << shift)` (BV mode) or
`source - byte*base + update*base` (int mode). For non-constant,
if-chain.

### Why the byteop lowering exists

C allows taking a `char*` to a `struct` and scanning byte-by-byte.
SMT doesn't know that a struct is addressable as bytes. So when
the dereference layer emits a `byte_extract` (because the
source object is byte-backed and the target type is wider),
these lowering paths do the work of "treat this scalar as an
implicit byte array".

Common in:
- `memcpy` / `memset` modelling.
- Byte-backed dynamic objects (heap allocations through the
  `dynamic_object` mechanism).
- Type-punning via pointer cast.

## Overflow — `overflow_arith` / `overflow_cast` / `overflow_neg`

`smt_overflow.cpp`. Detect when a compile-time-unsigned or signed
arithmetic would escape the type's range. Used by
`--overflow-check`.

### `overflow_arith` (line 3)

Dispatches on `overflow.operand->expr_id`:

| Op | Detection (signed, BV mode) |
|---|---|
| `add` | `(op1 > 0 ∧ op2 > 0 ∧ result ≤ 0) ∨ (op1 < 0 ∧ op2 < 0 ∧ result ≥ 0)` |
| `sub` | `(op1 > 0 ∧ op2 < 0 ∧ result < 0) ∨ (op1 < 0 ∧ op2 > 0 ∧ result > 0)` (plus int mode explicit range check) |
| `mul` | Cross-product of sign cases, plus result-divides-by-operand sanity |
| `div` | `op2 == 0 ∨ (op1 == MIN_INT ∧ op2 == -1)` |
| `modulus` | same as div |

Under int encoding (`--ir`), compares the computed integer result
against `MIN_INT` / `MAX_INT` derived from `type->get_width()`. No
wrap semantics — the int is exact, the overflow is any value
outside the representable range.

### `overflow_cast` (line 301)

For narrowing cast: overflow iff the cast-back value differs from
the cast-from value.

### `overflow_neg` (line 334)

Specific to `INT_MIN` (has no positive representation in two's
complement).

## Tuples — `smt/tuple/` subdir

Three flavours, all implementing `tuple_iface`:

- **`smt_tuple_node_flattener`** (`tuple/smt_tuple_node.cpp` 266 LOC
  + `smt_tuple_node_ast.cpp` 181 LOC) — **default**.
- **`smt_tuple_sym_flattener`** (`tuple/smt_tuple_sym.cpp` 228 LOC
  + `smt_tuple_sym_ast.cpp` 200 LOC) — symbol-based alternative.
- **Native backend** — z3 / cvc5 / bitwuzla have native tuple
  support; when available + not overridden, the factory uses it.

### Node flattener — the default

Each tuple value is represented as a `tuple_node_smt_ast` which
holds a `std::vector<smt_astt> elements` — one AST per field.

- `tuple_create(structdef)` — create the tuple with a fresh name,
  populate `elements[i]` from `convert_ast(field_i)`.
- `tuple_fresh(sort, name)` — create with empty `elements`; lazy
  populate on first access via `make_free`.
- `make_free` (at `tuple_node_ast.cpp:16`) — iterate the struct's
  fields; for each, call `mk_fresh` (or recurse for nested
  tuples/arrays). The per-field names are `name.field0`, `name.field1`.
- `project(idx)` — force `make_free`; return `elements[idx]`.
- `update(value, idx)` — create a new `tuple_node_smt_ast` with a
  fresh name, copy `elements`, replace `elements[idx] = value`.
- `eq(other)` — force both `make_free`, zip `elements`, conjunct
  per-field equality.
- `ite(cond, false_op)` — force both, per-field ite.
- `assign(sym)` — copy `elements` to `sym->elements`; symbol-level
  aliasing.
- Arrays of tuples → `array_conv.mk_array_symbol(...)` with tuple
  subtype — **this is the KNOWNBUG #3 trigger point for arrays of
  structs** in non-native-array backends.

### Sym flattener — the alternative

Same interface. Each tuple value is a single SMT symbol whose
fields are accessed by name-mangling: field `f` of tuple
`X.` is represented by a symbol named `X.f`. Equalities etc.
are per-field symbol-level equalities.

Tradeoff:

- **Node**: more ASTs, but each AST is a simple symbol; lazy.
- **Sym**: fewer ASTs, but every operation goes through the
  name-mangling layer.

Historically node was faster; sym was kept as a backup because
specific solver bugs occasionally regressed under node.

### Array of tuples

Both flatteners delegate **back to `array_conv`** (the universal
array flattener) for arrays of tuples. The struct is
representation-decomposed first, so `mk_array_symbol` is called
with a tuple-sort as the array range. **This is why `array_convt`
needs to handle array-of-struct even when the backend has native
array theory**: the tuple flattener unconditionally routes through
`array_conv.mk_array_symbol`.

See the assertion at `array_conv.cpp:92-95` — if the struct sort
is itself an array sort (array of array of struct), assertion
fires. The only way to avoid this assertion in tuple-array
contexts is to have a **native** tuple implementation that is also
array-aware (cvc5 / bitwuzla / z3 with native arrays AND tuples) —
in which case `tuple_api != nullptr && array_api != nullptr`, the
factory skips both flatteners, and the backend handles the nested
representation directly.

## Floating-point — `fp/fp_conv.cpp`

2228 LOC. Two-axis behaviour:

- **Native FP** (bitwuzla / cvc5 / z3) — backend implements
  `fp_convt` and handles IEEE 754 encoding natively.
- **FP-to-BV** (`--fp2bv`, default on solvers without native FP) —
  the generic `fp_convt` in this file bitblasts FP operations to
  bitvectors. All IEEE 754 semantics (rounding modes, special
  values, denormals) encoded explicitly.
- **Real-arith** (`--ir` / `--ir-ieee`) — handled one level up in
  `smt_conv.cpp` via `apply_ieee754_*_enclosure`. The fp_convt is
  bypassed.

This doc doesn't go deeper — FP encoding is a field of its own.
The `README.txt` §Interval/Real-Arithmetic mode and the comments in
`smt_conv.cpp:1593+` (the `ieee_add_id` case) cover the real-arith
enclosures. If you need to touch FP, read
`fp/fp_conv.{cpp,h}` directly.

## Interactions worth knowing

- **Tuple flattener + array flattener** = `array_convt.mk_array_symbol`
  gets struct-sort subtypes. Always. The whole
  array-of-struct-of-arrays assertion failure chain routes through
  this.
- **Byte-ops + tuples** = byte-extract against a struct goes via
  `convert_typecast_to_struct` (reinterpret as BV first). The
  tuple flattener is then asked to create a fresh tuple bound to a
  sliced BV; which usually works but can fail on padded/aligned
  structs.
- **Casts + pointer tuples** = casting int↔ptr rebuilds the tuple
  from the int via the address-space lookup. Cost is O(n_objects)
  if-chain per cast.
- **Overflow + int encoding** = the overflow check compares against
  `MIN_INT` / `MAX_INT` derived from type width. Under int mode the
  BV semantics don't apply; overflow is purely "outside the range
  integers of this width can hold".

## Common pitfalls

- **"Typecast to bool returned (value != 0) but I expected (value
  == 1)"** — convert_typecast_to_bool in smt_casts.cpp is 0-vs-nonzero,
  per C semantics. Use `eq(val, 1)` if you want strict.
- **"Bitcast changed my value"** — bitcast between different widths
  extends/truncates. Endianness matters for struct↔BV. Check the
  config's `big_endian` flag.
- **"Byte-extract on a struct aborts"** — the code at
  `smt_byteops.cpp:10` asserts `!is_array_type(source)`. The
  struct-decomposition path should have lowered it to byte-extract
  on a BV — if it didn't, your struct type has unusual alignment.
- **"Overflow check says safe but the BV wrapped"** — under BV
  mode, overflow_arith checks for sign-flipping patterns; under
  int mode, it checks against fixed bounds. Mixing modes on
  different operands is undefined.
- **"Tuple array assertion fires"** — array_conv assertion at
  line 92-95 on nested unbounded array. See
  [array-conv.md](array-conv.md) §KNOWNBUG.
