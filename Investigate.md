# Investigation: ESBMC-Solidity `try/catch` vs `__ESBMC_reverted()` for revert-rule differential tests

> **UPDATE 2026-06-23 — FIXED.** `try/catch` is now revert-correlated under
> opt-in (the unit declares the `__ESBMC_reverted` stub). The lowering hoists
> the call and branches on the real flag with a save/clear/snapshot/restore
> discipline, so the catch arm is entered iff the call reverted. The original
> finding below describes the *pre-fix* behaviour (still the default when a unit
> does not opt in). See `docs/claude/solidity/revert-observation.md` §10 and
> `regression/esbmc-solidity/trycatch_*`. EXP1/EXP6 with the opt-in stub now
> verify `SUCCESSFUL`. The plan and residual KNOWNBUGs are in `plan.md`.

**Date:** 2026-06-23
**Question:** Can the `InvMutTest` template's `try/catch` revert-flag idiom
(`bool r; try c.f(a) { r=false; } catch { r=true; } assert(<rule over r>)`)
be used to drive ESBMC for differential (mutant-killing) revert rules?

**Short answer: No.** ESBMC models `try/catch` as a **pure nondeterministic
branch** whose catch arm is feasible *regardless of whether the call actually
reverts*. Any property whose truth depends on "the catch arm was taken ⇔ the
call reverted" is **unsound**: it yields `VERIFICATION FAILED` even on the
correct contract `P` (false positive), which directly violates the template's
requirement that the test *holds on `P`*.

**Use `__ESBMC_reverted()` instead.** It is a verification-only intrinsic that
precisely tracks whether the most-recent external call reverted, and it gives
the correct verdicts in every experiment below.

---

## 1. How `try/catch` is lowered

`src/solidity-frontend/solidity_convert_stmt.cpp`, `case TryStatement`
(lines ~930–1118). The model is documented in-source as:

```
if (nondet_bool()) { <externalCall>; <success_body> }
else               { <catch_block(s)> }
```

- **Success arm** executes the real external call, then runs the success body
  (return params bound to **nondet**).
- **Catch arm** runs the catch body. It is gated **only** by `nondet_bool()` —
  it is **not** predicated on the call actually reverting, and it does **not**
  re-execute / inspect the call's revert status.

Consequences:

1. The catch arm is feasible on **every** path, even when the call can never
   revert. So `r = true` (catch taken) is always reachable.
2. The success arm is **also** feasible even when the call reverts. Note: the
   in-source comment in the `TryStatement` case says an internal revert emits
   `__ESBMC_assume(false)` and prunes the success arm — but that is **stale**
   for the relevant case here. Current public/external `require`/`revert`
   lowering is **rollback + `return <nondet>`** (non-propagation: the caller
   keeps executing — see `src/solidity-frontend/README.md:443-477`), so for an
   external callee under `--bound` the success arm is generally **not** pruned;
   it just continues with nondet return values. This makes the arm choice
   *even less* correlated with the real outcome, not more.
3. There is **no correlation** between the arm chosen and the call's true
   outcome. The "revert flag" you build from the arms is just a free nondet bit.

This is *correct* for "either arm may be entered" tautologies
(`try_catch_1`: `assert(ok==true||ok==false)` → SUCCESSFUL) and for "the catch
arm is reachable" claims (`try_catch_2`: `assert(ok==true)` → FAILED). It is
*wrong* for any property that needs the arm to reflect the real revert outcome —
which is exactly what a differential revert rule needs.

State-rollback inside the arms (a separate concern) is handled correctly by SSA
branching + the B1 per-frame `*this` snapshot; see
`docs/claude/solidity/approximation-ledger.md` entry #16 and the
`try_catch_*_rollback_pass` regression tests. That is orthogonal to the
nondet-arm problem documented here.

## 2. The sound mechanism: `__ESBMC_reverted()`

Fully documented in `docs/claude/solidity/revert-observation.md`. Summary:

- User declares a stub `function __ESBMC_reverted() internal returns (bool) {}`;
  the frontend hijacks it at analysis time (gate:
  `solidity_convert.cpp:214`, fires when the source string contains
  `__ESBMC_reverted`).
- A global flag is **cleared at every public/external entry**
  (`solidity_convert_modifier.cpp:446`) and **set at every captured revert site**
  reachable from that call — body, modifiers, internal/private helpers (mark
  injected inside `build_revert_rollback_block`,
  `solidity_convert_modifier.cpp:843-850`). The user-stub `__ESBMC_reverted`
  name is hijacked to the flag-read intrinsic at
  `solidity_convert_ref.cpp:489` (that line is the stub-rename, **not** the mark
  site).
- `__ESBMC_reverted()` reads the flag: it is **true iff the most-recent external
  call reverted** on that path. Sound for both "must revert" and "must not
  revert" directions — **but only when used within the documented limits**:
  read it *immediately* after the call and before any other external call
  (the next external entry clears it); run with `--bound`; and note that
  constructor / library / free-function / `transfer` / `send` reverts are **not**
  captured (they prune the path — use the unreachability idiom instead), and
  post-revert *state* after an internal/modifier revert carries B1 caveats.
