# `irep2` — The Intermediate Representation

`src/irep2/irep2.h` (1368 LOC) + `irep2_expr.h` (3824 LOC) +
`irep2_type.h` (738 LOC) + `irep2_utils.h` (715 LOC). The universal
IR every frontend produces and every backend consumes.

This doc is a **reference index**, not a deep-dive — irep2 is a
vocabulary you use by name, not a subsystem you modify often. For
each entry it gives the kind, the semantics, and the file that
produces / consumes it.

## Smart-pointer wrappers

Two reference-counted wrappers:

- **`expr2tc`** — pointer to `expr2t` (expression). Cow semantics:
  modification goes through `.get()` which calls `.detach()` to
  avoid sharing.
- **`type2tc`** — pointer to `type2t` (type).

Almost every function in the pipeline takes these by const-ref
(`const expr2tc &e`). Ownership is shared; no explicit deletes.

## The two enums

### `type2t::type_ids` (`irep2.h:392-409`)

14 type kinds:

| ID | Meaning | Payload |
|---|---|---|
| `bool_id` | C bool | — |
| `empty_id` | void | — |
| `symbol_id` | Named type reference | `symbol_name` |
| `struct_id` | C struct | `members[]`, `member_names[]`, name, packed |
| `union_id` | C union | same as struct |
| `code_id` | Function type | return type, arg types, arg names, ellipsis |
| `array_id` | Array | subtype, size_expr (`gen_ulong(N)` or nil for inf), `size_is_infinite` |
| `vector_id` | Vector (SIMD) | subtype, array_size |
| `pointer_id` | Pointer | subtype, carry_provenance |
| `unsignedbv_id` | Unsigned BV | width |
| `signedbv_id` | Signed BV | width |
| `fixedbv_id` | Fixed-point | width, integer_bits |
| `floatbv_id` | IEEE 754 float | exponent, fraction |
| `cpp_name_id` | C++ mangled name | identifier |
| `end_type_id` | Sentinel | — |

### `expr2t::expr_ids` — expression kinds

Enumerated indirectly via the `ESBMC_LIST_OF_EXPRS` macro in
`irep2.h:26-139`. Approximately 100 kinds. Rough grouping:

**Constants (11 kinds)**
- `constant_int`, `constant_fixedbv`, `constant_floatbv`,
  `constant_bool`, `constant_string`, `constant_struct`,
  `constant_union`, `constant_array`, `constant_vector`,
  `constant_array_of`, `symbol`.

**Arithmetic (integer)**
- `add`, `sub`, `mul`, `div`, `modulus`, `neg`, `abs`.

**Arithmetic (IEEE)**
- `ieee_add`, `ieee_sub`, `ieee_mul`, `ieee_div`, `ieee_fma`,
  `ieee_sqrt`.

**Bitwise**
- `shl`, `ashr`, `lshr`, `bitand`, `bitor`, `bitxor`, `bitnand`,
  `bitnor`, `bitnxor`, `bitnot`.

**Boolean**
- `not`, `and`, `or`, `xor`, `implies`.

**Comparisons**
- `equality`, `notequal`, `lessthan`, `greaterthan`,
  `lessthanequal`, `greaterthanequal`.

**Casts**
- `typecast` (value-preserving), `bitcast` (bit-preserving),
  `nearbyint`.

**Control**
- `if` (ternary ?:), `sideeffect` (malloc, new, printf, etc.).

**Array / struct**
- `index` — `a[i]`.
- `with` — functional update: `a WITH [i := v]` or `s WITH [f := v]`.
- `member` — `s.f` (struct field access).
- `member_ref` — C++ pointer-to-member.
- `ptr_mem` — C++ pointer-to-member-function invoke.
- `concat` / `extract` — BV bit-range ops.
- `byte_extract`, `byte_update` — byte-granular access.

**Pointer**
- `address_of` — `&x`.
- `dereference` — `*p`.
- `same_object(a, b)` — do these pointers point into the same
  allocated object?
- `pointer_offset(p)` / `pointer_object(p)` /
  `pointer_capability(p)` — project the tuple fields.
- `dynamic_object(p)` — is p in dynamic storage?
- `object_descriptor` — container for (object, offset, alignment)
  triples (produced by dereference.cpp).
- `null_object` / `invalid_pointer`.
- `valid_object(p)` — is p live?
- `dynamic_size(p)` — size of the allocation p points at.
- `deallocated_obj(p)` — was p free'd?

**FP predicates**
- `isnan`, `isinf`, `isnormal`, `isfinite`, `signbit`.

**Integer helpers**
- `popcount`, `bswap`.

**Overflow checks**
- `overflow(op)` — does op over/underflow?
- `overflow_cast(cast)` — does cast lose data?
- `overflow_neg(val)` — INT_MIN negation check.

**Unknown / Invalid**
- `unknown` — "we don't track this pointer's target precisely".
- `invalid` — "this pointer is known-bad".

**Code statements (used in goto_convert, not in SSA)**
- `code_block`, `code_assign`, `code_init`, `code_decl`,
  `code_dead`, `code_printf`, `code_expression`, `code_return`,
  `code_skip`, `code_free`, `code_goto`, `code_function_call`,
  `code_comma`, `code_asm`, `code_cpp_*` (C++ specific).

**Concurrency**
- `races_check(lhs)` — assertion that no data race on lhs.

**Quantifiers**
- `forall`, `exists` — pair of (bound_variable, predicate).

**CHERI**
- `capability_base`, `capability_top`.

**Python-specific**
- `isinstance`, `hasattr`, `isnone`.

