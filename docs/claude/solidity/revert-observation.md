# Revert Observation: `__ESBMC_reverted()`

A verification-only intrinsic that lets a harness **observe whether a called
function reverted**, so you can write properties such as "this call must
revert", "this call must *not* revert", or "there exists a path where A does
not revert but B does".

> Status: Solidity frontend feature. Verification-only — it has no on-chain
> meaning and is hijacked from a user-supplied stub at analysis time (like
> `__ESBMC_assume`). See also
> [approximation-ledger.md](approximation-ledger.md) and
> [language-support.md](language-support.md).

---

## 1. Motivation

`revert()` / `require(cond)` / `revert CustomError()` are lowered with
EVM-faithful **state-rollback** semantics: when a public/external function
reverts, the frontend restores `*this` to the function-entry snapshot and
performs `return <nondet>` (see `build_revert_rollback_block` in
`src/solidity-frontend/solidity_convert_modifier.cpp`). This is sound for
safety verification, but it has a side effect:

> After a callee reverts, control returns to the caller with a nondet return
> value — **the caller cannot tell "the callee reverted" from "the callee
> returned normally"**.

There is therefore no built-in way for a harness to assert *that a call
reverts*. `__ESBMC_reverted()` closes that gap with a single readable boolean.

---

## 2. API reference

| Symbol | Visibility | Returns | Meaning |
|--------|-----------|---------|---------|
| `__ESBMC_reverted()` | user-facing | `bool` | Did the **most recent external (public/external) call** revert? |

Backing model (internal, **not** called by users), in
`src/c2goto/library/solidity/solidity_misc.c`:

```c
_Bool __ESBMC_sol_reverted_flag;            /* global revert flag       */
void  __ESBMC_sol_mark_revert(void);        /* set flag (at revert site)*/
void  __ESBMC_sol_clear_revert(void);       /* clear flag (at pub entry)*/
_Bool __ESBMC_reverted(void);               /* read flag (user-facing)  */
```

The flag is a **global**, not part of any contract's state, so a revert's
`*this` rollback does not reset it.

---

## 3. Required stub declaration (so `solc` can compile)

ESBMC consumes the AST that `solc` emits, so the source must compile. Declare a
stub inside the contract; ESBMC replaces it with the real intrinsic at analysis
time (the same hijack used for `__ESBMC_assume`):

```solidity
function __ESBMC_reverted() internal returns (bool) {}
function __ESBMC_assume(bool) internal pure {}   // if you also use assume
```

`__ESBMC_sol_mark_revert` / `__ESBMC_sol_clear_revert` are internal symbols
injected by the frontend; you never write them.

---

## 4. Required flags

| Situation | Flags |
|-----------|-------|
| Observing **external** calls (`A.test(x)`, `this.f()`, other contracts) | `--bound` (binds all contracts so the call actually dispatches) |
| 256-bit arithmetic in the harness/contract | `--cvc5` (Z3 is weak on QF_BV256) |
| Bounded exploration | `--unwind N` with `N ≥` loop/dispatcher depth |
| Focused, fast iteration | `--no-standard-checks`, `--no-unwinding-assertions` |

Typical command line:

```
esbmc test.sol --contract Harness --bound --no-standard-checks \
      --unwind 4 --no-unwinding-assertions --cvc5
```

Observing **internal** calls (a function in the same contract) works without
`--bound`. `--k-induction` / `--incremental-bmc` are compatible; do not combine
`--unwind` with them (they own the k-ladder).

---

## 5. Scope semantics — "the most recent external call"

`__ESBMC_reverted()` returns whether the **most recent public/external call**
reverted. The mechanism:

- The flag is **cleared at the entry of every public/external function** (the
  EVM call boundary).
- The flag is **set at every revert site** that is reachable from that call
  (the function's own body, its modifiers, and its internal/private helpers —
  see §7).
