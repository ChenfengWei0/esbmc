# Plan: Single-function harness loop elision to aid k-induction convergence

**Branch:** `feat/solidity-revert-observation` (work would land on a new `feat/single-fn-loop-elision`)
**Status:** PROPOSAL — pending adversarial review
**Author:** investigation 2026-06-20

---

## 1. Problem statement

A large share of the `esbmc-solidity` regression suite is forced onto the
`--unwind N --no-unwinding-assertions` combination because k-induction does not
converge on the generated verification harness.

Empirical scan of `regression/esbmc-solidity/*/test.desc`:

- **292** tests use `--no-unwinding-assertions`.
- Of the 278 whose `--contract` target I could parse, the public/external
  function-count distribution is:

  | #pub/ext fns | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
  |---|---|---|---|---|---|---|---|---|
  | tests | 7 | **223** | 36 | 4 | 4 | 1 | 1 | 2 |

- So **223 / 292 (≈76%)** target a contract with exactly **one** public/external
  non-constructor function. Only 2 of those 223 also declare a
  `fallback`/`receive`.

`--unwind N --no-unwinding-assertions` is itself an unsound silent-truncation
combo (see memory `feedback_silent_truncation_flags`): paths beyond `N`
iterations are dropped with no diagnostic. The suite uses it as a pragmatic
workaround for k-induction divergence, not because single-tx truncation is the
intended semantics in every case.

## 2. Root cause of k-induction divergence

The per-contract harness `_ESBMC_Main_<C>` is built in
`multi_transaction_verification` (`src/solidity-frontend/solidity_convert_contract.cpp:653-745`):

```
_ESBMC_Main_C() {
  __ESBMC_HIDE:
  // constructor already ran (deployer binding)
  while (nondet_bool()) {          // <-- lines 700-707
    _sol_per_tx_reseed();          //     fresh msg.sender/value, block.* per tx
    _ESBMC_Nondet_Extcall_C();     //     inner if-chain dispatcher
  }
}
```

The inner dispatcher `_ESBMC_Nondet_Extcall_<C>`
(`get_unbound_function`, `solidity_convert_constructor.cpp:235-424`) is an
if-chain `if (nondet_bool()) { f1(); return; } if (nondet_bool()) { f2(); return; } ...`
over the public/external functions (constructor skipped; fallback/receive ARE
dispatched).

The outer `while (nondet_bool())` models an **unbounded sequence of
transactions**. It is the sound model of a deployed contract (an attacker may
call any public function, any number of times, in any order). It is also the
**only structurally-unbounded loop** in the harness for many contracts — and the
one k-induction must find an inductive invariant for. When it cannot, the run
diverges and the test is forced onto bounded `--unwind`.

Bounded *inner* source loops (`for (i<3)`, fixed `.push` chains) are fully
unwound by k-induction and are not the convergence problem; the dispatcher loop
is.

## 3. Proposed change

When the contract under harness has **exactly one** public/external,
non-constructor function, and **no** `fallback`/`receive`, emit the dispatch
**once** instead of wrapping it in `while (nondet_bool())`:

```
_ESBMC_Main_C() {
  __ESBMC_HIDE:
  _sol_per_tx_reseed();
  _ESBMC_Nondet_Extcall_C();   // inner if-chain still present: "0-or-1 call of f"
}
```

With the unbounded loop gone the harness is straight-line over bounded inner
loops, so k-induction converges (or plain BMC suffices) and the test no longer
needs `--no-unwinding-assertions` for the dispatcher.

### 3.1 Gating

Per the user's request, gate the elision behind **`--solidity-precise`** (see
§6 for the soundness concern with this binding). It must be **opt-in**; default
behaviour (full `while (nondet_bool())` loop) is unchanged so the existing suite
and external users are unaffected.

### 3.2 Eligibility predicate

Reuse the existing function-signature data (`funcSignatures[c_name]`, already
consumed by `get_unbound_function` and by `has_callable_func`,
`solidity_convert_call.cpp:744-755`). Eligible iff:

