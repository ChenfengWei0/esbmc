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
| `solidity_grammar.cpp/h` | Grammar enums and type mapping tables |
| `solidity_language.cpp/h` | Language plugin interface |
| `pattern_check.cpp/h` | Vulnerability pattern detection (e.g. SWC-115 tx.origin) |

## Solidity Operational Models (c2goto)

Solidity built-in types, variables, and functions are implemented as C operational models in `src/c2goto/library/solidity/`. These are pre-compiled into a **separate goto binary** (`sol64.goto`) via the c2goto pipeline and embedded into the ESBMC binary. At runtime, `add_cprover_library()` loads from `sol64_buf` (not the full `clib64`) for fast symbol loading.

| File | Content |
|------|---------|
| `solidity_types.h` | Type definitions: `int256_t`, `uint256_t`, `address_t` via `_BitInt(256)`, `sol_llc_ret` struct |
| `solidity_builtins.c` | Global variables (msg/tx/block) and built-in functions (keccak256, gasleft, etc.) |
| `solidity_bytes.c` | `BytesStatic`/`BytesDynamic` structs and 60+ byte manipulation functions |
| `solidity_mapping.c` | Mapping data structures (`_ESBMC_Mapping`, `mapping_t`, and `_fast` variants) |
| `solidity_array.c` | Dynamic array tracking: push, pop, length, arrcpy |
| `solidity_units.c` | Ether/time unit conversions (wei, gwei, ether, seconds, days, etc.) |
| `solidity_string.c` | String operations, integer-to-string, hex conversion |
| `solidity_address.c` | Address management, contract object tracking |
| `solidity_misc.c` | Min/max, reentrancy check, state initialization |

### c2goto Architecture

- **Build pipeline**: Solidity `.c` files → `c2goto --64 --fixedbv` → `sol64.goto` (524KB) → `flail.py` → `sol64.c` (byte array) → linked into esbmc binary
- **Separate from clib64**: Solidity models are NOT compiled into `clib64.goto`. This avoids reading the full 1.9MB clib when only Solidity symbols are needed.
- **Loading path**: `add_cprover_library()` in `cprover_library.cpp` detects `language->id() == "solidity_ast"` and reads from `sol64_buf` instead of `clib64_buf`. No whitelist filtering needed since sol64 contains only Solidity symbols.
- **Whitelist**: The `solidity_c_models` vector in `cprover_library.cpp` is retained for reference but not used for filtering (all sol64 symbols are loaded directly).
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

## Solidity Language Support Audit (2026-03-30)

Comprehensive audit against Solidity 0.8.x official documentation. Minimum supported version: 0.5.0 (recommended: 0.8.x).

### Fully Supported

| Category | Features |
|----------|----------|
| **Value types** | `bool`, `uint8`-`uint256`, `int8`-`int256`, `address`/`address payable`, `string`, `bytes1`-`bytes32`, `bytes` (dynamic) |
| **Composite types** | `struct` (nested, with arrays), `enum`, fixed arrays `T[N]`, dynamic arrays `T[]` (push/pop/length), multi-dimensional arrays |
| **Single-level mapping** | `mapping(K => V)` — modeled via infinite arrays |
| **Operators** | All arithmetic (`+`,`-`,`*`,`/`,`%`,`**`), bitwise, comparison, logical, compound assignment (`+=` etc.), prefix/postfix `++`/`--`, ternary `?:` |
| **Control flow** | `if`/`else`, `for`, `while`, `break`, `continue`, `return` (including multi-value via tuples) |
| **Contract core** | Contract/library/interface definitions, functions (regular/constructor/receive/fallback), state variables, visibility (`public`/`private`/`internal`/`external`), state mutability (`pure`/`view`/`payable`) |
| **Modifiers** | Definition, parameters, placeholder `_` expansion, chaining |
| **Events** | `event` definition, `emit` (modeled as function calls) |
| **Custom errors** | `error` definition, `revert CustomError(...)` (Solidity 0.8.4+) |
| **Inheritance** | Multiple inheritance with C3 linearization, `virtual`/`override`, abstract contracts, interfaces |
| **Libraries** | Library contracts, library function calls |
| **Import** | Multi-file with topological sort (17 tests) |
| **Globals** | `msg.sender`/`.value`/`.sig`/`.data`, `block.number`/`.timestamp`/`.coinbase`/`.difficulty`/`.gaslimit`/`.chainid`/`.basefee`, `tx.origin`/`.gasprice` |
| **Built-ins** | `require()`, `assert()`, `revert()`, `keccak256()`, `sha256()`, `ripemd160()`, `ecrecover()`, `addmod()`, `mulmod()`, `gasleft()`, `selfdestruct()` |
| **ABI encoding** | `abi.encode()`, `abi.encodePacked()`, `abi.encodeWithSelector()`, `abi.encodeWithSignature()`, `abi.encodeCall()` |
| **Address members** | `.balance`, `.code`, `.codehash`, `.transfer()`, `.send()`, `.call()`, `.delegatecall()`, `.staticcall()` |
| **Type info** | `type(T).min`, `type(T).max`, `type(C).creationCode` |
| **Units** | Ether (`wei`/`gwei`/`ether`), time (`seconds`/`minutes`/`hours`/`days`/`weeks`) |
| **Unchecked** | `unchecked { ... }` blocks (parsed as normal blocks) |
| **Verification** | Overflow/underflow, division-by-zero, reentrancy detection (mutex-based), bound/unbound address modes, whole-contract verification |

