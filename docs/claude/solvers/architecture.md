# Solvers Architecture

## Pipeline

```
symex_target_equationt (SSA_steps)
  └── bmct::run_thread
        └── eq->convert(smt_convt &)                         src/goto-symex/symex_target_equation.cpp:154
              └── for each SSA_step:
                    convert_internal_step
                      ├── assignment → smt_convt::convert_assign(cond)
                      ├── assume    → smt_convt::convert_ast(cond), conjoined into assumpt_ast
                      ├── assert    → smt_convt::convert_ast(cond), negated + pushed onto OR chain
                      └── renumber  → smt_convt::renumber_symbol_address
              └── smt_convt::assert_ast(make_n_ary_or(negated_asserts))
        └── smt_convt::dec_solve()                            solver-native entry
        └── smt_convt::l_get / get / get_by_type              extract model
```

`smt_convt` is an **abstract class**; the factory in
`src/solvers/solve.cpp` picks a concrete subclass (`z3_convt`,
`cvc5_convt`, `bitwuzla_convt`, `boolector_convt`, ...). Every subclass
implements a small set of pure-virtual primitives
(`mk_smt_bv`, `mk_smt_bool`, `mk_smt_symbol`, `mk_extract`,
`mk_sign_ext`, `mk_zero_ext`, `mk_concat`, `mk_ite`, `get_bool`,
`get_bv`, `assert_ast`, `dec_solve`, `solver_text`). Everything else
either has a default implementation on `smt_convt` or is routed
through one of three *interfaces*: `tuple_iface`, `array_iface`,
`fp_convt`.

## The three plug-in interfaces

Attached after construction by `create_solver`:

```
smt_convt
  ├── tuple_iface *tuple_api   — struct/tuple sort handling
  ├── array_iface *array_api   — array store/select
  └── fp_convt    *fp_api      — floating-point
```

Choice of implementation is per-solver × per-option:

```
solve.cpp:148      pick_solver()   → backend constructor returns:
                                     (ctx, tuple_api?, array_api?, fp_api?)

solve.cpp:158      tuple_api:
                   - native (cvc5/z3) if solver returned one AND no override
                   - --tuple-node-flattener → smt_tuple_node_flattener
                   - --tuple-sym-flattener  → smt_tuple_sym_flattener
                   - else → smt_tuple_node_flattener  (default)

solve.cpp:173      array_api:
                   - native if solver returned one AND not --array-flattener
                   - else → array_convt              (universal flattener)

solve.cpp:180      fp_api:
                   - native if solver returned one AND not --fp2bv
                   - else → fp_convt                 (real-arith or fixedbv)
```

So a backend can "natively support" any subset of {arrays, tuples,
FPs}; whatever's missing, the generic flattener fills in.

## Core abstractions

### `smt_sort` (`smt/smt_sort.h`)

An abstract SMT sort. Concrete sorts are built via
`mk_bool_sort` / `mk_bv_sort(width)` / `mk_int_sort` / `mk_real_sort`
/ `mk_array_sort(domain, range)` / `mk_fbv_sort` / `mk_bvfp_sort` /
`mk_fpbv_sort`. Sort IDs (`smt_sort_kind`): INT, REAL, BV, FIXEDBV,
ARRAY, BOOL, STRUCT, BVFP, FPBV, BVFP_RM, FPBV_RM.

An array sort stores (domain width, range sort). Multi-dim arrays
are flattened to single-dim via `flatten_array_type` — the domain
widths are *concatenated* into one wide index type. See
[array-conv.md](array-conv.md).

### `smt_ast` (`smt/smt_ast.h`)

An abstract SMT term. Subclassed per backend
(`z3_smt_ast`, `cvc5_smt_ast`, ...) and per interface type
(`array_ast`, `tuple_node_smt_ast`). Has five virtual operations that
matter for compound types:

- `ite(ctx, cond, false_op)` — produce an if-then-else.
- `eq(ctx, other)` — equality (struct equality decomposes to per-field).
- `assign(ctx, sym)` — bind a symbol to this ast.
- `update(ctx, value, idx, idx_expr)` — array `WITH` / tuple `WITH`.
- `select(ctx, idx)` — read an array or tuple element.
- `project(ctx, elem)` — extract a struct field.

