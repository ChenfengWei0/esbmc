# CLAUDE_Solidity.md

Solidity frontend specific guidance for Claude Code.

## Solidity Frontend File Structure

The core converter is a single class `solidity_convertert` (declared in `solidity_convert.h`), with implementations split by concern across multiple `.cpp` files:

| File | Purpose |
|------|---------|
| `solidity_convert.cpp` | Entry point, initialization, AST preprocessing |
| `solidity_convert_expr.cpp` | Expression conversion (`get_expr` and operator helpers) |
| `solidity_convert_call.cpp` | Function calls, transfers, high/low-level calls |
| `solidity_convert_type.cpp` | Type descriptions, elementary type mapping, parameter lists |
| `solidity_convert_decl.cpp` | Declarations: variables, functions, structs, contracts, enums |
| `solidity_convert_util.cpp` | Utilities: locations, symbols, JSON helpers, array helpers |
| `solidity_convert_constructor.cpp` | Constructor creation, initializer migration, contract instantiation |
| `solidity_convert_contract.cpp` | Contract instances, multi-transaction/multi-contract verification |
| `solidity_convert_ref.cpp` | Symbol resolution: variable/function/enum/builtin references |
| `solidity_convert_mapping.cpp` | Solidity `mapping` to infinite array conversion |
| `solidity_convert_stmt.cpp` | Statement and block conversion |
| `solidity_convert_modifier.cpp` | Function modifiers, reentrancy checks |
| `solidity_convert_builtin.cpp` | Built-in symbols/properties (msg, tx, block, address) |
| `solidity_convert_tuple.cpp` | Tuple definition, instantiation, assignment unpacking |
| `solidity_convert_inheritance.cpp` | Contract inheritance handling |
| `solidity_convert_literals.cpp` | Literal conversion (integer, bool, string, hex) |
| `solidity_grammar.cpp/h` | Grammar enums, `SolType` enum, type mapping and classification functions |
| `solidity_language.cpp/h` | Language plugin interface |
| `pattern_check.cpp/h` | Vulnerability pattern detection (e.g. SWC-115 tx.origin) |

## Running ESBMC on Solidity

ESBMC supports two ways to verify Solidity contracts:

```sh
# Auto-invoke solc (recommended): ESBMC finds and runs solc automatically
esbmc contract.sol --contract MyContract

# Manual AST generation (legacy): user runs solc separately
solc --ast-compact-json contract.sol > contract.solast
esbmc --sol contract.sol contract.solast --contract MyContract
```

**solc discovery order**: `--solc-bin <path>` > `$SOLC` env var > `solc` in `$PATH`. When auto-invoked, ESBMC prints the solc path and version (e.g. `Compiling Solidity AST using: /usr/local/bin/solc (v0.8.30)`).

**Version compatibility** (checked at AST level via `PragmaDirective`):
- pragma *pinned* to `< 0.5.0` (no upper bound, or upper bound also < 0.5.0): rejected
- pragma with pre-0.5 lower bound but upper bound ≥ 0.5.0 (e.g. `pragma solidity >=0.4.0 <0.9.0;`): warning, accepted (solc has already compiled with a modern compiler — see `solidity_convert.cpp::check_min_version`)
- `0.5.0 – 0.7.0`: warning (may cause unexpected behaviour)
- `>= 0.7.0`: fully supported (tested against 0.8.x)

### Verification Modes — cheat sheet

Solidity runs under several orthogonal switches. Two dimensions matter:

1. **Entry-point selection**: what code does ESBMC actually enter?
   — controlled by (nothing) vs `--contract` vs `--function` vs `--focus-function`.
2. **External-call resolution**: how are `addr.call(...)` / `A(x).f()` modelled?
   — controlled by `--bound` vs the default unbound.

These compose: e.g. `--contract A --focus-function f --bound` is valid,
`--function` is incompatible with `--focus-function` (see below). The
dimensions are documented in detail in the subsections that follow; this
table is the quick reference.

#### Entry-point dimension

| Invocation | State vars at entry | Harness that runs | What is verified | Soundness posture (entry dimension) |
|---|---|---|---|---|
| `esbmc contract.sol` (no `--contract`, no `--function`) | Constructor-initialised for every declared contract | Multi-contract wrapper (bound or unbound — see below); each contract gets its own `_ESBMC_Main_<C>` harness. In unbound mode, the wrapper calls them sequentially (`A`, then `B`, …). In bound mode, it uses a nondet switch to pick one per run. | Every public/external function of every contract, called from arbitrary order, arbitrary msg.sender/value. | **OVER** — all state-space false positives of `--contract` apply, compounded across contracts. |
| `esbmc contract.sol --contract C` | Constructor of `C` runs first, `C`'s state vars reflect constructor output; other contracts' state stays default. | `_ESBMC_Main_C()` = constructor + `while (nondet_bool) _ESBMC_Nondet_Extcall_C()`. Each loop iteration is one transaction; every public/external method of `C` is dispatched nondet-guarded inside the loop body. | Per-transaction invariants on `C` under an arbitrary sequence of externally-visible calls after construction. | **OVER** (harness over-approximates ordering, msg.sender/value, return values of outbound calls). See ledger row 7. VERIFICATION SUCCESSFUL is sound for safety under the single-transaction model; VERIFICATION FAILED may be a spurious call-ordering or nondet-context counterexample. |
| `esbmc contract.sol --contract C --focus-function f` | Constructor of `C` runs first, same as `--contract C`. | Same `_ESBMC_Main_C` harness, but the internal nondet dispatch loop of `_ESBMC_Nondet_Extcall_C` **filters** every public/external method except `f`. `f` is still called from inside the loop (so re-entry is possible), construction still happens. Not a state-space change — purely a verification-cost optimisation. | Safety of `f` reached from a *constructed* contract state, where the only callable entry in the nondet loop is `f` itself. | **OVER, identical strength to `--contract C` for `f`'s paths**. Sound for safety. VERIFICATION SUCCESSFUL is sound. VERIFICATION FAILED is real if it does not rely on a call-context over-approximation (ledger row 7). Other functions of `C` are never exercised, so bugs in them are invisible by construction — that is an intentional scoping decision, not an unsoundness of `f`'s proof. |
| `esbmc contract.sol --contract C --function f` | **Nondet (fully symbolic)** — the constructor does NOT run, no state initialiser applies, no harness is built. | `f` is made the GOTO entry point and called once with nondet parameters. State variables start at unconstrained nondet of their declared type. | Safety of `f` over *every conceivable* initial state of `C`, regardless of whether such a state is reachable from the constructor. | **OVER on state space (sound for safety)** — see the call-out below. This is the only mode where VERIFICATION SUCCESSFUL gives a stronger guarantee than the other modes, at the cost of possible false-positive counterexamples. |

##### ⚠ `--function` mode soundness call-out (important)

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
files (see `### --function Mode Semantics` below for the enforcement
note and the rationale): regression tests must exercise the full
constructor + dispatch harness, otherwise adversarial tests
degenerate into trivial benchmarks and the frontend / solver
weaknesses they were designed to expose stop showing up.

##### `--function` vs `--focus-function` — pick one

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

#### External-call dimension

`--bound` and its absence (unbound) are orthogonal to the entry-point
choice and can be combined with any entry-point mode.

| Flag | `addr.call(data)` / cross-contract dispatch | State shared across contracts? | Soundness posture (ext-call dimension) |
|---|---|---|---|
| default (unbound) | `get_unbound_expr()` re-enters the *current* contract's nondet dispatch and returns a nondet `(bool, bytes)` tuple. Target address is **ignored**. | No — each contract verified independently. | **OVER** on reentrancy and on return values. Safe for single-contract safety; cross-contract invariants are invisible. |
| `--bound` | `get_bound_low_level_call()` emits an if-then-else chain over `contractNamesList`; address match dispatches to that contract's `_ESBMC_Nondet_Extcall_<target>`. No match → hard `false` return (trusted-closed-world). | Yes — static instances share storage; `_ESBMC_bind_cname` tracks concrete type per address. | **UNDER** relative to real EVM (unknown addresses cannot succeed), **sound under the trusted closed-world assumption**. Strictly more precise than unbound *for properties involving known callees*; strictly less precise for attacker-controlled addresses. |

Both modes currently share the same low-level-call semantic gaps
listed in §D (no storage context swap for `delegatecall`, no
read-only enforcement for `staticcall`).

#### Composition cheat sheet

| Command | Entry posture | Ext-call posture | Typical use |
|---|---|---|---|
| `esbmc c.sol --contract C` | --contract (OVER, sound) | unbound (OVER) | Default single-contract verification |
| `esbmc c.sol --contract C --bound` | --contract | bound (UNDER, trusted) | Single-contract that makes cross-contract calls, when callee shapes matter |
| `esbmc c.sol --contract A --contract B --bound` | multi-contract | bound | Multi-contract interaction (token + exchange) |
| `esbmc c.sol --contract C --focus-function f` | focus-function (same as --contract for `f`) | unbound | Cut verification cost by skipping unrelated functions |
| `esbmc c.sol --contract C --function f` | **nondet state** (OVER on state) | unbound | Over-approximate safety proof for a pure/view function. **Interactive only — banned in regression tests.** |
| `esbmc c.sol` (no `--contract`, no `--function`) | all declared contracts | depends on `--bound` | Verify every contract declared in the file; useful for quick smoke tests. |

#### Interpreting results

- **VERIFICATION SUCCESSFUL** under `--contract` / `--focus-function` /
  `--function` is a safety proof *within the frontend's approximation
  ledger*. Consult that ledger (previous section) to understand what
  classes of bugs are outside the model.
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

### Address Binding Modes (`--bound` / default unbound)

ESBMC supports two verification strategies for multi-contract Solidity programs, controlled by the `--bound` flag. The default is **unbound** mode.

```sh
# Unbound mode (default): external calls modeled as nondet (over-approximate)
esbmc contract.sol --contract A --contract B --unwind 5

# Bound mode: contracts linked together as a complete system
esbmc contract.sol --contract A --contract B --bound --unwind 5
```

#### Unbound Mode (default)

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

#### Bound Mode (`--bound`)

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
- Address binding: `x._ESBMC_bind_cname = "ContractName"` assigned at `new` expressions
- Contract instances: share state, cross-contract interactions modeled
- Polymorphism/inheritance dispatch: supported through binding
- Best for: multi-contract interaction verification (e.g., token + exchange)

**⚠ Low-level call accuracy gaps (both modes):** See §D below for the specific semantic gaps that survived into bound mode — in particular, `delegatecall` does not swap storage context and `staticcall` does not enforce read-only.

#### Implementation Details

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

### `--function` Mode Semantics

`--function funcName` verifies a single function in isolation, under **arbitrary initial state**. All state variables are initialized to nondeterministic (symbolic) values, NOT to their declared initializers or constructor-assigned values. This is by design: `--function` mode checks whether the function is correct for **all possible** contract states, not just post-constructor states.

**Implications:**
- `x = 42; assert(x == 42)` where `x` is a state variable will **fail** because nondet state can be re-entered between assignment and assertion
- `--function` is best for verifying **function-local** properties (pure/view functions, local variable logic) and for **over-approximate** analysis where any input state is valid
- `constant` state variable values are only available in `--contract` mode (where the initializer runs)
- To verify state-dependent properties, use `--contract ContractName` instead (which runs the constructor first and then dispatches all public functions); to narrow verification to a single function without dropping the constructor, use `--focus-function` (below)

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

### `--focus-function` Mode Semantics

`--focus-function funcName` narrows verification to a single function while keeping the full contract harness:

- The constructor runs, state variables get their declared initializers, inheritance linearization applies, and the whole `_ESBMC_Main_<C>` harness is built.
- Only the named function is verified: the nondet dispatch loop inside `_ESBMC_Nondet_Extcall_<C>` filters out every public/external method except `funcName`, so the BMC engine never explores paths that call other functions on the target contract. This is a pure verification-cost optimization; no state is fabricated as nondet.

**Requirements:**
- Requires `--contract <name>` to pick the target contract when the source declares more than one contract. If the source has exactly one (non-library, non-interface) contract, `--contract` is auto-inferred.
- `funcName` must be a `public` or `external` method on the target contract (not the constructor, not `receive`/`fallback`).
- Works with both `--bound` and `--unbound`. In `--bound` mode, other contracts reached via cross-contract calls still dispatch their full public surface — the filter only applies to the focus target contract's own harness.

**Implementation:** the filter lives in `solidity_convert_constructor.cpp:get_unbound_function()` inside the `for (const auto &method : methods)` loop: when `focus_func` is set and `c_name == *tgt_cnt_set.begin()`, methods whose name differs from `focus_func` are skipped before the if-branch is emitted. Validation (contract disambiguation, function existence) happens in `solidity_convert.cpp:convert()` right after `populate_auxiliary_vars()`.

**Tests:** see `focus_function_1`, `focus_function_2`, `focus_function_4` for: focus-function isolates `f` after construction (pass), full harness exposes a `g`-before-`f` violation that focus-function hides (fail), unbound single-contract auto-inference (pass).

#### Performance Considerations

- **Unbound** is significantly faster for single-contract verification since it avoids cross-contract symbolic exploration.
- **Bound** mode can be very slow when contracts have complex interactions (e.g., `transfer_send_2` test: >1200s timeout with `--bound`).
- When using `--bound` with `--contract A --contract B`, all contracts are instantiated and their constructors run, which increases the state space.

### Implementation (auto-solc)

Auto-solc is implemented in `solidity_language.cpp`:
- `find_solc()`: searches for solc binary in priority order
- `get_solc_version()`: extracts version string from `solc --version`
- `invoke_solc()`: runs `solc --ast-compact-json` to temp file, displays errors on failure
- `parse()`: detects `.sol` vs `.solast` input, auto-invokes solc for `.sol` files
- `.sol` extension registered in `langapi/mode.cpp` alongside `.solast`
- `esbmc_parseoptions.cpp`: auto-sets Solidity options (no-align-check, force-malloc-success) when `.sol` file detected in positional args

## Solidity Operational Models (c2goto)

Solidity built-in types, variables, and functions are implemented as C operational models in `src/c2goto/library/solidity/`. These are pre-compiled into a **separate goto binary** (`sol64.goto`) via the c2goto pipeline and embedded into the ESBMC binary. At runtime, `add_cprover_library()` loads from `sol64_buf` (not the full `clib64`) for fast symbol loading.

| File | Content |
|------|---------|
| `solidity_types.h` | Type definitions: `int256_t`, `uint256_t`, `address_t` via `_BitInt(256)`, `sol_llc_ret` struct |
| `solidity_blockchain.c` | Block/tx/msg global variables, `blockhash`, `blobhash` (EIP-4844), `gasleft`/`gasConsume` — all nondet (over-approximate) |
| `solidity_builtins.c` | Integer exponentiation (`sol_pow_uint`), modular arithmetic (`addmod`/`mulmod` with 512-bit precision), `llc_nondet_bytes`, `selfdestruct` |
| `solidity_crypto.c` | Cryptographic hash functions: keccak256, sha256, ripemd160, ecrecover (deterministic bijective abstraction) |
| `solidity_abi.c` | ABI encoding/decoding models: `abi_encode`, `abi_encodePacked`, `abi_encodeWithSelector`, `abi_encodeWithSignature`, `abi_encodeCall` (identity), `abi_decode` (nondet) |
| `solidity_bytes.c` | `BytesStatic`/`BytesDynamic` structs, 60+ byte manipulation functions, `bytes_dynamic_concat` (pass-by-value for variadic nesting) |
| `solidity_mapping.c` | Mapping data structures (`_ESBMC_Mapping`, `mapping_t`, and `_fast` variants) |
| `solidity_array.c` | Dynamic array tracking: push, pop, length, arrcpy |
| `solidity_units.c` | Ether/time unit conversions (wei, gwei, ether, seconds, days, etc.) |
| `solidity_string.c` | String operations (`string_concat` for variadic concat), integer-to-string, hex conversion |
| `solidity_address.c` | Address management, contract object tracking |
| `solidity_misc.c` | Min/max (`_min`/`_max`), `_creationCode`/`_runtimeCode`/`_interfaceId` (nondet), reentrancy check, state initialization |

### c2goto Architecture