### Partially Supported / Compromises

| Feature | Status | Detail |
|---------|--------|--------|
| **Nested mapping** | Not supported | `mapping(K1 => mapping(K2 => V))` rejected with error (`solidity_grammar.cpp:230`) |
| **Try/Catch** | Recognized, not converted | AST node registered but conversion aborts: `"Try/Catch is not fully supported yet"` (`solidity_convert_stmt.cpp:631`) |
| **Nested tuple destructuring** | Not supported | `((a,b),c) = ...` marked TODO (`solidity_convert_expr.cpp:2212`, `solidity_convert_tuple.cpp:59`) |
| **Data location semantics** | Parsed, not modeled | `storage`/`memory`/`calldata` qualifiers recognized in typeStrings but reference-vs-copy and immutability semantics not enforced |
| **`memory` param copy** | Known gap | TODO: memory type parameters should create copies (`solidity_convert_call.cpp:102`) |
| **Mapping index range** | Truncated | Index limited to `unsigned long long` — keys >64-bit may collide (`solidity_convert_expr.cpp:1058`) |
| **Crypto functions** | Abstracted | `keccak256` etc. return `nondet` values (standard model checking abstraction), not real hashes |
| **`using A for B`** | Parsed, skipped | `UsingForDef` AST node handled but does not alter operator dispatch (`solidity_convert_decl.cpp:53`) |
| **Bitwise on dynamic bytes** | Static only | Bitwise ops limited to `bytesN`, not dynamic `bytes` (`solidity_convert_expr.cpp:2155`) |
| **`constant`/`immutable`** | Partial | `constant` handled at file and contract level; `immutable` may not enforce set-once semantics |

### Not Supported

| Feature | Notes |
|---------|-------|
| **`do-while` loops** | No grammar entry, no handler |
| **Inline assembly / Yul** | No handler at all — blocks many optimized production contracts (OpenZeppelin etc.) |
| **`fixed`/`ufixed` types** | TODO stubs only (`solidity_grammar.h:213-214`); Solidity itself hasn't finalized these |
| **`abi.decode()`** | No handler |
| **`delete` operator** | No handler (reset storage to default) |
| **Function types** | `function(uint) returns (bool)` as first-class values not supported |
| **Free functions** | File-level functions (outside contracts) not supported (only file-level constants) |
| **`bytes.concat()`** | Not handled (only `string.concat` via library) |
| **`type(C).runtimeCode`** | Not handled |
| **`type(I).interfaceId`** | Not handled |
| **`block.prevrandao`** | Post-Merge replacement for `block.difficulty` not handled |
| **`block.blobbasefee`** / `blobhash()` | EIP-4844, not handled |
| **Transient storage** | EIP-1153 (`transient` keyword), not handled |
| **Global `using for` + custom operators** | `using {add as +} for MyType global` not supported |
| **`erc7201()`** | Storage namespace hash, not handled |
| **Function overloading** | Same-name different-param functions within one contract may have resolution issues |

### Priority for Future Work

**High impact** (blocks real-world contracts):
1. Nested mapping — required by ERC20 (`allowance`), ERC721, most DeFi
2. Inline assembly — used pervasively in optimized contracts
3. `do-while` loops — simple to implement
4. `delete` operator — common storage cleanup pattern

**Medium impact**:
5. Try/Catch — DeFi error handling
6. `abi.decode()` — low-level call return parsing
7. Data location semantics — soundness improvement
8. Free functions — increasingly common pattern

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