Solver backends override these when their native representation
needs special handling.

### `smt_convt` (`smt/smt_conv.h`)

The beast. ~1100-line header; 5229-line `.cpp`. Public API:

- **Entry**: `convert_ast(expr2tc)` — the expr→ast dispatch.
- **Assignment**: `convert_assign(expr)` — shortcut for `lhs == rhs`
  that backends can optimize natively.
- **Top-level**: `assert_ast`, `dec_solve`, `get`, `l_get`.
- **Context stack**: `push_ctx`, `pop_ctx`, `pre_solve`.
- **Primitive constructors**: `mk_smt_bv`, `mk_smt_bool`,
  `mk_smt_symbol`, `mk_smt_int`, `mk_smt_real`, `mk_extract`,
  `mk_ite`, `mk_concat`, `mk_sign_ext`, `mk_zero_ext`, `mk_fresh`.
- **Function-apps**: `mk_bvadd`, `mk_bvmul`, … the ~60 `mk_*`
  methods that map onto SMT builtins.
- **High-level pointer helpers**: `convert_pointer_arith`,
  `convert_ptr_cmp`, `convert_addr_of`, `convert_identifier_pointer`.
- **Byte-level**: `convert_byte_extract`, `convert_byte_update`.
- **Typecast**: `convert_typecast` + ~a dozen specialised
  `convert_typecast_to_*` helpers.
- **Overflow**: `overflow_arith`, `overflow_cast`, `overflow_neg`.
- **Float**: `apply_ieee754_semantics` + the RNE/RNA/RUP/RDN/RTZ
  interval-lifting helpers.

State carried on `smt_convt`:

- `smt_cachet smt_cache` — expr2tc → smt_astt cache (hashed, with
  push/pop levels).
- `smt_sort_cachet sort_cache` — type2tc → smt_sortt cache.
- `pointer_logic` (list, for push/pop) — the memory model's
  book-keeping: object-id counter, address-space layout.
- `pointer_struct` / `machine_ptr` — the tuple shape used to
  represent C pointers (object_id : int, offset : int).
- `addr_space_*` — running state of the address-space array that
  asserts non-overlap between allocated objects.
- `live_asts` + `live_asts_sizes` — lifetime tracking for push/pop.
- `renumber_map` — records `realloc` re-numberings per-push-level.
- `int_encoding` — flag toggling between SBV and LIA encoding.

### `smt_func_kind`

An enum (defined in `smt_conv.cpp` near the top) that names every
function application ESBMC produces. `convert_ast` maps an expr2tc
kind to an `smt_func_kind`, then the generic path calls
`mk_func_app(args, kind, return_sort)` which dispatches to the
backend. (In practice most dispatches go straight to the
hand-written `mk_bvadd` / `mk_bvmul` / ... shortcuts — the
`mk_func_app` route is a default for backends that prefer one entry
point.)

## Flatteners

### `array_convt` (`smt/array_conv.{cpp,h}`, 1377 LOC)

Universal array flattener. Used whenever the backend doesn't
advertise native array support. Strategy: Kroening's decision
procedure — maintain a list of (array, index, value) store
triples, generate a set of Ackermann-style axioms constraining
`select(store(a,i,v), j) == if i==j then v else select(a,j)`.

Critical limitation lives at lines 92-95: array-of-array is not
representable when the outer array is unbounded. See
[array-conv.md](array-conv.md).

### `smt_tuple_*` (`smt/tuple/`, 1423 LOC)

Three flattener styles:

- **`smt_tuple_node_flattener`** (default) — each struct instance
  becomes a *node* that owns one smt_ast per field, plus per-field
  book-keeping. Struct equality = per-field equality.
- **`smt_tuple_sym_flattener`** — each struct instance becomes a
  single symbol; field access produces a fresh per-field symbol
  constrained to equal the corresponding part of the parent. Simpler
  but produces more symbols.
- **Backend native** — cvc5 and z3 both support tuple sorts
  natively; then tuple_api points at their implementation.