- **Build pipeline**: Solidity `.c` files → `c2goto --64 --fixedbv` → `sol64.goto` (524KB) → `flail.py` → `sol64.c` (byte array) → linked into esbmc binary
- **CMake auto-glob**: `file(GLOB_RECURSE c2goto_solidity_files ... "library/solidity/*.c")` in `src/c2goto/CMakeLists.txt:146-148` — any new `.c` file in the `library/solidity/` directory is automatically compiled into `sol64.goto`. No CMakeLists.txt changes needed when adding new model files. However, function names must be registered in `solidity_c_models` in `cprover_library.cpp`.
- **Separate from clib64**: Solidity models are NOT compiled into `clib64.goto`. This avoids reading the full 1.9MB clib when only Solidity symbols are needed.
- **Loading path**: `add_cprover_library()` in `cprover_library.cpp` detects `language->id() == "solidity_ast"` and reads from `sol64_buf` instead of `clib64_buf`. No whitelist filtering needed since sol64 contains only Solidity symbols.
- **Whitelist**: The `solidity_c_models` vector in `cprover_library.cpp` lists all function names that should be extracted from sol64. New functions must be added here.
- **Build flag**: `ENABLE_SOLIDITY_FRONTEND=ON` required for CMake to compile Solidity models and generate sol64.

### Symbol Naming (C vs C++ frontend)

The c2goto pipeline compiles Solidity models as **C** (not C++). This affects struct tag naming:

| C frontend (c2goto) | C++ frontend (old template) |
|---------------------|-----------------------------|
| `tag-struct BytesPool` | `tag-BytesPool` |
| `tag-struct _ESBMC_Mapping` | `tag-_ESBMC_Mapping` |
| `tag-struct sol_llc_ret` | `tag-sol_llc_ret` |

The converter uses two prefixes:
- `prefix = "tag-"` — for Solidity-defined structs (created by the converter itself)
- `lib_prefix = "tag-struct "` — for c2goto library structs (BytesPool, BytesDynamic, BytesStatic, _ESBMC_Mapping, mapping_t, sol_llc_ret)

### typecheck() Flow

```
1. convert_intrinsics(new_context)    — Parse minimal C++ file for ESBMC built-in symbols
                                        (nondet_bool, nondet_uint, __ESBMC_assert, etc.)
2. add_cprover_library(new_context)   — Load sol64 operational models (already adjusted)
3. converter.convert()                — Convert Solidity AST to ESBMC IR
4. clang_cpp_adjust(new_context)      — Adjust converter-generated code
   (sol64 function bodies are saved before adjust and restored after,
    since they were already adjusted by c2goto's clang_c_adjust)
5. c_link(context, new_context)       — Merge into final context
```

### Known Limitations

- 6 THOROUGH tests fail due to C/C++ frontend differences in struct bit-field layout and `fixedbv` typecast handling. All CORE tests pass.
- The `sol_llc_ret.x` field uses `unsigned int` (not `bool`) to avoid C/C++ bool representation mismatch.

### Resolved Bugs (2026-03-31 / 2026-04-15)

Bugs 1-5 fixed 2026-03-31 (targeted regression work). Bugs 6-9 fixed
2026-04-15 while stress-testing the frontend on 1inch swap-vm and
fusion-protocol. Bug 10 fixed 2026-04-16 (KNOWNBUG promotion). Summary:

| Bug | Description | Root cause | Fix location |
|-----|-------------|-----------|--------------|
| **1** | Sub-256-bit overflow check missed `uint8`/`uint16` overflow | C integer promotion widens to `signed int` before arithmetic; `overflow2tc` checks at 32-bit width | `goto_check.cpp`: narrowing cast check + narrowing assignment check for `.sol` files; suppressed inside `unchecked` blocks |
| **2** | Large constants like `10**36` silently evaluated to 0 | solc truncates `typeString` with `"..."` notation; `string2integer()` returns 0 for non-alphanumeric input | `solidity_grammar.cpp:785`: skip `LiteralWithRational` when `typeString` contains `"..."`, fall through to `BO_Pow` BigInt path |
| **3** | `unchecked { }` blocks had no effect on overflow checking | `UncheckedBlock` AST nodes parsed as normal `Block` | `solidity_convert_stmt.cpp`: tag locations with `#sol_unchecked`; `goto_check.cpp`: skip overflow checks when tag present |
| **4** | `a ** b` (non-constant) crashed with "unexpected typecast to fixedbv" | Frontend called `double pow()` (floatbv) but sol64.goto compiled with `--fixedbv` → type mismatch | `solidity_builtins.c`: new `sol_pow_uint(uint256_t, uint256_t)` integer pow; `solidity_convert_expr.cpp`: call `sol_pow_uint` instead of `pow` |
| **5** | Z3 sort mismatch on mapping struct fields | c2goto padding shifted struct component indices; frontend used hardcoded `at(1)` | `solidity_mapping.c`: `__attribute__((packed))`; `solidity_convert_decl.cpp`: name-based component lookup |
| **6** | Multi-file import cycle silently drops files (e.g. `ISwapVM.sol ↔ MakerTraits.sol`) causing downstream "failed to find reference AST node" | `topological_sort()` uses Kahn's algorithm, which leaves cycle-participating nodes stuck at `in_degree > 0` and never emitted | `solidity_convert.cpp::topological_sort`: after main Kahn loop, force-drain remaining nodes by repeatedly picking the lowest-residual-`in_degree` node (commit `4461578016`) |
| **7** | Interface-nested `struct`/`enum`/`error`/`event` unresolved when a round-1 library references them as a return type (core dump on `IB.Order memory` in library signatures) | Interfaces only processed in round 2 of `convert()`; round-1 libraries look up nested types that haven't been registered yet | `solidity_convert.cpp::convert`: pre-round walk registers interface-nested type children before round 1; `solidity_convert_decl.cpp::get_noncontract_defition`: interface branch recurses into nested decls (commit `db74a7652c`) |
| **8** | `TypeMemberCall` crash on function reference used as r-value inside an inline function-pointer array (e.g. `[_self, Base._b, _self]`) | `TypeMemberCall` handler asserted `args_json.contains("arguments")`; when the parent is a `TupleExpression`/inline array it has `components`, not `arguments` | `solidity_convert_expr.cpp` (line ~691): detect non-call-target use via `find_last_parent`, emit opaque `void*` typecast tagged `#sol_func_ptr` mirroring the existing `super.f` r-value lowering (commit `53affdd290`) |
| **9** | **SOUNDNESS** — tuple-LHS `(x, y) = cond ? (a, b) : (b, a);` was silently dropped, leaving x/y at default zero and producing unsound `VERIFICATION SUCCESSFUL`. Affected SwapVM/Aqua/Limit routers (6 statements across 3 files) and any contract using the common binary-reorder idiom | `construct_tuple_assigments` in the `TUPLE_RETURNS` branch only understood FunctionCall-shaped RHS (`.expression` callee lookup); Conditional RHS hit `log_error("cannot locate function call in RHS"); return true` whose error bit was ignored by callers → statement became a no-op | `solidity_convert_tuple.cpp`: detect Conditional RHS before function-call extraction. Both branches TupleExpression → decompose element-wise into per-slot ternaries `lhs[i] = cond ? t_comps[i] : f_comps[i]`; other shapes → fall back to existing `rhs_is_nondet` sound over-approximation (commit `106c0e9c22`) |
| **10** | Inline array of function pointers with constant index `[f, g][0](x)` fell through to nondet indirect-call path, losing the actual callee resolution | `get_call_expr` resolved the callee JSON by navigating `expr["expression"]`; an `IndexAccess` on a `TupleExpression(isInlineArray)` has no `referencedDeclaration`, so it hit the `[APPROX: UNDER]` nondet fallback | `solidity_convert_expr.cpp::get_call_expr`: after paren-unwrap, detect `IndexAccess` on inline array literal with constant `Literal` index; redirect `callee_p` to `components[k]` in the original AST so the resolved element goes through normal call dispatch. KNOWNBUG `stress_libsol_fntype_inline_array_value_call` promoted to CORE |

### Remaining Known Issue

- **mapping_13** (THOROUGH): NULL pointer dereference check in `map_get_raw` library function (`solidity_mapping.c:29`). ESBMC's pointer analysis cannot always infer that a pointer is non-NULL from a `while(ptr)` loop guard. Unrelated to struct layout.

- **`revert` / `require` do not roll back state.** The frontend models Solidity `revert`/`require`/failed `transfer()` as `__ESBMC_assume(false)`, which only marks the current path infeasible. Real EVM semantics roll back all state changes made in the current call (and sub-calls) before the revert. SSA assignments emitted before the `assume` are still in the constraint set.
  - In pure single-path BMC the infeasibility propagates correctly, so unreachable code after the revert is not exercised.
  - **Completeness gap**: `try/catch` bodies that depend on state having been rolled back cannot be reasoned about — the catch arm is also pruned along with the try arm.
  - **Soundness gap (theoretical)**: partial state changes that would be rolled back in reality remain recorded in SSA. If another path of the same harness iteration observes the shared static contract instance, it can see "pre-revert" modifications that cannot actually occur on chain, leading to spurious counterexamples.
  - Correct fix requires snapshot/restore: snapshot touched state on function entry, write back on revert. Deferred — the cost of snapshotting every potential revert boundary is non-trivial.

### Design Notes

- **No floating-point in Solidity pipeline:** sol64.goto is compiled with `--fixedbv` (CMakeLists.txt:249), but the Solidity frontend no longer generates any float/fixedbv types. The `--fixedbv` runtime flag is unnecessary for Solidity and should NOT be forced on — it has no performance benefit and risks side effects in shared code paths.
- **`_ExtInt` struct alignment:** `_ExtInt(N)` types in C structs require bitfield notation (`: N`) to avoid `ext_int_pad` name collisions from ESBMC's padding logic (`padding.cpp:116-131`). Use `__attribute__((packed))` to prevent alignment padding on top of bitfields.

## Approximation Ledger (soundness & completeness trade-offs)

**Purpose.** This section records every deliberate abstraction in the
Solidity frontend that sacrifices soundness or completeness. Each entry
documents (1) where the approximation lives, (2) whether it is
over-approximate or under-approximate, (3) what kinds of false positives
or false negatives it can produce. Code sites carry an `[APPROX: OVER]`
or `[APPROX: UNDER]` marker that mirrors the entries here — grep for
`\[APPROX:` to find every in-source warning.

### Terminology

- **Over-approximation (sound, incomplete)**: the model admits *more*
  behaviours than the real system. Counterexamples may be spurious
  (false positives). Proofs of safety carry over to the real system.
- **Under-approximation (unsound, may be complete)**: the model admits
  *fewer* behaviours. Bugs reachable only in the missing behaviours are
  not detected (false negatives). Counterexamples are real bugs.

"Sound for safety" = no real bug is missed. "Sound for equality" = the
abstraction is deterministic and injective; reasoning about identity
holds but reasoning about bit patterns does not.

### Ledger

| # | Area | Site | Direction | Rationale | False positives | False negatives |
|---|------|------|-----------|-----------|-----------------|-----------------|
| 1 | Inline assembly | `solidity_convert_stmt.cpp::InlineAssemblyStatement` | OVER | Assembly body never executed; every externally referenced variable (including `.slot`/`.offset` state) is havoc'd to nondet of its declared type. | Assembly-enforced invariants on the havoc'd variable cannot be verified. | None for reads. Writes that the assembly *would have made but we skipped* are reflected as havoc, so no bug is hidden. |
| 2 | Crypto hashes | `solidity_crypto.c` (keccak256/sha256/ripemd160/ecrecover) | OVER + UNDER | Identity-like bijective abstraction (`~x`, `~(x+1)`, …). Deterministic, injective, distinct families. | Properties of the form `keccak256(0) == 0xc5d2...` (specific hash bits) cannot be proved. | Preimage / collision / signature-forgery properties cannot be refuted; `ecrecover` ignores `(v,r,s)`. |
| 3 | ABI encode | `solidity_abi.c` (`abi_encode*`, `abi_encodeCall`, `abi_encodeWithSelector`, `abi_encodeWithSignature`, `abi_encodePacked`) | OVER + UNDER | Identity on the first argument; remaining arguments evaluated for side effects only. | Packed byte-layout properties (selector presence, delimiters) cannot be verified. | Two distinct multi-argument encodings that share the same first argument look equal → function-selector dispatch checks may report spurious success. |
| 4 | ABI decode | `solidity_abi.c::abi_decode` | OVER | Returns nondet uint256_t. | None — every concrete value is admitted. | Round-trip `abi.decode(abi.encode(x)) == x` is NOT provable (decoder is detached from encoder). |
| 5 | `msg` / `tx` / `block` variables | `solidity_blockchain.c` | OVER | All fields nondet uint256_t / address_t on every access. | Monotonicity of `block.number` / `block.timestamp` across reads, relationships between `msg.sender` and contract identity. | None for safety. |
| 6 | `blockhash` / `blobhash` | `solidity_blockchain.c` | OVER | Nondet uint256_t. | Properties of specific block hashes. | None for safety. |
| 7 | Entry-harness unbound dispatch | `solidity_convert_constructor.cpp::get_unbound_function` | OVER | Each public/external method called inside a nondet-guarded branch with nondet arguments, nondet msg.sender/value. | Order-of-call invariants ("init before transfer") fail spuriously; payable assertions like `assert(msg.value > 0)` fire on entries that solc's original test exercised only with value > 0 (e.g. `stress_libsol_fntype_inline_array_value_call`). | Multi-transaction bugs whose reachability depends on state surviving across *transactions* are not explored unless `--multi-transaction` mode is used. |
| 8 | External call re-entry (unbound mode) | `solidity_convert_contract.cpp::get_unbound_expr` + `solidity_convert_expr.cpp` low-level call path | OVER | `addr.call` / `delegatecall` / `staticcall` return `(nondet bool, nondet bytes)`; side-effect re-enters the *current* contract's nondet dispatch; callee address is ignored. | Reentrancy paths are explored unconditionally even for callees that would revert, causing extra counterexamples for properties that assume specific callee behaviour. | Cross-contract effects on the actual callee address are invisible. |
| 9 | Calldata bytes length | `solidity_convert_call.cpp::assign_param_nondet` + `solidity_builtins.c::llc_nondet_bytes` | OVER | Harness-generated `bytes calldata` parameters flow through `llc_nondet_bytes()` which assumes `length ∈ [32, 1024]` and `initialized == 1`. | None for small-index reads. | OOB reads at index > 1024 are not caught; properties that depend on calldata being shorter than 32 bytes cannot be modelled. |
| 10 | Calldata array-of-bytes | `solidity_convert_expr.cpp::get_index_access_expr` (calldata `bytes[] / bytes[N]` element read) | OVER | When the base array is calldata (`#sol_data_loc == "calldata"`) and the element type is `BYTES_DYN`, `a[i]` is replaced with a fresh `llc_nondet_bytes()` — a BytesDynamic with `length ∈ [32, 1024]`, `initialized == 1`. Storage / memory `bytes[] x;` keeps the precise index_exprt path. | None for small-index reads of calldata element content. | Repeated reads of the same `a[i]` are **independent samples** (no `a[i] == a[i]` invariant); OOB at index >1024 inside a calldata element is not caught. `string[] calldata` elements still stay on the precise path because `string` lowers to `char*` and type-mismatches `BytesDynamic` — those tests remain `KNOWNBUG`. |
| 11 | Function-reference identity | `solidity_convert_expr.cpp::MemberAccess` (used-as-value) | OVER (for identity) + UNDER (for content) | `this.f` as a value lowers to `(void*)(fn_id + 1)` — stable, distinct per callee, so `this.f == this.f` and `this.f != this.g` hold. | None for identity comparisons. | Indirect calls through fn-ptrs never execute the real body; `solidity_convert_call.cpp` substitutes a nondet return of the declared type. Bugs inside functions reachable only via an indirect call are invisible. |
| 12 | Indirect callees without `referencedDeclaration` | `solidity_convert_expr.cpp::get_call_expr` | UNDER | Ternary on fn refs, `IndexAccess` on a fn-ptr mapping, etc. → call result is nondet of the declared return type; the real body is never invoked. | None (the nondet return covers every value). | Side effects and bugs in the target function are not observed. |
| 13 | Function-typed r-value arguments | `solidity_convert_call.cpp` (`t_function_internal_` / `t_function_external_` branch) | UNDER | Passing a fn ref as an argument substitutes an opaque nondet pointer. The callee will dispatch it via #12 above. | None. | See #12. |
| 14 | IndexRangeAccess slices (`b[s:e]`) | `solidity_convert_expr.cpp::get_index_range_access_expr` | OVER | Slice is a fresh nondet value of the result type; no link to parent array, no constraint `s <= e <= length`. | Slice-range bounds assertions cannot be verified. | None for safety. |
| 15 | `type(I).interfaceId` / `type(C).creationCode` / `type(C).runtimeCode` | `solidity_misc.c::_interfaceId`, `_creationCode`, `_runtimeCode` | OVER | Nondet bytes4 / bytes. | Interface-id dispatch checks `type(I).interfaceId == 0x...` cannot be proved. | None for safety. |
| 16 | `revert` / `require` / failed `.transfer()` | `solidity_convert_stmt.cpp` (emits `__ESBMC_assume(false)`) | UNDER for state, OVER for control flow | Revert marks path infeasible but does NOT roll back state mutations already recorded in SSA. | Another harness iteration can observe "pre-revert" modifications to the shared static contract instance — spurious cross-iteration bugs possible. | `try/catch` bodies that rely on state having been rolled back are pruned with the try arm. |
| 17 | Uninitialized internal function pointers | (no explicit code) inline assembly read of internal fn-ptr tag | OVER (via inline assembly havoc) | Reading `z := t` in assembly havocs `z`; the real legacy-codegen panic tag / yul 0-init distinction is not modelled. | Tests that assert a specific tag value (`z != 0` on legacy, `z == 0` on yul) fail. | `stress_libsol_uninit_fnptr_*` cannot be fixed under a single model. |
| 18 | Multi-inheritance linearisation | `solidity_convert_inheritance.cpp` | sound-so-far | C3 linearisation follows solc; no known false positives or negatives. | — | — |
| 19 | Internal pool for `bytes` / `string` | `solidity_bytes.c::BytesPool` | OVER | Single monotonically-growing pool per contract instance; `free` is a no-op. | Pool-capacity exhaustion on very long runs is unreachable (pool is practically infinite in model). | None for bytes semantics. |
| 20 | `selfdestruct` | `solidity_builtins.c::selfdestruct` | UNDER | Modelled as `exit(0)` — terminates the harness path. Ether transfer and subsequent state reachability are not modelled. | None. | Post-selfdestruct behaviour of the destroyed contract (address reuse, CREATE2 re-deployment) is not explored. |
| 21 | Free-function `bytes memory` return of a string literal | `solidity_convert_stmt.cpp::ReturnStatement` (free-function path) | OVER | When a free function (declared outside any contract, e.g. `using { f } for T;`) returns a string literal as `bytes memory`, the conversion from `array of signed char` to `BytesDynamic` needs a dynamic pool, but a free function has no containing contract to supply one. The return value is replaced with `llc_nondet_bytes()` — a BytesDynamic with `length ∈ [32, 1024]` and `initialized == 1`. Contract-member functions keep the precise `bytes_dynamic_from_string` path. | None for length-range / existence checks. | The actual byte content of the returned literal is discarded — two successive calls return independent samples (no `f() == f()` invariant). Content equality / hash-match tests across calls would fail spuriously; `stress_func_ptr_longdata_1` escapes this by using `keccak256(a) == keccak256(b)` which is already nondet under the abstract keccak model. |

