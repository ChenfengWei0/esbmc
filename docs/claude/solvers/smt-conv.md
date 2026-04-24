# `smt_conv.cpp` — The Expression Dispatch

`src/solvers/smt/smt_conv.cpp` (5229 LOC). The heart of the solver
layer. Implements the abstract `smt_convt` class: takes an
`expr2tc` (ESBMC's irep), dispatches on its `expr_id`, and returns an
`smt_astt` (backend-opaque handle to an SMT term). This is where
"expression in ESBMC's IR" becomes "term in the solver".

## Entry points

| Function | Line | Purpose |
|---|---|---|
| `smt_post_init()` | 150 | One-time setup after the backend ctor runs. Initializes the address-space array and (for int mode) the shift power-of-two lookup. |
| `convert_ast(expr2tc)` | 1327 | **The** dispatch. ~1400 LOC switch on `expr_id`. |
| `convert_terminal(expr)` | 2902 | Handles leaf exprs: constant_int, constant_bool, constant_fixedbv/floatbv, symbol. |
| `convert_sort(type2tc)` | 2753 | type2tc → smt_sortt. Dispatches on `type_id`. Cached. |
| `convert_assign(eq)` | 321 | Faster path for `lhs == rhs`: uses `smt_ast::assign` virtual so backends can optimise. |
| `push_ctx()` / `pop_ctx()` | 175 / 231 | Stack discipline for tuple/array/pointer/renumber state + live_ast GC. |
| `assert_expr(e)` | 2748 | `assert_ast(convert_ast(e))` one-liner. |

## The `convert_ast` contract

Input: `expr2tc` (may contain any of ~170 `expr2t` kinds).
Output: `smt_astt` whose sort matches `convert_sort(expr->type)`.

Two-stage process:

### Stage 1 — Operand recursion

```cpp
switch (expr->expr_id) {
  case with_id: case constant_array*_id: case index_id:
  case address_of_id: case ieee_{add,sub,mul,div,fma,sqrt}_id:
  case pointer_{offset,object,capability}_id:
    break;                              // do NOT pre-convert operands
  default:
    for (op : expr.operands) args.push_back(convert_ast(op));
}
```

The carved-out cases are ones where the handler needs raw access to
the sub-expressions (to inspect index offsets, pointer targets,
rounding modes, etc.) rather than already-converted ASTs. Everything
else bulk-converts operands first.

### Stage 2 — Per-kind dispatch

One big switch, ~1300 lines. Shape:

```
terminal kinds         → convert_terminal (constants + symbols)
constant_struct_id     → tuple_api->tuple_create
constant_union_id      → bitcast→typecast to uint
constant_vector_id     → array_create (vectors are arrays at SMT level)
constant_array{,_of}_id → flatten_array_body + tuple_array_create_despatch OR array_create
add/sub/mul/div        → mk_{bv}add/sub/... ; pointer arith goes through convert_pointer_arith
ieee_{add,sub,mul,…}   → int_encoding: apply_ieee754_semantics with interval enclosures
                          else: fp_api->mk_smt_fpbv_{add,sub,...}
modulus_id             → mk_bv{s,u}mod / mk_mod
index_id               → convert_array_index (possibly via decompose_select_chain)
with_id                → struct: srcval->update(...)
                          union: bitcast→concat
                          array: convert_array_store (possibly decompose_store_chain)
member_id              → convert_member
same_object_id         → args[0]->project(0) == args[1]->project(0)
pointer_offset_id      → project(1)   [pointer is (obj_id, offset) tuple]
pointer_object_id      → project(0)
pointer_capability_id  → project(2)   [CHERI]
typecast_id            → convert_typecast (dispatch into ~12 cast helpers)
if_id                  → args[1]->ite(args[0], args[2])   [virtual for tuples/arrays]
isnan/isinf/isnormal/isfinite/signbit/popcount/bswap → dedicated convert_* helpers
overflow{,_cast,_neg}  → overflow_arith/cast/neg
byte_{extract,update}  → convert_byte_{extract,update}
address_of_id          → convert_addr_of
equality_id / notequal → args[0]->eq(args[1])   [struct/pointer equality via virtual]
shl/ashr/lshr          → bv ops; int_encoding uses power-of-two lookup
comparisons (<, <=, >, >=) → convert_ptr_cmp when pointer-typed,
                              else mk_bv{s,u}{lt,le,gt,ge} / fp_api->...
concat/extract         → mk_concat / mk_extract
bitand/bitor/bitxor/…  → mk_bv{and,or,xor,nand,nor,nxor,not}
and/or/xor/not/neg/implies → straight mk_{and,or,xor,not,neg,implies}
bitcast_id             → convert_bitcast
forall/exists          → mk_quantifier after body substitution
code_comma_id          → right-hand side only (leftover from goto conv)
```

After the switch, the result is cached by
`(expr, ast, ctx_level)` in `smt_cache` so subsequent `convert_ast`
on the same expression returns the same ast.

## Cache lifecycle

- **`smt_cache`** — boost multi_index: hashed by expr2tc, ordered
  by level. Inserts are guarded by `smt_cache_mutex`.
- **`sort_cache`** — simpler type2tc → smt_sortt map. Sorts outlive
  pushes so no level tracking.
- **`pop_ctx`** drops every cache entry at `level > new_ctx_level`
  (uses the ordered index on level). Also frees `live_asts` beyond
  the snapshot.

The cache is load-bearing for the tuple flatteners: in the sym
flattener, an assignment's `side2` is force-inserted into the cache
keyed on `side1`, so subsequent reads through `side1` see the
assigned value directly. See `convert_assign` at line 321 for the
pattern.

## Sort conversion — `convert_sort`

`smt_conv.cpp:2753`. Type-dispatch table:

| `type_id` | Sort |
|---|---|
| `bool_id` | cached `boolean_sort` |
| `struct_id` | `tuple_api->mk_struct_sort(type)` |
| `code_id`, `pointer_id` | `tuple_api->mk_struct_sort(pointer_struct)` — pointers are 2-tuple (object_id, offset), optionally 3-tuple with capability under CHERI |
| `unsignedbv_id` / `signedbv_id` | `mk_int_bv_sort(width)` — branches on `int_encoding` |
| `fixedbv_id` | `mk_real_fp_sort(int_bits, frac_bits)` |
| `floatbv_id` | `mk_real_fp_sort(exp_width, frac_width)` |
| `vector_id`, `array_id` | see below |
| `union_id` | `mk_int_bv_sort(total_bytes * 8)` — unions are unsigned BVs |
| `empty_id` | 1-bit BV placeholder (shows up in some Solidity nested-mapping intermediates) |

### Array sort — the flattening branch

`smt_conv.cpp:2800`:

```cpp
case array_id:
{
  const array_type2t &arrtype = to_array_type(type);

  // Infinite array of array: keep nested, do NOT flatten
  if (arrtype.size_is_infinite && is_array_type(arrtype.subtype)) {
    type2tc t = make_array_domain_type(arrtype);
    smt_sortt d = mk_int_bv_sort(t->get_width());
    smt_sortt r = convert_sort(arrtype.subtype);   // RECURSIVE
    result = mk_array_sort(d, r);
    break;
  }

  // Finite (nested) arrays: flatten domains via multiplication
  type2tc t = make_array_domain_type(to_array_type(flatten_array_type(type)));
  smt_sortt d = mk_int_bv_sort(t->get_width());

  type2tc range = get_flattened_array_subtype(type);
  if (is_tuple_ast_type(range)) {                   // array of struct
    type2tc thetype = flatten_array_type(type);
    rewrite_ptrs_to_structs(thetype);
    result = tuple_api->mk_struct_sort(thetype);
    break;
  }

  smt_sortt r = is_bool_type(range) && !array_api->supports_bools_in_arrays
                  ? mk_int_bv_sort(1) : convert_sort(range);
  result = mk_array_sort(d, r);
}
```

Two important behaviours:

1. **Nested infinite arrays** (e.g. Solidity `mapping(K => mapping(K
   => V))`) stay nested. The recursive `convert_sort(arrtype.subtype)`
   reproduces the outer shape at the SMT level.
2. **Nested finite arrays** flatten via `flatten_array_type` +
   `decompose_select_chain` / `decompose_store_chain`. The outer
   `T[M][N]` becomes a single-domain array with a
   `width = M*N` index computed as `i * N + j`.

This asymmetry (nested infinite stays, nested finite flattens) is
important — it's why the multi-dim fixed-array KNOWNBUGs occur
*not at this layer* but in `array_convt` when the flattener runs.
See [array-conv.md](array-conv.md).

### Flatten helpers

| Helper | Purpose |
|---|---|
| `flatten_array_type(type)` | Multi-dim → single-dim type with size product. Leaves infinite arrays alone. |
| `flatten_array_body(expr)` | Multi-dim `constant_array` → single-dim `constant_array`. |
| `get_flattened_array_subtype(type)` | Walks through any number of nested array types, returns the innermost non-array element type. |
| `make_array_domain_type(array_type)` | Compute the BV domain width needed to index `array_size` elements. |
| `decompose_select_chain(index, &base)` | For `a[i][j][k]`, produce `i*A + j*B + k` and return `a` in `base`. |
| `decompose_store_chain(with, &val)` | Dual, for nested `WITH` writes. |

## Pointer dispatch

Four pointer-specific entry points, all dispatched from
`convert_ast`:

- `convert_pointer_arith(expr, type)` — fires for `add`/`sub` whose
  result type is pointer or whose operands are pointer-typed. Uses
  element-size multiplication and sign-matching — no direct bitvector
  add on a pointer struct.
- `convert_ptr_cmp(a, b, template_expr)` — `<`/`<=`/`>`/`>=` on
  pointers. Optimises for known-same-object pointers (compare
  offsets only).
- `convert_addr_of(expr)` — `&x`. Produces an (obj_id, 0) constant
  tuple, registering `x` in `pointer_logic` and
  `addr_space_data` if first use.
- `convert_identifier_pointer(sym_expr, name, type)` — the real
  work for symbol addresses; allocates a fresh object id and
  constrains its addrspace slot.

Details in [memory-model.md](memory-model.md).

## IEEE 754 dispatch

Two paths, selected by `int_encoding`:

1. **`int_encoding == true`** (`--ir` / `--ir-ieee`) — real
   arithmetic with epsilon enclosures. `ieee_add/sub/mul/div`
   compute the real-valued result via `mk_add/sub/mul/div`, then
   `apply_ieee754_semantics` (or the specialised
   `apply_ieee754_{rne,rna,rup,rdn,rtz}_enclosure` helpers for
   `--ir-ieee`) wrap the result in `ra_lo ≤ result ≤ ra_hi`
   inequalities.
2. **`int_encoding == false`** (default) — bit-precise. Delegates to
   `fp_api->mk_smt_fpbv_{add,sub,mul,div,...}` which either hits
   native FP theory (bitwuzla/cvc5/z3) or the `fp_convt`
   bitblasting reducer.

The 5-mode enclosure helpers are solver-layer code (don't touch
them unless you understand IEEE 754 rounding semantics —
`README.txt` §Interval/Real-Arithmetic mode has the math). These
were added to make the real-arith path actually sound; before the
enclosures, `--ir` produced spurious unsat because the solver could
pick real values with finer precision than IEEE 754 allows.

## Shift handling in int mode

`shl_id` / `ashr_id` / `lshr_id` at lines 2331–2404. In BV mode,
direct `mk_bvshl` / `mk_bvashr` / `mk_bvlshr`. In int mode, can't
shift directly — instead:

```cpp
smt_astt powval = int_shift_op_array->select(this, shift_amount);
a = mk_mul(args[0], powval);     // for shl, or mk_div for lshr/ashr
```

The `int_shift_op_array` is the 64-element array `[1, 2, 4, ..., 2^63]`
built at `smt_post_init`. Shifts beyond 63 produce whatever the solver
decides is out-of-bounds — usually zero, but not standardised.

## The `smt_ast` virtuals as dispatch glue

A few cases route through `smt_ast` virtual methods so that tuple
flatteners and array wrappers can override without touching
`smt_convt`:

- `args[1]->ite(args[0], args[2])` — if_id. Tuples ite per-field.
- `args[0]->eq(args[1])` — equality/notequal. Tuples and pointers
  decompose to per-field equality.
- `srcval->update(value, idx)` — `with_id` on struct/pointer. Tuples
  emit per-field write.
- `a->select(this, idx)` — array read, tuple-array projection.
- `a->project(this, elem)` — struct member extract,
  `pointer_{offset,object,capability}` projection.

If you add a new tuple representation, you override these five on
the custom `smt_ast`.

## Quantifiers

`forall_id` / `exists_id` at `smt_conv.cpp:2677`. ESBMC represents
quantifiers symbolically as `forall(&x, body)` / `exists(&x, body)`.
The dispatch:

1. Peel `typecast` and `address_of` to extract the bound symbol.
2. Mint `__ESBMC_quantifier_N` fresh bound variable.
3. Inline any cached quantifier-valued definitions (so nested
   quantifiers work correctly).
4. Substitute `bound_symbol` for the original in the predicate
   (`replace_name_in_body`).
5. `mk_quantifier(is_forall, [bound], body_ast)` — default is abort
   unless overridden by a backend with quantifier support.

Only some backends support this (cvc5 with quantifier tactics, z3
with its quantifier engine). Most Solidity / C code never uses them
at the IR level.

## Context stack

`push_ctx` / `pop_ctx` at lines 175 / 231. Pushes:

- `tuple_api->push_tuple_ctx()` / `array_api->push_array_ctx()` —
  interface-specific bookkeeping.
- `addr_space_data` / `addr_space_sym_num` — memory model snapshot.
- `pointer_logic` — the `pointer_logict` instance.
- `renumber_map` — realloc re-numberings.
- `live_asts_sizes.back() = live_asts.size()` — snapshot for GC.

Pop reverses, deletes all ASTs after the snapshot, and drops
`smt_cache` / `cache_numindex` entries at the popped level.
`ctx_level` decrements.

## Adding a new expr kind

Rare — most language-level features lower to existing irep2 kinds in
`goto_symex_*`. When truly needed:

1. Add the kind to `ESBMC_LIST_OF_EXPRS` (in `irep2/irep2_expr.h`).
2. Generate the `is_*2t`, `to_*2t`, and `*_2tc` wrappers.
3. Add a `case X_id:` in `convert_ast` (`smt_conv.cpp`).
4. Either call into existing `mk_*` primitives, or add a new
   abstract virtual to `smt_convt` and a default + override in each
   backend.
5. Update the operand-recursion carve-out list at the top of
   `convert_ast` if you need raw operand access.

Check whether the thing you're adding truly belongs at the SMT
layer. The file comment lists what should NOT be here:
pointer dereferencing, control flow guards, pointer liveness,
dynamic allocation — those are symex concerns.

## Debugging

- `--symex-ssa-trace` / `--ssa-trace` / `--ssa-smt-trace` — progressive
  verbosity. First logs the SSA step, second its text, third calls
  `cond_ast->dump()` on the converted AST.
- `--dump-smt` / `print_model` — post-solve dumps.
- `--smt-formula-only` — don't solve, just build + dump.
- Build with `-DNDEBUG=0`: enables every assert including the
  array-sort compatibility check at `smt_sort.h:50`.
- `to_solver_smt_ast<derived>` — safe cast from `smt_astt` to a
  backend-specific subclass. Assertions inside catch bad
  dispatches.

## Common pitfalls

- **Mixing `int_encoding` and BV constants in the same formula** —
  every `mk_smt_bv` call under `int_encoding` will crash. Always
  use `mk_int_bv_sort(width)` and `gen_zero`/`gen_one` helpers.
- **Struct equality returning a BV** — backends that don't override
  `smt_ast::eq` for tuples fall back to the base `mk_eq` which
  compares the opaque backend pointers (never equal). Always
  override in custom tuple asts.
- **Pointer equality between differently-typed pointers** — pointer
  tuples include the type width in their `addr_space_data`
  record; two pointers with different widths can test equal via
  `same_object` if their object_ids happen to coincide. Be explicit
  about casts.
- **Cache staleness across push/pop** — the sort cache is NOT
  push/pop aware. If a type's sort representation depends on
  context state (unlikely in practice), sort_cache can return stale
  entries.
- **`empty_id` sort is 1-bit BV** — a placeholder for some
  Solidity-frontend intermediate. If you see this in the SMT formula,
  check whether a real type should have been emitted upstream.
