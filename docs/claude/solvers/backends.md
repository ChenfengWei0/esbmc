# Backends — Per-Solver Capability Survey

Nine backends live under `src/solvers/`. This doc surveys what each
one implements natively, what it delegates to the universal flatteners,
and which flags activate / deactivate it.

## Factory wiring

`src/solvers/solve.cpp:139-187`:

```cpp
smt_convt *create_solver(name, ns, options) {
  solver_creator &factory = pick_solver(name, options);  // based on CLI flags
  smt_convt *ctx = factory(options, ns, &tuple_api, &array_api, &fp_api);

  // tuple_iface: native → flattener fallback
  ctx->set_tuple_iface(
    tuple_api != null && !tuple_flat ? tuple_api
    : tuple_node_flat ? new smt_tuple_node_flattener(ctx, ns)
    : tuple_sym_flat  ? new smt_tuple_sym_flattener(ctx, ns)
    :                   new smt_tuple_node_flattener(ctx, ns));  // default

  // array_iface
  ctx->set_array_iface(
    array_api != null && !array_flat ? array_api
    :                                   new array_convt(ctx));

  // fp_convt
  ctx->set_fp_conv(
    fp_api == null || fp2bv ? new fp_convt(ctx)
    :                          fp_api);

  ctx->smt_post_init();
}
```

Each backend's `create_new_<solver>_solver` function populates
zero or more of `tuple_api`/`array_api`/`fp_api` via pointer-out.
Those that do **not** populate a pointer force the factory to use
the universal flattener.

## Backend capability matrix

| Backend | File | `tuple_api` | `array_api` | `fp_api` | Quantifiers | Multi-inherits |
|---|---|---|---|---|---|---|
| **z3** | `z3/z3_conv.{cpp,h}` | native | native | native | yes (native) | `smt_convt, tuple_iface, array_iface, fp_convt` |
| **cvc5** | `cvc5/cvc5_conv.{cpp,h}` | flattener (omits tuple_api*) | native | native | yes (`mk_quantifier`) | `smt_convt, array_iface, fp_convt` |
| **bitwuzla** | `bitwuzla/bitwuzla_conv.{cpp,h}` | flattener | native | native | no (QF-only) | `smt_convt, array_iface, fp_convt` |
| **boolector** | `boolector/boolector_conv.{cpp,h}` | flattener | flattener | flattener | no | `smt_convt` only — the reference for "pure SAT-based backend" |
| **cvc4** | `cvc4/cvc_conv.{cpp,h}` | flattener | (varies) | flattener | no | legacy |
| **mathsat** | `mathsat/mathsat_conv.{cpp,h}` | flattener | native | native | no | `smt_convt, array_iface, fp_convt` |
| **yices** | `yices/yices_conv.{cpp,h}` | native | native | flattener | no | `smt_convt, tuple_iface, array_iface` |
| **smtlib** | `smtlib/smtlib_conv.{cpp,h}` | flattener | flattener | flattener | varies | Text-dump backend; pipes to an external solver |
| **minisat** | `minisat/` | — | — | — | no | Propositional only (bit-blasted by prop/sat layer) |

(*) cvc5 does natively support datatypes including tuples, but
ESBMC's cvc5 backend currently leaves the tuple API null and lets
the flattener handle it. This is a historical choice that
could potentially be flipped (see Further notes below).

## z3 — the all-rounder

`src/solvers/z3/z3_conv.{cpp,h}`. Populates all three APIs:

```cpp
smt_convt *create_new_z3_solver(...) {
  z3_convt *conv = new z3_convt(ns, options);
  *tuple_api = static_cast<tuple_iface *>(conv);
  *array_api = static_cast<array_iface *>(conv);
  *fp_api    = static_cast<fp_convt *>(conv);
  return conv;
}
```

Constructor invokes the Z3 C++ API directly. Builds a solver with
the tactic pipeline `simplify & solve-eqs & simplify & smt`.

**Push/pop**: `solver.push()` / `solver.pop()` on the Z3 solver,
plus `smt_convt::push_ctx/pop_ctx` for the abstract state.

**Quirks**:
- The `smtlib2_compliant` flag is a Z3 config option — when set,
  the debug dumps match the SMTLIB2 standard.
- `relevancy=0` disables Z3's relevancy propagation — a speed
  tradeoff; on Solidity 256-bit BV it can hurt more than help.
