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
- `< 0.5.0`: rejected (unsupported)
- `0.5.0 – 0.7.0`: warning (may cause unexpected behaviour)
- `>= 0.7.0`: fully supported (tested against 0.8.x)

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
- `x = 42; assert(x == 42)` where `x` is a state variable will **fail** because the nondet external call dispatch (`_ESBMC_Nondet_Extcall`) can re-enter the function and modify state between the assignment and assertion
- To verify state-dependent properties, use `--contract ContractName` instead (which runs the constructor first and then dispatches all public functions)
- `--function` is best for verifying **function-local** properties (pure/view functions, local variable logic) and for **over-approximate** analysis where any input state is valid
- `constant` state variable values are only available in `--contract` mode (where the initializer runs)

**When to use which:**
| Mode | State vars | Harness | Best for |
|------|-----------|---------|----------|
| `--contract C` | Initialized by constructor | Constructor + nondet dispatch of all public/external functions | Testing contract invariants, state-dependent assertions |
| `--function f` | Nondet (arbitrary) | No constructor, no dispatch loop; `f` is called once with symbolic state | Function-local logic, over-approximate soundness |
| `--contract C --focus-function f` | Initialized by constructor | Constructor + nondet dispatch restricted to `f` only | Verifying `f` after proper construction without exploring other public functions |

### `--focus-function` Mode Semantics

`--focus-function funcName` sits between `--contract` and `--function`:

- **Like `--contract`**: the constructor runs, state variables get their declared initializers, inheritance linearization applies, and the whole `_ESBMC_Main_<C>` harness is built.
- **Like `--function`**: only the named function is verified — but unlike `--function`, constructor state is preserved.
- **Unlike both**: the nondet dispatch loop inside `_ESBMC_Nondet_Extcall_<C>` filters out every public/external method except `funcName`, so the BMC engine never explores paths that call other functions on the target contract. This is a pure verification-cost optimization.

**Requirements:**
- Requires `--contract <name>` to pick the target contract when the source declares more than one contract. If the source has exactly one (non-library, non-interface) contract, `--contract` is auto-inferred.
- Incompatible with `--function` — the two modes have opposite semantics (nondet state vs. constructor-initialized state).
- `funcName` must be a `public` or `external` method on the target contract (not the constructor, not `receive`/`fallback`).
- Works with both `--bound` and `--unbound`. In `--bound` mode, other contracts reached via cross-contract calls still dispatch their full public surface — the filter only applies to the focus target contract's own harness.

**Implementation:** the filter lives in `solidity_convert_constructor.cpp:get_unbound_function()` inside the `for (const auto &method : methods)` loop: when `focus_func` is set and `c_name == *tgt_cnt_set.begin()`, methods whose name differs from `focus_func` are skipped before the if-branch is emitted. Validation (contract disambiguation, function existence, `--function` conflict) happens in `solidity_convert.cpp:convert()` right after `populate_auxiliary_vars()`.

**Tests:** see `focus_function_1..4` for: focus-function isolates `f` after construction (pass), full harness exposes a `g`-before-`f` violation that focus-function hides (fail), `--function f` misses constructor init (fail), unbound single-contract auto-inference (pass).

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

### Resolved Bugs (2026-03-31)

All 5 diagnosed bugs have been fixed. Summary:

| Bug | Description | Root cause | Fix location |
|-----|-------------|-----------|--------------|
| **1** | Sub-256-bit overflow check missed `uint8`/`uint16` overflow | C integer promotion widens to `signed int` before arithmetic; `overflow2tc` checks at 32-bit width | `goto_check.cpp`: narrowing cast check + narrowing assignment check for `.sol` files; suppressed inside `unchecked` blocks |
| **2** | Large constants like `10**36` silently evaluated to 0 | solc truncates `typeString` with `"..."` notation; `string2integer()` returns 0 for non-alphanumeric input | `solidity_grammar.cpp:785`: skip `LiteralWithRational` when `typeString` contains `"..."`, fall through to `BO_Pow` BigInt path |
| **3** | `unchecked { }` blocks had no effect on overflow checking | `UncheckedBlock` AST nodes parsed as normal `Block` | `solidity_convert_stmt.cpp`: tag locations with `#sol_unchecked`; `goto_check.cpp`: skip overflow checks when tag present |
| **4** | `a ** b` (non-constant) crashed with "unexpected typecast to fixedbv" | Frontend called `double pow()` (floatbv) but sol64.goto compiled with `--fixedbv` → type mismatch | `solidity_builtins.c`: new `sol_pow_uint(uint256_t, uint256_t)` integer pow; `solidity_convert_expr.cpp`: call `sol_pow_uint` instead of `pow` |
| **5** | Z3 sort mismatch on mapping struct fields | c2goto padding shifted struct component indices; frontend used hardcoded `at(1)` | `solidity_mapping.c`: `__attribute__((packed))`; `solidity_convert_decl.cpp`: name-based component lookup |

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