### How to use this ledger

- **Before claiming "verification successful"** in a security review,
  check which approximations the contract relies on. If the property
  depends on a column marked "False positives" → the proof is real.
  If it depends on a column marked "False negatives" → the proof is
  NOT sound; re-verify with a tighter model or manual reasoning.
- **Before filing a bug on spurious counterexamples**, check this
  ledger. A counterexample rooted in row 7 (nondet msg.value), row 5
  (non-monotonic block numbers) or row 16 (cross-iteration state) is
  expected behaviour, not a bug.
- **Adding a new approximation**: drop an `[APPROX: OVER|UNDER]` marker
  at the code site and append a row here with the same rationale wording.

### In-source markers

Every approximation above has a matching code comment; `rg '\[APPROX:'`
finds the canonical list. Table rows and code comments MUST be kept in
sync. If you remove an approximation, delete the marker *and* the row.

## Solidity Language Support Audit (2026-03-30)

Comprehensive audit against Solidity 0.8.x official documentation. Minimum supported version: 0.5.0 (recommended: 0.8.x).

### Fully Supported

| Category | Features |
|----------|----------|
| **Value types** | `bool`, `uint8`-`uint256`, `int8`-`int256`, `address`/`address payable`, `string`, `bytes1`-`bytes32`, `bytes` (dynamic) |
| **Composite types** | `struct` (nested, with arrays), `enum`, fixed arrays `T[N]`, dynamic arrays `T[]` (push/pop/length), multi-dimensional arrays |
| **Mapping** | `mapping(K => V)`, nested `mapping(K1 => mapping(K2 => V))`, mapping-in-struct, and `mapping(K => V)[]` (array of mappings) — modeled via (nested) infinite SMT arrays; struct mapping fields are lifted to global arrays; mapping arrays use auxiliary `_mapping_arr_len` variable for push/pop |
| **Operators** | All arithmetic (`+`,`-`,`*`,`/`,`%`,`**`), bitwise, comparison, logical, compound assignment (`+=` etc.), prefix/postfix `++`/`--`, ternary `?:`, `delete` |
| **Control flow** | `if`/`else`, `for`, `while`, `do-while`, `break`, `continue`, `return` (including multi-value via tuples) |
| **Contract core** | Contract/library/interface definitions, functions (regular/constructor/receive/fallback), free functions, state variables, visibility (`public`/`private`/`internal`/`external`), state mutability (`pure`/`view`/`payable`) |
| **Modifiers** | Definition, parameters, placeholder `_` expansion, chaining |
| **Events** | `event` definition, `emit` (modeled as function calls) |
| **Custom errors** | `error` definition, `revert CustomError(...)` (Solidity 0.8.4+) |
| **Inheritance** | Multiple inheritance with C3 linearization, `virtual`/`override`, abstract contracts, interfaces |
| **Libraries** | Library contracts, library function calls |
| **Import** | Multi-file with topological sort (17 tests) |
| **Globals** | `msg.sender`/`.value`/`.sig`/`.data`, `block.number`/`.timestamp`/`.coinbase`/`.difficulty`/`.gaslimit`/`.chainid`/`.basefee`/`.prevrandao`/`.blobbasefee`, `tx.origin`/`.gasprice` |
| **Built-ins** | `require()`, `assert()`, `revert()`, `keccak256()`, `sha256()`, `ripemd160()`, `ecrecover()`, `addmod()`, `mulmod()`, `gasleft()`, `selfdestruct()`, `blobhash()`, `string.concat()` (variadic), `bytes.concat()` (variadic), `super.method()` |
| **ABI encoding** | `abi.encode()`, `abi.encodePacked()`, `abi.encodeWithSelector()`, `abi.encodeWithSignature()`, `abi.encodeCall()` |
| **Address members** | `.balance`, `.code`, `.codehash`, `.transfer()`, `.send()`, `.call()`, `.delegatecall()`, `.staticcall()` |
| **Type info** | `type(T).min`, `type(T).max`, `type(C).name`, `type(C).creationCode`, `type(C).runtimeCode`, `type(I).interfaceId` (nondet bytes4) |
| **Units** | Ether (`wei`/`gwei`/`ether`), time (`seconds`/`minutes`/`hours`/`days`/`weeks`) |
| **Unchecked** | `unchecked { ... }` blocks suppress overflow/underflow checks (Solidity 0.8+ semantics) |
| **Verification** | Overflow/underflow (all integer widths including sub-256-bit), division-by-zero, reentrancy detection (mutex-based), bound/unbound address modes, whole-contract verification |

### Known Limitations and Deficiencies (detailed audit 2026-04-01)

#### A. Crypto Functions — Deterministic Bijective Abstraction (2026-04-04)

`keccak256`, `sha256`, `ripemd160`, `ecrecover` are modeled as **deterministic bijective functions** using simple bitvector transformations. Each function uses a distinct transformation to ensure cross-function outputs differ:

| Function | Model (`solidity_crypto.c`) | Properties |
|----------|------|------------|
| `keccak256(x)` | `return ~x;` | Functional consistency ✓; bijective (zero collisions) ✓ |
| `sha256(x)` | `return ~(x+1);` | Functional consistency ✓; bijective ✓; differs from keccak256 |
| `ripemd160(x)` | `return (address_t)(~(x+2));` | 256→160 bit truncation after transform |
| `ecrecover(hash,v,r,s)` | `return (address_t)(~hash);` | Ignores v/r/s — no signature verification |

#### A2. Modular Arithmetic — 512-bit Arbitrary Precision (2026-04-04)

`addmod` and `mulmod` use a 512-bit intermediate type (`_BitInt(512)`) to implement arbitrary-precision arithmetic per the Solidity spec (no wrap at 2^256):

| Function | Model (`solidity_builtins.c`) | Properties |
|----------|------|------------|
| `addmod(x,y,k)` | `(uint512_t)x + (uint512_t)y) % (uint512_t)k` | Correct for all inputs ✓ |
| `mulmod(x,y,k)` | `(uint512_t)x * (uint512_t)y) % (uint512_t)k` | Correct ✓; KNOWNBUG: MAX\*MAX crashes ESBMC constant evaluator (SIGFPE) |

`abi.encode*` functions are modeled as **identity functions** (`return x;`) in `solidity_abi.c` so that `keccak256(abi.encodePacked(x))` is deterministic in `x`. Multi-argument `abi.encodePacked(a, b, c)` only captures the first argument; the rest are evaluated but discarded. `abi.decode` is modeled as **nondet** (over-approximation).

| Function | Model (`solidity_abi.c`) | Status |
|----------|------|--------|
| `abi.encode(x)` | `return x;` (identity) | ✓ Working — 3 regression tests |
| `abi.encodePacked(x)` | `return x;` (identity) | ✓ Working — 3 regression tests |
| `abi.encodeWithSelector(sel, x)` | `return sel;` (identity, captures 1st arg = selector) | ✓ Working — 3 CORE regression tests |
| `abi.encodeWithSignature(sig, x)` | `return sig;` (identity, captures 1st arg = signature) | ✓ Working — 3 regression tests |
| `abi.encodeCall(fn, (x))` | `return fn;` (identity) | ✓ Working — 3 CORE regression tests (interface/function pointer accepted by converter) |
| `abi.decode(data, (T))` | `uint256_t result;` (nondet) | ✓ Working — 3 CORE regression tests (type tuple accepted by converter) |

**Properties:**
- **Functional consistency**: `keccak256(x) == keccak256(x)` always holds ✓
- **Injectivity**: `x != y → keccak256(x) != keccak256(y)` always holds ✓
- **String equality via hash**: `keccak256(abi.encodePacked(s1)) == keccak256(abi.encodePacked(s2))` ↔ `s1 == s2` ✓
- **O(1) SMT cost**: single BV NOT operation per hash call
- **Limitation**: concrete hash values are not computed; `assert(keccak256(0) == 0xc5d2...)` is not provable
- **Limitation**: `abi.decode` is nondet — decoded values are unconstrained; `encode(x) → decode → y` does not guarantee `y == x`. Guarded round-trips still work (e.g. `require(decoded > 0); assert(decoded > 0);`); see `abi_decode_1/2/3` tests.

#### A3. Dynamic Array State Variables — SMT Array Model (2026-04-09)

State-variable dynamic arrays (`uint[] public items`) are now modeled as **infinite SMT arrays + auxiliary length variable** instead of the previous pointer + C model (`malloc`/`realloc`). This enables the solver to track element values through `push()` operations:

```solidity
items.push(100);
assert(items[0] == 100); // VERIFICATION SUCCESSFUL ✓ (was 0 VCCs before)
```

**Implementation:**
- `solidity_convert_decl.cpp`: State-var DYNARRAY type changed from `pointer_typet(elem)` to `array_typet(elem, infinity)` with `#sol_dynarray_state` flag; auxiliary `_dynarray_len` variable created
- `solidity_convert_ref.cpp`: `push(v)` → `items[len] = v; len++`; `pop()` → `len--`; `.length` → `len`
- `solidity_convert_expr.cpp`: Literal assignment `items = [1,2,3]` generates element-wise writes + length set; `new uint[](n)` sets length = n
- Global static lifetime (like mappings): not a struct member, resolved directly via symbol

