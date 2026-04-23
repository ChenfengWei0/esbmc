# Verification Modes — cheat sheet

Solidity runs under several orthogonal switches. Two dimensions matter:

1. **Entry-point selection**: what code does ESBMC actually enter?
   — controlled by (nothing) vs `--contract` vs `--function` vs `--focus-function`.
2. **External-call resolution**: how are `addr.call(...)` / `A(x).f()` modelled?
   — controlled by `--bound` vs the default unbound.

These compose: e.g. `--contract A --focus-function f --bound` is valid,
`--function` is incompatible with `--focus-function` (see below). The
dimensions are documented in detail in the subsections that follow; this
table is the quick reference.

## Entry-point dimension

| Invocation | State vars at entry | Harness that runs | What is verified | Soundness posture (entry dimension) |
|---|---|---|---|---|
| `esbmc contract.sol` (no `--contract`, no `--function`) | Constructor-initialised for every declared contract | Multi-contract wrapper (bound or unbound — see below); each contract gets its own `_ESBMC_Main_<C>` harness. In unbound mode, the wrapper calls them sequentially (`A`, then `B`, …). In bound mode, it uses a nondet switch to pick one per run. | Every public/external function of every contract, called from arbitrary order, arbitrary msg.sender/value. | **OVER** — all state-space false positives of `--contract` apply, compounded across contracts. |
| `esbmc contract.sol --contract C` | Constructor of `C` runs first, `C`'s state vars reflect constructor output; other contracts' state stays default. | `_ESBMC_Main_C()` = constructor + `while (nondet_bool) _ESBMC_Nondet_Extcall_C()`. Each loop iteration is one transaction; every public/external method of `C` is dispatched nondet-guarded inside the loop body. | Per-transaction invariants on `C` under an arbitrary sequence of externally-visible calls after construction. | **OVER** (harness over-approximates ordering, msg.sender/value, return values of outbound calls). See ledger row 7. VERIFICATION SUCCESSFUL is sound for safety under the single-transaction model; VERIFICATION FAILED may be a spurious call-ordering or nondet-context counterexample. |
| `esbmc contract.sol --contract C --focus-function f` | Constructor of `C` runs first, same as `--contract C`. | Same `_ESBMC_Main_C` harness, but the internal nondet dispatch loop of `_ESBMC_Nondet_Extcall_C` **filters** every public/external method except `f`. `f` is still called from inside the loop (so re-entry is possible), construction still happens. Not a state-space change — purely a verification-cost optimisation. | Safety of `f` reached from a *constructed* contract state, where the only callable entry in the nondet loop is `f` itself. | **OVER, identical strength to `--contract C` for `f`'s paths**. Sound for safety. VERIFICATION SUCCESSFUL is sound. VERIFICATION FAILED is real if it does not rely on a call-context over-approximation (ledger row 7). Other functions of `C` are never exercised, so bugs in them are invisible by construction — that is an intentional scoping decision, not an unsoundness of `f`'s proof. |
| `esbmc contract.sol --contract C --function f` | **Nondet (fully symbolic)** — the constructor does NOT run, no state initialiser applies, no harness is built. | `f` is made the GOTO entry point and called once with nondet parameters. State variables start at unconstrained nondet of their declared type. | Safety of `f` over *every conceivable* initial state of `C`, regardless of whether such a state is reachable from the constructor. | **OVER on state space (sound for safety)** — see the call-out below. This is the only mode where VERIFICATION SUCCESSFUL gives a stronger guarantee than the other modes, at the cost of possible false-positive counterexamples. |

### ⚠ `--function` mode soundness call-out (important)

`--function` replaces the constructor with "all state variables are
fresh nondet of their declared type". This is **strictly more
permissive** than any real execution of `C`:

- Every state reachable from `constructor() → f()` under any tx
  sequence is also reachable under `--function`, because nondet
  contains all of those states.
- Additionally, many states that the constructor never produces
  (e.g. violating a declared invariant, skipping a required
  initialisation) are also explored.

Consequences:

- **VERIFICATION SUCCESSFUL under `--function` is a real safety
  proof.** Sound. If the function is safe under every conceivable
  state, it is a fortiori safe under the states the constructor
  actually produces. This is the strongest positive result any of the
  modes can give — it is equivalent to proving a per-function
  inductive invariant, quantified universally over the class
  invariant.