- Internal/private helpers and modifier bodies do **not** clear the flag, so a
  revert deep inside the call subtree propagates up to the external boundary
  (matching EVM's "uncaught revert bubbles up").

**Discipline:** read `__ESBMC_reverted()` immediately after the call you care
about and *before the next external call*, because the next external call's
entry clears the flag.

This boundary-scoped clearing also means a `--bound` dispatcher that calls many
methods does not leak a previous call's revert state into the next.

---

## 6. Usage patterns

All snippets assume the stubs from §3 are declared.

### 6.1 "This call must revert" (universal)

```solidity
function harness(uint x) public {
    c.withdraw(x);                    // external call to contract c
    assert(__ESBMC_reverted());       // claim: withdraw always reverts
}
```
`VERIFICATION SUCCESSFUL` ⇒ proven to always revert.
`VERIFICATION FAILED` + counterexample ⇒ there is an input where it does *not*
revert (the counterexample is that input).

### 6.2 "This call can revert" (existence, via negation)

```solidity
function harness(uint x) public {
    c.withdraw(x);
    assert(!__ESBMC_reverted());      // claim: withdraw NEVER reverts
}
```
`VERIFICATION FAILED` + counterexample ⇒ **witness input that triggers a
revert**. `VERIFICATION SUCCESSFUL` ⇒ proven it never reverts.

### 6.3 Two calls, independent observation (the headline use case)

Verify "whenever A does not revert, B reverts":

```solidity
function harness(int x) public {
    a.test(x);
    __ESBMC_assume(!__ESBMC_reverted());   // constrain to A-did-not-revert
    b.test(x);
    assert(__ESBMC_reverted());            // assert B reverted
}
```
To instead obtain a **witness** of "A does not revert but B does", negate B's
assertion (`assert(!__ESBMC_reverted())`); the `FAILED` counterexample is the
path.

### 6.4 "A specific value triggers a revert"

Pin the input, then assert:

```solidity
function harness(uint x) public {
    __ESBMC_assume(x >= CAP);          // restrict to the suspect range
    c.deposit(x);                      // deposit has require(x < CAP)
    assert(__ESBMC_reverted());        // proven: deposit reverts for x >= CAP
}
```

### 6.5 Modifier / internal-helper reverts

`onlyOwner`-style guards and reverts buried in internal helpers are observed at
the external boundary (see §7), so 6.1–6.4 work unchanged even when the
`require`/`revert` lives in a modifier or a private helper.

---

## 7. Capture scope

| Revert location | Captured by `__ESBMC_reverted()`? | Why |
|-----------------|-----------------------------------|-----|
| public/external function **body** | ✅ | has rollback snapshot; flag set on rollback |
| **modifier** guard (`onlyOwner`, `whenNotPaused`, chains) | ✅ | modifier-aux frame lowered to *set-flag + return* under the feature gate |
| **internal / private helper** (incl. nested) | ✅ | same set-flag + return lowering |
| **constructor** | ❌ | revert prunes the path (models EVM aborting contract creation) |
| **library** function | ❌ | revert prunes the path (no `*this` to snapshot) |
| **free function** / event / error body | ❌ | no rollback context |
| `transfer` / `send` insufficient-balance revert | ❌ | library-model revert, not routed through the flag |

For the ❌ rows, the revert still *prunes* the path (legacy
`__ESBMC_assume(false/cond)` lowering); it just becomes invisible to the flag
(the path disappears instead of returning with the flag set). To test a
constructor/library revert, use the unreachability idiom instead:

```solidity
// constructor reverts ⇒ code after `new C()` is unreachable
new C(badArg);
assert(false);     // SUCCESSFUL ⇒ construction always reverted
```

The whole feature is **gated**: it only changes lowering when the compilation
unit references `__ESBMC_reverted`. Contracts that do not use it are byte-for-
byte unchanged (protects k-induction stability and existing results).

---

## 8. Unsoundness and limitations

Read this before relying on a result.

1. **No state restore for modifier/internal frames.** When a revert is captured
   in a modifier or internal helper, the frame is lowered to *set-flag +
   `return <nondet>`* **without** restoring `*this` (unlike public-body
   rollback). This inherits the existing B1 over-approximation: the model
   *admits more paths and never rules out a real EVM path* (sound for the "must
   revert" direction), but the post-revert state may retain writes that real
   EVM would have rolled back. Do not rely on state values *after* an observed
   internal/modifier revert.

2. **Non-propagation over-approximation.** After a callee "revert-returns", the
   caller continues executing (the revert is not propagated up the call stack).
   Combined with boundary-scoped clearing, a pathological shape can hide an
   earlier revert:

   ```solidity
   function f() public {        // external entry clears the flag
       g();                     // g reverts  -> flag = 1 (g is internal)
       h.other();               // EXTERNAL call -> entry clears flag = 0 (!)
       // __ESBMC_reverted() here reflects h.other(), not g()
   }
   ```
   Read the flag *immediately* after the call under test, before any further
   external call.

3. **Constructor and library reverts are invisible to the flag** (§7). They
   prune the path; use the unreachability idiom.

4. **"Most recent external call" only.** The flag is a single global. For
   several sequential external calls in one harness, read it after each one
   (before the next external call). It is not a per-call history.

5. **Polarity matters.** `assert(__ESBMC_reverted())` proves *universal* "always
   reverts"; `assert(!__ESBMC_reverted())` is used to *find* an existence
   witness (via `FAILED`). Mixing these up inverts the meaning of the verdict.

6. **`--bound` is required for external calls.** Without it, an external call's
   revert is not dispatched/observed; internal-call observation still works.

7. **`transfer`/`send` balance reverts are not captured** (§7). Use the
   standalone transfer/balance harnesses for those.

---

## 9. Diagnostics

- `esbmc test.sol ... --goto-functions-only` — confirm the lowering:
  - revert/require/customerror sites contain `__ESBMC_sol_mark_revert()`;
  - public/external function bodies start with `__ESBMC_sol_clear_revert()`;
  - modifier-aux and internal helpers use *mark + return* (not `assume(false)`);
  - constructor/library still prune;
  - a contract **not** using the intrinsic has **no** injection (gate works).
- `--condition-coverage` — the `mark`/`clear` injections are tagged `skipped`
  and live in a library file, so they do not add condition obligations; the
  condition count for a harness that uses the intrinsic matches the same
  harness without it.