**Semantic change:** The global length variable is visible to re-entrant calls in `--unbound` mode, which is MORE correct than the old model (where the C model's internal tracking was opaque to the solver). Test `github_2580_1` is currently `KNOWNBUG` because multi-dispatch of `test()` accumulates length across iterations.

Tests: `dynarray_push_1` (push + pop + length pass), `dynarray_push_2` (wrong value fail).

#### B. Multi-Dimensional Arrays — Partially Supported (2026-04-07)

1D static and 1D dynamic arrays are fully supported. 2D dynamic arrays (`T[][]`) now work after fixing the `NestedArrayTypeName` handler:

| Pattern | Status | Issue |
|---------|--------|-------|
| `uint[N]` | ✓ Works | — |
| `uint[]` | ✓ Works | push/pop/length supported |
| `uint[N][]` | ✓ Works | `solidity_grammar.cpp:239` logs "Experimental support" |
| `uint[][]` | ✓ Works (2026-04-07) | Declaration, push, indexing, length, storage ref passing |
| `uint[][N]` | ✗ Not detected | Grammar only checks `t_array$_t_array$` prefix |
| `uint[N][M]` | ✗ Broken | `get_array_size()` regex captures only one dimension |
| `uint[][][]` (3D+) | ✗ Broken | Type conversion recurses only one level via `baseType` |

**Fix (2026-04-07):** `NestedArrayTypeName` in `solidity_convert_type.cpp` had two bugs:
1. Recursive call to `get_type_description` passed `decl["typeName"]` as `decl`, but the inner handler expects `decl["typeName"]` to exist — fixed by wrapping `baseType` in a synthetic `decl`
2. Expression-context calls (no `decl` available) crashed — added string-based extraction with `rfind("_$dyn")` to find the outer array's suffix

Root causes of remaining gaps: array size extraction regex `.*\\[([0-9]+)\\]` in `solidity_convert_util.cpp` captures only one dimension, blocking `uint[N][M]`.

**Fix (2026-04-13):** `make_array_elementary_type()` previously used a greedy regex `\$_\w*_\$` that mis-parsed nested identifiers like `t_array$_t_string_memory_ptr_$2_memory_ptr` (producing element id `t_string_memory_ptr` with typeString `"string_memory_ptr"`), crashing downstream `get_type_name_t`. Rewrote it to scan backwards for the outer `_$<size>` delimiter and strip `_memory_ptr`/`_storage_ptr`/`_calldata_ptr` suffixes from both identifier and typeString. Unblocks `string[2][]` and fixed-size arrays of reference-type elements.

**Fix (2026-04-13):** Qualified struct constructor calls like `Pairing.G1Point(1, 2)` previously crashed with `nlohmann::json type_error 305` because the `TypeMemberCall` branch in `solidity_convert_expr.cpp` fed `StructDefinition` AST nodes into `get_func_decl_ref_t`, which dereferenced a non-existent `decl["parameters"]["parameters"]`. Added a StructDefinition branch that resolves the struct type via `ns.follow()`, then populates fields by walking `parent_call["arguments"]` against the struct members.

#### C. Data Location Semantics — Partially Implemented

`storage`/`memory`/`calldata` qualifiers are extracted from AST and tagged as `#sol_data_loc` metadata (`solidity_convert_type.cpp:417-422`).

**Storage reference semantics for library functions** — ✅ Implemented (2026-04-07):

Library functions with `storage` struct parameters now correctly modify caller's state variables via a three-part mechanism:
1. **Bridge variables** (`solidity_convert_modifier.cpp`): at function end, modified parameter values are stored in global `$out` bridge variables
2. **Copy-back** (`solidity_convert_expr.cpp`): at call site, after the library call returns, the `$out` bridge values are copied back to the caller's state variable
3. **Alias redirection** (`solidity_convert_decl.cpp`): local `Wrapper storage ref = param` declarations are redirected to the source parameter via `storage_ref_aliases` map

Both direct library calls (`TestLibrary.func(arg)`) and `using-for` calls (`arg.func()`) are supported. Tests: `storage_ref_1` through `storage_ref_4`.

| Remaining Gaps | Impact | Location |
|----------------|--------|----------|
| **Memory copy on function call** | Memory params should be deep-copied; currently aliased | TODO at `solidity_convert_call.cpp:98-103` |
| **Calldata immutability** | Calldata params should be read-only; no enforcement | — |
| **Copy-on-assign for memory structs/arrays** | `memory` assignment should copy; may alias | — |
| **Storage ref for non-library functions** | Storage params in regular contract functions not yet handled | — |
| ~~**Storage ref write to mapping element**~~ | **FIXED** (2026-04-12): expression-based alias map (`storage_ref_expr_aliases`) resolves `campaigns[0]` initializers. CORE: `storage_ref_mapping_write_1` | `solidity_convert_decl.cpp` + `solidity_convert_expr.cpp:get_decl_ref_expr` |
| ~~**Local storage ref to dynamic array**~~ | **FIXED** (2026-04-12): same expression-based alias map handles non-struct storage refs. CORE: `dangling_ref_1` | `solidity_convert_decl.cpp` — `is_storage_ref_alias` flag skips init handling |
| ~~**`new T[N][](size)` expression**~~ | **FIXED** (2026-04-12): `is_dyn_array()` now recognizes `NestedArrayTypeName` with dynamic outer dimension. CORE: `new_fixdyn_array_1` | `solidity_convert_util.cpp:is_dyn_array` |
| ~~**Struct with dynarray member via storage ref**~~ | **FIXED** (2026-04-12): test updated to use locally-created array (avoids nondet null param). CORE: `struct_dynarray_member_1` | Test-only fix; underlying harness parameter modeling unchanged |

#### D. Low-Level Calls — Partial Modeling (2026-04-10)

`.call()`, `.delegatecall()`, `.staticcall()` all return `(bool success, bytes memory data)`. The three are recognized and accepted by the frontend in both bound and unbound modes, but their semantic accuracy differs significantly by mode **and** by call kind. This table captures the ground truth as of 2026-04-10:

| Aspect | `.call()` | `.delegatecall()` | `.staticcall()` |
|---|---|---|---|
| Unbound: target dispatch | closed-world: target address ignored (by design) | closed-world: target address ignored (by design) | closed-world: target address ignored (by design) |
| Unbound: side effect | ✓ nondet reentry into *current* contract's nondet dispatch (models attacker callback) | ✓ same | ✓ same (strictly over-approx — staticcall target cannot mutate, but leaving it is harmless) |
| Unbound: return value | nondet `(bool, BytesDynamic)` | nondet `(bool, BytesDynamic)` | nondet `(bool, BytesDynamic)` |
| Bound: target dispatch | ✓ if-chain over `_ESBMC_Object_X.$address`, calls `_ESBMC_Nondet_Extcall_<target>` | ✓ same if-chain | ✓ same if-chain |
| Bound: `msg.sender` | ✓ swapped to caller's `this.address`, restored after | ✓ **preserved** (correct EVM semantics) | ✓ swapped to caller's `this.address`, restored after |
| Bound: **storage context** | ✓ correct — target code runs against `_ESBMC_Object_<target>` | ✓ **storage shadow** — target body is cloned into the caller's function context; state var reads/writes resolve by name against the caller's this pointer. Internal helpers inlined recursively. Falls back to the old target-instance dispatch when preconditions are not met (see v3 notes below). | ✓ correct — target code runs against `_ESBMC_Object_<target>` |
| Bound: **read-only enforcement** | N/A | N/A | ✓ **enforced via snapshot+rollback** — target struct is snapshotted before dispatch and restored after, so any writes performed by the nondet extcall are invisible to the caller. Tests: `staticcall_readonly_1/2`. |
| Function selector from ABI payload | ✓ signature-based dispatch for `abi.encodeWithSignature` literal | ✓ signature-based shadow dispatch for `abi.encodeWithSignature` literal | ✗ ignored |

**Unbound-mode design note (not a bug):** Under the closed-world assumption, other contracts do not exist, so the target address is intentionally ignored. The side effect — nondet reentry into the current contract via `_ESBMC_Nondet_Extcall_<self>` — is the intentional over-approximation of "attacker-controlled external callee may call back into any of our public functions during the call". This is what enables detection of classic reentrancy bugs (SWC-107), because it exposes the state space where `withdraw()` is re-entered *before* the post-call balance update. The harness-level `while(nondet) { _ESBMC_Nondet_Extcall_self(); }` only explores *sequential* transactions and cannot reach this state space on its own. Test: `swc_107_2` relies on this behavior to detect the reentrancy vulnerability.

**Bound-mode design philosophy:** Bound mode aligns with SMTChecker's `--model-checker-ext-calls=trusted` mode — external calls to addresses in `contractNamesList` are treated as deterministic dispatch to the declared contract. This is the "trusted" assumption: the user guarantees that callable addresses really do point to the declared contracts. Fallback is hard `return false`, not nondet over-approximation; programs that call unknown addresses have those paths pruned. This is sound under the trusted assumption and fast in practice.

**Concrete bound-mode failure modes:**

1. **`delegatecall` storage context — fixed in bound mode via the delegate-shadow fast path.** See the dedicated subsection below for v1/v2/v3 mechanics and restrictions. The generic `$delegatecall#0` dispatcher (which runs the target against `_ESBMC_Object_<target>`) is retained as the fallback when the shadow preconditions are not met.

2. **`staticcall` read-only — fixed via snapshot+rollback.** `get_staticcall_definition` now emits a per-arm full-struct snapshot of `_ESBMC_Object_<target>` before the nondet extcall and restores it immediately after, so any writes the target performs during dispatch are invisible to the caller. This matches real EVM semantics (write attempts inside a staticcall context revert) without needing per-field assertions. The nondet extcall is still invoked so path exploration and reentrancy modelling behave identically to `.call()` otherwise. Mutex toggling for reentrancy checks is no longer needed inside the staticcall arm because the rollback guarantees the target's observed state is invariant across the call boundary. Tests: `staticcall_readonly_1/2` (bound-mode verify/refute of target state invariance). **Not yet handled:** surfacing "target tried to write" as an explicit verification failure — the current model silently rolls back, which is sound but loses detection of buggy targets.

3. **ABI selector matching:** `.call(abi.encodeWithSignature("foo(uint)", 42))` now routes through a signature-based dispatch helper in bound mode (`try_get_signature_dispatched_call` @ `solidity_convert_call.cpp:~1830`) that resolves the literal signature to the matching target function and calls a typed shim with the provided argument values. `.delegatecall(...)` reuses the same signature extraction inside the storage shadow path, and since 2026-04-11 (`extract_abi_encode_signature` v4) also accepts `abi.encodeWithSelector(FnRef.selector, args...)` and `abi.encodeCall(FnRef, (args...))` — the canonical signature is rebuilt from the referenced FunctionDefinition via `build_canonical_signature`. Dynamic signature strings (built at runtime from a `bytes` variable) still fall back to the nondet dispatcher.

**Implementation references:**
- Unbound call lowering: `solidity_convert_expr.cpp:452` → `get_unbound_expr()` at `solidity_convert_constructor.cpp:159`
- Bound dispatch dispatcher: `solidity_convert_expr.cpp:468` → `get_bound_low_level_call()` → per-kind definitions:
  - `.call()`: `get_call_definition()` @ `solidity_convert_call.cpp:1220+` (generic fallback) + `try_get_signature_dispatched_call` for literal signatures
  - `.delegatecall()`: `try_get_delegate_shadow_call` @ `solidity_convert_call.cpp:~1700` (fast path) + `get_delegatecall_definition()` @ `solidity_convert_call.cpp:2347` (generic fallback)
  - `.staticcall()`: `get_staticcall_definition()` @ `solidity_convert_call.cpp:2203`
- Per-target nondet dispatcher: `get_unbound_function()` @ `solidity_convert_constructor.cpp:213` — always binds `this = _ESBMC_Object_<target>`, used by the fallback paths

**Tests:** `delegatecall_1/2`, `staticcall_1/2` — unbound-mode lowering smoke tests. `staticcall_readonly_1/2` — bound-mode staticcall read-only enforcement via snapshot+rollback (target.modify writes get rolled back). `delegate_shadow_1..9` — bound-mode storage-shadow regression tests (happy path, value propagation, layout mismatch fallback, return value, early return in conditional branches, internal helper inlining with swapped Proxy layout, nested helpers with return values, adversarial wrong-assertion cases).

---

##### Delegate-shadow fast path (bound mode only)

Real EVM `delegatecall` runs the target function's code **in the caller's storage context**: state writes inside the target land on the caller's slots, `msg.sender` and `msg.value` are preserved, and `address(this)` stays pointing at the caller. The previous bound-mode model executed the target function against its own static instance `_ESBMC_Object_<target>`, which is wrong for library and proxy patterns and silently accepts programs that would fail on chain.

The delegate-shadow fast path rewrites `caller_addr.delegatecall(abi.encodeWithSignature("f(T,...)", args))` into a sequence of inlined dispatch arms guarded by address comparisons. Entry point: `try_get_delegate_shadow_call` @ `solidity_convert_call.cpp`. On any unsupported shape it returns `true`, and `get_low_level_member_accsss` falls back to the generic `$delegatecall#0` dispatcher.

**v1 mechanics** (`ba18f79173`):

1. Extract the literal `sig` string via `extract_abi_encode_signature`. Each encoded argument JSON node is retained for later conversion.
2. Walk `contractNamesList`; for each candidate whose `find_function_by_signature(str, sig)` finds a matching external/public function with a body, run `validate_delegate_shadow_compatible` to reject state-var references whose name+typeString do not exist on the caller. Candidates that fail validation are skipped (but other candidates may still match).
3. For each surviving candidate, allocate `$dl_arg_i` locals from the caller's supplied arguments, plus a `$dl_success` bool. The target body is converted via `get_block` with `current_baseContractName` temporarily swapped to the target contract (so `find_decl_ref` still resolves the target's parameter AST ids) while `current_functionDecl` stays on the caller (so `this` keeps resolving to the caller's this pointer).
4. `delegate_shadow_param_remap` maps each target-formal-parameter AST id to the matching `$dl_arg_i` local id. `get_decl_ref_expr` consults this map before its normal AST lookup, so parameter references in the inlined body pick up the caller-side locals.
5. Each dispatch arm becomes `if (_addr == _ESBMC_Object_cand.$address) { inlined_body; $dl_success = true; }`. The whole sequence is staged into a single wrapper code_blockt and pushed to `front_block` as one unit — pushing decls individually would be unsafe because nested `get_block` calls inside the body (e.g. for an `if` at the head of the target function) flush `front_block` at their first statement and would scramble decl order.
6. The call expression itself evaluates to the existing `(bool, bytes)` tuple shape, with the bool slot filled in from `$dl_success`.

**v2 additions** — return-value support (`66f90bb04e`):

- `rewrite_returns_for_delegate_shadow` walks the converted body in-place and replaces every `return X;` with `{ $dl_ret$slot$i = X; goto $dl_end$slot$i; }`. Bare `return;` becomes just the goto. A `$dl_end$slot$i:` label is emitted at the tail of each arm so the goto lands inside the arm, not outside the enclosing caller function.
- `delegate_shadow_target_return_params` is a scoped override consulted by the ReturnStatement handler in `solidity_convert_stmt.cpp`. While the target body is being converted, it points at the target function's `returnParameters` node so the return expression is typed against the target's signature, not the caller's (which is usually void). Without this, returning a literal like `return 999;` crashes with `cannot use operator[] with a string argument with null` because `make_return_type_from_typet` is given an empty parameter list.
- Only single-return functions are handled by the shadow; tuple returns fall through to the generic path.

**v3 additions** — internal helper inlining (`c9b6b6697f`):

- `get_call_expr` checks `delegate_shadow_target_cname` early: if set, and the callee resolves to a `FunctionDefinition` whose `scope` matches the target contract, `try_inline_delegate_shadow_helper_call` is invoked instead of building a normal function call. The generic path would otherwise emit `helper((Target*)this, args)`, and the `(Target*)this` cast silently depends on struct-layout coincidences between caller and target. Swap the field order on one side and writes land on the wrong slots — the v1/v2 tests happened to pass because Proxy and Logic declared fields in the same order.
- The helper inliner allocates fresh `$dl_harg_i` locals for the helper's parameters (separate slot from the outer body's `$dl_arg_i`), optionally a `$dl_hret` for single-return helpers, and a `$dl_hend$slot` end label. It swaps `delegate_shadow_param_remap` and `delegate_shadow_target_return_params` to the helper's shape, recursively calls `get_block` on the helper body, runs the return-rewrite with the helper's own `$dl_hret`/`$dl_hend`, and pushes the whole thing to `front_block` as a single wrapper.
- Nested helpers (A calls B calls C) inline naturally: each hook activation saves/restores the remap and return-params state, so deeper helpers see their own formal parameters without clobbering the outer remap.
- For non-void helpers `new_expr` becomes `symbol_expr($dl_hret)` so RHS usage like `x = _helper(v)` still evaluates correctly. For void helpers it becomes `code_skipt()` since the only valid caller context is an expression statement where the result is discarded.
- Helpers that live in a base contract of the target, helpers that return tuples, and helpers called through `this.foo()` (external self-call) still fall through to the normal call path.