- **Known weakness**: Z3 slow on QF_BV with 256-bit arithmetic
  (documented in `CLAUDE.md`). CVC5 and Bitwuzla both outperform it
  on Solidity regressions by 5-20×.

## cvc5 — Solidity default

`src/solvers/cvc5/cvc5_conv.{cpp,h}`. Populates `array_api` + `fp_api`:

```cpp
smt_convt *create_new_cvc5_solver(...) {
  cvc5_convt *conv = new cvc5_convt(ns, options);
  *array_api = static_cast<array_iface *>(conv);
  *fp_api    = static_cast<fp_convt *>(conv);
  return conv;
}
```

Note: `tuple_api` is not set — the factory uses the
`smt_tuple_node_flattener` default. So even under cvc5, **tuples
still go through the flattener**, and any tuple-array ends up
routing into `array_convt::mk_array_symbol`.

**Push/pop**: `smt_convt::push_ctx / pop_ctx` (inherited).
`cvc5::Solver` has its own push/pop, inherited via the
`smt_convt` standard mechanism (verified via
`cvc5_convt::push_ctx` override if present).

**Quantifiers**: `mk_quantifier` overrides abstract base; delegates
to cvc5's quantifier engine. This is the reason cvc5 is the default
when quantifiers are needed (symex-synthesised forall's in C model
libraries).

**Strengths on Solidity**:
- Native bit-precise 256-bit BV. Tight integration with the
  BV decision procedure.
- Native FP theory (rarely used in Solidity, but available).
- Generally 2-5× faster than Z3 on mainnet contract verification.

## Bitwuzla — fast on pure QF_BV

`src/solvers/bitwuzla/bitwuzla_conv.{cpp,h}`. Populates `array_api`
+ `fp_api`:

```cpp
smt_convt *create_new_bitwuzla_solver(...) {
  bitwuzla_convt *conv = new bitwuzla_convt(ns, options);
  *array_api = static_cast<array_iface *>(conv);
  *fp_api    = static_cast<fp_convt *>(conv);
  return conv;
}
```

Constructor **rejects `--ir` / int-encoding**:
```
if (options.get_bool_option("int-encoding")) {
  log_error("Bitwuzla does not support integer encoding mode");
  abort();
}
```

So Bitwuzla is BV-only. This is a deliberate limit: Bitwuzla's
sweet spot is quantifier-free BV and arrays, and it's specifically
optimised for the pattern Solidity produces.

**Push/pop**: `bitwuzla_push / pop` delegates, and pop_ctx also
cleans `symtable` (level-indexed via `get<1>().erase(ctx_level)`).

**Setup is simple** — opaque term manager, options, one solver
instance. No tactic pipeline.

**CLAUDE.md notes**: the Solidity default preference sequence is
Bitwuzla first, then CVC5, then Z3. Bitwuzla requires
`libgmp-dev` + `meson/ninja` to build.

## Boolector — the reference "bring your own abstraction"

`src/solvers/boolector/boolector_conv.{cpp,h}`. **Populates none**
of tuple_api, array_api, fp_api:

```cpp
smt_convt *create_new_boolector_solver(...) {
  boolector_convt *conv = new boolector_convt(ns, options);
  return conv;   // no *tuple_api = ..., *array_api = ..., *fp_api = ...
}
```

So Boolector uses:
- `smt_tuple_node_flattener` (default tuple flattener).
- `array_convt` (universal array flattener).
- `fp_convt` (generic bitblast FP).

The upstream README calls Boolector "the best reference for how new
backends should be arranged" — because every abstraction is done
through the universal flatteners, it's a minimal backend.

**This is also the backend most affected by KNOWNBUG #3.** A
Solidity contract with `mapping(K => T[N])` running under Boolector
hits `array_convt::mk_array_symbol`'s nested-array assert at line
92-95. Under native-array backends (cvc5/bitwuzla/z3), the native
path handles the outer level but the tuple flattener still routes
inner struct-arrays through `array_convt` — so the assert can still
fire on struct-element nested arrays.

## Mathsat — BV + native array + native FP, no quantifiers

`src/solvers/mathsat/mathsat_conv.{cpp,h}`. Populates `array_api`
+ `fp_api`. No quantifier support (MathSAT supports some but
ESBMC's backend doesn't wire it up).

Often useful as a differential-solver sanity check against cvc5/z3.

## Yices — native tuple + native array

`src/solvers/yices/yices_conv.{cpp,h}`. Populates `tuple_api`
+ `array_api` but **not** `fp_api` — Yices doesn't support FP
natively, so FP falls back to bitblast.