**Semantic change:** The global length variable is visible to re-entrant calls in `--unbound` mode, which is MORE correct than the old model (where the C model's internal tracking was opaque to the solver). Test `github_2580_1` updated to use `--function` to avoid false reentrancy effects.

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

Root causes of remaining gaps: `make_array_elementary_type()` has comment `"current implement does not consider Multi-Dimensional Arrays"` (`solidity_convert_util.cpp:387`); array size extraction regex `.*\\[([0-9]+)\\]` captures only one dimension (`solidity_convert_util.cpp:430`).

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
| **`using A for B`** | Partial | Library function dispatch works (including storage ref copy-back); custom operator dispatch not supported |
| **Bitwise on dynamic bytes** | Static only | Ops limited to `bytesN`, not dynamic `bytes` (`solidity_convert_expr.cpp:2155`) |
| **`constant`/`immutable`** | Partial | `constant` works; `immutable` may not enforce set-once |
| **Named return parameters** | ✅ Fixed (2026-04-05) | Single named return: DECL + zero-init + implicit return. Tuple named returns still use existing tuple machinery. |
| **Function overloading** | Partial | Same-name different-param functions may misresolve in `find_decl_ref` |
| **receive/fallback** | ✓ Works | `receive() external payable` and `fallback() external [payable]` fully supported; tests: `receive_1/2`, `fallback_1/2` |
| **Fallback with params** | Partial | Basic fallback exists; `fallback(bytes calldata) returns (bytes memory)` params ignored |
| **Custom storage layout** | ✓ Works (2026-04-07) | `contract C layout at <expr>` (Solidity 0.8.29+) — AST parses without error; ESBMC ignores storage slots (models state vars as struct members); tests: `layout_1/2` |
| **Array slices (`IndexRangeAccess`)** | ✅ Over-approx (2026-04-09) | `data[:4]`, `data[1:3]` on calldata arrays/bytes — modeled as nondet; tests: `array_slice_1/2` |
| **`abi.decode()`** | ✓ Nondet (CORE) | Parses `(T)` type tuple via `ElementaryTypeNameExpression`; result is an unconstrained nondet value. Tests: `abi_decode_1/2/3` |
| **`abi.encodeCall()`** | ✓ Identity (CORE) | Interface/function pointer (`ITarget.transfer`) accepted by converter; canonical signature rebuilt from referenced `FunctionDefinition`. Tests: `abi_encodeCall_1/2/3` |
| **`mulmod(MAX,MAX,k)`** | KNOWNBUG | 512-bit model is correct but ESBMC constant evaluator crashes (SIGFPE) when both operands are near `type(uint256).max` |
| **Inline assembly / Yul** | ✅ Havoc (2026-04-05) | Over-approximated: all externally referenced variables are havoc'd to nondet. Does not model Yul semantics. |
| **Function types** | Not supported | `function(uint) returns (bool)` as first-class values |
| **`using for` + custom operators** | Not supported | Operator dispatch table per type |
| **Transient storage (EIP-1153)** | Not supported | New data location model |
| **User-defined value types** | ✓ Basic (2026-04-07) | `type C is V` with `.wrap()`/`.unwrap()` works; `using { f as op }` custom operators NOT supported; tests: `udv_type_1/2` |

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

### Test Baseline (2026-04-11)

**503 total tests** (2026-04-11): 503 pass, 0 failed, 0 timeout (46s). Test flags: always use `--unwind N --no-unwinding-assertions` for bounded verification; omitting `--unwind` causes OOM on the SMT solver.

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
# Branch coverage (recommended: use --function for targeted analysis)
esbmc contract.sol --contract MyContract --function myFunc \
  --branch-coverage-claims --unwind 10 --no-unwinding-assertions

# Condition coverage (works without --function)
esbmc contract.sol --contract MyContract \
  --condition-coverage-claims --unwind 10 --no-unwinding-assertions

# Assertion coverage
esbmc contract.sol --contract MyContract --function myFunc \
  --assertion-coverage-claims --unwind 10 --no-unwinding-assertions
```

### Solidity-Specific Handling

- **Multi-tx harness auto-disabled**: The `_ESBMC_Main*` while-loop is neutralized in coverage mode so `--function` is optional (but recommended for performance)
- **Modifier prefix matching**: `--function deposit` matches `deposit_onlyPositive`
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