**v4 additions** — `encodeWithSelector` and `encodeCall` (#1):

- `extract_abi_encode_signature` now recognises three payload shapes: the original `abi.encodeWithSignature("sig(T,...)", args...)` (literal), the selector form `abi.encodeWithSelector(Logic.f.selector, args...)`, and `abi.encodeCall(Logic.f, (args...))`. For the two new forms the canonical signature is rebuilt from the referenced `FunctionDefinition` via `build_canonical_signature`, using a full-AST lookup (`find_node_by_id`) instead of the scope-restricted `find_decl_ref` so cross-contract references resolve correctly from the caller.
- `encodeCall` flattens a `TupleExpression` into its component args. A single-element parenthesised expression is accepted as a 1-arg tuple because solc collapses `(x)` to `x` in the AST.
- Dynamic signature strings (e.g. passing a `bytes` variable built at runtime) still fall through to the generic `$delegatecall#0` helper.

**Current restrictions — fall back to `$delegatecall#0` on any miss:**

- Payload must be one of `abi.encodeWithSignature("sig(T,...)", ...)`, `abi.encodeWithSelector(FnRef.selector, ...)`, or `abi.encodeCall(FnRef, (...))`. Raw bytes variables and dynamic signature strings are unsupported.
- Every state variable the target body reads or writes must exist on the caller with the same name and typeString. Rules out EIP-1967 / UUPS / Diamond layouts where the proxy uses dedicated storage slots.
- Target and helper functions are inlined by body, not by byte layout. Contracts that use `assembly` / `sload` / `sstore` to access storage by slot index are not handled.
- Only single-return functions are shadowed; tuple return values fall back to the generic path.
- Helpers defined in a base contract of the target (not the target itself) fall through to the generic path, which reintroduces the `(Target*)this` cast soundness issue for those particular calls.
- `return` statements inside the inlined body are rewritten to neutralise the "escape the caller" hazard, but the rewrite only walks `codet` children. Returns hidden inside statement expressions, inline assembly, or try/catch bodies are not rewritten.

ESBMC models the return as:

```c
// solidity_types.h — BytesDynamic is a nondet struct
typedef struct BytesDynamic { size_t offset; size_t length; size_t capacity; int initialized; } BytesDynamic;
// success = nondet_bool(), data = nondet BytesDynamic (via llc_nondet_bytes())
```

- `bool success` works correctly — `require(success)` patterns are verifiable
- `bytes memory data` is a nondet `BytesDynamic` struct — `data.length` is accessible and verifiable (fixed 2026-04-02)
- `abi.decode(data, (T))` is supported as a nondet over-approximation — decoded content is unconstrained but the call parses and type-checks cleanly
- Key fix (2026-04-02): `data.length` member expression type was `uint32` instead of `size_t`; corrected in `solidity_convert_ref.cpp:482`

#### E. Tuple / Multi-Return — Mostly Resolved (2026-04-02)

**Working** (after 4-phase refactoring):
- Flat destructuring `(x, y) = func()`, partial skip `(x, ) = func()`, tuple swap `(x, y) = (y, x)`, multi-position omit `(x, , y) = func()`
- Position-based component matching (name-based + positional fallback) — replaces fragile `at(i)` indexing
- Nested tuple destructuring `((a,b),c) = ...` via `flatten_nested_tuple_assignment()`
- External call tuple returns `(a,b) = externalContract.f()` — cross-contract and same-contract
- Low-level call tuple `(bool success, ) = addr.call(...)` — positional matching for library structs

| Remaining Limitation | Detail | Location |
|----------------------|--------|----------|
| **`abi.decode()` is nondet** | Parses and type-checks, but decoded content is unconstrained (over-approximation) | See Section D |

**Fix (2026-04-13):** Library multi-return tuple lookup. Library functions like `library L { function reverse(S calldata) returns (uint, uint) { ... } }` create a tuple instance under the library's scope (`sol:@C@L@tuple_instance$<id>`), but `get_tuple_function_ref` only iterated `contractNamesList` — which deliberately `continue`s past libraries in `populate_auxiliary_vars`. Cross-library tuple returns therefore hit `cannot find tuple instance for declaration id N` and failed with CONVERSION ERROR. Added a second fallback pass over `nonContractNamesList` (libraries, interfaces, abstract contracts). Test: `stress_calldata_struct_lib_1` (CORE).

**Fix (2026-04-14c):** `phi_function: no symbol for 'sol:@C@<L>@F@<f>@<field>#<id>'` warnings followed by silent dropping of writes through library `storage` references aliased to a struct member. Repro pattern (1inch ExplicitLiquidVoting._update): `Data storage self; ...; { V.Data memory snap = self.data; if (cond) { V.Data storage sd = self.data; sd.field = ...; } }`. Two compounding bugs in `solidity_convert_decl.cpp`. (1) `get_var_decl` registered the `sd = self.data` alias on the *integer-id* path (`storage_ref_aliases[sd_id] = field_id`) whenever the RHS carried a `referencedDeclaration` — but a `MemberAccess` RHS *also* carries one (the field's id), and chasing it loses the `self.` base, so `sd` later resolved to a bare symbol referencing the struct field as if it were a free variable. Fix: require `nodeType == "Identifier"` for the int-id path; everything else (MemberAccess, IndexAccess, ...) takes the `storage_ref_expr_aliases` JSON path which preserves the full base expression. (2) `get_local_var_decl_name` used the function-local naming template (`sol:@C@<cname>@F@<func>@<name>#<id>`) for *any* VariableDeclaration walked while inside a function body — including struct-field VariableDeclarations re-walked via `get_var_decl_ref` during alias resolution. The synthetic id collided with the local-variable namespace, never landed in the symbol table (the field's real symbol lives under the struct-qualified id), and goto-symex's phi merge then warned and skipped. Fix: detect struct fields by `member_entity_scope.count(scope) > 0` *before* the function-local branch and use the struct-qualified template. Together the two fixes restore correct write-through and clear all phi warnings on FarmingRewards, FarmingVoter, MooniswapFactory, MooniswapFactoryGovernance, ReferralFeeReceiver. Tests: `stress_libsol_storage_ref_member_alias_pass` / `_fail` (the `_fail` test asserts the negation of the actual write to ensure the write is observed — if the alias were dropped, the assertion would silently hold and mask the regression).

**Fix (2026-04-14b):** Two `migrate expr failed` crashes during goto-program generation, both encountered on the 1inch liquidity-protocol contracts. (1) **Tuple-LHS dispatch on extcall RHS** — `(vb.balance, vb.time) = mooniswap.virtualBalancesForAddition(token);` builds a `code_blockt` LHS holder in `get_tuple_expr` (under `current_lhsDecl`). When the cross-contract call RHS is rewritten by `convert_unboundcall_nondet` into a plain `sideeffect/nondet`, the TUPLE_RETURNS tag on `rt_sol` is erased, so `get_binary_operator_expr` skips the tuple branch and falls into the standard assign path with the LHS code_blockt as an operand — `migrate_expr` then rejects the bare code_blockt operand. Fix: also dispatch into `construct_tuple_assigments` when `lhs.is_code() && to_code(lhs).statement() == "block"` (the marker the tuple-LHS path produces); the function's existing `rhs_is_nondet` fallback handles the non-struct sideeffect rhs by per-slot independent nondet assignment. (2) **Contract-typed null cast `Pool(address(0))`** — for nested type conversions, `solidity_convert_expr.cpp`'s `TypeConversionExpression` case forwarded the *outer caller's* `literal_type` to `get_expr` for the argument. When the outer caller pinned the type to a contract pointer (e.g. an `==` against a state-var of contract type), the inner `Literal 0` reached `convert_integer_literal` with a pointer-type hint; `bv_width(pointer)` produced an empty binary string and `constant_exprt(...)` was minted with an empty `value()` slot, which `migrate_expr` then rejects. Fix: when the outer hint is a CONTRACT pointer, strip it and pass the literal's own typeDescriptions instead — preserves the existing bytes32/address literal handling for non-contract conversions. Tests: `stress_libsol_tuple_lhs_extcall_pass` / `_fail` and `stress_libsol_contract_null_cast_pass` / `_fail` (each pair includes a SUCCESSFUL and FAILED variant — the dual `_fail` tests guard against the fix accidentally turning the goto program into a no-op that always verifies).

**Fix (2026-04-14):** `goto_symext: unexpected statement: symbol` on inherited constructors. `solidity_convertert::get_var_decl` early-returned a bare `symbol_exprt` when the local variable's symbol was already in context — which happens whenever a parent constructor body is re-walked during inheritance population. The bare symbol propagated through the `VariableDeclStatement` decl-block as an `OTHER symbol;` instruction, which `goto_symext::symex_other` rejects. Wrapping the result in `code_declt(symbol_expr(...))` keeps the statement context well-formed. Same commit also adds: (a) lazy on-demand creation of `tuple_instance$<id>` in `get_tuple_function_ref` for the case where a tuple-returning callee is referenced before its `FunctionDefinition` has been walked (fixes `cannot find tuple instance for declaration id N` when MooniswapDeployer calls into Mooniswap); (b) defensive skip of bare-symbol `ExpressionStatement` lowerings in `solidity_convert_stmt.cpp` (Solidity uses `this;` / state-var-name as a no-op hint to suppress unused-mutability warnings — Context.sol's `_msgData` is the canonical example). Test: `stress_libsol_inherited_ctor_local_var` (CORE).

#### F. Mapping Library Efficiency

Two implementations coexist:

| Mode | Data Structure | Lookup | Per-op SMT cost |
|------|---------------|--------|-----------------|
| **Bound** (`--bound`) | Infinite SMT array | O(1) array index | Linear |
| **Unbound** (default) | Linked list (`_ESBMC_Mapping`) | O(n) while-loop | Exponential in chain length |

The unbound mode's `map_get_raw()` uses a `while(cur)` linked-list traversal (`solidity_mapping.c:27`). Each iteration adds branch conditions to the SMT solver, requiring `--unwind ≥ (max_chain_length + 1)`. This causes:
- Timeout on multi-entry mappings with insufficient unwind
- K-induction failure (chains grow non-deterministically, breaking invariants)
- Each `map_set_raw` calls `malloc`, creating new symbolic allocation constraints

**Recommendation**: Prefer `--bound` mode for mapping-heavy contracts; the infinite SMT array approach avoids loop unrolling entirely.

#### F.1. Arrays of Mappings (`mapping(K => V)[]`) — ✅ Fixed (2026-04-09)

Dynamic arrays of mappings previously crashed because the dynamic array C model used `malloc(sizeof(element))` + `memcpy`, but `sizeof(mapping)` = `sizeof(infinite_array)` is undefined.

**Solution (2026-04-09):** Redesigned to model `mapping(K=>V)[]` as a 2D infinite array with an auxiliary `_mapping_arr_len` variable for push/pop tracking. No `malloc` needed — mappings are pre-existing infinite arrays, so `push()` simply increments the length counter. The inner mapping's subtype chain is populated from the AST's `typeName.baseType` node.

Implementation:
- `solidity_convert_decl.cpp`: detect `#sol_mapping_array` flag, populate inner mapping subtypes from AST, create `_mapping_arr_len` auxiliary symbol, exclude from struct components and initializer
- `solidity_convert_ref.cpp`: `.length` returns auxiliary length variable, `.push()`/`.pop()` increment/decrement it
- `solidity_convert_decl.cpp`: `get_struct_class_fields` skips mapping array fields (same as regular mappings)

Tests: `clearing_mapping_1` (write/read), `clearing_mapping_2` (push + index + assert pass), `clearing_mapping_3` (assert fail).

**Extension (2026-04-14):** Fixed-size variant `mapping(K=>V)[N]` now uses the same inf-array model. Previously fell through to the standard pointer-with-fixed-size path and crashed `array_type2t::get_width` during state-var zero-initialisation (the inner mapping is itself an infinite-sized array). The `get_array_pointer_type` special-case now drops the `!decl["typeName"].contains("length")` guard. Test: `stress_libsol_array_mapping_struct`.

#### G. Address / Contract Type Conversion

Basic conversions work:
- `address(contractInstance)` → extracts `$address` member ✓
- `ContractType(addr)` → binds to static `_ESBMC_Object_*` instance ✓
- `payable(addr)` ↔ `address` conversions ✓
- Nested `uint8(bytes1(x))` chains ✓

**Limitations**:
- Address→contract conversion assumes all addresses are known static instances; unknown/external addresses cannot be properly converted
- No runtime type checking that an address actually holds the expected contract type
- Dynamic dispatch through address conversion is limited — `Base(address(derived))` binds to the static Base instance, not the actual derived instance

#### H. Cryptographic Hash Function Abstraction — Deterministic Bijective (2026-04-04)

Hash/crypto functions use **deterministic bijective transformations** (see Section A for details). `blockhash` and `blobhash` remain **nondet** since they depend on external blockchain state, not on program inputs.

| Function | Abstraction | Functional consistency |
|----------|-------------|----------------------|
| `keccak256` | `~x` (deterministic) | ✓ same input → same output |
| `sha256` | `~(x+1)` (deterministic) | ✓ same input → same output |
| `ripemd160` | `(address_t)(~(x+2))` (deterministic) | ✓ same input → same output |
| `ecrecover` | `(address_t)(~hash)` (deterministic) | ✓ same hash → same output |
| `blockhash` | nondet `uint256_t` | ✗ (external state) |
| `blobhash` | nondet `uint256_t` | ✗ (external state) |

| Scenario | Verifier result | Correct? |
|----------|----------------|----------|
| `h1 = keccak256(x); h2 = keccak256(x); assert(h1 == h2);` | SUCCESSFUL | ✓ Functional consistency |
| `assert(keccak256(1) != keccak256(2));` | SUCCESSFUL | ✓ Injectivity (bijective) |
| `keccak256(abi.encodePacked(s1)) == keccak256(abi.encodePacked(s2))` | ↔ `s1 == s2` | ✓ String equality via hash |
| `assert(keccak256(0) == 0xc5d2...);` | FAILED | Expected — concrete hash not computed |

Implementation: crypto hashes in `src/c2goto/library/solidity/solidity_crypto.c`, modular arithmetic and `sol_pow_uint` in `solidity_builtins.c`, block/tx/msg context in `solidity_blockchain.c`, ABI functions in `solidity_abi.c`.

#### I. uint256 Modeling Constraints

256-bit integers (`_BitInt(256)`) are supported for arithmetic, but:

| Issue | Detail | Location |
|-------|--------|----------|
| ~~**Mapping key truncation**~~ | ✅ Fixed (2026-04-02): XOR-fold 256→64 bit via `xor_fold_key_to_64bit()`; collision rate 2^-64 | `solidity_convert_mapping.cpp` |
| **SMT solver performance** | 256-bit bitvector operations significantly slower than smaller widths; OOM possible for complex arithmetic | `README.md:123` |
| **`--16` workaround** | Reducing to 16-bit improves speed but introduces precision loss | — |

**Automatic solver selection (2026-04-11):** When the user does not pass an explicit solver flag, the Solidity frontend auto-selects `bitwuzla > cvc5 > boolector > z3` (first available). Z3 is kept as the default only when `--k-induction`, `--k-induction-parallel`, `--incremental-bmc`, or `--falsification` is set, because those modes rely heavily on incremental SMT queries where Z3 is more robust. The chosen backend is logged at startup with an override hint. Implementation: `src/esbmc/esbmc_parseoptions.cpp` inside the Solidity detection block after `get_command_line_options()`.

Observed regression-suite timings at 60s CTest timeout (same machine, 509 tests):

| Default backend | Wall time | Notes |
|-----------------|----------:|-------|
| z3 (old default) | 47s | baseline |
| cvc5 | 45s | 2 THOROUGH tests time out and need `--z3` pin: `import_15`, `mul_cnt_ver_2` |
| **bitwuzla** | **39s** | fastest; both previously-pinned THOROUGH tests also finish in 3–5s with `--bitwuzla` |

The `--z3` pin on `import_15` and `mul_cnt_ver_2` is preserved so minimal builds (only `z3 + cvc5`, no bitwuzla/boolector) still pass the suite. On a full build the auto-select picks bitwuzla and those tests would pass without the pin as well.

**Build requirements for bitwuzla backend (Ubuntu/WSL):**

```bash
sudo apt install -y libgmp-dev
pip install --user --break-system-packages meson ninja
cd build && cmake -DENABLE_BITWUZLA=ON -DDOWNLOAD_DEPENDENCIES=ON ..
cmake --build . -j$(nproc) --target esbmc
```

`libgmp-dev` is required for the bitwuzla pkg-config check; `meson`/`ninja` are required by bitwuzla's upstream build (it is compiled from source by CPM).

#### J. `super` Keyword — Implemented (2026-04-05)

`super.funcName()` calls are now supported. Detection is in `get_call_expr()` (`solidity_convert_expr.cpp`) which checks for `MemberAccess` where `expression.name == "super"`. The dispatch logic is in `get_super_function_call()` (`solidity_convert_call.cpp`):

1. For non-overriding case (base function merged into derived contract): use the merged copy directly — `this` type matches, no cast needed.
2. For overriding case (derived contract overrides the same name): detect via ID mismatch after `find_decl_ref`, fall back to original in base contract via `find_contract_name_for_id()`, insert a `this` typecast.

**Supported patterns**:
- `super.method()` with no arguments, with arguments, with return values ✅
- Non-overriding case: base function merged into derived contract, no cast needed ✅
- Overriding case: derived overrides the same name, calls original base with `this` typecast ✅
- Multi-level dispatch (e.g. `Child.abc() → p1() → super.myFunc()`) ✅
- **`super.f` as r-value (fn-ptr capture)** (2026-04-14): `function() internal returns (uint) x = super.f;` lowers to an opaque func_ptr (typecast of `referencedDeclaration + 1`). Indirect calls through the captured pointer return nondet, same UNDER-approximation as other fn-ptr captures. Required three frontend tweaks: (a) `t_super$_X_$N` typeIdentifier classified as `ContractTypeName` in `solidity_grammar.cpp`; (b) `ContractTypeName` extraction in `solidity_convert_type.cpp` switched to `rfind(" ")` to strip the `super` qualifier; (c) `get_expr` TypeMemberCall branch detects the bare `super` Identifier child and emits the func_ptr directly. Tests: `stress_libsol_super_in_ctor_assign`, `stress_libsol_super_function_deployed`, `stress_libsol_virtual_function_deployed`.

**Cooperative super chain — Fully supported** (2026-04-05):

```solidity
contract A { uint counter; function inc() virtual { counter += 1; } }
contract B is A { function inc() override { super.inc(); counter += 10; } }
contract C is B {
    function test() {
        uint before = counter;
        super.inc();
        assert(counter == before + 11);  // VERIFICATION SUCCESSFUL ✅
    }
}
```

This works because ESBMC's `is_prefix_of` mechanism (`dereference.cpp:603`) recognises that `A`, `B`, `C` contract structs all share the same prefix layout (inherited fields have identical name and type in order). When `A.inc` writes `counter` through an `(A*)this` pointer to a `C` object, `symex_assign_typecast` (`symex_assign.cpp:528`) generates `C_obj_new = with(C_obj_old, [counter := new_val])`, correctly updating the `C`-typed object. No backend change was needed.

**Test design note**: Use relative assertions (`counter == before + 11`) rather than absolute values (`counter == 11`). With `--contract C`, ESBMC's non-deterministic main can call any public function (including inherited `inc()`) in any order before `test()`, so an absolute counter value is not guaranteed at entry to `test()`.

#### K. Other Gaps