- count of `sig` with `sig.name != c_name` and
  `visibility ∈ {public, external}` is exactly **1**, AND
- the contract declares no `fallback` and no `receive`.

Implementation site: `multi_transaction_verification`, replacing the
`code_whilet` construction at `solidity_convert_contract.cpp:700-707` with a
conditional — single call when eligible+flag, while-loop otherwise.

### 3.3 Inner-dispatcher semantics

The inner `if (nondet_bool()) { f(); return; }` makes the elided form a
**"0-or-1 call of f"**. For FAILED detection the 1-call path exists (nondet
covers it); for SUCCESSFUL both 0 and 1 call are checked. Optionally we could
call `f` unconditionally when eliding (exactly-one-call), but 0-or-1 is a
harmless superset and needs no change to `get_unbound_function`. **Decision
deferred to review.**

## 4. Scope — what this actually buys

Removing the dispatcher loop only helps a test drop `--no-unwinding-assertions`
if the dispatcher loop was the *binding* unbounded construct. Breaking the 223
single-fn tests down by other loop sources:

| subset | count | notes |
|---|---|---|
| single-fn, **no** source loop, **not** `--bound` | **86** | cleanest win: dispatcher loop is the only unbounded loop |
| single-fn, `--bound`, no source loop | 84 | may still need `--unwind` for EOA-balance / address-uniqueness scan loops |
| single-fn, has `for`/`while`/`do`/`push`/`pop` | 53 | inner loop ⇒ `--unwind` still required regardless |

So the realistic clean win is **~86 tests** (possibly up to ~170 if the
`--bound` scan loops are small/bounded enough for k-induction). The remaining
~53 keep needing `--unwind` for inner loops — this change does **not** make all
223 drop the flag. This is an honesty caveat the headline "223 tests" must not
obscure.

## 5. Soundness analysis (the crux)

`while (nondet_bool()) { reseed(); f(); }` is the **sound** model (unbounded
tx sequence). Eliding it to a single call is an **under-approximation**:

- **FAILED verdict: stays sound.** Any property violation found within one tx is
  a real violation.
- **SUCCESSFUL verdict: becomes unsound in general.** A bug that only manifests
  after ≥2 calls of `f` (state accumulated across transactions) is missed,
  yielding a false `VERIFICATION SUCCESSFUL`.

  Minimal realistic counterexample:
  ```solidity
  contract C {                 // single public function
      uint256 count;
      function f() public { count++; assert(count < 2); }
  }
  ```
  One call: `count == 1`, assert holds → SUCCESSFUL. The loop model reaches
  `count == 2` → FAILED. Loop elision hides the bug.

- **Reentrancy / TOD:** even a single public function can be re-entered or
  participate in transaction-ordering attacks across calls; eliding the loop
  removes those interleavings (memory `feedback_no_dispatcher_loop_collapse`).

It **is** sound to elide when the single function cannot carry state across
calls — e.g. it is `pure`/`view`, or provably writes no persistent state that it
(or the checked property) later reads. That is a strict subset of the 223.

**Verification of the existing corpus:** I manually inspected the
multi-call-suspicious single-fn `_fail` tests (`assert_revert_two_calls_fail`,
`peer_contract_state_var_dangling_fail`, `swc115_phishing_buggy_fail`, …). All
fail on a *single* top-level call — e.g. "two_calls" denotes two *sub-calls*
(`a.test`; `b.test`) inside one `check`, not two dispatcher iterations. So no
*current* `_fail` test is expected to regress. But the transformation is still
an under-approximation for *future* contracts and for the SUCCESSFUL direction.

## 6. The `--solidity-precise` binding tension (must resolve before coding)

`--solidity-precise` is documented (`options.cpp:221-233`) as *"Opt into precise
(sound) modelling for Solidity primitives that currently default to a loose
under-approximation."* It currently selects the **sound** address-uniqueness
scan over the loose 16-slot if-chain — i.e. the flag means **"make me more
sound."**

