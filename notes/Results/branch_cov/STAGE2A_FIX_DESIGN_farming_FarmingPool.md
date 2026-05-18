# Stage B — FarmingPool SIGABRT fix design (diagnosis; no code)

Companion to STAGE1_SIGABRT_farming_FarmingPool.md and the ACTIVE plan
section. FarmingPool-specific (kept separate from the aqua-related
STAGE2A_FIX_DESIGN.md to avoid polluting it).

## Mechanism (file:line, Read-verified this session)

1inch `FarmingLib` packs a memory `Info` pointer into a `bytes32`
callback context via inline assembly:
`_contextToInfo(bytes32 ctx) returns(Info memory self) { assembly { self := ctx } }`
and the symmetric `_infoToContext` (`ctx := self`).

- `bytes32` lowers to `struct BytesStatic`
  (`src/solidity-frontend/solidity_convert_type.cpp:1061`).
- The Yul body hits the `dst := src` (rhs = `YulIdentifier`) fast-path
  at `src/solidity-frontend/solidity_convert_stmt.cpp:1862-1896`. LHS
  `self` resolves to `tag-Info` (struct), RHS `ctx` to the
  `BytesStatic` struct symbol.
- Line **1890-1891**: `rhs = src_expr; solidity_gen_typecast(ns, rhs,
  lhs.type())`. `solidity_gen_typecast` is only
  `c_typecastt::implicit_typecast` (`src/solidity-frontend/typecast.cpp:15-19`),
  which has **no rule to bridge two distinct struct tags** → the cast
  is a silent no-op; a type-mismatched `code_assignt(self:struct Info,
  ctx:struct BytesStatic)` is emitted (symmetric for `ctx := self`).
- Component-wise struct copy during migration
  (`src/util/migrate.cpp:200-224`) calls
  `struct_union_data::get_component_number`
  (`src/irep2/irep2_type.cpp:288`), looking the LHS struct's
  `getTotalSupply` member up against the RHS `BytesStatic` type →
  not found → `abort()` (the observed SIGABRT).

## Hypothesis adjudication

- **H1** (named return param / call binding of `_contextToInfo`
  carries `BytesStatic`): **rejected**. `get_var_decl_ref`
  derives the declared type from the AST `typeName`; the function's
  declared return type `Info memory` and the call-result binding
  `Info memory self = _contextToInfo(ctx)` both keep `tag-Info`. The
  mismatch is not introduced at the call boundary.
- **H2** (the YulAssignment re-types the `self` symbol in place):
  **rejected**. The symbol's `.type()` is never mutated;
  `make_yul_local` (the u256 hardcode path) is not reached for `self`
  / `ctx` — they are external references resolved via
  `get_var_decl_ref`, not Yul `let`-scoped locals.
- **Actual cause**: the mismatch is created *in place at the
  YulAssignment typecast* (line 1890-1891) because
  `implicit_typecast` cannot bridge `struct Info` ↔ `struct
  BytesStatic` and silently leaves the RHS at its own struct type.

Function-pointer struct members are modeled correctly
(`solidity_convert_type.cpp:68-93` → `void*` `#sol_func_ptr`,
registered in `member_names` via `migrate.cpp:200-224`); a
correctly-typed `Info` receiver resolves `getTotalSupply` normally.
The defect is solely the receiver type collapsing to `BytesStatic`.

## Fix locus + strategy

Single primary edit point:
`src/solidity-frontend/solidity_convert_stmt.cpp`, the `dst := src`
fast-path `else` at lines **1888-1892**. When both `lhs.type()` and
`src_expr.type()` resolve (through pointer/symbol indirection) to a
struct with **different identifying symbol tags** — an EVM
pointer/value reinterpret `implicit_typecast` cannot bridge — emit
`get_nondet_expr(lhs.type(), rhs)` (`solidity_convert_call.cpp:449`)
instead of the mismatched copy, plus one visible `[approx]`
`log_warning` mirroring the existing style at
`solidity_convert_stmt.cpp:1113-1122`. Compatible paths (same-tag
struct, scalar↔scalar, the existing `#sol_func_ptr` zero-init at
1886-1887) are untouched and stay silent.

Resolution idiom reused from `struct_type_has_component`
(`solidity_convert_builtin.cpp:585-602`): follow `pointer` →
`subtype`, follow `symbol` chain via `context.find_symbol(...)`,
require final `id()=="struct"`; the discriminator is the resolved
symbol identifier (guaranteed available; `struct_union_typet` exposes
no plain tag accessor). Implemented as a local lambda (no new public
method — reuse over abstraction).

Fallback (only if primary insufficient): same guard at the general
YulAssignment tail (lines 1899-1906, second `solidity_gen_typecast` at
1903).

## Soundness

Over-approximation: the destination becomes a fresh nondet of *its
own declared type*, so the dependent `self.getTotalSupply()` is a
call through a nondet `#sol_func_ptr` member that flows into the
existing indirect-call nondet fallback. No reachable branch is
under-reported (havoc only adds reachable behaviour; it never prunes
a path). Value-imprecise by construction (the reinterpreted bit
pattern is not modeled) — documented by the PASS/FAIL dual in Stage C.

## Forbidden files

`src/c2goto/library/solidity/solidity_blockchain.c`,
`src/c2goto/library/solidity/solidity_misc.c`,
`src/solidity-frontend/solidity_language.cpp` are NOT on the fix path
and are not touched by this design (they carry pre-existing unrelated
Modified state from before this session).
