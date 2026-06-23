# Plan: make ESBMC-Solidity `try/catch` a sound revert observer

> Revised after **two** Codex adversarial-review rounds (2026-06-23).
> Round 1 found 4 Critical holes (unit-wide gate too broad, ctor call-chain,
> stale global flag, callee self-clobbering) → architecture pivot: **do not
> touch the feature gate**; opt in via the existing `__ESBMC_reverted` stub
> baked into the fixed template scaffold; fix only the `TryStatement` lowering;
> pre-call flag clear.
> Round 2 found 3 new Critical holes against the revision: (1) **nested
> external-call contamination** of the single global flag (a false positive that
> breaks "holds on P") → fixed with **save/restore** around each `try` (§2.2);
> (2) the `__ESBMC_reverted` **stub is not elided** → add it to
> `check_intrinsic_function` (§2.5b); (3) **Panic-style reverts not observable**
> → residual R7. Plus payable-value-call (R8), reentrant-direct-revert (R-RE).
> Residuals that need full revert propagation are pinned KNOWNBUG, never hidden;
> KNOWNBUG tests assert the *exact* current/intended wrong verdict.
> Round 3 **confirmed the save/restore design is semantically sound** in all
> interleavings (traces a–e) and that callee rollback must stay mark-only. It
> demoted F1/F2/F3 to implementation NEEDS-CHANGE: (1) call front/back block
> ordering is now a **HARD requirement** (§2.2), not preflight — if the call's
> wrapper executes before `save/clear`, `clear` wipes the mark → false success;
> (2) F2's "no symbol" claim **narrowed** — `check_intrinsic_function` only
> elides the body; signature/method registration are separate surfaces (§2.5b);
> (3) **R-RE broadened** to any uncaught nested direct-call revert (incl.
> `try new C()`); (4) **R7 escalated** — a panicking callee fails verification at
> its *own* property, polluting the differential verdict → diagnostic KNOWNBUG +
> usability warning. The riskiest preflight is the GOTO front/back ordering
> (§5.2): if it cannot be made clean, F1 collapses for ordinary high-level calls.

## 0. Goal & success criterion

LLM-generated differential/mutant-killing tests use **standard Solidity
`try/catch`** to capture whether a call reverted:

```solidity
bool r;
try c.f(a) returns (...) { r = false; } catch { r = true; }
assert(<general rule over params, values read from c via reads, and r>);
```

Today ESBMC lowers `try/catch` to a *free nondet branch*, so `r` is
uncorrelated with the real revert outcome → spurious `VERIFICATION FAILED` on
the correct contract `P` (see `Investigate.md`, EXP1 & EXP6).

**Success criterion:** the catch arm is entered **iff the call actually
reverted within the captured scope** (callee's own public/external body +
modifiers + internal helpers, per `revert-observation.md` §7), so the template
holds on `P` and is violated by a behavioural mutant `M`. Concretely:

- EXP1 (callee never reverts, `assert(!r)`): `FAILED` → **`SUCCESSFUL`**.
- EXP6 (A/B rule holds on `P`, `assert(rb)`): `FAILED` → **`SUCCESSFUL`**.
- EXP2–EXP5 (`__ESBMC_reverted` baselines): unchanged.
- `try_catch_1..4` (no opt-in stub): **verdict + GOTO unchanged** (legacy nondet
  lowering retained — see §2.1).
- `try_catch_*_rollback_pass` (T2.2): unchanged.
- Any unit not opting in: GOTO byte-for-byte unchanged (k-induction stability).

## 1. Root cause (verified against code)

`src/solidity-frontend/solidity_convert_stmt.cpp`, `case TryStatement`
(~930–1118):

```
if (nondet_bool()) { <externalCall>; <success_body> }   // nondet_bool_expr
else               { <catch_body(s)> }
```

Two defects: (1) the top-level branch is `nondet_bool_expr` (lines 1104, 1114),
not the real revert outcome; (2) the external call sits *inside* the success arm
(line 966), so it only "executes" on the nondet-true path — real EVM always
performs the call.