| Feature | Status | Detail |
|---------|--------|--------|
| ~~**Try/Catch**~~ | ✅ Done (2026-04-05) | Modeled as `if(nondet_bool) { success } else { catch }` with nondet return values; supports multiple catch clauses (Error, Panic, catch-all) |
| **`using A for B`** | Partial (2026-04-13) | Library function dispatch works (including storage ref copy-back). Free-function binding `using { f } for S` — where `x.f(args)` calls a top-level `function f(S, ...)` — now rewritten in `get_call_expr` to `f(x, args)` before the normal call path. Previously fed the `FunctionDefinition` decl into `get_var_decl_ref`, abusing its `VariableDeclaration` assertion and crashing in `nlohmann::json` bool/number coercions (`solidity_convert_decl.cpp:1453`). Custom operator dispatch (`using { f as op }`) still not supported. |
| **Bitwise on dynamic bytes** | Static only | Ops limited to `bytesN`, not dynamic `bytes` (`solidity_convert_expr.cpp:2155`) |
| **`constant`/`immutable`** | Partial | `constant` works; `immutable` may not enforce set-once |
| **Named return parameters** | ✅ Fixed (2026-04-05) | Single named return: DECL + zero-init + implicit return. Tuple named returns still use existing tuple machinery. |
| **Function overloading** | Partial | Same-name different-param functions may misresolve in `find_decl_ref` |
| **receive/fallback** | ✓ Works | `receive() external payable` and `fallback() external [payable]` fully supported; tests: `receive_1/2`, `fallback_1/2` |
| **Fallback with params** | Partial | Basic fallback exists; `fallback(bytes calldata) returns (bytes memory)` params ignored |
| **Custom storage layout** | ✓ Works (2026-04-07) | `contract C layout at <expr>` (Solidity 0.8.29+) — AST parses without error; ESBMC ignores storage slots (models state vars as struct members); tests: `layout_1/2` |
| **Array slices (`IndexRangeAccess`)** | ✅ Over-approx (2026-04-09) | `data[:4]`, `data[1:3]` on calldata arrays/bytes — modeled as nondet; tests: `array_slice_1/2` |
| **`abi.decode()`** | ✓ Nondet (CORE, extended 2026-04-13) | Parses `(T)` type tuple via `ElementaryTypeNameExpression`; result is an unconstrained nondet value. Tuple-shaped return position (`return abi.decode(data, (uint256, bytes));`) and tuple-destructuring assignment (`(a,b,...) = abi.decode(...)`) both fall back to nondet-per-member. Tests: `abi_decode_1/2/3`, `stress_libsol_abi_decode_simple` |
| **`abi.encodeCall()`** | ✓ Identity (CORE) | Interface/function pointer (`ITarget.transfer`) accepted by converter; canonical signature rebuilt from referenced `FunctionDefinition`. Tests: `abi_encodeCall_1/2/3` |
| **`mulmod(MAX,MAX,k)`** | KNOWNBUG | 512-bit model is correct but ESBMC constant evaluator crashes (SIGFPE) when both operands are near `type(uint256).max` |
| **Inline assembly / Yul** | ✅ Havoc (2026-04-05) | Over-approximated: all externally referenced variables are havoc'd to nondet. Does not model Yul semantics. |
| **Function types (internal)** | ✓ Parses, nondet calls (2026-04-13) | `function(uint) pure returns (uint) f` parameters / struct fields lowered to opaque `void *`; indirect calls `f(x)` return a nondet value of the declared return type (no crash). Precise semantics (function inlining / monomorphization) not implemented — tests that assert specific computed values through `.map(fn).reduce(fn)` remain KNOWNBUG. See `func_internal_type_1` |
| **Function types (external)** | Partial (2026-04-13) | `.address` on a function-typed variable returns nondet (opaque void* lowering cannot recover the bound contract). `.address` on a fresh reference (`this.f.address`) reads the instance `$address`. Passing a function reference (`this.callback`) as an argument still crashes during argument marshalling in `get_non_library_function_call`. Tests: `func_external_type_1`, `stress_libsol_ext_fn_to_address` |
| **`using for` + custom operators** | Not supported | Operator dispatch table per type |
| **Transient storage (EIP-1153)** | Not supported | New data location model |
| **User-defined value types** | ✓ Basic (2026-04-07, extended 2026-04-13) | `type C is V` with `.wrap()`/`.unwrap()` works, both file-scope and contract-scope (`contract C { type T is int; }`) registered in `UserDefinedVarMap`; bare `T.wrap;` expression statement no longer crashes (elided as skip in ExprStmt handler); `using { f as op }` custom operators NOT supported; tests: `udv_type_1/2`, `stress_libsol_udvt_wrap_unwrap` |

### Roadmap: Priority for Future Work

#### Tier 1 — Correctness Fixes (soundness gaps in current implementation)

These are bugs or unsound abstractions in features we claim to support:

| # | Task | Effort | Why |
|---|------|--------|-----|
| 1 | ~~**Fix mapping key truncation**~~ — XOR-fold 256→64 bit in frontend | ✅ Done | Resolved via `xor_fold_key_to_64bit()` (2026-04-02); 2^-64 collision rate |
| 2 | ~~**Fix crypto function abstraction**~~ — deterministic bijective hash for all crypto functions | ✅ Done | Resolved via deterministic bijective transforms (2026-04-04); see Section A/H. Functional consistency ✓, injectivity ✓, O(1) SMT cost. `abi.encodePacked` changed from nondet to identity to complete the `keccak256(abi.encodePacked(x))` chain |
| 3 | ~~**Fix external call tuple returns**~~ | ✅ Done | Resolved in 4-phase tuple refactoring (2026-04-02) |
| 4 | ~~**Low-level call bytes return**~~ — model as `BytesDynamic` instead of nondet_uint | ✅ Done | Resolved via `get_tuple_assignment` substitution (2026-04-02); `bytes memory data` is now a nondet `BytesDynamic`. `data.length` comparisons work correctly (fixed 2026-04-02: `solidity_convert_ref.cpp` used `uint_type()` instead of `size_type()` for `.length` member type). `abi.decode()` converter support landed since — see Tier 2 #7 |

#### Tier 2 — High-Impact Missing Features

| # | Task | Effort | Why |
|---|------|--------|-----|
| 5 | **`super` keyword** | ✅ Done (2026-04-05) | Non-override and override cases; cooperative super chain; `find_contract_name_for_id` + `get_super_function_call`; backend `is_prefix_of` handles cross-type writes |
| 6 | ~~**Try/Catch**~~ | ✅ Done (2026-04-05) | Nondet success/fail branching with multi-clause catch support |
| 7 | ~~**`abi.decode()`**~~ | ✅ Done | Converter accepts `(T)` type tuple; model is nondet over-approximation (guarded round-trips work via `require`). Tests: `abi_decode_1/2/3`. Tighter encode/decode round-trip remains future work if concrete payloads are ever needed. |
| 8 | **Function overloading** | Hard (~400 lines) | Name mangling or overload resolution table |
| 9 | **Data location semantics** | Partial (2026-04-07): storage ref for library params done; memory copy-on-call, calldata immutability, non-library storage ref remain | soundness gap |

#### Tier 3 — Completeness / Usability

| # | Task | Effort | Why |
|---|------|--------|-----|
| 10 | **Multi-dimensional arrays** | Partial (2026-04-07): `T[][]` works (declaration, push, indexing, storage ref); `T[N][M]` and 3D+ still broken | Recursive type/size extraction needed for remaining cases |
| 10b | **`mapping(K=>V)[]`** | ✅ Done (2026-04-09) | Modeled as 2D infinite array with auxiliary `_mapping_arr_len`; tests: `clearing_mapping_1/2/3` |
| 11 | ~~**Nested tuple destructuring**~~ | ✅ Done | Resolved in 4-phase tuple refactoring (2026-04-02) |
| 12 | **User-defined value types** | Partial (2026-04-07): `type C is V` + `wrap`/`unwrap` work; custom operators (`using { f as op }`) not supported | Increasingly common in modern Solidity |
| 13 | **`immutable` set-once enforcement** | Easy (~80 lines) | |
| 14 | ~~**`bytes.concat()` / `string.concat()`**~~ | ✅ Done | Variadic support with nested binary calls (2026-04-04) |
| 15 | ~~**`type(C).runtimeCode` / `type(I).interfaceId`**~~ | ✅ Done | Nondet over-approximation (2026-04-04) |

#### Tier 4 — Long-Term / Architectural

| # | Task | Effort | Why |
|---|------|--------|-----|
| 16 | **Inline assembly / Yul** | ✅ Havoc (2026-04-05) | Over-approximated via nondet havoc; unblocks contracts with assembly |
| 17 | **Mapping library optimization** — migrate unbound mode to SMT arrays | Hard | Eliminates linked-list loop unrolling overhead |
| 18 | ~~**Tuple return refactoring**~~ — position-based matching + nested + external | ✅ Done | Completed including LLC bytes return (2026-04-02) |
| 19 | **Function types** | Very hard | First-class function values |
| 20 | **Transient storage / custom storage layout** | Very hard | EVM evolution features |

**Performance bottlenecks** (slow THOROUGH tests):
- `transfer_send_2` (>1200s timeout) — k-induction + `--bound` cross-contract reasoning
- `typedef_1` (~420s) — k-induction with complex type aliases
- `continue_3`/`break_4` (~200-250s) — `--unwind 20` with nested control flow
- `bytes_17` (~175s) — bytes operations with `--bound` mode

### Solidity Documentation Examples (2026-04-12)

Tests sourced from the official Solidity documentation (Arrays, Structs, Mapping Types, Dangling References sections). All use `--multi-property` for stress testing.

| Test | Source | Status | Notes |
|------|--------|--------|-------|
| `mapping_erc20_1` | Docs: Mapping Types (ERC20-style) | ✅ CORE | Nested mappings, require guards, events |
| `array_flags_1` | Docs: Arrays (ArrayContract) | ✅ CORE | `bool[2][]` push/set/resize/delete |
| `array_bytes_1` | Docs: Arrays (ArrayContract.byteArrays) | ✅ CORE | bytes push, index write, delete element |
| `struct_mapping_direct_1` | Docs: Structs (CrowdFunding) | ✅ CORE | Struct-in-mapping with direct field writes |
| `dangling_ref_1` | Docs: Dangling References | ✅ CORE | Local storage ref to dynarray element via expr alias |
| `new_fixdyn_array_1` | Docs: Arrays (ArrayContract.createMemoryArray) | ✅ CORE | `new T[N][](size)` for memory arrays |
| `struct_dynarray_member_1` | Docs: Arrays (ArrayContract.StructType) | ✅ CORE | Struct with `uint[]` member via storage ref |
| `storage_ref_mapping_write_1` | Docs: Structs (CrowdFunding pattern) | ✅ CORE | Storage ref write to mapping element — propagated |

## Code Architecture Notes

### Expression Conversion (`get_expr`)

The main expression converter `get_expr()` dispatches to focused handler functions:
- `get_decl_ref_expr()` — variable/function/contract reference resolution
- `get_literal_expr()` — integer, bool, string, hex, bytes literals
- `get_tuple_expr()` — tuple expressions (init lists, swap, multi-return)
- `get_call_expr()` — function calls (builtin, struct ctor, normal, event/error)
- `get_contract_member_call_expr()` — cross-contract member access (x.func(), x.data())
- `get_index_access_expr()` — array/mapping index access
- `get_new_object_expr()` — `new` expressions (contract instantiation, dynamic arrays)

### Declaration Lookup (`find_decl_ref`)

After inheritance merging, AST node IDs are **not unique** across contracts (inherited nodes are copied into derived contracts). The lookup uses two functions:

| Function | Purpose |
|----------|---------|
| `find_node_by_id(subtree, id)` | Pure DFS — find node by ID in any subtree |
| `find_decl_ref(id)` | Scoped lookup: searches `current_baseContractName` + libraries + globals, falls back to `overrideMap` |

### Solidity ↔ C Type Mapping (`SolType` enum)

The `SolidityGrammar::SolType` enum (defined in `solidity_grammar.h`) annotates `typet` objects to preserve Solidity type semantics through the C/irep2 pipeline. Stored in irep via the `#sol_type` attribute, but accessed only through type-safe helpers:

```cpp
set_sol_type(typet &t, SolidityGrammar::SolType st);   // solidity_convert.h
SolidityGrammar::SolType get_sol_type(const typet &t);  // solidity_convert.h
```

Classification functions (in `SolidityGrammar` namespace):
- `is_uint_type(SolType)` — UINT8–UINT256
- `is_int_type(SolType)` — INT8–INT256 (excluding UINT)
- `is_integer_type(SolType)` — all integers
- `is_bytesN_type(SolType)` — BYTES1–BYTES32
- `is_bytes_type(SolType)` — BYTES1–BYTES32 + BYTES_DYN + BYTES_STATIC
- `is_address_type(SolType)` — ADDRESS + ADDRESS_PAYABLE
- `elementary_to_sol_type(ElementaryTypeNameT)` — maps grammar enum to SolType

**Value types:**

| Solidity | `SolType` enum | irep2/C type |
|----------|---------------|--------------|
| `uint8`–`uint256` (×32) | `UINT8`–`UINT256` | `unsignedbv_typet(N)` |
| `int8`–`int256` (×32) | `INT8`–`INT256` | `signedbv_typet(N)` |
| `bool` | `BOOL` | `bool_type()` |
| `address` | `ADDRESS` | `unsignedbv_typet(160)` |
| `address payable` | `ADDRESS_PAYABLE` | `unsignedbv_typet(160)` |
| `bytes1`–`bytes32` (×32) | `BYTES_STATIC` *(inherited from `byte_static_t`)* | `symbol_typet(lib_prefix + "BytesStatic")` with `#sol_bytesn_size` |
| `bytes` (dynamic) | `BYTES_DYN` | `symbol_typet(lib_prefix + "BytesDynamic")` |
| `string` | `STRING` | `pointer_typet(signed_char_type())` |
| `enum` | `ENUM` | `enum_type()` (= `unsignedbv_typet(8)`) |

**Composite/reference types:**

| Solidity | `SolType` enum | irep2/C type |
|----------|---------------|--------------|
| `T[N]` (static array) | `ARRAY` / `ARRAY_LITERAL` | `array_typet(sub, size)` with `#sol_array_size` |
| `T[]` (dynamic array) | `DYNARRAY` | `pointer_typet(sub_type)` |
| `mapping(K=>V)` | `MAPPING` | `array_typet()` (infinity size) or `symbol_typet("mapping_t")` |
| `struct S` | `STRUCT` | `symbol_typet(prefix + "struct " + name)` |
| contract instance | `CONTRACT` | `pointer_typet(symbol_typet(id))` with `#sol_contract` |
| library | `LIBRARY` | `code_typet(...)` (marker only) |

**Literals/temporaries:**

| Concept | `SolType` enum | irep2/C type |
|---------|---------------|--------------|
| integer constant | `INT_CONST` | `signedbv_typet(256)` |
| string literal | `STRING_LITERAL` | `string_constantt(...).type()` |
| array literal | `ARRAY_LITERAL` | `array_typet(sub, size)` |
| new allocation | `ARRAY_CALLOC` | (allocation marker) |
| BytesStatic (runtime) | `BYTES_STATIC` | `symbol_typet(lib_prefix + "BytesStatic")` |
| BytesDynamic (runtime) | `BYTES_DYN` | `symbol_typet(lib_prefix + "BytesDynamic")` |

**Internal tuple types:**

| Concept | `SolType` enum | irep2/C type |
|---------|---------------|--------------|
| multi-return | `TUPLE_RETURNS` | `struct_typet()` |
| tuple instance | `TUPLE_INSTANCE` | (derived from function return type) |

**Note:** `bytes1`–`bytes32` inherit `BYTES_STATIC` from the `byte_static_t` member (not individually typed as `BYTES1`–`BYTES32`) and are differentiated only by the `#sol_bytesn_size` irep attribute. The `SolType` enum defines `BYTES1`–`BYTES32` for future use, but they are not yet assigned in `get_elementary_type_name()` due to downstream code paths that depend on the `BYTES_STATIC` value.

### RAII State Guards

The converter uses `ScopeGuard<T>` and `StackGuard<T>` templates for safe save/restore of mutable state:
- `current_baseContractName` — scoped contract context for `find_decl_ref`
- `current_BinOp_type` — stack-based type context for binary operator conversion

### Auxiliary Name Generation

`get_unique_name(name_prefix, id_prefix, ...)` is the shared helper for generating collision-free auxiliary variable/function/array names. Called by `get_aux_var()` and `get_aux_array_name()`.

## Debugging with C PoC Equivalents

When a Solidity KNOWNBUG might involve the ESBMC middle-end (symex, SSA) or backend (solvers), rather than the Solidity frontend (`solidity_convert_*.cpp`), **write an equivalent C program** and verify it through ESBMC's C frontend. This isolates whether the bug is in:

- **Solidity frontend** (AST→GOTO conversion): C PoC works, Solidity doesn't → frontend bug
- **Solidity C model** (`src/c2goto/library/solidity/`): C PoC works because it uses simpler primitives → model bug
- **ESBMC symex/solver engine**: C PoC also fails → engine bug (rare, file upstream)

### Technique

