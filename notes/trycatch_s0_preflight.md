# S0 preflight results — try/catch revert observation

Branch `feat/trycatch-revert-observation`. Binary `build/src/esbmc/esbmc`.
All checks empirical (commands in `scratchpad/exp*.sol`).

## §5.1 solc syntax — PASS
- `try address(c).call(...)` → solc **error**: "Try can only be used with
  external function calls and contract creation calls."
- `try c.f()` and `try new C()` → accepted.
- ⇒ low-level / delegatecall / staticcall in `try` cannot reach ESBMC. R8's
  low-level variant is a compile-time impossibility (payable `{value:}` external
  call still reaches us → R8 keeps the value-refund residual only).

## §5.2 GOTO ordering — PASS (riskiest item retired)
Dump of EXP1 `run` (current nondet lowering):
```
IF !nondet_bool THEN GOTO catch        // 3142
FUNCTION_CALL: f(this->c, x)           // 3143  <-- call INSIDE success arm
... return-value nondet bind, msg_sender swap ...
r = false                              // 3150  success body
GOTO end
catch: r = true                        // 3155
end:  ASSERT !r                        // 3156
```
**Before the `IF` there is nothing related to the external call** — the call and
its context setup are fully contained in the success arm. So for the common call
shape `get_expr(externalCall)` does **not** emit an escaping `front-block`;
hoisting the call between `clear` and `snapshot` is feasible. F1 implementation
will still capture `expr_frontBlockDecl`/`expr_backBlockDecl` around the
`get_expr` call defensively (value/tuple shapes), per the §2.2 HARD requirement.

## §5.3 core baselines (current binary)
| case | stub? | construct | current verdict | after-fix target |
|------|-------|-----------|-----------------|------------------|
| EXP1 | no | try, never-revert | FAILED (spurious) | n/a (no opt-in → stays nondet) |
| EXP1b | **yes** | try, never-revert | **FAILED (spurious)** | **SUCCESSFUL** (headline flip) |
| EXP6 | no | try A/B rule-holds | FAILED (spurious) | n/a |
| EXP2 | yes | `__ESBMC_reverted`, never-revert | SUCCESSFUL | unchanged |
| EXP3 | yes | `__ESBMC_reverted`, cond witness | FAILED+witness | unchanged |
| EXP4 | yes | `__ESBMC_reverted`, guarded | SUCCESSFUL | unchanged |
| EXP5 | yes | `__ESBMC_reverted`, A/B | FAILED+witness | unchanged |
**Key:** EXP1b (stub present, try) is STILL spuriously FAILED → adding the stub
alone does nothing; the §2.2 lowering patch is the sole flip trigger. EXP1b is
the headline KNOWNBUG→CORE test.

## §5.4 flag symbols — PASS (all linked under opt-in)
- global flag id: **`c:@_ESBMC_sol_reverted_flag`** (C global scheme `c:@<name>`,
  same as `c:@msg_sender`). Read+write via `symbol_expr(*context.find_symbol(...))`
  (pattern: `solidity_convert_contract.cpp:487`).
- clear: `c:@F@_ESBMC_sol_clear_revert` (`flag=0`); mark:
  `c:@F@_ESBMC_sol_mark_revert` (`flag=1`); read: `c:@F@__ESBMC_reverted`
  (`return flag`).
- Under opt-in (stub present): clear injected at external entry, mark at revert
  site, all confirmed in GOTO. F1 hard-errors if any are absent.
- save/restore uses **direct global read+write** (mark/clear only set 0/1, so
  restoring an arbitrary saved value needs the direct assign).

## §5.5 stub elision — characterized
`check_intrinsic_function` (`solidity_convert_util.cpp:309-318`) governs body
materialization only; `populate_function_signature` (`solidity_convert.cpp:1468`)
and `get_struct_class_method` (`solidity_convert_decl.cpp:1406`) also register
the stub. F2 disposition (filter all three, or narrow the test claim) decided in
S1.

## §5.6 Panic routing (R7) — confirmed hazard
`try c.f()` where `f` does `assert(false)`:
```
State 12 file exp_panic.sol line 2 function f
Violated property: assertion 0      <-- the CALLEE's assert, not the harness
VERIFICATION FAILED
```
The panic is NOT caught; ESBMC fails on the callee property. Confirms R7: a
panicking callee pollutes the differential verdict. R7 test must grep the callee
line, not a bare `^VERIFICATION FAILED$`.

## §5.7 gate fragility — noted
Existing gate is `dump().find("__ESBMC_reverted")`; a source string literal could
trip it. Out of scope; scaffold must not embed that substring in a string.

## Verdict
All preflight green or characterized. **F1 is feasible** (riskiest GOTO-ordering
item retired). Ready for S1 (patch §2.2 lowering + §2.5b stub disposition).