The sound revert flag already exists (`docs/claude/solidity/revert-observation.md`):
global `_ESBMC_sol_reverted_flag` (`solidity_misc.c:201`), cleared at every
public/external entry (`solidity_convert_modifier.cpp:446`) and set at every
captured revert site (`solidity_convert_modifier.cpp:843`), both gated on
`uses_revert_observation`.

## 2. Design (robust architecture, post-review)

### 2.1 Do NOT extend the feature gate (rejects draft §2.1)

**Rejected:** turning `uses_revert_observation` on for any unit containing a
`TryStatement`. Codex Critical #1/#2: that flips revert lowering for the *whole
compilation unit* — unrelated internal/private helper reverts switch from
legacy path-prune to `mark + return` (`solidity_convert_modifier.cpp:799-802`),
and a constructor-called internal helper would `return` instead of aborting
construction, making an impossible constructed object reachable. An unrelated
function's verdict could change just because a sibling has a `try`. Also the
`dump().find("\"TryStatement\"")` scan can false-positive on an identifier or
string literal named `TryStatement` (Critical/High #5).

**Adopted:** keep the gate exactly as-is (`uses_revert_observation` ⇔ source
mentions `__ESBMC_reverted`). Opt in by **baking the stub into the fixed
template scaffold**, not the LLM-authored body:

```solidity
contract InvMutTest {
  {{NAME}} c;
  constructor({{NAME}} _c) { c = _c; }
  function __ESBMC_reverted() internal returns (bool) {}   // FIXED scaffold line
  function run(/* params */) public {
    /* LLM writes natural Solidity here: require(...); try c.f(a) {...} catch {...}; assert(...); */
  }
}
```

The LLM still writes only standard `try/catch` (it never sees the intrinsic).
The scaffold author adds one fixed line. Result: opt-in units get the full,
already-documented observation semantics; every other unit (including
`try_catch_1..4`) is byte-for-byte unchanged. No new gate, no blast radius.

### 2.2 Fix the `TryStatement` lowering, conditioned on the existing gate

The flag is observed with a **save / clear / call / snapshot / restore**
discipline so that the single global behaves like a *per-`try` scoped*
observation (this is the fix for round-2 Critical #1, nested-call
contamination):

```
if (uses_revert_observation) {
    bool __try_saved   = _ESBMC_sol_reverted_flag;    // (A) SAVE caller's prior status
    _ESBMC_sol_clear_revert();                        // (B) clean baseline for THIS call
    <externalCall>;                                   // (C) ALWAYS executed (fixes defect 2)
    bool __try_reverted = _ESBMC_sol_reverted_flag;   // (D) snapshot IMMEDIATELY into a fresh temp
    _ESBMC_sol_reverted_flag = __try_saved;           // (E) RESTORE caller's status
    if (!__try_reverted) {                             // (F) success: call did NOT revert
        <success return params = nondet>;
        <success_body>;
    } else {                                           // catch: call DID revert
        <catch params = nondet>;
        <catch_body>;
    }
} else {
    <legacy nondet lowering, unchanged>               // no opt-in → status quo (no regression)
}
```

- **(A)+(E) save & restore** make the observation stack-disciplined. Worst case
  from round-2 Critical #1: outer `try c.f()` where `c.f()` re-enters `run()`
  and an *inner* `try d.alwaysReverts()` marks the flag, the inner catch handles
  it, and `c.f()` returns normally. Without restore the inner mark leaks and the
  **outer catch is spuriously entered though `c.f()` did not revert (false
  positive — breaks "holds on P")**. With (A)+(E): the inner `try` restores the
  flag to its own saved value (`0`, since the outer cleared), so the outer
  snapshot reads `0` → success. Correct. This also gives flag *hygiene*: a `try`
  never leaks its observation into a later `__ESBMC_reverted()` read in the same
  body.
- **(B) pre-call clear** guarantees a clean `false` baseline even when the call
  is an opaque/unbound/library/ctor path that never clears — so a prior call's
  `true` flag cannot leak in (round-1 Critical #3). Double-clear with the
  callee entry is idempotent.
- **(C) hoist the call** above the branch so it always executes.
- **(D) snapshot into a fresh temp immediately**, before either body runs and
  before the restore, because a body may itself make another external call that
  rewrites the global. Temp decl/assign + flag helper calls tagged `skipped`.
- **(F) branch on `!__try_reverted`** (real outcome), replacing
  `nondet_bool_expr`. Read/write the global via `symbol_expr` of
  `_ESBMC_sol_reverted_flag` (no call-in-condition).
- **No silent fallback (round-1 Medium #12; round-3 F5 confirmed SOUND):** if
  `uses_revert_observation` is set but the flag symbol — or the
  `_ESBMC_sol_clear_revert` helper used for the pre-call clear — is absent,
  **hard-error** (`log_error` + return true). Never drop to the unsound nondet
  branch.
- **Strict gate hygiene (round-2 High #6):** all flag-symbol lookups and helper
  construction live *inside* the `if (uses_revert_observation)` branch. A
  no-opt-in unit must not even touch the flag symbol table, so its artifact is
  provably unchanged.

> **HARD requirement — call front/back block capture (round-3 Critical, F1).**
> `get_expr(stmt["externalCall"], ...)` can push the actual high-level call
> wrapper into `expr_frontBlockDecl` / `expr_backBlockDecl`, which `get_block`
> flushes *before* the returned statement and *after* it
> (`solidity_convert_stmt.cpp:146`, `solidity_convert_call.cpp:946`). If S1
> naively emits `save; clear; call_expr; snapshot; restore`, the call's
> front-block can execute **before** `save/clear` — then `clear` wipes the mark
> and the `try` falsely takes success. The implementation MUST capture the
> complete `call-front; call; call-back` sequence and place it *between* `clear`
> and `snapshot`, all inside the observation block. Concretely: snapshot and
> swap `expr_frontBlockDecl`/`expr_backBlockDecl` around the `get_expr` call (or
> build the block manually) so the emission order is provably
> `save; clear; <call-front>; <call>; <call-back>; snapshot; restore; if`. This
> is no longer "preflight if not" — it is a precondition of F1's soundness and
> is verified in S1 via `--goto-functions-only` before any other test.

### 2.2b Residual after save/restore (reentrancy via a *direct* call)

Save/restore fully fixes the nested-`try` case. It does **not** fix a callee
that re-enters and reverts via a **non-`try` direct** call in the subtree: that
revert marks the flag with no restoring `try` frame, so it propagates to the
outer snapshot. Directionally this is defensible (the outer call's subtree *did*
revert uncaught), but it does not match EVM's per-frame rollback. Pinned as R-RE
(KNOWNBUG), distinct from R4.

### 2.3 Multiple catch clauses

Outer decision (revert or not) uses the **real flag**; *within* the catch,
clause selection (`Error(string)` vs `Panic(uint)` vs low-level) stays
`nondet_bool` because ESBMC cannot distinguish the revert reason. Document as
over-approximation: tests must assert only **handler-insensitive** properties
(Codex Medium). A property that depends on *which* catch clause ran is out of
scope and can false-positive.

### 2.4 Explicit non-goals (Codex High #7)

- **Success/catch return & catch params stay nondet** (cross-contract return
  resolution is out of AST-frontend scope). `try c.f() returns (uint y) { assert(y==k); }`
  reasons over a nondet `y` and can give a wrong verdict *even with revert
  selection fixed*. The fix is about **revert observation only**. The template
  comment and docs must state: rules may use the revert flag and values read
  from `c` via ordinary calls, **not** the `returns(...)` bindings. A KNOWNBUG
  test pins this.
- **Low-level calls / delegatecall / staticcall (Codex High #8):** Solidity
  *rejects* `try` on these (try is only valid on external function calls and
  `new C()`). Confirm with `solc` during S0; if confirmed, the hazard cannot
  reach ESBMC and is documented as a compile-time impossibility. If solc ever
  accepts a shape that lowers to a bool/tuple "success" not mapped to the flag,
  pin KNOWNBUG.

### 2.5b Elide the `__ESBMC_reverted` stub (round-2 Critical #2)

`check_intrinsic_function()` (`src/solidity-frontend/solidity_convert_util.cpp:309-318`)
elides `__ESBMC_assume` / `__VERIFIER_assume` / `__ESBMC_assert` /
`__VERIFIER_assert` stubs but **not** `__ESBMC_reverted` — so the scaffold's
empty `function __ESBMC_reverted() internal returns (bool) {}` is currently
converted as a real Solidity function with an empty body. Add `__ESBMC_reverted`
to that elision list so the dead body is not materialized (direct calls are
already hijacked to the C intrinsic by `get_sol_builtin_ref`,
`solidity_convert_ref.cpp:486-500`).

**Narrowed claim (round-3 High, F2 INSUFFICIENT).** `check_intrinsic_function`
governs **body materialization** in `get_function_definition`
(`solidity_convert_modifier.cpp:30`), but it is **not** the only registration
surface: `populate_function_signature` (`solidity_convert.cpp:1468`) and
contract-method population via `get_struct_class_method`
(`solidity_convert_decl.cpp:1406`) add the `FunctionDefinition` *without*
intrinsic filtering. So eliding the body does **not** guarantee zero symbols for
the stub. Two acceptable dispositions, decided in S0:
- (preferred) also filter `__ESBMC_reverted` at those two sites so no signature/
  method symbol is created; or
- (fallback) accept a dead, never-called signature symbol and **narrow the test
  claim** to "the stub adds no *body* and does not change behaviour" rather than
  "no function symbol". The direct-call hijack makes the dead signature inert.
Test must match whichever disposition is chosen (assert behaviour-unchanged; and
symbol-count only if full filtering is implemented).

### 2.5 What stays the same

State rollback in the catch arm is still provided by B1 (`*this` + per-frame
global-store snapshot) + SSA branching; catch body remains in the `else` arm, so
the T2.2 rollback tests keep passing.

## 3. Residual unsoundness — documented + KNOWNBUG, never hidden

Each follows from the single-global-flag + non-propagation model. Pinned as
KNOWNBUG so the boundary is visible.

- **R1** revert inside a **library** function called in `try` → not captured.
- **R2** revert inside a **constructor** (`try new C(bad)`) → not captured.
- **R3** `transfer`/`send` insufficient-balance revert in the callee → not captured.
- **R4 (Codex Critical #4)** callee self-clobbering: callee reverts in an
  internal helper (marks), then makes a *further external call* before returning
  (that call's entry clears the flag) → caller's `try` reads `false`, takes
  success, though real EVM reverts. **False negative.** Cannot be closed without
  full revert propagation. KNOWNBUG.
- **R5 (Codex Critical #2)** constructor-internal-helper revert under the
  opt-in: an internal helper called from a constructor lowers to `mark + return`
  (observable scope) instead of aborting construction → an impossible
  constructed object may be reachable. KNOWNBUG; document that opt-in units must
  not rely on constructor-helper reverts aborting construction.
- **R6** revert-reason-dependent catch clause selection (§2.3) → over-approx.
- **R7 (round-2 Critical #3, escalated round-3 High) Panic-causing callees are
  not observable — and worse, they POLLUTE the differential verdict.** EVM
  `Panic(...)` — `assert(false)`, arithmetic overflow, division by zero, array
  out-of-bounds — is NOT routed through `build_revert_rollback_block`. `assert`
  is lowered to an ESBMC verification property
  (`solidity_convert_ref.cpp:392-420`), so a callee that panics makes ESBMC
  report the **callee's own assertion/overflow violation** as `VERIFICATION
  FAILED` — not a caught panic, and not the harness rule. **Usability hazard:**
  in the differential template the callee *is* `P`/`M`; if `P` contains a
  reachable `assert` or can overflow, `try`-ing it yields `FAILED` for the wrong
  reason, polluting the mutant-kill signal. Disposition:
  (i) document prominently that the LLM template's callees must avoid
  Panic-causing operations on covered paths (or run with `--no-standard-checks`
  to suppress overflow/OOB, leaving only explicit `assert`);
  (ii) the R7 test is a **diagnostic KNOWNBUG**: it must `grep` the
  counterexample/source line to prove the failure is the *callee panic property*,
  not the harness assertion — a bare `^VERIFICATION FAILED$` is not acceptable
  (it could pass for the wrong reason). Not counted as an ordinary "try caught
  wrong" residual.
- **R8 (round-2 High #5) payable `try c.f{value:v}()`.** A high-level call with
  `{value:}` debits/credits around the call; on a `mark + return` (non-real
  propagation) revert the caller-side value effects may not be refunded as EVM
  would. R3 covers `.transfer`/`.send` only. KNOWNBUG.
- **R-RE (§2.2b, broadened round-3 High) uncaught nested direct-call revert in
  the call subtree.** Not limited to reentrancy: *any* direct (non-`try`)
  high-level external call inside the observed callee's subtree that reverts and
  returns under the non-propagating model marks the global and is seen by the
  outer `try` — even when the immediate callee "succeeds" in the model. Includes
  `try new C()` where `C`'s constructor succeeds but calls something that marks
  the flag. KNOWNBUG; tests cover the non-reentrant, reentrant, and constructor
  shapes.

## 4. Adversarial regression suite

Under `regression/esbmc-solidity/`. Default flags: `--contract InvMutTest
--bound --no-standard-checks --unwind 4 --no-unwinding-assertions --cvc5`
(scaffold includes the `__ESBMC_reverted` stub). PASS/FAIL duals throughout.

### Core soundness (headline fix)
1. `trycatch_obs_never_revert_pass` — never reverts; `assert(!r)` → SUCCESSFUL (EXP1 regression).
2. `trycatch_obs_never_revert_fail` — same; `assert(r)` → FAILED.
3. `trycatch_obs_always_revert_pass` — always reverts; `assert(r)` → SUCCESSFUL.
4. `trycatch_obs_always_revert_fail` — same; `assert(!r)` → FAILED.
5. `trycatch_obs_conditional_witness_fail` — reverts iff `x>=100`; `assert(!r)` → FAILED, witness `x>=100`.
6. `trycatch_obs_conditional_guarded_pass` — `require(x<100)` then `assert(!r)` → SUCCESSFUL.

### Differential / mutant-killing (the use case)
7. `trycatch_diff_AB_rule_pass` — `require(!ra); assert(rb)` → SUCCESSFUL on correct pair (EXP6 regression).
8. `trycatch_diff_AB_mutant_fail` — mutant guard flipped; witness `x∈[50,100)` → FAILED.

### Capture-scope breadth
9. `trycatch_obs_modifier_revert_pass` — revert in `onlyOwner` modifier; catch entered → SUCCESSFUL.
10. `trycatch_obs_internal_helper_revert_pass` — revert in private helper; catch entered → SUCCESSFUL.
11. `trycatch_obs_custom_error_revert_pass` — `revert MyError()`; catch entered → SUCCESSFUL.

### Stale-flag / clobbering / nesting / ordering (round-1 #3,#4 + round-2 #1)
12. `trycatch_obs_stale_flag_pass` — **(r1 Critical #3)** call `a.alwaysReverts();`
    (sets flag) THEN `try b.neverReverts() {...} catch {...}`; the pre-call clear
    (B) must give the 2nd try `r==false` → assert `!r` SUCCESSFUL.
13. `trycatch_obs_sequential_two_calls_pass` — two back-to-back try blocks; each
    observes its own call (save/restore + temp snapshot isolate them).
14. `trycatch_obs_call_in_success_body_pass` — success body makes another
    external call (rewrites flag *after* the temp read) → outer `r` still correct.
15. `trycatch_obs_nested_reentrant_try_pass` — **(r2 Critical #1, the headline
    save/restore fix)** outer `try c.f()` where `c.f()` re-enters `run()` and an
    inner `try d.alwaysReverts() { } catch { }` handles d's revert; `c.f()`
    returns normally → outer must take **success** (`assert(!r_outer)` →
    SUCCESSFUL). Without (A)+(E) this is a spurious catch (false positive).
16. `trycatch_obs_callee_clobber_knownbug` — **(r1 Critical #4 / R4)** callee
    reverts in a helper then makes a further external call; false-negative
    KNOWNBUG. Must assert the exact current (unsound) verdict.
17. `trycatch_obs_reentrant_direct_revert_knownbug` — **(R-RE)** callee re-enters
    and reverts via a *direct* (non-try) call; documents flag propagation to the
    outer snapshot. KNOWNBUG, exact verdict asserted.

### Gate non-interference (round-1 Critical #1, round-2 High #6) — main regression surface
18. `trycatch_gate_noninterference_pass` — a unit that **contains a `try`** AND a
    separate non-try path calling a private helper with `require(false); assert(false);`.
    The `assert(false)` must **stay unreachable** (helper revert still prunes).
    Run with and without opt-in stub.
19. `trycatch_no_optin_unchanged` — `try_catch_1..4`-shaped unit **without** the
    stub: verdict identical AND a **full artifact comparison** (symbol table +
    function dump, not only GOTO body text — round-2 High #6) byte-identical to
    pre-patch. Proves no flag-symbol lookup before the gated branch.
20. `trycatch_identifier_named_TryStatement_unchanged` — no actual try but a
    function/var/string literal named `TryStatement`: artifact unchanged.

### Constructor propagation (round-1 Critical #2 / R5)
21. `trycatch_ctor_helper_revert_knownbug` — `constructor(){ init(); x=1; }
    function init() internal { require(false); }` in an opt-in unit: documents
    construction is **not** aborted (impossible object reachable). KNOWNBUG,
    exact verdict asserted.

### Multi-transaction global-flag leak (round-1 Medium)
22. `trycatch_obs_multitx_pass` — `--solidity-max-tx 2`: tx1 reverts, tx2 runs a
    try; save/restore + pre-call clear must isolate it from tx1's flag.

### Callee-entry-does-not-clear surfaces (round-1 Medium)
23. `trycatch_obs_view_pure_pass` — callee `external view` / `public pure`.
24. `trycatch_obs_fallback_receive_pass` — revert in `fallback`/`receive`.

### try in modifier / constructor / loop (round-2 Medium #8)
25. `trycatch_in_loop_pass` — `try` inside a `for` loop body (`--unwind` bounded);
    each iteration's observation isolated by save/restore.
26. `trycatch_in_modifier_pass` / `trycatch_in_constructor_knownbug` — `try`
    placed in a modifier body and in a constructor; verify
    `current_function_revert_observable` classification does not misbehave
    (constructor case likely KNOWNBUG — exact verdict asserted).

### Multiple catch clauses (round-1 Medium, R6)
27. `trycatch_obs_multi_clause_pass` — `catch Error(string) / catch`; outer real,
    inner nondet; assert a **handler-insensitive** property → SUCCESSFUL.
28. `trycatch_obs_multi_clause_reverted_pass` — always-revert callee → some catch
    clause runs (not success) → SUCCESSFUL.

### Panic / value-call limitations (round-2 Critical #3 / R7, High #5 / R8)
29. `trycatch_panic_callee_knownbug` — callee `assert(false)` / array-OOB /
    division-by-zero. **Diagnostic KNOWNBUG (round-3):** `test.desc` must match
    the counterexample's *callee panic* source line (not a bare
    `^VERIFICATION FAILED$`), proving the failure is the callee property, not the
    harness rule.
30. `trycatch_value_call_knownbug` — `try c.f{value:v}()` payable; value-effect
    refund mismatch on revert. KNOWNBUG.

### Return-value / shape limitations (round-1 High #7)
31. `trycatch_returns_nondet_knownbug` — `try c.f() returns (uint y) { assert(y==k); }`:
    `y` is nondet → KNOWNBUG / intended over-approx, exact verdict.
32. `trycatch_obs_returns_destructure_pass` — tuple `returns (uint a, uint b)`;
    tautology over `a,b` → SUCCESSFUL (no crash on nondet tuple binding).

### State rollback in catch (no T2.2 regression)
33. confirm `try_catch_internal_revert_rollback_pass`,
    `try_catch_mapping_mutation_rollback_pass`,
    `try_catch_transfer_failed_rollback_pass`,
    `try_catch_no_mutation_clean_catch_pass` unchanged.

### Residual KNOWNBUGs
34. `trycatch_lib_revert_knownbug` (R1), `trycatch_transfer_revert_knownbug` (R3).
    Each asserts the exact current/intended wrong verdict (round-2 High #4):
    distinguish "success arm taken" from "path pruned / vacuous", whichever the
    model actually produces — recorded empirically in S0.

### Round-3 added positive guards against over-eager observation (false-positive-on-P)
37. `trycatch_obs_nested_both_revert_pass` — F1 trace (c): inner call AND outer
    call both revert; each `try` must see its own (`r_inner==true && r_outer==true`)
    → SUCCESSFUL. Guards the save/restore against under- or over-restoring.
38. `trycatch_obs_nondet_bound_target_pass` — under `--bound`, the call target is
    selected nondet across two implementations, one reverting and one not; assert
    a property true for **both** dispatch outcomes (handler/branch over `r`) →
    SUCCESSFUL. Guards that the flag reflects the dispatched callee, not a stale
    sibling-iteration value.
39. `trycatch_obs_new_C_success_pass` — `try new C() {...} catch {...}` where the
    constructor succeeds and marks nothing; `assert(!r)` → SUCCESSFUL. Guards the
    `new`-call shape against spurious catch (paired with R-RE's constructor-marks
    KNOWNBUG).

### Cross-mode
35. `trycatch_obs_incremental_bmc_pass` — case 1 under `--incremental-bmc`.
36. `trycatch_obs_no_bound_*` — case 1 without `--bound`. Decide empirically in
    S0: documented required-flag behaviour or KNOWNBUG.

## 5. Preflight checks BEFORE writing code (both review verdicts)

1. **solc shape confirmation:** verify `try` is rejected by solc on low-level
   call / delegatecall / staticcall, and accepted only on external calls + `new`.
2. **GOTO ordering proof — RISKIEST ITEM (round-3 Critical).** Dump
   `--goto-functions-only` for a minimal opt-in try and confirm emission order is
   `save; clear; call-front-block; call; call-back-block; snapshot; restore;
   if(!flag)`. `get_expr` for a call emits front/back blocks
   (`expr_frontBlockDecl`/`expr_backBlockDecl`); the call's front-block must land
   *after* `clear`, and the snapshot *after* the call's back-block. If this
   ordering cannot be produced cleanly (capture/swap the block buffers around the
   `get_expr` call, or build the call statement manually), **F1 collapses for
   ordinary high-level calls** and the whole approach must be reconsidered before
   writing the rest of S1. Verify this FIRST.
3. **Reproduce all 6 core cases as KNOWNBUG against the current binary** AND
   record the exact current verdict of every residual case (R1–R8, R-RE) —
   KNOWNBUG tests must assert the real current behaviour ("success arm" vs
   "pruned/vacuous" vs "callee assertion failure"), not just a label
   (round-2 High #4).
4. **Confirm the flag symbol id** (`_ESBMC_sol_reverted_flag`) and that it is
   linked whenever `uses_revert_observation`; confirm the read AND write
   (restore) forms lower correctly in goto.
5. **Confirm the stub-elision site** (`solidity_convert_util.cpp:309-318`,
   `check_intrinsic_function`) and that adding `__ESBMC_reverted` there removes
   the empty stub function without breaking the direct-call hijack
   (`solidity_convert_ref.cpp:486-500`).
6. **Verify Panic routing** (round-2 Critical #3): confirm `assert` / overflow /
   div-zero / array-OOB inside a callee do NOT pass through
   `build_revert_rollback_block`, so the flag is not marked — establishes R7's
   exact behaviour empirically.
7. **Note the pre-existing gate fragility** (round-2 High #7): the existing
   `dump().find("__ESBMC_reverted")` can be tripped by a string literal. Out of
   scope to fix here, but the scaffold must not embed that substring in a
   user-facing string; document it.

## 6. Execution order (incremental, test-after-each)

- **S0** Preflight §5 (all 7). **DONE 2026-06-23** — results in
  `notes/trycatch_s0_preflight.md`. All green/characterized; F1 feasible
  (GOTO-ordering riskiest item retired). Flag id `c:@_ESBMC_sol_reverted_flag`.
  EXP1b (stub+try) confirmed still-spurious-FAILED = headline flip trigger.
  R7 Panic hazard confirmed (callee assert reported, must grep callee line).
  Baseline KNOWNBUG test dirs to be materialized alongside S1.
- **S1 — DONE 2026-06-23.** Patched `TryStatement` lowering
  (`solidity_convert_stmt.cpp`): two-lambda refactor (`build_success_arm`,
  `build_catch_expr`); legacy nondet path kept byte-for-byte; opt-in path does
  save/clear/call(+front/back drain)/snapshot/restore/`if(!reverted)`; hard-error
  if flag symbol missing. Plus §2.5b stub elision
  (`check_intrinsic_function` += `__ESBMC_reverted`). Verified:
  - **GOTO ordering exactly as designed** (riskiest item retired): dump shows
    `save; clear; call; snapshot; restore; IF reverted GOTO catch` (correct
    polarity).
  - **2 headline flips:** EXP1b (never-revert) and EXP6b (A/B rule-holds-on-P)
    spurious `FAILED` → `SUCCESSFUL`.
  - **4 `__ESBMC_reverted` baselines unchanged** (EXP2/4 SUCCESSFUL, EXP3/5
    FAILED+witness); EXP1 (no stub, legacy) unchanged FAILED.
  - **No regression:** 93 try/catch tests (incl. 10 Solidity + rollback + tod)
    and 64 revert/reentry/observation tests all pass; cppcheck clean.
- **S2** Patch §2.3 multi-clause. Run 27–28.
- **S3** Regression: 33 (rollback), 19 (no-optin unchanged), 7–8 (differential),
  22 (multi-tx), 23–24 (entry-no-clear), 25–26 (modifier/ctor/loop), 32 (tuple),
  37–39 (round-3 over-eager-observation guards: both-revert, nondet-target, new C).
- **S4** Document residuals R1–R8, R5, R-RE; add KNOWNBUGs 16–17, 21, 29–31, 34;
  cross-mode 35–36.
- **S5** Update `Investigate.md`, `docs/claude/solidity/revert-observation.md`
  (new "try/catch is revert-correlated under opt-in" section + residuals),
  `approximation-ledger.md`, and the LLM prompt template (standard `try/catch`,
  scaffold carries the stub, comment on return-value limitation).
- Each step: `cppcheck` on changed Solidity-frontend files; `code-reviewer`
  agent on the diff; `esbmc-verifier` on representative cases. No full-regression
  loops (targeted subsets per testing-speed feedback).

## 7. Open questions deferred to implementation

- **Resolved by save/restore (§2.2):** flag hygiene (no leak into a later
  `__ESBMC_reverted()` read) and nested-`try` reentrancy are handled by (A)+(E).
  Still verify the *direct-call* reentrancy residual R-RE empirically (test 17).
- Is there any path where the hoisted call's back-block references the success
  return binding (so hoisting reorders a dependency)? Inspect tuple/return cases
  (preflight §5.2).
- Does `restore` (a write to the global flag) ever get simplified away by symex
  if the temp is the only subsequent reader? Confirm the write survives in goto
  (preflight §5.4).
- For `--bound` multi-tx, does the dispatcher's own machinery read the flag
  between transactions? If so, restore-to-saved (vs clear-to-0) at try exit
  matters — verify with test 22.