1. **Read the Solidity contract** and understand the expected verification result
2. **Dump the GOTO** (`--goto-functions-only --goto-functions-too`) to see what the frontend generates
3. **Write an equivalent C program** that replicates the GOTO's logic:
   - Map `uint256` → `unsigned _ExtInt(256)`, `address` → `unsigned _ExtInt(160)`
   - Map Solidity dynamic arrays → C arrays (stack or malloc)
   - Map contract structs → C structs with `$address`, `$balance` fields
   - Map `transfer()` → C function with if-else dispatch over known contract instances
   - Map the harness loop → `while(nondet_bool()) { ... }` with nondet function dispatch
   - Use `__ESBMC_assume(0)` for `revert` / insufficient balance pruning
   - Use `nondet_uint256()` / `nondet_addr()` for unconstrained extern functions
4. **Run the C program through ESBMC** with the same `--unwind` and check the result
5. **Progressively add complexity** if the simple version works:
   - Start with constants → then nondet values
   - Start with direct global access → then pointer indirection (`this->field`)
   - Start without the harness loop → then add `while(nondet_bool())`
   - Add nondet addresses (instead of constants) to match `_ESBMC_get_unique_address`

### Example: Isolating a Balance Transfer Bug

```c
// Models: new D{value: amount}(arg) with D.constructor calling transfer back
typedef unsigned _ExtInt(256) uint256_t;
typedef unsigned _ExtInt(160) addr_t;
uint256_t nondet_uint256(void);

struct Contract { addr_t address; uint256_t balance; };
struct Contract C_instance, D_instance;

void transfer(struct Contract *src, addr_t dest_addr, uint256_t val) {
    if (dest_addr == C_instance.address) {
        if (src->balance < val) __ESBMC_assume(0);
        src->balance -= val;
        C_instance.balance += val;    // direct global access
    }
}

void createAndEndowD(struct Contract *this_ptr, uint256_t amount) {
    uint256_t before = this_ptr->balance;         // pointer deref
    if (this_ptr->balance < amount) return;
    this_ptr->balance -= amount;                   // pointer deref
    struct Contract tmp_D = { .balance = amount };
    transfer(&tmp_D, this_ptr->address, (uint256_t)1);
    uint256_t after = this_ptr->balance;           // pointer deref
    assert(after == before - amount);  // should FAIL: after = before - amount + 1
}
```

If the C version correctly finds the violation but Solidity doesn't, the bug is in the Solidity-specific layer (C model functions, address model, array model), not in the frontend conversion or the ESBMC engine.

### Known Bug Categories by Layer

| Layer | Symptom | Example |
|-------|---------|---------|
| **Frontend crash** | `json type_error`, `abort`, segfault during "Converting" | Unhandled AST node kinds in type/expr converters (historically: `FunctionTypeName`, nested `make_array_elementary_type`, qualified struct constructor — all fixed 2026-04-13) |
| **C model bug** | C PoC works, Solidity produces wrong result | Dynamic array copy loses values (library_11), address model too complex for solver (send_ether_via_creation_2) |
| **Engine bug** | Both C PoC and Solidity fail the same way | (Not yet encountered in Solidity work) |

## Building & Testing Solidity

**Prerequisites:** `solc` (Solidity compiler) must be installed.

```bash
# Configure with Solidity + regression tests enabled
cd build
cmake .. -DENABLE_SOLIDITY_FRONTEND=ON -DENABLE_REGRESSION=ON

# Build
cmake --build . -j$(nproc)

# Run Solidity regression tests only
ctest -j4 -L "esbmc-solidity" --output-on-failure

# Run a single Solidity test
ctest -R "regression/esbmc-solidity/address_1"
```

**Note:** Both `ENABLE_SOLIDITY_FRONTEND` and `ENABLE_REGRESSION` must be ON. The default build (`./scripts/build.sh`) sets `ENABLE_REGRESSION=OFF`, so regression tests won't appear in `ctest -N` unless explicitly enabled.

### Test Baseline (2026-04-14)

**607 total tests** (2026-04-14): 607 pass, 0 failed, 0 timeout (~45s). Test flags: always use `--unwind N --no-unwinding-assertions` for bounded verification; omitting `--unwind` causes OOM on the SMT solver.

Growth from 570 → 607 (2026-04-14) reflects 37 new `stress_libsol_*` regression tests added during a libsolidity semanticTests sweep. After the fixes below, the full libsolidity stress sweep (1663 single-source files) shows **0 frontend crashes**.

**Slow THOROUGH tests** (>60s, avoid running in tight iteration loops):

| Test | Time | Root cause |
|------|------|------------|
| `transfer_send_2` | >1200s (KNOWNBUG) | k-induction + `--bound` causes solver timeout |
| `typedef_1` | ~420s | k-induction with complex type aliasing |
| `continue_3` | ~250s | `--unwind 20` with nested control flow |
| `break_4` | ~200s | `--unwind 20` with nested control flow |
| `bytes_17` | ~175s | `--unwind 6` with `--bound` and bytes operations |

**Tip:** Use `ctest --timeout 60` to skip slow tests during development, or run targeted tests with `ctest -R "esbmc-solidity/test_name"`.

**Adversarial tests added (2026-03-31):**

| Test | Type | What it verifies |
|------|------|-----------------|
| `bitwise_ops_1` | CORE | AND, OR, XOR, NOT, left/right shifts on uint8 |
| `bitwise_ops_2` | CORE | Incorrect bitwise assertion detected |
| `int_boundary_1` | CORE | uint8/uint256/int8/int256 min/max boundary values |
| `int_boundary_2` | CORE | uint8 overflow detection |
| `typeconv_3` | CORE | Narrowing, widening, signed↔unsigned conversions |
| `typeconv_4` | CORE | Narrowing data loss detected |
| `compound_assign_1` | CORE | All 10 compound assignment operators |
| `compound_assign_2` | CORE | Compound assignment overflow detection |
| `enum_boundary_1` | CORE | Enum values, uint conversion, comparison |
| `struct_nested_1` | CORE | Nested struct read/write, default values |
| `array_boundary_1` | CORE | Static array indexing, overwrite |
| `unchecked_block_3` | CORE | Overflow wrapping inside unchecked block |
| `unchecked_block_4` | CORE | Checked overflow detected outside unchecked |
| `perf_large_uint_1` | CORE | uint256 large arithmetic, chained ops, max value |
| `prevrandao_1` | CORE | block.prevrandao access (SUCCESSFUL) |
| `prevrandao_2` | CORE | block.prevrandao nondet value (FAILED) |
| `do_while_1` | CORE | do-while sum loop (SUCCESSFUL) |
| `do_while_2` | CORE | do-while at-least-once execution (FAILED) |
| `delete_1` | CORE | delete resets uint/bool/uint8 (SUCCESSFUL) |
| `delete_2` | CORE | delete value reset verification (FAILED) |
| `free_function_1` | CORE | Free function call + composition (SUCCESSFUL) |
| `free_function_2` | CORE | Division by zero in free function (FAILED) |

**Calldata / free-function stress tests added (2026-04-13):**

Eight `stress_*` contracts exercising calldata-array corners, library multi-returns, using-for free function binding, function pointers, and calldata slicing. See commit `c50754e6c4`.

| Test | Type | Status |
|------|------|--------|
| `stress_calldata_struct_lib_1` | CORE | Library multi-return `L.reverse(s)` — fixed via `nonContractNamesList` tuple fallback |
| `stress_calldata_array_overload_1` | CORE | Overloaded `f(uint[])/f(uint[][])/f(uint[2])` |
| `stress_calldata_bytes_return_slice_1` | CORE | `this.test(x)[2]` — nondet-length OOB fixed via `llc_nondet_bytes` length bound |
| `stress_calldata_bytes_overload_inner_1` | CORE | internal/external `f(bytes calldata b, uint)` — fixed via overload-aware lookup in nondet extcall dispatcher |
| `stress_free_fn_longdata_asm_1` | CORE | Free-function + using-for with long-string return assigned to `bytes` state var |
| `stress_free_fn_longdata_asm_2` | CORE | Duplicate of asm_1 |
| `stress_func_ptr_longdata_1` | CORE | `function() pure returns(bytes memory) f` state var + `f = S.longdata` |
| `stress_calldata_slice_abi_1` | KNOWNBUG | `abi.encode(data[start:end])` — calldata slicing + abi.encode crashes in BMC |

**libsolidity semantic-test stress tests added (2026-04-13):**

Unmodified examples copied from `solidity/test/libsolidity/semanticTests/` to
stress ESBMC frontend coverage against upstream corner cases. No
`--function` / `--focus-function` cheats.

| Test | Type | Status |
|------|------|--------|
| `stress_libsol_uninit_fnptr_legacy` | KNOWNBUG | Uninit internal-fn-ptr legacy codegen — relies on solc-specific nonzero init |
| `stress_libsol_uninit_fnptr_yul` | KNOWNBUG | Uninit internal-fn-ptr yul codegen — relies on solc-specific zero init |
| `stress_libsol_fntype_inline_array_value_call` | KNOWNBUG | `[this.f, this.g][0]{value:1}()` with `msg.value` assertion — fails under nondet extcall `msg.value` |
| `stress_libsol_ext_fn_to_address` | CORE | `.address` on external function-typed variable — fixed via nondet-address fallback |
| `stress_libsol_abi_decode_simple` | CORE | `return abi.decode(data, (uint256, bytes));` — fixed by skipping unused get_expr in nondet-tuple return path |
| `stress_libsol_udvt_wrap_unwrap` | CORE | `MyAddress.wrap;` as bare statement — fixed by eliding empty-op0 typecast in ExprStmt + full UDVT plumbing |
| `stress_libsol_udvt_abicodec` | KNOWNBUG | UDVT in function signature + `abi.decode` tuple — frontend now converts, but concrete-value assertions unsatisfiable under nondet `abi.decode` |
| `stress_libsol_try_return_function` | KNOWNBUG | `C(address(0x1234)).fun` inline contract cast + try/catch with function-type return |
| `stress_libsol_calldata_string_array` | KNOWNBUG | (Earlier batch) dynamic string array in calldata |

**libsolidity stress sweep — round 2 (2026-04-14):**

Hand-pulled from `solidity/test/libsolidity/semanticTests/` after a full
1663-file sweep with `--contract <first>` discovered residual frontend
crashes/conversion errors. Each row is one fix → one regression test.
After this round, the sweep reports 0 frontend crashes.

| Test | What it exercises | Frontend fix |
|------|-------------------|--------------|
| `stress_libsol_library_struct_as_expr` | bare `Arst.Foo;` (library struct as no-op stmt) | ExpressionStatement skips `type(...)`-valued sub-exprs |
| `stress_libsol_external_public_calldata` | abstract→memory override of `uint256[] calldata` returns | `add_offset(src, ...)` correctly slices second field of `start:length:index` (single-digit start no longer crashes `stoul`) |
| `stress_libsol_nested_tuples` | `((a,b))=(2,true)`, `(((a,),))=...`, mixed-shape nested tuple LHS | `flatten_nested_tuple_assignment` unwraps redundant outer parens; tuple-literal RHS no longer mis-classified as TUPLE_RETURNS |
| `stress_libsol_udvt_in_paren` | `(MyInt).wrap(...)`, `(MyInt).unwrap(...)` | UDVT wrap/unwrap base resolution walks paren TupleExpression wrappers |
| `stress_libsol_udvt_via_contract_name` | `C.T.wrap(x)` where `C` is the containing contract | UDVT base accepts MemberAccess and falls back to typeString |
| `stress_libsol_address_code_length` | `address(this).code.length`, `addr.code.length` | `.length` on a uint256-modeled bytes container falls back to nondet uint (only when base.type is unsignedbv/signedbv — bytes structs keep precise path) |
| `stress_libsol_modifier_local_uint8_void` | `modifier mod1 { uint8 a; uint8 b; _; } modifier mod2(bool a) { if (a) return; else _; }` | Bare `return;` inside a value-returning modifier path no longer becomes a symbol-typed assignment; replaced with `code_skipt` |
| `stress_libsol_consteval_array_length` | `uint[(a/b)*b]` constant-folded length | `get_array_pointer_type` recovers folded size from typeString when length AST node is a BinaryOperation |
| `stress_libsol_inline_array_return` | `return ([1, 2, 3, 4, 5]);` (paren-wrapped inline array) | `get_tuple_expr` strips paren around inline-array literal at function entry |
| `stress_libsol_lib_internal_call_parens` / `stress_libsol_lib_attached_call_parens` | `(L.f)()` | `find_last_parent` returns the owning OBJECT (was returning the array node when the target was an array element); `get_call_expr` unwraps paren wrappers around the callee |
| `stress_libsol_struct_event_emit` | `emit L.Ev(Item(1))` from a library | TypeMemberCall branch handles EventDefinition / ErrorDefinition referenced declarations as `code_skipt` |
| `stress_libsol_base_access_fnptr_var` | `C.x = g;` where x is a function-typed state variable | TypeMemberCall branch handles VariableDeclaration referencedDeclaration as nondet of declared type (UNDER for fn-ptr storage) |
| `stress_libsol_modifier_tuple_return_ref` / `stress_libsol_modifier_tuple_return_complex` | `function f() public m1(...) returns (uint x, uint y) {}` | Modifier-wrapped tuple-returning functions: detect empty/TUPLE_RETURNS aux return type and treat as void (no aux return variable, no return rewrite) |
| `stress_libsol_array_mapping_struct` | `mapping(K=>V)[3] n;` | Fixed-size mapping arrays now use the inf-array model (was crashing `array_type2t::get_width` during state-var zero-init); aligns with the existing dynamic mapping-array path |
| `stress_libsol_super_in_ctor_assign` / `stress_libsol_super_function_deployed` / `stress_libsol_virtual_function_deployed` | `function() internal returns (uint) x = super.f;` | `super` r-value capture (see Section J) |
| `stress_libsol_uncalled_blockhash` / `stress_libsol_uncalled_blobhash` | `(blockhash)(block.number - 1)` | `get_sol_builtin_ref` unwraps paren TupleExpression around the FunctionCall callee before symbol lookup |
| `stress_libsol_err_named_params_shadow` | `error E2(EnumType StructType, StructType EnumType); revert E2({EnumType: ..., StructType: ...});` | Event/error call branch in `get_call_expr` now reorders named arguments via `reorder_arguments` (was only applied to normal function calls) |
| `stress_libsol_pragma_range_legacy` | `pragma solidity >=0.4.0 <0.9.0;` | Version gate accepts pre-0.5 lower bound when the upper bound permits 0.5+ |

Other fixes from the same round that didn't get a dedicated regression test (e.g. cumulative refactors covered by the tests above):

- `find_last_parent` arrays-of-mappings cleanup (always return owning object).
- `get_type_description` huge-dimension `T[2**240]` no longer crashes `stoul`; degrades to DYNARRAY (sound over-approximation).
- `get_func_modifier`: `aux_var = <empty>` for bare `return;` replaced with `code_skipt` so symex never sees a symbol-typed assignment.

**Mapping-in-struct tests added (2026-04-01):**

| Test | Type | What it verifies |
|------|------|-----------------|
| `mapping_18` | CORE | `mapping(uint => uint)` inside struct: set, get, assert (SUCCESSFUL) |
| `mapping_19` | CORE | `mapping(uint => mapping(uint => uint))` (nested) inside struct (SUCCESSFUL) |

**Coverage gaps** (no tests exist):
- Bitwise operators on uint256 (OOM with default solver settings)
- Signed integer arithmetic right-shift edge cases
- ABI encoding/decoding operations
- Abstract contracts

## Structural Coverage Analysis

ESBMC supports all 4 coverage criteria on Solidity contracts. See `CLAUDE_COVERAGE.md` § "Solidity Coverage Support" for full details.

### Quick Reference

```bash
# Branch coverage (use --focus-function for targeted analysis)
esbmc contract.sol --contract MyContract --focus-function myFunc \
  --branch-coverage-claims --unwind 10 --no-unwinding-assertions

# Condition coverage (whole-contract)
esbmc contract.sol --contract MyContract \
  --condition-coverage-claims --unwind 10 --no-unwinding-assertions

# Assertion coverage
esbmc contract.sol --contract MyContract --focus-function myFunc \
  --assertion-coverage-claims --unwind 10 --no-unwinding-assertions
```

### Solidity-Specific Handling

- **Multi-tx harness auto-disabled**: The `_ESBMC_Main*` while-loop is neutralized in coverage mode; `--focus-function` is optional but recommended for performance
- **Pretty-printed expressions**: C casts and internal names are mapped to Solidity equivalents in coverage output (e.g., `msg_sender` → `msg.sender`, `this->owner` → `owner`)
- **`require()` invisible to branch coverage**: Modeled as `assume`, not a branch — this is correct Solidity semantics
- **Zero-goal summary**: Coverage summary always printed, even for straight-line code with no branches

### Future Work: Coverage