Loop elision does the **opposite**: it is a *stronger* under-approximation
(1 tx) than the default (unbounded). Binding it to `--solidity-precise` would
make a flag named/advertised as "precise/sound" silently *reduce* soundness for
single-fn contracts — directly contradicting the flag's contract and the
guidance in memories `feedback_solidity_precise_criterion` ("small realistic
test exposing the gap ⇒ not behind \[that] flag") and
`feedback_no_soundness_escape_hatch` ("slow tests get THOROUGH, not a
correctness-skipping flag").

Options:

- **(A) Bind to `--solidity-precise`, but only elide when provably sound**
  (single fn is `pure`/`view`). Consistent with the flag's meaning, but helps
  only a small subset (most of the 223 are state-mutating feature tests).
- **(B) Dedicated, honestly-named flag** e.g. `--solidity-single-tx`,
  documented as an under-approximation that bounds verification to one
  transaction. Helps the full ~86–170 subset; honest naming; does not corrupt
  `--solidity-precise` semantics. **Recommended.**
- **(C) Bind to `--solidity-precise` unconditionally for single-fn** (literal
  user request) and document the under-approximation in the flag help. Simplest;
  but overloads "precise" with an unsound transform and conflicts with stored
  guidance.

**Recommendation:** (B). Surface this to the user before implementing
(memory `feedback_no_silent_substitution`: surface the collision before coding).

## 7. Implementation steps

1. New branch `feat/single-fn-loop-elision` off the current head.
2. Add eligibility helper (e.g. `bool harness_is_single_entry(const std::string&)`)
   near `has_callable_func` in `solidity_convert_call.cpp`, reusing
   `funcSignatures` + a fallback/receive check.
3. In `multi_transaction_verification`
   (`solidity_convert_contract.cpp:700-707`), branch on `flag && eligible`:
   emit the single `reseed(); f();` sequence instead of the `code_whilet`.
4. Resolve the flag decision (§6) — wire to the chosen option.
5. Update help text / `src/solidity-frontend/README.md`.
6. Tests (§8).
7. cppcheck on changed frontend files; clang-format; targeted ctest.

## 8. Test plan

At least one PASS and one FAIL exercising the new flag (per repo policy):

- `single_fn_loop_elision_pass` — single-fn contract, single-tx-safe property,
  passes with the flag under k-induction (no `--no-unwinding-assertions`).
- `single_fn_loop_elision_fail` — single-fn contract with a single-tx bug, still
  FAILED with the flag (confirms FAILED soundness preserved).
- `single_fn_multi_call_bug_knownbug` — the §5 counter contract: FAILED without
  the flag (loop present), but SUCCESSFUL **with** it. Pin as **KNOWNBUG** to
  document the under-approximation honestly (memory
  `feedback_knownbug_tests`), NOT as a CORE pass.
- Targeted re-run of the ~86 clean-win tests with the flag added and
  `--no-unwinding-assertions` removed to confirm convergence; only migrate a
  test if it converges AND keeps its verdict. Do **not** mass-edit all 223.

## 9. Open questions for adversarial review

1. Is the `--solidity-precise` binding acceptable, or must this be a separate
   under-approximation flag (§6)? Does overloading "precise" with an unsound
   transform violate the flag's contract?
2. Is "single public/external function, no fallback/receive" the right
   eligibility predicate, or must we additionally require `pure`/`view` (or a
   no-cross-call-state analysis) to keep the SUCCESSFUL direction sound?
3. Multi-contract harness: `prepare_harness_entry_functions` builds one
   `_ESBMC_Main_` per contract and a nondet switch over entries
   (`solidity_convert_contract.cpp:846,919`). Does eliding the per-contract loop
   interact badly with cross-contract reentrancy / multi-entry interleavings?
4. Does removing the loop silently defeat reentrancy/TOD detection for
   single-fn contracts that the suite *does* care about (memory
   `feedback_no_dispatcher_loop_collapse`)?
5. Is "0-or-1 call" (inner `if(nondet)`) acceptable, or should elision force
   exactly-one call?
6. Given only ~86 tests cleanly benefit, is the soundness/complexity cost worth
   it versus leaving the suite on documented bounded `--unwind`?