The native tuple support here contrasts with CVC5/Bitwuzla — means
tuple-array combinations on Yices avoid the `array_convt` tuple
routing. But Yices is less commonly maintained in the ESBMC tree.

## CVC4 — legacy

`src/solvers/cvc4/cvc_conv.{cpp,h}`. Largely superseded by cvc5.
Still in-tree for legacy compatibility; populated partially.

## SMTLIB — text dump + external solver

`src/solvers/smtlib/smtlib_conv.{cpp,h}`. Doesn't link any solver
library — instead emits SMTLIB2 text and pipes it to an external
process (specified via CLI). Populates nothing natively; all
abstraction goes through flatteners.

Used when:
- Debugging a formula (the dumped `.smt2` file can be fed to any
  other solver).
- A solver not built-in is desired.

## Per-backend push/pop discipline

All backends **must** override `push_ctx`/`pop_ctx` to delegate to
their native push/pop **and** chain to `smt_convt::push_ctx/pop_ctx`
(which handles tuple/array/fp + live_asts + caches).

Failure mode: if the backend's native push/pop is skipped, the
solver accumulates all assertions across pushes — formally correct
but slow. If `smt_convt`'s is skipped, the abstract caches leak
memory.

Pattern:
```cpp
void X_convt::push_ctx() {
  smt_convt::push_ctx();          // abstract first
  native_push();                   // then solver
}
void X_convt::pop_ctx() {
  <optional: clean backend-specific symtab>
  native_pop();                    // solver first
  smt_convt::pop_ctx();            // abstract last
}
```

The order (push abstract-first, pop abstract-last) maintains the
invariant that the abstract cache never points at a freed backend
term.

## Solver selection logic

`solve.cpp:83-137`. Priority:

1. `--<solver>` flag explicitly set → that solver.
2. `--default-solver <name>` option → `<name>`.
3. `--ir` mode → prefer Z3 (integer/real arithmetic requires it).
4. Default: Boolector if built; else walk the `all_solvers` list
   (z3 / minisat / boolector / cvc4 / cvc5 / mathsat / yices /
   bitwuzla) and pick the first built-in.

For Solidity, the `CLAUDE.md` guidance is to explicitly request
`--bitwuzla` (auto-selected when built) or `--cvc5`. Z3 is
acceptable but slow on 256-bit BV.

## Backend-specific quirks worth noting

- **Z3**: watch for `solve-eqs` tactic aggressively eliminating
  pinned symbols. The `--ir-ieee` enclosures use bidirectional
  inequalities specifically to survive this.
- **CVC5**: tuple flattener routing means KNOWNBUG #3 patterns
  still fail even under CVC5 when the array element is a struct.
  Fix B in [array-conv.md](array-conv.md) §Fix landscape is the
  right workaround for the Solidity-specific case; Fix A for a
  general repair.
- **Bitwuzla**: int-encoding mode forbidden — rejects at
  construction. If a test sets both `--bitwuzla` and `--ir`, the
  abort is loud but fast.
- **Boolector**: triggers the most `array_convt` limitations. If a
  test fails only on Boolector, check whether a nested array is
  involved.

## When to use which

| Use case | Recommended |
|---|---|
| Default C | Z3 (most feature-complete) or Boolector (fastest QF_BV) |
| Solidity 256-bit BV | Bitwuzla > CVC5 > Z3 |
| Quantified formulas | Z3 or CVC5 |
| `--ir` real-arith mode | Z3 (only one with native real support that ESBMC uses) |
| FP-heavy (float32/64) | Z3 / CVC5 / Bitwuzla (all have native FP) |
| Portability (text-only) | SMTLIB with external |
| Differential testing | Run multiple; compare verdicts |

## Common pitfalls

- **Verdict differs between backends** — usually either (a) a
  backend-specific bug (file against that backend, easy to isolate
  via `--<solver>` flag) or (b) the universal flatteners diverging
  from a native path. Check with `--array-flattener` /
  `--tuple-node-flattener` forcing.
- **"Backend crashed mid-query"** — almost always a push/pop
  ordering bug. Check the backend's `pop_ctx` carefully.
- **"Bitwuzla refused to start"** — usually `--ir` was enabled by
  a default config. Either remove `--ir` or switch to z3/cvc5.
- **"Model readback is wrong"** — `get_bool` / `get_bv` /
  `get_fpbv` are backend-specific; each must call the native model
  API. If a new backend returns nonsense, re-check these.