**Already works (no changes needed):**
- `--cov-report-json` — JSON report generation is language-agnostic, uses standard location format
- `scripts/cov-report.py` — HTML report generator reads JSON, works with any source file including `.sol`
- Counterexample traces — built from SSA steps, language-agnostic

**Needs new code (medium-large effort):**
- Solidity testcase generator — new `solidity_testcase_generator` class (~2000-3000 lines). Current `pytest_generator` and `ctest_generator` are Python/C-specific (type mappings, variable name mangling, output format). Solidity would need: uint256/address/bytes32 type mapping, contract state initialization, ABI encoding, and choice of test framework (Hardhat/Foundry)

## 1inch liquidity-protocol Scan (archived)

The `liquidity-protocol-master/` tree used for stress testing has been removed
from the working copy. Empirical findings from that scan, captured here so
they survive the deletion:

### Best known flags for 1inch Solidity contracts

```
esbmc <contract>.solast --sol <contract>.sol --contract <Name> \
  --no-standard-checks --unwind 1 --no-unwinding-assertions --cvc5
```

**Why these flags and not others**:
- **Z3** (default fallback) fails with `Z3 error datatype is not well-founded`
  on the recursive struct datatypes ESBMC emits for Solidity storage. Affects
  most contracts in the repo.
- **Bitwuzla** (currently auto-selected by `esbmc_parseoptions.cpp` for
  Solidity) prints `[bzla] warning: Equality over constant arrays not fully
  supported yet` and then aborts with `ERROR: SMT solver failed` on any
  contract that touches mappings via const-array equality. Do NOT rely on
  bitwuzla for mapping-heavy code.
- **CVC5** is the only backend that reaches verdicts on most of this repo —
  pass `--cvc5` explicitly to override the bitwuzla auto-select.
- **`--unwind 1` instead of 2**: the synthesized `_ESBMC_Nondet_Extcall_*`
  harness forms a mutually-recursive external-call graph through the
  contract-to-contract dispatch. Under `--unwind 2`, symex fans out and
  times out. Under `--unwind 1`, symex finishes in 40-100s per contract.
  `--unwind 1` does NOT lose coverage here — the harness is already an
  over-approximation.

### Scan result (post-fix)

Of 8 business contracts: 6 reach a verdict, 2 hit independent ESBMC bugs
unrelated to the frontend itself.

| Contract | Outcome | Notes |
|----------|---------|-------|
| BalanceAccounting | ✅ verdict | |
| FarmingVoter | ✅ verdict | required `94013c6517` (get_line_number OOB) |
| FarmingRewards | ✅ verdict | same fix |
| MooniswapFactoryGovernance | ✅ verdict | required `4c4f1a57e9` (bitwuzla auto-select under k-induction) |
| ReferralFeeReceiver | ✅ FAILED | reports `function call: not enough arguments` at MooniswapGovernance.sol line 1 — real frontend bug (stub called with wrong arity), worth chasing but NOT a crash |
| MooniswapDeployer | ✅ FAILED | same arity-mismatch pattern |
| Mooniswap | ❌ crash | see below |
| MooniswapFactory | ❌ crash | see below |

### Open bugs (liquidity-protocol)

Both are deterministic crashes, not slow runs — widening timeouts or
adjusting `--unwind` / `--slice-formula` / `--no-*-check` does NOT help.

**Mooniswap — SMT-encoding SIGSEGV (release path)**:
- Release build: reaches "Encoding remaining VCC(s) using bit-vector/
  floating-point arithmetic", then SIGSEGV in the SMT encoder.
- ASAN build: hits a DIFFERENT failure first — `Looking up index of
  nonexistant member "$address" in struct/union "UniERC20"` in
  `get_high_level_member_access` because `structureTypingMap[_cname]`'s
  `cname_set` can include a `using-for` library (UniERC20) whose struct
  has no `$address` field. Commit `ddc7a84712` guards that path with
  `has_component()`, which unblocks ASAN — but release still SIGSEGVs
  later at SMT encoding. Root cause of the release segv is NOT the
  `$address` site; it is a separate encoder-level bug that needs gdb or
  valgrind on a release build with debug symbols to locate.
- Next step: `./scripts/build.sh -b RelWithDebInfo build` then attach gdb
  or run under valgrind against Mooniswap.solast.

**MooniswapFactory — CVC5ApiException**:
- Aborts with `terminate called after throwing an instance of
  'cvc5::CVC5ApiException' what(): Given sort is not associated with the
  node manager of this solver`.
- This is a CVC5 backend bug in `src/solvers/cvc5/`: a sort created in
  one solver-instance context is later handed to a different context.
  Independent of the frontend.
- Next step: add assertions in `src/solvers/cvc5/cvc5_conv.cpp` on
  every Sort created/consumed to trace the cross-context leak.

### Debug-only frontend fixes landed during this scan

These were masked in release builds by `NDEBUG` and only fired under
Debug / Sanitizer builds, but each was a real bug that made the
sanitizer useless on the 1inch codebase:

- `94013c6517` — `get_line_number` heap-buffer-overflow: unconditionally
  did `contract_contents.begin() + (stoul(pos)+1)` and handed the result
  to `std::count`. Clamp `byte_position` to `contract_contents.size()`
  before the count.
- `537c5d6b07` — three asserts on solc 0.6.x AST nodes:
  1. `has_modifier_invocation` asserted `m.contains("kind")` when the
     very next line already guards `m.contains("kind") && ...`.
     Base-constructor ModifierInvocation nodes from solc 0.6.x legitimately
     omit "kind".
  2. `move_inheritance_to_ctor` bare-accessed `c_mdf["kind"]` without
     `contains("kind")`. Same root cause.
  3. `get_statement` / Return asserted
     `current_functionDecl.returnParameters.id == stmt.functionReturnParameters`
     — does not hold for solc-inlined base-contract bodies reached through
     modifiers or internal trampolines.
- `ddc7a84712` — `get_high_level_member_access` crashed on using-for library
  attachment (described above).

### Never do

- Do NOT reintroduce the `incremental_mode` guard in
  `esbmc_parseoptions.cpp`'s bitwuzla auto-select — `4c4f1a57e9` removed
  it because k-induction on recursive Solidity datatypes was pinned to
  Z3 and thus ALWAYS crashed with "not well-founded". Bitwuzla is a
  strict improvement there.
- Do NOT try to fix the Mooniswap/MooniswapFactory crashes by tuning
  timeouts or flag combinations — they are crashes in ESBMC code, not
  verification timeouts.

## TOD (Transaction Order Dependence) Detection

Reference paper: *TransRacer: Function Dependence-Guided Transaction Race Detection
for Smart Contracts* (ESEC/FSE 2023).

### Status snapshot

| Capability | State |
|---|---|
| TOD-State (any public state var differs after reordering) | **Shipped** (Phase 1–3) |
| Auto-discovery of candidate function pairs | **Shipped** (Phase 2, Tier-2 algorithm) |
| Targeted assertions (only on shared footprint) | **Shipped** (Phase 3) |
| One-shot auto-verify (internal solc invocation, no manual chain) | **Shipped** (Phase 3) |
| TOD-Balance (`address(this).balance` differs after reordering) | **Not implemented** — blocked by SMTChecker balance model fix |
| Multi-sender harness (different `msg.sender` per call) | **Not implemented** — Phase 4 |
| Setup phase (nondet calls reach non-initial states) | **Not implemented** — Phase 4 |
| Cross-contract TOD | Out of scope (same as TransRacer) |

### CLI surface (2 flags)

| Option | Value | Purpose |
|---|---|---|
| `--tod` | `auto` \| `f1,f2` | Run TOD detection; `auto` (or bare `--tod`) auto-discovers pairs, `f1,f2` targets one pair |
| `--dump-harness` | flag | Modifier: print the generated Solidity harness to stdout instead of verifying |

`--contract <name>` is required. Use `--tod=auto` (or plain `--tod`) for
auto-discovery, or `--tod=f1,f2` for an explicit pair. Use `=` between
`--tod` and its value; space-separated (`--tod auto`) is ambiguous with
positional args and not supported.

### Four work modes (matrix of the 2 flags)

| # | Command | What it does |
|---|---|---|
| **A** | `--contract C --tod=A,B` | Generate harness, write `tod_A_B_harness.sol` next to source, redirect cmdline to it, set `--bound --no-standard-checks --no-unwinding-assertions --unwind 2`, run BMC in-process. One verdict. |
| **B** | `--contract C --tod=A,B --dump-harness` | Same generation, print to stdout, no verification. |
| **C** | `--contract C --tod=auto` (or `--tod`) | R/W footprint analysis → list candidate pairs → write a multi-harness `tod_auto_C_harness.sol` → subprocess loop ESBMC over each `TOD_<a>_<b>` → summary line `N pair(s) — X clean, Y TOD found, Z error` + list of failing pairs. Exit non-zero if any pair fails. |
| **D** | `--contract C --tod=auto --dump-harness` | Print candidate list + multi-pair harness, no verification. |

### Core idea: harness-based reduction to BMC

Given contract `V` with functions `A` and `B`:

> ∃ args_a, args_b such that
>   state(init → A(args_a) → B(args_b)) ≠ state(init → B(args_b) → A(args_a))

This is an existential query — perfect for BMC. We materialise both orderings
in a generated harness and assert state equality at the end; a counterexample
is the witness pair of transactions that reveals TOD.

### Critical ESBMC constraint: singleton aliasing

ESBMC creates ONE global static instance per contract type (`_ESBMC_Object_<Name>`).
Two `new V()` calls share the same singleton — their state is aliased. So
`V c1 = new V(); V c2 = new V(); c1.setX(1); c2.setX(2); assert(c1.getX()==1)`
**incorrectly fails** because both write to `_ESBMC_Object_V.x`.

**Workaround — Two-Copy Rename**: duplicate the contract source with different
names (`V_C1`, `V_C2`). Each gets its own singleton. Only the contract name
changes; all internal logic is identical. Multi-pair harnesses share these two
copies (emitted once) across all `TOD_<a>_<b>` test contracts.

### Resolved ESBMC constraint: auto-bind for `new`-created instances

Previously, in unbound mode (default `--contract`), cross-contract calls
returned nondet **without executing the function body**, which caused TOD
harnesses to silently report SUCCESSFUL even on real bugs. **Fixed in
`59176dd`**: the three `!is_bound` branches in `get_contract_member_call_expr()`
(`solidity_convert_expr.cpp`) now check `is_new_created_var(base_expr_json)`
and dispatch to the bound path when the base instance was created via `new`.
The `_ESBMC_bind_cname` assignment in `get_new_object_expr` is unconditional.

Effect: a TOD harness no longer needs the user to pass `--bound` manually
for the `new V()` setup pattern. (We still pass `--bound` in the harness
auto-verify pipeline for consistency.)

### Algorithm: Tier-2 R/W footprint with intra-contract callgraph closure

Module: `src/solidity-frontend/solidity_tod_analysis.{h,cpp}`.

**Step 1 — per-callable R/W footprint** (single AST walk):
- `Assignment.leftHandSide` → write target (recurse with `is_write_target=true`)
- `Assignment.rightHandSide` → read context
- Compound assignment (`+=` etc.) → re-visit LHS as a read too
- `UnaryOperation` with `++` / `--` / `delete` → write on argument
- `IndexAccess.baseExpression` / `MemberAccess.expression` → inherit parent
  write context (so `m[k] = v` writes `m`, reads `k`)
- Any `Identifier` whose `referencedDeclaration` is a state variable id → R or W
- `FunctionCall` whose callee is a same-contract callable → call edge
- `ModifierInvocation` → modifier body folded in via call edge

**Step 2 — call-graph closure**:
```
footprint(f) = local(f) ∪ ⋃_{c ∈ callees(f)} footprint(c)
```
Iterated to a fixed point. External calls (`c.foo()`, `this.foo()`) are
**conservatively skipped** — out of scope for Tier 2 (would belong in Tier 3).

**Step 3 — pair candidacy**:
```
W(a) ∩ (R(b) ∪ W(b))  ∪  W(b) ∩ (R(a) ∪ W(a))  ≠ ∅
```
Pair filters:
- Both functions must be `public` or `external`
- Skip `view` / `pure` (no writes by definition)
- Skip `constructor` / `fallback` / `receive` (cannot be reordered)
- Skip self-pairs and symmetric duplicates (sort lexicographically, `a < b`)

### Harness emission (`solidity_tod_harness.{h,cpp}`)

Layout of an auto-generated `.sol`:

```solidity
// Header: pair list + verify command
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// ===== Copy 1 (V renamed to V_C1) =====
contract V_C1 { /* original verbatim */ }
// ===== Copy 2 =====
contract V_C2 { /* identical, V renamed to V_C2 */ }

// ===== TOD Harness contracts =====
// Targeted state variables (referenced by BOTH functions):
//   - x
contract TOD_A_B {
    function test(/* ctor + A + B params, prefixed to avoid collisions */) public {
        V_C1 c1 = new V_C1(...);
        V_C2 c2 = new V_C2(...);
        c1.A(...); c1.B(...);   // order 1
        c2.B(...); c2.A(...);   // order 2
        assert(c1.x() == c2.x());
    }
}
// ... one TOD_<a>_<b> per discovered pair
```

**Targeted assertions** consume the **closure-closed** R/W footprint, not just
body-local references. So a public function that only writes `x` via an
internal helper still produces the right `assert(c1.x() == c2.x())`.

**Mapping handling** (`mapping(K => V)` and `mapping(K1 => mapping(K2 => V))`):
the harness collects every parameter of type `K` from `A` and `B`, emits one
assertion per Cartesian-product key tuple. Three or more nested levels are
skipped with an explanatory comment.

### What "TOD" means in the current implementation

**TOD-State only.** The harness asserts equality on every public state variable
in the closure intersection. Any field of type `uint` / `int` / `address` /
`bool` / `bytes32` / `mapping(...)` qualifies — including a state variable
literally named `balance` (declared as `uint public balance;`).

**TOD-Balance is NOT covered.** Real ETH balance lives in the contract's hidden
`$balance` field, not in user-declared state vars. The R/W analyser does not
flag `transfer` / `send` / `call{value:}` / `selfdestruct` as writes; the
harness does not emit `assert(address(c1).balance == address(c2).balance)`.
Adding it requires:

1. Fixing the SMTChecker-style balance model so `address(this).balance` reads
   from `this->$balance` instead of allocating a fresh nondet via
   `get_aux_property_function`. **This is a prerequisite, not optional.**
   See "Balance model gap" docs in `solidity_convert_builtin.cpp:100-115` and
   `solidity_convert_contract.cpp:645-654`.
2. Extending `solidity_tod_analysis` to mark the value-transferring builtins
   as W on a virtual `__balance` token, so the candidate finder pairs up
   value-transferring functions correctly.
3. Emitting balance equality asserts in `emit_harness_contract` whenever
   `__balance` is in the shared footprint.

### Open work list (priority order)

1. ~~**SMTChecker balance model fix**~~ — done (`f507686`).
2. ~~**TOD-Balance** detection~~ — done (`c492d09`).
3. ~~**Option consolidation**~~ — done: folded `--tod-functions` and
   `--tod-auto` into a single `--tod[=auto|=f1,f2]` with `--dump-harness`
   as the only modifier.
4. **Phase 4 — multi-sender / setup phase**: proxy contracts for distinct
   `msg.sender` per call; optional nondet setup-phase call sequence to reach
   non-initial states (TransRacer reports 63.1% of races need state
   exploration).

### Known limitations of the current implementation

- **Same-sender only**: all harness calls share `msg.sender = address(this)`
  (the harness contract). Misses TOD requiring distinct senders (e.g.
  `approve` + `transferFrom`).
- **Constructor-state only**: no setup phase between constructor and the
  reordered call pair.
- **External calls dropped from R/W closure**: `c.foo()` and `this.foo()`
  contribute nothing to the footprint, so a pair that interacts only through
  an external surface may not be flagged as a candidate.
- **3+ level nested mapping**: assertions are skipped with a comment.

### Regression coverage

| Test | Mode | Property |
|---|---|---|
| `tod_counter_fail` | A (single pair, auto-verify) | Both functions write `x` → FAILED |
| `tod_disjoint_pass` | A | Disjoint state → SUCCESSFUL |
| `tod_auto_multi` | C | Two independent pairs both flagged |
| `tod_auto_closure` | C | Footprint closure across internal helpers |
| `tod_auto_clean` | C | Disjoint state → "0 candidates, exit cleanly" |
| `ext_call_new_autobind_pass` / `_fail` | n/a | Auto-bind fix prerequisite |

