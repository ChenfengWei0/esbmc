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

### Known Bugs (diagnosed 2026-03-31)

#### Bug 1: Sub-256-bit overflow check ineffective (HIGH severity)

`--unsigned-overflow-check` does not detect overflow for uint8/uint16/etc. Example: `uint8 x = 255; x + 1` reports VERIFICATION SUCCESSFUL.

**Root cause:** The Solidity frontend creates binary ops with the correct narrow type (e.g. `unsignedbv_typet(8)`), and `goto_check.cpp:278` inserts `overflow2tc(expr)`. However, the overflow predicate evaluates incorrectly for narrow types — the SMT-level encoding may widen operands or the overflow intrinsic may not account for sub-256-bit wrap semantics.

**Affected code path:** `goto_check.cpp:278-319` (`overflow_check`), `smt_casts.cpp` (overflow predicate encoding)

**Regression tests:** `int_boundary_2` (KNOWNBUG), `compound_assign_2` (KNOWNBUG), `unchecked_block_4` (KNOWNBUG)

**Fix strategy:** After each narrow-type arithmetic operation, insert an explicit range-check assertion: `assert(result <= type_max)` at the GOTO level. This can be done in `goto_check.cpp`'s `overflow_check` for Solidity when the type width < 256. Alternatively, fix `overflow2tc` to correctly model overflow for arbitrary bit-widths.

#### Bug 2: Large constant-folded literals truncated to zero (MEDIUM severity)

Solidity AST expressions like `10**36` whose `typeString` is abbreviated by solc (e.g. `"int_const 1000...(29 digits omitted)...0000"`) are silently evaluated to 0.

