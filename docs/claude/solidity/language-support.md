# Solidity Language Support Audit

Comprehensive audit against Solidity 0.8.x official documentation. Minimum supported version: 0.5.0 (recommended: 0.8.x). See also [approximation-ledger.md](approximation-ledger.md) for the full list of deliberate trade-offs.

## Fully Supported

| Category | Features |
|----------|----------|
| **Value types** | `bool`, `uint8`-`uint256`, `int8`-`int256`, `address`/`address payable`, `string`, `bytes1`-`bytes32`, `bytes` (dynamic) |
| **Composite types** | `struct` (nested, with arrays), `enum`, fixed arrays `T[N]`, dynamic arrays `T[]` (push/pop/length), multi-dimensional arrays |
| **Mapping** | `mapping(K => V)`, nested `mapping(K1 => mapping(K2 => V))`, mapping-in-struct, `mapping(K => V)[]` (array of mappings), `mapping(K => V)[N]` (fixed array of mappings), `mapping(K => V[])` (mapping of scalar dynamic arrays), `mapping(K => T[N])` and `mapping(K => T[M][N])` (mapping of fixed / 2D fixed arrays — both `is_new_expr` and unbound-state-var paths routed through `map_fixed_arr_get`) — modelled via (nested) infinite SMT arrays; struct mapping fields are lifted to global arrays; mapping arrays use auxiliary `_mapping_arr_len`; mapping-of-dynarray uses auxiliary `_mapdynarr_len`. |
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

## Known Limitations and Deficiencies

### A. Crypto Functions — Deterministic Bijective Abstraction

`keccak256`, `sha256`, `ripemd160`, `ecrecover` are modeled as **deterministic bijective functions** using simple bitvector transformations. Each function uses a distinct transformation to ensure cross-function outputs differ:

| Function | Model (`solidity_crypto.c`) | Properties |
|----------|------|------------|
| `keccak256(x)` | `return ~x;` | Functional consistency ✓; bijective (zero collisions) ✓ |
| `sha256(x)` | `return ~(x+1);` | Functional consistency ✓; bijective ✓; differs from keccak256 |
| `ripemd160(x)` | `return (address_t)(~(x+2));` | 256→160 bit truncation after transform |
| `ecrecover(hash,v,r,s)` | `return (address_t)(~hash);` | Ignores v/r/s — no signature verification |

### A2. Modular Arithmetic — 512-bit Arbitrary Precision

`addmod` and `mulmod` use a 512-bit intermediate type (`_BitInt(512)`) to implement arbitrary-precision arithmetic per the Solidity spec (no wrap at 2^256):

| Function | Model (`solidity_builtins.c`) | Properties |
|----------|------|------------|
| `addmod(x,y,k)` | `(uint512_t)x + (uint512_t)y) % (uint512_t)k` | Correct for all inputs ✓ |
| `mulmod(x,y,k)` | `(uint512_t)x * (uint512_t)y) % (uint512_t)k` | Correct ✓; KNOWNBUG: MAX\*MAX crashes ESBMC constant evaluator (SIGFPE) |

`abi.encode*` functions are modeled as **identity functions** (`return x;`) in `solidity_abi.c` so that `keccak256(abi.encodePacked(x))` is deterministic in `x`. Multi-argument `abi.encodePacked(a, b, c)` only captures the first argument; the rest are evaluated but discarded. `abi.decode` is modeled as **nondet** (over-approximation).

**Properties:**
- **Functional consistency**: `keccak256(x) == keccak256(x)` always holds ✓
- **Injectivity**: `x != y → keccak256(x) != keccak256(y)` always holds ✓
- **String equality via hash**: `keccak256(abi.encodePacked(s1)) == keccak256(abi.encodePacked(s2))` ↔ `s1 == s2` ✓
- **O(1) SMT cost**: single BV NOT operation per hash call
- **Limitation**: concrete hash values are not computed; `assert(keccak256(0) == 0xc5d2...)` is not provable
- **Limitation**: `abi.decode` is nondet — decoded values are unconstrained; `encode(x) → decode → y` does not guarantee `y == x`. Guarded round-trips still work (e.g. `require(decoded > 0); assert(decoded > 0);`).