### `fp_convt` (`smt/fp/fp_conv.cpp`, 2228 LOC)

Floating-point reducer. Depending on config:

- Bitwuzla / cvc5 / z3 → native FP theory.
- `--fp2bv` → lowered to BV operations.
- `--ir-ieee` → real-arithmetic mode with epsilon enclosures
  (documented in `solvers/README.txt`).

## Context stack (push/pop)

`smt_convt::push_ctx` / `pop_ctx` snapshot the live-ast vector, the
address-space state, and the renumber map. Used by:

- `--smt-during-symex` family (`runtime_encoded_equationt`) — mid-SSA
  queries.
- The bmct incremental loop — try k=1, k=2, …, restoring solver
  state between bounds.

Backend implementations either use native push/pop or throw. Most
non-Boolector backends support it, but the upstream comment at
`smt_conv.h:113` warns not to trust it.

## Lifetime

`smt_convt` owns the ast vector; `pop_ctx` frees ast created above
the snapshot level. Free-cache entries above that level are also
dropped. Sort cache is kept across pushes (sorts are long-lived).

## Where each subsystem lives

| Dir / file | LOC | Purpose |
|---|---|---|
| `solve.{cpp,h}` | 189 | Factory — pick solver, attach flatteners |
| `smt/smt_conv.{cpp,h}` | 5229 + 1131 | The abstract class — convert_ast, sort/ast cache, pointer+byte helpers |
| `smt/smt_ast.h` | 118 | Abstract term — 5 virtual ops + backend subclasses |
| `smt/smt_sort.h` | 227 | Sort enum + base class; width/domain/range accessors |
| `smt/smt_array.h` | 64 | `array_iface` — 4 methods + capability flags |
| `smt/array_conv.{cpp,h}` | 1377 + 454 | Universal array flattener |
| `smt/smt_memspace.cpp` | 648 | Pointer encoding — addrspace, `same_object`, `pointer_offset` |
| `smt/smt_casts.cpp` | 776 | Typecast lowering |
| `smt/smt_bitcast.cpp` | 260 | Bitcast (reinterpret) lowering |
| `smt/smt_byteops.cpp` | 533 | `byte_extract` / `byte_update` lowering |
| `smt/smt_overflow.cpp` | 370 | Integer arithmetic overflow detection |
| `smt/tuple/smt_tuple.h` | 68 | `tuple_iface` — 8 methods |
| `smt/tuple/smt_tuple_node{,_ast}.cpp` | 266 + 181 | Node flattener |
| `smt/tuple/smt_tuple_sym{,_ast}.cpp` | 228 + 200 | Symbol flattener |
| `smt/tuple/smt_tuple_array_ast.cpp` | 195 | Tuple-array combo |
| `smt/fp/fp_conv.{cpp,h}` | 2228 + 277 | Floating-point reducer |
| `z3/z3_conv.{cpp,h}` | — | Z3 backend |
| `cvc5/cvc5_conv.{cpp,h}` | — | cvc5 backend (Solidity default) |
| `bitwuzla/bitwuzla_conv.{cpp,h}` | — | Bitwuzla backend |
| `boolector/` | — | Boolector backend (the reference impl, per upstream README) |
| `cvc4/` | — | CVC4 (legacy) |
| `smtlib/` | — | SMTLIB text-dump backend (no native solver) |
| `mathsat/` | — | MathSAT backend |
| `yices/` | — | Yices backend |
| `minisat/` | — | MiniSAT (SAT-only, via full bit-blast) |
| `prop/` | — | Propositional layer + pointer_logict |
| `sat/` | — | SAT common utilities |

## Reading order for a fresh contributor

1. [architecture.md](architecture.md) — this file.
2. [smt-conv.md](smt-conv.md) — `convert_ast` dispatch structure.
3. [memory-model.md](memory-model.md) — pointers in the formula,
   the address-space array.
4. [array-conv.md](array-conv.md) — the flattener + KNOWNBUG #3.
5. [type-encoding.md](type-encoding.md) — casts, bytes, tuples,
   overflow.
6. [backends.md](backends.md) — per-solver capability survey.