**Valid-object (memory safety)**
- `valid_object(p)` — life of allocated object.

## The `ESBMC_LIST_OF_EXPRS` macro

Boost preprocessor list of every expr kind. Used to:

1. Auto-generate enum values (`irep2.h:619-620`).
2. Auto-generate `get_expr_id(expr_id)` → string.
3. Auto-generate copy constructors, factory functions, predicates.

To add a new expression kind:

1. Insert its name into the macro list at the right slot.
2. Define the class in `irep2_expr.h` inheriting from `expr2t`.
3. Add `is_X2t(e)`, `to_X2t(e)` (const), `to_X2t(expr2tc)`
   (mutable), and `X2tc(args...)` factory.
4. Add handling in `convert_ast` in `src/solvers/smt/smt_conv.cpp`.
5. Add handling in any pattern-matchers in symex
   (`symex_assign.cpp`, `symex_goto.cpp`, etc.).

## Common access patterns

### Predicate / cast

```cpp
if (is_add2t(e)) {
  const add2t &op = to_add2t(e);
  // op.side_1, op.side_2, op.type accessible
}
```

Also `to_add2t(expr2tc)` mutable (triggers COW detach) and the
factory `add2tc(type, side_1, side_2)` that returns `expr2tc`.

### Walking operands

```cpp
e->foreach_operand([](const expr2tc &sub) { ... });   // const
e->Foreach_operand([](expr2tc &sub) { ... });         // mutable
```

Captures the child expressions regardless of the parent's kind.

### Walking subtypes of a type

```cpp
t->foreach_subtype(ns, [](const type2tc &sub) { ... });
```

For compound types (struct, array, etc.).

## Factory functions — `X2tc`

Every expression and type kind has a factory returning its smart
pointer wrapper:

```cpp
expr2tc e1 = constant_int2tc(get_uint_type(32), BigInt(42));
expr2tc e2 = add2tc(e1->type, e1, e1);
expr2tc e3 = symbol2tc(get_uint_type(32), "my_var");

type2tc t = unsignedbv_type2tc(32);
type2tc t2 = pointer_type2tc(t);
type2tc t3 = array_type2tc(t, gen_ulong(10), false);
```

These are generated by macro from `ESBMC_LIST_OF_EXPRS` /
`ESBMC_LIST_OF_TYPES`.

## Simplification — `do_simplify` / `simplify`

`irep2_utils.h` declares `simplify(expr2tc &e)` that runs
constant-folding / peephole optimisation. Called heavily in symex
to keep SSA equations tractable.

`goto_symex_statet::do_simplify` wraps this with a flag so
`--no-simplify` can disable.

## Migration from old irep

The old string-based `irept` (in `src/util/`) is still used in some
places — frontends produce it first, then convert. Helpers:

- `migrate_expr(const exprt &old, expr2tc &new)` — convert.
- `migrate_type(const typet &old)` → `type2tc`.
- `migrate_expr_back(expr2tc)` → `exprt`.
- `migrate_type_back(type2tc)` → `typet`.

When adding a new expr2 kind, also teach `migrate_expr` if the old
form can produce it (typically frontend work, not solver/symex).

## `get_sub_expr(i)` / `get_num_sub_exprs()`

Generic accessors indexed by position:

```cpp
for (unsigned i = 0; i < e->get_num_sub_exprs(); ++i) {
  const expr2tc *sub = e->get_sub_expr(i);
  ...
}
```

Used rarely — prefer the typed `to_*2t(e).side_1` access when you
know the kind.

## Helper types

- **`BigInt`** (`util/big-int/`) — arbitrary-precision integer used
  for `constant_int2t::value`, array sizes, bit widths.
- **`irep_idt`** — string-interned identifier; constant-time
  equality.
- **`dstring`** — underlying irep_idt storage.
- **`ieee_floatt`** — IEEE 754 float used for
  `constant_floatbv2t::value`.
- **`fixedbvt`** — fixed-point value.
- **`locationt`** — (file, line, column, function) tuple.

## Common traps

- **Don't mutate `expr2tc` through a const reference** — UB.
- **`is_*2t(nil_expr)` is false** — nil exprs are their own state,
  not a member of any kind.
- **`to_X2t(e)` on `e` of wrong kind aborts in debug, UB in
  release** — always pair with `is_X2t`.
- **Factory produces its own kind** — `add2tc(type, s1, s2)` always
  produces `add2t`. To produce `symbol2t`, use `symbol2tc`.
- **Types must match in arithmetic** — `add2tc(type, s1, s2)`
  expects both sides same type; if not, insert `typecast2tc`.
- **`with2t` vs `index2t`/`member2t` direction** — `with` produces
  a new aggregate value, not a lvalue. The old aggregate is
  unchanged.
- **`sideeffect2t` has many sub-kinds** (`malloc`, `va_arg`,
  `cpp_new`, `function_call`, ...) — check
  `to_sideeffect2t(e).kind`.

## Debug inspection

- `e->dump()` — prints the irep2 tree.
- `e->pretty(0)` — indented pretty print.
- `get_expr_id(e)` — stringifies the kind name.
- `<<e` — operator<< calls pretty.

## Reading order

1. `irep2.h` top — smart pointers, `ESBMC_LIST_OF_EXPRS` macro.
2. `irep2_type.h` — the 14 type classes (bool, bv, array, struct,
   ...).
3. `irep2_expr.h` — the ~100 expr classes. Each has a data class
   (holds fields) + a boilerplate class (adds cloning/comparison).
4. `irep2_utils.h` — `gen_zero`, `gen_ulong`, `is_pointer_type`,
   simplify, migration helpers.