### A3. Dynamic Array State Variables — SMT Array Model

State-variable dynamic arrays (`uint[] public items`) are modelled as **infinite SMT arrays + auxiliary length variable** instead of the previous pointer + C model (`malloc`/`realloc`). This enables the solver to track element values through `push()` operations:

```solidity
items.push(100);
assert(items[0] == 100); // VERIFICATION SUCCESSFUL ✓
```

**Implementation:**
- `solidity_convert_decl.cpp`: State-var DYNARRAY type changed from `pointer_typet(elem)` to `array_typet(elem, infinity)` with `#sol_dynarray_state` flag; auxiliary `_dynarray_len` variable created
- `solidity_convert_ref.cpp`: `push(v)` → `items[len] = v; len++`; `pop()` → `len--`; `.length` → `len`
- `solidity_convert_expr.cpp`: Literal assignment `items = [1,2,3]` generates element-wise writes + length set; `new uint[](n)` sets length = n
- Global static lifetime (like mappings): not a struct member, resolved directly via symbol

**Semantic note:** The global length variable is visible to re-entrant calls in `--unbound` mode, which is MORE correct than the old model (where the C model's internal tracking was opaque to the solver).

### B. Multi-Dimensional Arrays — Partially Supported

1D static and 1D dynamic arrays are fully supported. 2D dynamic arrays (`T[][]`) work.

| Pattern | Status |
|---------|--------|
| `uint[N]` | ✓ Works |
| `uint[]` | ✓ Works (push/pop/length supported) |
| `uint[][]` | ✓ Works (declaration, push, indexing, length, storage ref passing) |
| `uint[][N]` | ✓ Works (observed round-trip SUCCESSFUL; outer fixed, inner dyn) |
| `uint[N][]` | ✓ Works (fixed 2026-04-24) — two complementary frontend fixes: (1) the `convert_array_index` select-decompose path now tests the outermost source's finitude (src/solvers/smt/smt_conv.cpp:3636-3673), closing a latent soundness gap where `i*N+j` was flattened against an infinite outer domain; (2) the `is_dynarray_state` promotion in `solidity_convert_decl.cpp` now detects pointer-backed inner fixed arrays and rewrites them to `array_typet(T, N)` before wrapping in the outer infinite array, so the promoted state-var type is `array<array<T, N>, inf>` instead of the previous `array<pointer<T>, inf>`. Both slot-init (push) and slot-read values now agree on ARRAY sort, eliminating the `array_convt::execute_array_ite` crash. `_pass` verifies SUCCESSFUL at k=4 via k-induction in 0.2s with 12 VCCs. Standalone `T[N]` state-vars (not nested under a dynamic outer) still use the pointer model — broader fixed-array unification tracked as separate refactor. |
| `uint[N][M]` (all fixed, any depth) | ✓ Works — native `array_typet(array_typet(T, N), M)` embedded directly in the contract struct (option B, commit `c5eec55601` + zero-init unroll follow-up). Covers 2D, 3D and deeper as long as every dim is a compile-time constant. Verified across element types (uint256, int256, address, bool) and across placements (top-level state var, inside-struct field, function parameter, local variable) — see `multi_dim_fixed_{3d,addr_2d,bool_2d,int_2d,struct_field,fn_param,local}_{pass,fail}` and `esol_clone_multi_dim_{pass,3d_pass}`. |
| `bytes32[N][M]` (all fixed) | ✗ **KNOWNBUG** (silent unsoundness) — symex drops bytes32-equality assertions inside native 2D array_typet body ("Generated 0 VCC(s)"). Suspected interaction with the BytesStatic struct lowering. Trip-wire at `multi_dim_fixed_bytes32_2d_fail`. |
| `uint[4][][2]`-style mixed 3D | ⚠ Partial — existing `nested_array_mixed_1` marked CORE but observed to vacuously pass (40+ user asserts → 0 VCCs, silent-drop class). Same silent-unsoundness symptom as bytes32 2D; distinct from the §B `array_convt` crash class. |
| `uint[][][]` (all-dyn 3D+) | Not tested, expected OK via same path as `uint[][]` |

**Why all-fixed multi-dim works** (option B): the frontend's
`try_native_nested_fixed_array` helper walks the `NestedArrayTypeName`
down its `baseType` chain; if every level has a compile-time `length`,
it builds a native `array_typet(array_typet(..., inner_N), outer_M)` and
embeds it directly in the surrounding struct. This avoids the
value-set offset-granular points-to bug that used to break the old
`T**` + per-row-calloc lowering — the field is now a single flat slab
with no pointer indirection for symex to lose. The clone walker
(`__ESOL_deep_copy`) routes multi-dim through the existing
`_ESBMC_arrcpy_2d` helper, keeping clone isolation intact.

**Zero-init in the ctor**: emitting `this->grid = {{0}}` for a native
nested field trips a "Can't construct rvalue reference to array type
during dereference" crash in the pointer-analysis pipeline — writing
an array-valued rvalue through `*this` is unsupported. The constructor
instead unrolls the zero-init into per-leaf scalar assignments (see
`solidity_convert_constructor.cpp`, B3 block). Recursion handles both
array-dim walks and intermediate struct fields (via `ns.follow` on
`symbol_typet`), so a nested `uint256[N][M]` field inside a user struct
still zero-inits correctly. The unroll skips leaf types `gen_zero`
cannot materialise (union, opaque), which is sound — just loses the
zero-init for those leaves, matching the surrounding nondet default.

### C. Data Location Semantics — Partially Implemented

`storage`/`memory`/`calldata` qualifiers are extracted from AST and tagged as `#sol_data_loc` metadata.

**Storage reference semantics for library functions** — ✅ Implemented:

Library functions with `storage` struct parameters correctly modify caller's state variables via a three-part mechanism:
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

### D. Low-Level Calls — Partial Modeling

`.call()`, `.delegatecall()`, `.staticcall()` all return `(bool success, bytes memory data)`. The three are recognized and accepted by the frontend in both bound and unbound modes, but their semantic accuracy differs significantly by mode **and** by call kind.

| Aspect | `.call()` | `.delegatecall()` | `.staticcall()` |
|---|---|---|---|
| Unbound: target dispatch | closed-world: target address ignored (by design) | closed-world: target address ignored (by design) | closed-world: target address ignored (by design) |
| Unbound: side effect | ✓ nondet reentry into *current* contract's nondet dispatch (models attacker callback) | ✓ same | ✓ same (strictly over-approx — staticcall target cannot mutate, but leaving it is harmless) |
| Unbound: return value | nondet `(bool, BytesDynamic)` | nondet `(bool, BytesDynamic)` | nondet `(bool, BytesDynamic)` |
| Bound: target dispatch | ✓ if-chain over `_ESBMC_Object_X.$address`, calls `_ESBMC_Nondet_Extcall_<target>` | ✓ same if-chain | ✓ same if-chain |
| Bound: `msg.sender` | ✓ swapped to caller's `this.address`, restored after | ✓ **preserved** (correct EVM semantics) | ✓ swapped to caller's `this.address`, restored after |
| Bound: **storage context** | ✓ correct — target code runs against `_ESBMC_Object_<target>` | ✓ **storage shadow** fast path (see below) — target body is cloned into the caller's function context; falls back to target-instance dispatch when preconditions are not met | ✓ correct — target code runs against `_ESBMC_Object_<target>` |
| Bound: **read-only enforcement** | N/A | N/A | ✓ **enforced via snapshot+rollback** — target struct is snapshotted before dispatch and restored after, so any writes performed by the nondet extcall are invisible to the caller. |
| Function selector from ABI payload | ✓ signature-based dispatch for `abi.encodeWithSignature` literal | ✓ signature-based shadow dispatch for `abi.encodeWithSignature`/`encodeWithSelector`/`encodeCall` literal | ✗ ignored |

**Unbound-mode design note (not a bug):** Under the closed-world assumption, other contracts do not exist, so the target address is intentionally ignored. The side effect — nondet reentry into the current contract via `_ESBMC_Nondet_Extcall_<self>` — is the intentional over-approximation of "attacker-controlled external callee may call back into any of our public functions during the call". This is what enables detection of classic reentrancy bugs (SWC-107).

**Bound-mode design philosophy:** Bound mode aligns with SMTChecker's `--model-checker-ext-calls=trusted` mode — external calls to addresses in `contractNamesList` are treated as deterministic dispatch to the declared contract. Fallback is hard `return false`, not nondet over-approximation; programs that call unknown addresses have those paths pruned. Sound under the trusted assumption and fast in practice.

**Delegate-shadow fast path (bound mode only) — v1–v4:**

Real EVM `delegatecall` runs the target function's code **in the caller's storage context**: state writes inside the target land on the caller's slots, `msg.sender` and `msg.value` are preserved, `address(this)` stays pointing at the caller. The previous bound-mode model executed the target function against its own static instance `_ESBMC_Object_<target>`, which is wrong for library and proxy patterns.

Entry point: `try_get_delegate_shadow_call` @ `solidity_convert_call.cpp`. On any unsupported shape it returns `true`, and `get_low_level_member_accsss` falls back to the generic `$delegatecall#0` dispatcher.

- **v1** (basic shape): extract the literal `sig` string via `extract_abi_encode_signature`; walk `contractNamesList`; emit `if (_addr == _ESBMC_Object_cand.$address) { inlined_body; $dl_success = true; }` arms. `delegate_shadow_param_remap` maps target formal params to caller-allocated `$dl_arg_i` locals.
- **v2**: return-value support. `rewrite_returns_for_delegate_shadow` walks the converted body and replaces `return X;` with `{ $dl_ret$slot$i = X; goto $dl_end$slot$i; }`.
- **v3**: internal helper inlining via `try_inline_delegate_shadow_helper_call` — avoids the `(Target*)this` cast by directly inlining helper bodies with fresh `$dl_harg_i` locals.
- **v4**: accepts `abi.encodeWithSignature("sig(T,...)", args)`, `abi.encodeWithSelector(FnRef.selector, args)`, and `abi.encodeCall(FnRef, (args))`. Canonical signature rebuilt from referenced `FunctionDefinition` via `build_canonical_signature` + full-AST lookup `find_node_by_id`.

**Current restrictions — fall back to `$delegatecall#0` on any miss:**
- Payload must be one of the three `abi.encode*` shapes above. Raw bytes variables and dynamic signature strings are unsupported.
- Every state variable the target body reads or writes must exist on the caller with the same name and typeString. Rules out EIP-1967 / UUPS / Diamond layouts where the proxy uses dedicated storage slots.
- Contracts using `assembly` / `sload` / `sstore` to access storage by slot index are not handled.
- Only single-return functions are shadowed; tuple returns fall back.
- Helpers defined in a base contract of the target (not the target itself) fall through to the generic path.
- `return` statements hidden inside statement expressions, inline assembly, or try/catch bodies are not rewritten.

### E. Tuple / Multi-Return — Mostly Resolved

**Working:**
- Flat destructuring `(x, y) = func()`, partial skip `(x, ) = func()`, tuple swap `(x, y) = (y, x)`, multi-position omit `(x, , y) = func()`
- Position-based component matching (name-based + positional fallback) — replaces fragile `at(i)` indexing
- Nested tuple destructuring `((a,b),c) = ...` via `flatten_nested_tuple_assignment()`
- External call tuple returns `(a,b) = externalContract.f()` — cross-contract and same-contract
- Low-level call tuple `(bool success, ) = addr.call(...)` — positional matching for library structs

### F. Mapping Library Efficiency

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

#### F.1. Arrays of Mappings (`mapping(K => V)[]`)

Dynamic arrays of mappings previously crashed because the dynamic array C model used `malloc(sizeof(element))` + `memcpy`, but `sizeof(mapping)` = `sizeof(infinite_array)` is undefined.

**Solution:** Model `mapping(K=>V)[]` as a 2D infinite array with an auxiliary `_mapping_arr_len` variable for push/pop tracking. No `malloc` needed — mappings are pre-existing infinite arrays, so `push()` simply increments the length counter.

Fixed-size variant `mapping(K=>V)[N]` uses the same inf-array model; previously fell through to the standard pointer-with-fixed-size path and crashed `array_type2t::get_width` during state-var zero-initialisation.

#### F.2. Mapping of Dynamic Arrays (`mapping(K => V[])`)

The **inverse** shape of F.1 — outer mapping, inner dynamic array (e.g. `mapping(address => uint256[])` with `m[a].push(x)`). Common in real-world contracts.

**Model (state-var path, non-`new_expr`):**
- The outer mapping's typet is `array_typet(array_typet(elem_T, infinity), infinity)` — a nested infinite SMT array. Flagged `#sol_mapping_of_dynarr` on the outer, `#sol_dynarr_inner` on the inner.
- A sibling aux global `<name>_mapdynarr_len` of type `array_typet(uint256, infinity)` tracks per-key length, keyed by the same XOR-folded 64-bit mapping key.
- `m[k][i]` → `index_exprt(index_exprt(m, fold(k)), i)` — two nested infinite-array selects, backed directly by SMT.
- `m[k].push(x)` → `data[fold(k)][len[fold(k)]] = x;  len[fold(k)] = len[fold(k)] + 1` emitted as front-block + main-stmt.
- `m[k].pop()` → `len[fold(k)] = len[fold(k)] - 1`.
- `m[k].length` → `len[fold(k)]`.

**Scope:** the promotion fires only when the leaf element is a scalar. Non-scalar elements (`mapping(K => Struct[])`, `mapping(K => string[])`, etc.) still use the legacy pointer model in `solidity_mapping.c`.

#### F.3. Mapping of Fixed-Size Arrays (`mapping(K => T[N])`)

Third distinct mapping-of-array shape: outer mapping, inner **fixed-size** array (e.g. `mapping(address => InvestRecord[9])`). Common in staking/investment contracts.

**Model (`is_new_expr` path):**
- New C helper `map_fixed_arr_get(mapping_t *m, uint256_t k, size_t sz)` in `solidity_mapping.c`.
- First read for a given key lazily `calloc(1, sz)` a zero-filled slab and stores the pointer via `map_set_raw`.
- Subsequent reads return the same pointer — element writes via `[i]` mutate the heap slab in place and persist across reads.
- Unlike `map_dynarr_get` (returns NULL for unwritten keys), the fixed-size variant returns a valid pointer from the first access, matching Solidity's semantics that every key maps to a pre-bound zero-filled N-slot array.

**Unbound-mode state-var access — fixed by Phase 3 Fix B (2026-04-24):**
Previously the `map_fixed_arr_get` path only fired under
`should_treat_as_new()` — i.e. `--bound` + `new Store()`. Direct
state-var access on an unbound contract's `mapping(K => T[N])`
(no `--bound`, no `new`) missed the helper routing and crashed
during SMT encoding with Bitwuzla `terms with mismatching sort at
indices 0 and 1`, because the chained-subtype lowering produced
`array<T[N], inf>` — an unbounded array whose element sort is
itself an array sort, which `array_convt`
(`src/solvers/smt/array_conv.cpp:92-95`) cannot encode.

Fix B rewrites the state-var's lowered type in the decl layer: when
the mapping leaf value carries `SolType::ARRAY_LITERAL` (1D) or
`SolType::ARRAY` (2D+ nested fixed), the state var becomes a
`mapping_t` struct — same as the `is_new_expr` path — and tagged
with `#sol_mapping_fixed_arr_value`. The existing `is_mapping &&
is_new_expr` init block is extended to cover this tag so the
`{base=_ESBMC_inf_*, addr=this->$address}` fields are populated.
Access routes through `get_new_mapping_index_access`'s `fixed_arr`
branch, which now casts the `void*` return of `map_fixed_arr_get`
to `pointer<element>` (i.e. `value_t.subtype()`) so downstream
`m[k][i]` / `m[k][i][j]` index expressions use the correct element
stride.

**Current regression state:**
- `mapping_fixed_array_unbound_fail` (1D FAIL): **CORE**, passes.
- `mapping_fixed_2d_array_unbound_fail` (2D FAIL): **CORE**, passes.
- `mapping_fixed_array_unbound_pass` (1D PASS): **KNOWNBUG** —
  semantically correct encoding, but `map_get_raw`'s linked-list
  walk × k-induction iterations doesn't solve within the 60s
  regression timeout. Performance issue, not soundness.
- `mapping_fixed_2d_array_unbound_pass` (2D PASS): same, KNOWNBUG.

**Fixed 2026-04-24 (both `_fail` and `_pass` now CORE):**
- `outer_dyn_inner_fixed_array_fail`: fixed by the
  `smt_conv::convert_array_index` select-decompose symmetry patch
  (tests outermost array's finitude, matching the store side).
- `outer_dyn_inner_fixed_array_pass`: fixed by promoting the inner
  pointer-backed fixed array to `array_typet(T, N)` inside the
  `is_dynarray_state` block in `solidity_convert_decl.cpp`. The
  `T[N][]` state-var now lowers to `array<array<T, N>, inf>` instead
  of the previous `array<pointer<T>, inf>`. Both push's zero-init
  and read-of-unwritten-slot produce ARRAY-sorted values, so
  `array_convt::execute_array_ite` no longer merges two distinct
  SMT sorts. k-induction finds the inductive step at k=4 in 0.2s.
  Note: this is a targeted promotion for the nested case. Standalone
  `T[N]` state-vars (without a dynamic outer) still use the pointer
  model — unifying those is a broader refactor tracked separately.

### G. Address / Contract Type Conversion

Basic conversions work:
- `address(contractInstance)` → extracts `$address` member ✓
- `ContractType(addr)` → binds to static `_ESBMC_Object_*` instance ✓
- `payable(addr)` ↔ `address` conversions ✓
- Nested `uint8(bytes1(x))` chains ✓

**Limitations**:
- Address→contract conversion assumes all addresses are known static instances; unknown/external addresses cannot be properly converted
- No runtime type checking that an address actually holds the expected contract type
- Dynamic dispatch through address conversion is limited — `Base(address(derived))` binds to the static Base instance, not the actual derived instance

### H. Cryptographic Hash Function Abstraction

Hash/crypto functions use **deterministic bijective transformations** (see Section A for details). `blockhash` and `blobhash` remain **nondet** since they depend on external blockchain state.

### I. uint256 Modeling Constraints

256-bit integers (`_BitInt(256)`) are supported for arithmetic, but:

| Issue | Detail |
|-------|--------|
| **Mapping key truncation** | ✅ Resolved: XOR-fold 256→64 bit via `xor_fold_key_to_64bit()`; collision rate 2^-64 |
| **Nested mapping key packing** | ✅ Resolved: `combine_mapping_keys_256` packs xor-folded 64-bit keys into 64-bit lanes of a uint256 so `m[k1][k2]` reads/writes hit a single `map_<leaf>_get/set` slot. Up to 4 levels fit losslessly. |
| **SMT solver performance** | 256-bit bitvector operations significantly slower than smaller widths; OOM possible for complex arithmetic |
| **`--16` workaround** | Reducing to 16-bit improves speed but introduces precision loss |

**Build requirements for bitwuzla backend (Ubuntu/WSL):**

```bash
sudo apt install -y libgmp-dev libmpfr-dev
pip install --user --break-system-packages meson ninja
cd build && cmake -DENABLE_BITWUZLA=ON -DDOWNLOAD_DEPENDENCIES=ON ..
cmake --build . -j$(nproc) --target esbmc
```

### J. `super` Keyword — Implemented

`super.funcName()` calls are supported. Detection in `get_call_expr()` (`solidity_convert_expr.cpp`) checks for `MemberAccess` where `expression.name == "super"`. Dispatch logic in `get_super_function_call()` (`solidity_convert_call.cpp`):

1. For non-overriding case (base function merged into derived contract): use the merged copy directly — `this` type matches, no cast needed.
2. For overriding case (derived contract overrides the same name): detect via ID mismatch after `find_decl_ref`, fall back to original in base contract via `find_contract_name_for_id()`, insert a `this` typecast.

**Supported patterns**:
- `super.method()` with no arguments, with arguments, with return values ✅
- Non-overriding case: base function merged into derived contract, no cast needed ✅
- Overriding case: derived overrides the same name, calls original base with `this` typecast ✅
- Multi-level dispatch (e.g. `Child.abc() → p1() → super.myFunc()`) ✅
- **`super.f` as r-value (fn-ptr capture)**: `function() internal returns (uint) x = super.f;` lowers to an opaque func_ptr (typecast of `referencedDeclaration + 1`). Indirect calls through the captured pointer return nondet.

**Cooperative super chain — fully supported:**

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

Works because ESBMC's `is_prefix_of` mechanism (`dereference.cpp:603`) recognises that `A`, `B`, `C` contract structs all share the same prefix layout. When `A.inc` writes `counter` through an `(A*)this` pointer to a `C` object, `symex_assign_typecast` (`symex_assign.cpp:528`) generates `C_obj_new = with(C_obj_old, [counter := new_val])`, correctly updating the `C`-typed object.

### K. Other Gaps

| Feature | Status | Detail |
|---------|--------|--------|
| **Try/Catch** | ✅ Done | Modeled as `if(nondet_bool) { success } else { catch }` with nondet return values; supports multiple catch clauses (Error, Panic, catch-all) |
| **`using A for B`** | Partial | Library function dispatch works (including storage ref copy-back). Free-function binding `using { f } for S` rewritten in `get_call_expr` to `f(x, args)` before the normal call path. Custom operator dispatch (`using { f as op }`) still not supported. |
| **Bitwise on dynamic bytes** | Static only | Ops limited to `bytesN`, not dynamic `bytes` |
| **`constant`/`immutable`** | Partial | `constant` works; `immutable` may not enforce set-once |
| **Named return parameters** | ✅ Done | Single named return: DECL + zero-init + implicit return. Tuple named returns still use existing tuple machinery. |
| **Function overloading** | Partial | Same-name different-param functions may misresolve in `find_decl_ref` |
| **receive/fallback** | ✓ Works | `receive() external payable` and `fallback() external [payable]` fully supported; tests: `receive_1/2`, `fallback_1/2` |
| **Fallback with params** | Partial | Basic fallback exists; `fallback(bytes calldata) returns (bytes memory)` params ignored |
| **Custom storage layout** | ✓ Works | `contract C layout at <expr>` (Solidity 0.8.29+) — AST parses without error; ESBMC ignores storage slots (models state vars as struct members) |
| **Array slices (`IndexRangeAccess`)** | ✅ Over-approx | `data[:4]`, `data[1:3]` on calldata arrays/bytes — modeled as nondet |
| **`abi.decode()`** | ✓ Nondet | Parses `(T)` type tuple via `ElementaryTypeNameExpression`; result is an unconstrained nondet value. Tuple-shaped return position and tuple-destructuring assignment both fall back to nondet-per-member. |
| **`abi.encodeCall()`** | ✓ Identity | Interface/function pointer (`ITarget.transfer`) accepted by converter; canonical signature rebuilt from referenced `FunctionDefinition`. |
| **`mulmod(MAX,MAX,k)`** | KNOWNBUG | 512-bit model is correct but ESBMC constant evaluator crashes (SIGFPE) when both operands are near `type(uint256).max` |
| **Inline assembly / Yul** | ✅ Havoc | Over-approximated: all externally referenced variables are havoc'd to nondet. Does not model Yul semantics. |
| **Function types (internal)** | ✓ Parses, nondet calls | `function(uint) pure returns (uint) f` parameters / struct fields lowered to opaque `void *`; indirect calls `f(x)` return a nondet value of the declared return type. Precise semantics not implemented. |
| **Function types (external)** | Partial | `.address` on a function-typed variable returns nondet. `.address` on a fresh reference (`this.f.address`) reads the instance `$address`. Passing a function reference as argument may still crash during argument marshalling. |
| **`using for` + custom operators** | Not supported | Operator dispatch table per type |
| **Transient storage (EIP-1153)** | Not supported | New data location model |
| **User-defined value types** | ✓ Basic | `type C is V` with `.wrap()`/`.unwrap()` works, both file-scope and contract-scope; bare `T.wrap;` expression statement no longer crashes; `using { f as op }` custom operators NOT supported. |

## Roadmap

### Tier 1 — Correctness Fixes (soundness gaps in claimed features)

| # | Task | Status |
|---|------|--------|
| 1 | Fix mapping key truncation | ✅ Done — XOR-fold 256→64 bit; 2^-64 collision rate |
| 2 | Fix crypto function abstraction | ✅ Done — deterministic bijective transforms; functional consistency, injectivity, O(1) SMT cost |
| 3 | Fix external call tuple returns | ✅ Done |
| 4 | Low-level call bytes return | ✅ Done — `BytesDynamic` replaces nondet_uint |

### Tier 2 — High-Impact Missing Features

| # | Task | Status |
|---|------|--------|
| 5 | `super` keyword | ✅ Done |
| 6 | Try/Catch | ✅ Done |
| 7 | `abi.decode()` | ✅ Done (nondet over-approximation; tighter round-trip remains future work) |
| 8 | Function overloading | Hard (~400 lines): name mangling or overload resolution table |
| 9 | Data location semantics | Partial: storage ref for library params done; memory copy-on-call, calldata immutability, non-library storage ref remain |

### Tier 3 — Completeness / Usability

| # | Task | Status |
|---|------|--------|
| 10 | Multi-dimensional arrays | `T[][]` and all-fixed `T[N][M]`/3D+ work (native `array_typet` via option B, c5eec55601; zero-init unroll follow-up). `mapping(K=>T[N])` / `mapping(K=>T[M][N])` unbound: fixed by Phase 3 Fix B (2026-04-24, §F.3) — frontend now rewrites state-var type to `mapping_t` and routes through `map_fixed_arr_get`, so the array-of-array sort no longer appears. `T[N][]` outer-dyn + inner-fixed: both `_fail` and `_pass` promoted to CORE 2026-04-24 — `_fail` by the `convert_array_index` select-decompose symmetry fix (§B), `_pass` by promoting the inner pointer-backed fixed array to `array_typet(T, N)` at the `is_dynarray_state` promotion site. |
| 10b | `mapping(K=>V)[]` | ✅ Done |
| 11 | Nested tuple destructuring | ✅ Done |
| 12 | User-defined value types | Partial: wrap/unwrap work; custom operators not supported |
| 13 | `immutable` set-once enforcement | Easy (~80 lines) |
| 14 | `bytes.concat()` / `string.concat()` | ✅ Done (variadic) |
| 15 | `type(C).runtimeCode` / `type(I).interfaceId` | ✅ Done (nondet) |

### Tier 4 — Long-Term / Architectural

| # | Task | Status |
|---|------|--------|
| 16 | Inline assembly / Yul | ✅ Havoc over-approximation |
| 17 | Mapping library optimization | Hard — migrate unbound mode to SMT arrays |
| 18 | Tuple return refactoring | ✅ Done |
| 19 | Function types | Very hard — first-class function values |
| 20 | Transient storage / custom storage layout | Very hard |

**Performance bottlenecks** (slow THOROUGH tests):
- `transfer_send_2` (>1200s timeout) — k-induction + `--bound` cross-contract reasoning
- `typedef_1` (~420s) — k-induction with complex type aliases
- `continue_3`/`break_4` (~200-250s) — `--unwind 20` with nested control flow
- `bytes_17` (~175s) — bytes operations with `--bound` mode