- **VERIFICATION FAILED under `--function` may be a false positive.**
  The counterexample could rely on a combination of state-variable
  values that is unreachable from `constructor() → (any tx sequence)`.
  Before trusting the trace, re-verify with `--contract C
  --focus-function f` (or remove `--function` entirely): if that run
  ALSO fails, the bug is real; if it passes, the `--function` trace
  was spurious state-space expansion.
- The correct interpretation is: `--function` gives a cheaper,
  cleaner OVER-approximation in the state-space dimension, at the
  cost of false positives. It says nothing about the correctness of
  the constructor or about cross-function state invariants.

This is why `--function` is **banned from regression test.desc**
files: regression tests must exercise the full constructor + dispatch
harness, otherwise adversarial tests degenerate into trivial
benchmarks and the frontend / solver weaknesses they were designed to
expose stop showing up.

### `--function` vs `--focus-function` — pick one

|   | `--function f` | `--focus-function f` |
|---|---|---|
| Constructor runs? | No | Yes |
| State vars at entry | Nondet | Constructor-initialised |
| Harness | None (`f` is the entry) | Full `_ESBMC_Main_C` with dispatch loop filtered to only `f` |
| Re-entry into `f` during its own execution | No (single call) | Yes (via the nondet dispatch loop) |
| VERIFICATION SUCCESSFUL means | `f` is safe under *every* initial state — strongest positive result available | `f` is safe under the post-constructor state reachable by re-entering only `f` |
| VERIFICATION FAILED may be spurious because | Counterexample state may be unreachable from any constructor+tx sequence | Counterexample relies on ledger row 7 (call-context nondet) |
| Compatibility | Must not combine with `--focus-function` (`convert()` rejects the combination) | Must combine with `--contract` (or exactly one contract in source) |
| Best for | Quick property checks on *pure*/*view* functions, or over-approximate safety claims for a single function | Narrowing verification cost to one function while keeping a faithful harness |

Combining `--function` and `--focus-function` is rejected at convert
time (`solidity_convert.cpp:165`) — the two flags express different
intents and would silently cancel out. Pick one.

## External-call dimension

`--bound` and its absence (unbound) are orthogonal to the entry-point
choice and can be combined with any entry-point mode.

| Flag | `addr.call(data)` / cross-contract dispatch | State shared across contracts? | Soundness posture (ext-call dimension) |
|---|---|---|---|
| default (unbound) | `get_unbound_expr()` re-enters the *current* contract's nondet dispatch and returns a nondet `(bool, bytes)` tuple. Target address is **ignored**. | No — each contract verified independently. | **OVER** on reentrancy and on return values. Safe for single-contract safety; cross-contract invariants are invisible. |
| `--bound` | `get_bound_low_level_call()` emits an if-then-else chain over `contractNamesList`; address match dispatches to that contract's `_ESBMC_Nondet_Extcall_<target>`. No match → hard `false` return (trusted-closed-world). | Yes — static instances share storage; `_ESBMC_bind_cname` tracks concrete type per address. | **UNDER** relative to real EVM (unknown addresses cannot succeed), **sound under the trusted closed-world assumption**. Strictly more precise than unbound *for properties involving known callees*; strictly less precise for attacker-controlled addresses. |

Both modes currently share the same low-level-call semantic gaps
listed in [language-support.md](language-support.md) §D (no storage
context swap for `delegatecall`, no read-only enforcement for
`staticcall` — beyond the bound-mode snapshot+rollback work).

## Composition cheat sheet

| Command | Entry posture | Ext-call posture | Typical use |
|---|---|---|---|
| `esbmc c.sol --contract C` | --contract (OVER, sound) | unbound (OVER) | Default single-contract verification |
| `esbmc c.sol --contract C --bound` | --contract | bound (UNDER, trusted) | Single-contract that makes cross-contract calls, when callee shapes matter |
| `esbmc c.sol --contract A --contract B --bound` | multi-contract | bound | Multi-contract interaction (token + exchange) |
| `esbmc c.sol --contract C --focus-function f` | focus-function (same as --contract for `f`) | unbound | Cut verification cost by skipping unrelated functions |
| `esbmc c.sol --contract C --function f` | **nondet state** (OVER on state) | unbound | Over-approximate safety proof for a pure/view function. **Interactive only — banned in regression tests.** |
| `esbmc c.sol` (no `--contract`, no `--function`) | all declared contracts | depends on `--bound` | Verify every contract declared in the file; useful for quick smoke tests. |

## Interpreting results

- **VERIFICATION SUCCESSFUL** under `--contract` / `--focus-function` /
  `--function` is a safety proof *within the frontend's approximation
  ledger*. Consult [approximation-ledger.md](approximation-ledger.md)
  to understand what classes of bugs are outside the model.
- **VERIFICATION FAILED**:
  - Under `--contract` / `--focus-function`: likely real unless the
    counterexample depends on ledger row 7 (call-context nondet),
    row 16 (revert-without-rollback), or row 5 (non-monotonic block
    context).
  - Under `--function`: treat as tentative — re-run with
    `--focus-function` (or plain `--contract`) to confirm. A trace
    that fails under `--function` but passes under
    `--focus-function` is a spurious state-space expansion; the real
    contract cannot reach that state.
  - Under `--bound`: check that the counterexample does not depend on
    an untracked address — if it does, the unknown-address branch is
    hard-`false` in the model, and the CE is exercising a known
    callee. If the property is about attacker-controlled addresses,
    re-verify under unbound.

## Address Binding Modes (`--bound` / default unbound) — detail

ESBMC supports two verification strategies for multi-contract Solidity programs, controlled by the `--bound` flag. The default is **unbound** mode.

```sh
# Unbound mode (default): external calls modeled as nondet (over-approximate)
esbmc contract.sol --contract A --contract B --unwind 5

# Bound mode: contracts linked together as a complete system
esbmc contract.sol --contract A --contract B --bound --unwind 5
```

### Unbound Mode (default)

Each contract is verified **in isolation**. Low-level calls (`.call()`, `.delegatecall()`, `.staticcall()`) do **not** dispatch to a concrete target contract: instead, `get_unbound_expr()` (`solidity_convert_constructor.cpp:159`) re-invokes the *current* contract's nondet dispatch `_ESBMC_Nondet_Extcall_<current_contract>` and returns a fresh nondet `(bool, bytes)` tuple. This models arbitrary reentrancy into the current contract plus an over-approximated return, but the target address argument is **ignored**.

**Harness structure** (`_ESBMC_Main`):
```
_ESBMC_Main():
  _ESBMC_Main_ContractA()    // verify A in isolation
  _ESBMC_Main_ContractB()    // verify B in isolation
```

Each `_ESBMC_Main_X` creates a static instance `_ESBMC_Object_X`, calls its constructor, then enters a nondeterministic dispatch loop (`_ESBMC_Nondet_Extcall_X`) that can call any public/external function with nondet arguments.

**Key behaviors:**
- Low-level call return values: nondet `(bool, BytesDynamic)` tuple; side effect = nondet reentrancy into the *current* contract (target ignored)
- `.send()` / `.transfer()`: nondet bool return
- Address properties (`.balance`, `.codehash`): `nondet_uint`
- Contract instances: each verified independently, no cross-contract state
- Best for: single-contract verification, fastest performance

### Bound Mode (`--bound`)

Contracts are **linked together as a complete system** under a **trusted closed-world assumption** — analogous to SMTChecker's `--model-checker-ext-calls=trusted` mode. The verifier assumes every callable address corresponds to one of the declared contracts in `contractNamesList`; low-level calls resolve to the correct target by comparing the address argument against every known `_ESBMC_Object_X.$address`. If no address matches, the call's `$call#0` / `$transfer#0` / `$send#0` / `$staticcall#0` / `$delegatecall#0` definition returns hard `false` (no nondet fallback), which prunes the caller's `require(success)` path. This is an **under-approximation** relative to real EVM (where unknown addresses might still succeed) but is sound under the trusted assumption. Each contract instance also tracks its concrete type via a `_ESBMC_bind_cname` member variable.

**Harness structure** (`_ESBMC_Main`):
```
_ESBMC_Main():
  switch(nondet_uint()) {
    case 0: _ESBMC_Main_ContractA(); break;
    case 1: _ESBMC_Main_ContractB(); break;
  }
```

The nondeterministic switch picks **one** contract to fully explore per verification run. Within that run, cross-contract calls are resolved through the binding mechanism.

**Key behaviors:**
- Low-level calls: `get_bound_low_level_call()` routes to a per-contract `$call#0` / `$delegatecall#0` / `$staticcall#0` function that generates an if-then-else chain over `contractNamesList`; on address match, the target's nondet dispatch `_ESBMC_Nondet_Extcall_<target>` is invoked and `return true`; no address match → `return false` (trusted-closed-world under-approx) (`solidity_convert_call.cpp:1220+`, `:2203`, `:2347`)
- Same pattern for `.transfer()` / `.send()` (`:1751`, `:1997`)
- Address binding: `x._ESBMC_bind_cname = "ContractName"` assigned at `new` expressions. `C(_addr)` casts now also set the per-pointer `$bind` shadow (see below).
- Contract instances: share state, cross-contract interactions modeled
- Polymorphism/inheritance dispatch (function calls): `get_high_level_member_access` builds a dispatcher function that iterates the structural-typing cluster and routes on `_ESBMC_bind_cname` — so `c.f()` goes to the right singleton even if `c`'s declared type covers multiple implementations. Function-call dispatch still reads the singleton struct field (unchanged).
- **Per-pointer polymorphism (mapping getter read path only)**: because `_ESBMC_Object_<X>` is a shared singleton — every `X*` dereferences to the same memory — a per-pointer bind cannot live in the struct field. Each contract-typed local carries a companion `<var_id>$bind` shadow symbol. `new C()` writes BOTH the singleton field AND the shadow to the declared cname. `C(_addr)` writes ONLY the shadow, preferring *shadow-propagation* when the argument is `address(<local_var>)` (copies the source local's shadow verbatim), else falls back to an address-match if-ladder over singletons (valid only when all ctors have run). The public-mapping-getter polymorphism read (`c.m(k)` when `cname_set.size() > 1`) reads the shadow via an `if_exprt` ladder, falling back to the static singleton route when no shadow exists (state var / parameter). Function-call dispatch is NOT yet migrated to shadows — callers that cast across the cluster boundary see the declared-type dispatcher, which is the observable gap today.
- Best for: multi-contract interaction verification (e.g., token + exchange)

**⚠ Low-level call accuracy gaps (both modes):** See [language-support.md §D](language-support.md) for the specific semantic gaps that survived into bound mode — in particular, `delegatecall` does not swap storage context by default (fast-path shadow covers a subset) and `staticcall` enforces read-only via snapshot+rollback but does not surface "target tried to write" explicitly.

### Implementation Details

| Component | File | Function |
|-----------|------|----------|
| `is_bound` flag | `solidity_convert.h:783` | Parsed from `config.options.get_option("bound")` |
| Bound harness | `solidity_convert_contract.cpp:677` | `multi_contract_verification_bound()` |
| Unbound harness | `solidity_convert_contract.cpp:819` | `multi_contract_verification_unbound()` |
| Binding assignment | `solidity_convert_expr.cpp:1966` | `get_new_object_expr()` — sets `_ESBMC_bind_cname` |
| Nondet dispatch | `solidity_convert_constructor.cpp:205` | `_ESBMC_Nondet_Extcall_X` function generation |
| Ext call abstraction | `solidity_convert_call.cpp` | `.call()`/`.send()` → nondet in unbound |
| Bind name list | `solidity_convert.cpp:754` | `$X_bind_cname_list` array + `initialize_X_bind_cname()` |
| Static instances | `solidity_convert_contract.cpp:73` | `_ESBMC_Object_X` global instances |

## `--function` Mode Semantics

`--function funcName` verifies a single function in isolation, under **arbitrary initial state**. All state variables are initialized to nondeterministic (symbolic) values, NOT to their declared initializers or constructor-assigned values. This is by design: `--function` mode checks whether the function is correct for **all possible** contract states, not just post-constructor states.

**Implications:**
- `x = 42; assert(x == 42)` where `x` is a state variable will **fail** because nondet state can be re-entered between assignment and assertion
- `--function` is best for verifying **function-local** properties (pure/view functions, local variable logic) and for **over-approximate** analysis where any input state is valid
- `constant` state variable values are only available in `--contract` mode (where the initializer runs)
- To verify state-dependent properties, use `--contract ContractName` instead (which runs the constructor first and then dispatches all public functions); to narrow verification to a single function without dropping the constructor, use `--focus-function`

**When to use which:**
| Mode | State vars | Harness | Best for |
|------|-----------|---------|----------|
| `--contract C` | Initialized by constructor | Constructor + nondet dispatch of all public/external functions | Testing contract invariants, state-dependent assertions |
| `--function f` | Nondet (arbitrary) | No constructor, no dispatch loop; `f` is called once with symbolic state | Function-local over-approximate soundness, quick sanity checks on pure functions |
| `--contract C --focus-function f` | Initialized by constructor | Constructor + nondet dispatch restricted to `f` only | Verifying `f` after proper construction without exploring other public functions |

> **🚫 Hard rule — never use `--function` in regression tests.**
>
> `--function` fabricates nondet initial state, which makes the verifier's job dramatically easier (no pre-state from the constructor, no cross-function interaction, no dispatch loop pressure on the solver). That turns adversarial tests into toy benchmarks and hides real performance and soundness weaknesses in the Solidity frontend / SMT backends.
>
> Regression tests must verify the contract under realistic post-construction state. Use `--contract` for whole-contract verification and `--focus-function` when you need to narrow assertions to a single function while keeping the constructor + harness. `--function` remains available as an interactive / research knob — just not in `regression/esbmc-solidity/**/test.desc`.

## `--focus-function` Mode Semantics

`--focus-function funcName` narrows verification to a single function while keeping the full contract harness:

- The constructor runs, state variables get their declared initializers, inheritance linearization applies, and the whole `_ESBMC_Main_<C>` harness is built.
- Only the named function is verified: the nondet dispatch loop inside `_ESBMC_Nondet_Extcall_<C>` filters out every public/external method except `funcName`, so the BMC engine never explores paths that call other functions on the target contract. This is a pure verification-cost optimization; no state is fabricated as nondet.

**Requirements:**
- Requires `--contract <name>` to pick the target contract when the source declares more than one contract. If the source has exactly one (non-library, non-interface) contract, `--contract` is auto-inferred.
- `funcName` must be a `public` or `external` method on the target contract (not the constructor, not `receive`/`fallback`).
- Works with both `--bound` and `--unbound`. In `--bound` mode, other contracts reached via cross-contract calls still dispatch their full public surface — the filter only applies to the focus target contract's own harness.

**Implementation:** the filter lives in `solidity_convert_constructor.cpp:get_unbound_function()` inside the `for (const auto &method : methods)` loop: when `focus_func` is set and `c_name == *tgt_cnt_set.begin()`, methods whose name differs from `focus_func` are skipped before the if-branch is emitted. Validation (contract disambiguation, function existence) happens in `solidity_convert.cpp:convert()` right after `populate_auxiliary_vars()`.

**Tests:** see `focus_function_1`, `focus_function_2`, `focus_function_4` for: focus-function isolates `f` after construction (pass), full harness exposes a `g`-before-`f` violation that focus-function hides (fail), unbound single-contract auto-inference (pass).

## Performance Considerations

- **Unbound** is significantly faster for single-contract verification since it avoids cross-contract symbolic exploration.
- **Bound** mode can be very slow when contracts have complex interactions (e.g., `transfer_send_2` test: >1200s timeout with `--bound`).
- When using `--bound` with `--contract A --contract B`, all contracts are instantiated and their constructors run, which increases the state space.

## Automatic solver selection (Solidity-only)

When the user does not pass an explicit solver flag, the Solidity frontend auto-selects `bitwuzla > cvc5 > boolector > z3` (first available). Implementation: `src/esbmc/esbmc_parseoptions.cpp` inside the Solidity detection block after `get_command_line_options()`.

Observed regression-suite timings at 60s CTest timeout (same machine, 509 tests):

| Default backend | Wall time | Notes |
|-----------------|----------:|-------|
| z3 | 47s | baseline |
| cvc5 | 45s | 2 THOROUGH tests time out and need `--z3` pin: `import_15`, `mul_cnt_ver_2` |
| **bitwuzla** | **39s** | fastest; both previously-pinned THOROUGH tests also finish in 3–5s with `--bitwuzla` |

The `--z3` pin on `import_15` and `mul_cnt_ver_2` is preserved so minimal builds (only `z3 + cvc5`, no bitwuzla/boolector) still pass the suite. On a full build the auto-select picks bitwuzla and those tests would pass without the pin as well.