**Root cause:** `solidity_grammar.cpp:783-787` classifies BinaryOperation nodes as `LiteralWithRational` when their `typeString` starts with `"int_const"` AND the node has no `"value"` field (BinaryOperations don't). The `LiteralWithRational` handler (`solidity_convert_expr.cpp:89-103`) extracts the value from the truncated `typeString` and calls `string2integer()` (`mp_arith.cpp:39-46`), which returns 0 for any string containing non-alphanumeric characters (`...`, `(`, `)`, spaces).

**Fix (applied 2026-03-31):** Added `typeString.find("...") == std::string::npos` guard in `solidity_grammar.cpp:785`. When solc truncates a large constant's typeString with `...`, the expression now falls through to the BinaryOperatorClass path (e.g. `BO_Pow`), which correctly computes the value via BigInt arithmetic. Non-truncated rational expressions (e.g. `0.5 * 10 ether`) still use the `LiteralWithRational` path as before.

#### Bug 3: `unchecked` block semantics not implemented (LOW severity, masked)

`unchecked { ... }` blocks are parsed as normal blocks (`CLAUDE_Solidity.md` line 113). Tests pass because Bug 1 (overflow check ineffective) masks the missing unchecked semantics — overflow checks don't trigger regardless.

**Fix strategy:** After Bug 1 is fixed, propagate the `unchecked` flag from AST through GOTO conversion. In `goto_check.cpp`, skip overflow assertion insertion for expressions originating from unchecked blocks. Requires an irep attribute (e.g. `#unchecked`) set during `solidity_convert_stmt.cpp` block conversion.

#### Bug 4: fixedbv typecast error in pow() (THOROUGH-only)

`pow()` for non-constant exponentiation uses `double_type()` which becomes `fixedbv` when `--fixedbv` is set. The SMT cast handler `convert_typecast_to_fixedbv_nonint` (`smt_casts.cpp:39`) is missing a `floatbv` source type case.

**Affected tests:** inheritance_6, op_binary_5, op_binary_8 (all THOROUGH with `--k-induction`/`--bound`)

**Fix strategy:** Add `floatbv_type` handling in `smt_casts.cpp:22-41` (`convert_typecast_to_fixedbv_nonint`), similar to the existing `convert_typecast_to_fpbv` function.

#### Bug 5: Z3 sort mismatch in mapping struct (THOROUGH-only)

`mapping_t` struct uses bitfield `address_t addr : 160` in the C library (`solidity_mapping.c:20`). The bitfield representation from the C frontend (c2goto) doesn't match the C++ frontend's `unsignedbv_typet(160)`, causing a Z3 sort mismatch during SMT encoding.

**Affected tests:** mapping_13, mapping_16, transfer_send_2 (all THOROUGH)

**Fix strategy:** Remove the bitfield declaration in `solidity_mapping.c` — use `address_t addr;` instead of `address_t addr : 160;` since the bitfield width equals the type width and is redundant.

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
| **Unchecked** | `unchecked { ... }` blocks (parsed as normal blocks; see Bug 3 — semantics not enforced) |
| **Verification** | Overflow/underflow (uint256 only; see Bug 1), division-by-zero, reentrancy detection (mutex-based), bound/unbound address modes, whole-contract verification |

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

**Critical bugs** (verification soundness):
1. **Fix Bug 2** (large literal truncation) — one-line fix in `solidity_grammar.cpp:783`, high confidence
2. **Fix Bug 1** (sub-256-bit overflow) — requires `goto_check.cpp` changes, affects all narrow-type contracts
3. **Fix Bug 3** (unchecked semantics) — depends on Bug 1 being fixed first

**High impact** (blocks real-world contracts):
4. Nested mapping — required by ERC20 (`allowance`), ERC721, most DeFi
5. Inline assembly — used pervasively in optimized contracts
6. `do-while` loops — simple to implement
7. `delete` operator — common storage cleanup pattern

**Medium impact**:
8. Try/Catch — DeFi error handling
9. `abi.decode()` — low-level call return parsing
10. Data location semantics — soundness improvement
11. Free functions — increasingly common pattern

**Backend fixes** (THOROUGH-only, lower priority):
12. Fix Bug 4 (fixedbv typecast) — `smt_casts.cpp` missing case
13. Fix Bug 5 (mapping bitfield mismatch) — `solidity_mapping.c` bitfield removal

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

### Test Baseline (2026-03-31)

**356 total tests**: 350 CORE pass, 6 THOROUGH fail (Bugs 4 & 5), 3 KNOWNBUG (Bug 1). Test flags: always use `--unwind N --no-unwinding-assertions` for bounded verification; omitting `--unwind` causes OOM on the SMT solver.

**Adversarial tests added (2026-03-31):**

| Test | Type | What it verifies |
|------|------|-----------------|
| `bitwise_ops_1` | CORE | AND, OR, XOR, NOT, left/right shifts on uint8 |
| `bitwise_ops_2` | CORE | Incorrect bitwise assertion detected |
| `int_boundary_1` | CORE | uint8/uint256/int8/int256 min/max boundary values |
| `int_boundary_2` | KNOWNBUG | uint8 overflow detection (Bug 1) |
| `typeconv_3` | CORE | Narrowing, widening, signed↔unsigned conversions |
| `typeconv_4` | CORE | Narrowing data loss detected |
| `compound_assign_1` | CORE | All 10 compound assignment operators |
| `compound_assign_2` | KNOWNBUG | Compound assignment overflow (Bug 1) |
| `enum_boundary_1` | CORE | Enum values, uint conversion, comparison |
| `struct_nested_1` | CORE | Nested struct read/write, default values |
| `array_boundary_1` | CORE | Static array indexing, overwrite |
| `unchecked_block_3` | CORE | Overflow wrapping inside unchecked block |
| `unchecked_block_4` | KNOWNBUG | Checked overflow detection (Bug 1) |
| `perf_large_uint_1` | CORE | uint256 large arithmetic, chained ops, max value |

**Coverage gaps** (no tests exist):
- Bitwise operators on uint256 (OOM with default solver settings)
- Signed integer arithmetic right-shift edge cases
- ABI encoding/decoding operations
- Hash function return values
- Fallback/receive functions
- Abstract contracts
- Storage layout / packing