- **Requires `--bound`** for external calls (so the callee actually dispatches).
- Captured: public/external body, modifier guards, internal/private helpers.
  **Not** captured (revert prunes the path instead): constructor, library, free
  functions, `transfer`/`send` balance reverts — for those use the
  unreachability idiom (`new C(bad); assert(false);`).

## 3. Experiments (empirical, not inferred)

Binary: `build/src/esbmc/esbmc` (Opus dev build, commit `8caec0ef2e`).
Flags: `--contract <H> --bound --no-standard-checks --unwind 4 --no-unwinding-assertions --cvc5`.
Sources in `scratch_trycatch_investigation/exp*.sol`.

| # | Mechanism | Callee behaviour | Rule on `P` | Real EVM | **ESBMC** | Verdict |
|---|-----------|------------------|-------------|----------|-----------|---------|
| 1 | `try/catch` | never reverts | holds (`!r`) | SUCCESSFUL | **FAILED** | ❌ false positive |
| 2 | `__ESBMC_reverted` | never reverts | holds (`!reverted`) | SUCCESSFUL | SUCCESSFUL | ✓ |
| 3 | `__ESBMC_reverted` | reverts iff x≥100 | violated (`!reverted`) | FAILED + witness | FAILED + witness | ✓ |
| 4 | `__ESBMC_reverted` | reverts iff x≥100 | holds w/ `require(x<100)` | SUCCESSFUL | SUCCESSFUL | ✓ |
| 5 | `__ESBMC_reverted` | A: x<100, B: x≥100 | A/B existence witness | FAILED + witness | FAILED + witness | ✓ (intended) |
| 6 | `try/catch` | A: x<100, B: x≥100 | rule holds on real pair | SUCCESSFUL | **FAILED** | ❌ false positive |

### Decisive contrasts

- **EXP1 vs EXP2** (identical never-reverting callee, identical rule "call never
  reverts"): `try/catch` → spurious `FAILED` (counterexample violates `!r` at
  the `assert`, reached via the nondet catch arm even though `c.f` cannot
  revert); `__ESBMC_reverted()` → correct `SUCCESSFUL`.
- **EXP6 vs EXP5** (the user's exact A/B differential): the rule "whenever A does
  not revert, B reverts" *holds on the real pair*. `try/catch` (EXP6) reports
  `FAILED` on the **correct** contract `P` — the catch/success arm choice for
  `b.test(x)` is nondet, so the `rb=false` (success) arm is reachable even on
  paths where B genuinely reverts (the success arm still runs the modeled call,
  but its arm selection and nondet return do not reflect the real revert), and
  `assert(rb)` is violated. The test fails on `P`, so it is useless for
  mutant-killing.
  `__ESBMC_reverted()` (EXP5) correctly produces the intended existence witness.

EXP1's counterexample (verbatim):

```
State 10 file exp1_trycatch_neverrevert.sol line 15 function run
  Violated property:
    assertion !r
    !r
VERIFICATION FAILED
```

`c.f` is `pure` and unconditional — it can never revert — yet `r` is true,
proving the catch arm is entered nondeterministically.

## 4. Conclusion & recommendation

1. **Do not** use `try/catch` to capture a revert flag for differential
   revert-rule tests. The catch arm is pure nondet; the rule will register as
   `FAILED` on the correct contract `P`, breaking the "holds on `P`"
   contract and making the generated test worthless (false positives on
   everything, including non-mutants).

2. **Do** generate revert rules with `__ESBMC_reverted()`. Required pieces:
   - declare `function __ESBMC_reverted() internal returns (bool) {}`
     (and `function __ESBMC_assume(bool) internal pure {}` if used);
   - call the function, then read the flag **immediately**, before any other
     external call: `c.f(a); bool r = __ESBMC_reverted();`
   - run with `--bound` (plus `--cvc5` for 256-bit arithmetic).

3. **Update the LLM prompt template.** Replace the `try/catch` revert-flag line
   with the `__ESBMC_reverted()` idiom and add the stub + `--bound` note:

   ```solidity
   contract InvMutTest {
     {{NAME}} c;
     constructor({{NAME}} _c) { c = _c; }
     function __ESBMC_reverted() internal returns (bool) {}
     function run(/* params */) public {
       /* require(<preconditions>); */
       /* calls on c; for a revert rule:
          c.f(a); bool r = __ESBMC_reverted();   // r == true iff c.f(a) reverted */
       assert(/* general rule over params, values read from c, and revert flags */);
     }
   }
   ```
   Run command: `esbmc test.sol --contract InvMutTest --bound --no-standard-checks
   --unwind N --no-unwinding-assertions --cvc5`.

4. **Polarity reminder** (from the revert-observation doc): `assert(reverted)`
   proves *universal* "always reverts"; `assert(!reverted)` is used to *find* an
   existence witness via `FAILED`. Mixing them inverts the verdict's meaning.

## 5. Possible future fix (out of scope here)

Making `try/catch` sound for revert rules would mean gating the catch arm on the
real revert flag instead of a free nondet bit — i.e. fusing the `TryStatement`
lowering with the `__ESBMC_reverted()` mark/clear machinery so that
`catch ⇔ __ESBMC_sol_reverted_flag` after the call. Until then, `__ESBMC_reverted()`
is the only sound path and the template must use it.
