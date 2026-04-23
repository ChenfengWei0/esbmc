# Solidity Operational Models (c2goto)

Solidity built-in types, variables, and functions are implemented as C operational models in `src/c2goto/library/solidity/`. These are pre-compiled into a **separate goto binary** (`sol64.goto`) via the c2goto pipeline and embedded into the ESBMC binary. At runtime, `add_cprover_library()` loads from `sol64_buf` (not the full `clib64`) for fast symbol loading.

| File | Content |
|------|---------|
| `solidity_types.h` | Type definitions: `int256_t`, `uint256_t`, `address_t` via `_BitInt(256)`, `sol_llc_ret` struct |
| `solidity_blockchain.c` | Block/tx/msg global variables, `blockhash`, `blobhash` (EIP-4844), `gasleft`/`gasConsume` — all nondet (over-approximate) |
| `solidity_builtins.c` | Integer exponentiation (`sol_pow_uint`), modular arithmetic (`addmod`/`mulmod` with 512-bit precision), `llc_nondet_bytes`, `selfdestruct` |
| `solidity_crypto.c` | Cryptographic hash functions: keccak256, sha256, ripemd160, ecrecover (deterministic bijective abstraction) |
| `solidity_abi.c` | ABI encoding/decoding models: `abi_encode`, `abi_encodePacked`, `abi_encodeWithSelector`, `abi_encodeWithSignature`, `abi_encodeCall` (identity), `abi_decode` (nondet) |
| `solidity_bytes.c` | `BytesStatic`/`BytesDynamic` structs, 60+ byte manipulation functions, `bytes_dynamic_concat` (pass-by-value for variadic nesting) |
| `solidity_mapping.c` | Mapping data structures (`_ESBMC_Mapping`, `mapping_t`, and `_fast` variants) + `map_fixed_arr_get` |
| `solidity_array.c` | Dynamic array tracking: push, pop, length, arrcpy |
| `solidity_units.c` | Ether/time unit conversions (wei, gwei, ether, seconds, days, etc.) |
| `solidity_string.c` | String operations (`string_concat` for variadic concat), integer-to-string, hex conversion |
| `solidity_address.c` | Address management, contract object tracking, EOA balance pool |
| `solidity_misc.c` | Min/max (`_min`/`_max`), `_creationCode`/`_runtimeCode`/`_interfaceId` (nondet), reentrancy check, state initialization |

## c2goto Architecture

- **Build pipeline**: Solidity `.c` files → `c2goto --64 --fixedbv` → `sol64.goto` (524KB) → `flail.py` → `sol64.c` (byte array) → linked into esbmc binary
- **CMake auto-glob**: `file(GLOB_RECURSE c2goto_solidity_files ... "library/solidity/*.c")` in `src/c2goto/CMakeLists.txt:146-148` — any new `.c` file in the `library/solidity/` directory is automatically compiled into `sol64.goto`. No CMakeLists.txt changes needed when adding new model files. However, function names must be registered in `solidity_c_models` in `cprover_library.cpp`.
- **Separate from clib64**: Solidity models are NOT compiled into `clib64.goto`. This avoids reading the full 1.9MB clib when only Solidity symbols are needed.
- **Loading path**: `add_cprover_library()` in `cprover_library.cpp` detects `language->id() == "solidity_ast"` and reads from `sol64_buf` instead of `clib64_buf`. No whitelist filtering needed since sol64 contains only Solidity symbols.
- **Whitelist**: The `solidity_c_models` vector in `cprover_library.cpp` lists all function names that should be extracted from sol64. New functions must be added here.
- **Build flag**: `ENABLE_SOLIDITY_FRONTEND=ON` required for CMake to compile Solidity models and generate sol64.

## Symbol Naming (C vs C++ frontend)

The c2goto pipeline compiles Solidity models as **C** (not C++). This affects struct tag naming:

| C frontend (c2goto) | C++ frontend (old template) |
|---------------------|-----------------------------|
| `tag-struct BytesPool` | `tag-BytesPool` |
| `tag-struct _ESBMC_Mapping` | `tag-_ESBMC_Mapping` |
| `tag-struct sol_llc_ret` | `tag-sol_llc_ret` |

The converter uses two prefixes:
- `prefix = "tag-"` — for Solidity-defined structs (created by the converter itself)
- `lib_prefix = "tag-struct "` — for c2goto library structs (BytesPool, BytesDynamic, BytesStatic, _ESBMC_Mapping, mapping_t, sol_llc_ret)

## typecheck() Flow

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

## Design Notes

- **No floating-point in Solidity pipeline:** sol64.goto is compiled with `--fixedbv` (CMakeLists.txt:249), but the Solidity frontend no longer generates any float/fixedbv types. The `--fixedbv` runtime flag is unnecessary for Solidity and should NOT be forced on — it has no performance benefit and risks side effects in shared code paths.
- **`_ExtInt` struct alignment:** `_ExtInt(N)` types in C structs require bitfield notation (`: N`) to avoid `ext_int_pad` name collisions from ESBMC's padding logic (`padding.cpp:116-131`). Use `__attribute__((packed))` to prevent alignment padding on top of bitfields.
- **`sol_llc_ret.x`** uses `unsigned int` (not `bool`) to avoid C/C++ bool representation mismatch.

## VSA ASSUME integration — `__ESBMC_assume` is a VSA hint

`value_set_domaint::transform` now dispatches on ASSUME and calls
`value_sett::apply_assume(guard)` (2026-04-20). Previously ASSUME fell
through a `default: // do nothing` branch, meaning VSA silently ignored
every path constraint and treated `__ESBMC_assume(p != 0)` as a no-op at
the static-analysis layer — useful for symex but invisible to VSA.

`apply_assume` currently recognises a narrow-by-design set of guards:

- `p != 0` / `0 != p` where `p` is a pointer-typed symbol. Strips
  `null_object2t`, typecast-of-null, and constant-zero entries from the
  points-to set of `p` (unwraps typecasts on both sides; accepts literal
  NULL symbol as zero).

Everything else is a no-op. Extend the handler when new guard shapes
materialise (equality against specific allocations, compound
predicates, etc.).

**When to reach for this in library code.** If a c2goto operational
model returns a pointer that is contractually non-NULL, or accepts one
in a parameter, write `__ESBMC_assume(<non-null>)` at the boundary.
Without this, VSA admits the null branch through every downstream read
and subtly poisons points-to sets for struct-pointer fields. Canonical
examples carrying the assume:

- `_ESBMC_alloc_array` / `_ESBMC_alloc_array_sym` — assume
  `block != 0` right after the calloc/malloc.
- `_ESBMC_arrcpy` / `_ESBMC_arrcpy_2d` — assume `from_array != 0`
  at the top of the body.

**What this fixed.** The 3D `__ESOL_deep_copy` case (`esol_clone_multi_dim_3d_pass`) previously failed because VSA
admitted `<0, 8, void>` (null-plus-offset from calloc-fails-returns-NULL) in every downstream struct-field points-to set, which then let SMT find a null branch in the `_ESBMC_element_null_check` at the top of `_ESBMC_arrcpy_2d`. The assume handler + library-level `__ESBMC_assume` pair collapse that branch at the source. See `regression/esbmc-solidity/esol_clone_multi_dim_pass/INVESTIGATION.md` for the full investigation.

## `__ESOL_deep_copy` walker emission for multi-dim fixed arrays

`emit_clone_deep_copy_fixup` in `solidity_convert_constructor.cpp` has
two shapes for fixed arrays of scalar leaves:

- **2D `uint256[M][N]`**: single `c->grid = _ESBMC_arrcpy_2d(base->grid, M, N, 32)` call. One function frame; no per-slot writes at the frontend level.
- **3D+ `uint256[M][N][K]`**: outer `_ESBMC_alloc_array` + per-slot `_ESBMC_arrcpy_2d(base->arr[i], ...)` at the outermost layer (so the inner two layers collapse into the 2D helper, outermost layer per-slot).

Both shapes now pass. 3D's outer per-slot pattern was historically the failing case; the VSA ASSUME fix above closed it.

**VSA per-constant-index scheme landed (2026-04-20, Solidity-only).** `value_sett::assign_rec`, `get_value_set_rec`, and the `is_dereference2t` branches now recognise compile-time-constant indices and byte offsets, routing writes to a dedicated `[N]`/`[K]` suffix entry (strong update) instead of merging into the shared `[]` aggregate. Reads at a constant index query `[N] ∪ []` and reads at a concrete dereference offset K query `[K] ∪ []`. Gated on the `sol` option (set by the parseoptions wrapper whenever a `.sol` / `.solast` input is detected or `--sol` is passed); other frontends keep the legacy `[]`-only semantics. Full Solidity regression (723/723, 42 s under CVC5) passes with no regression or timing penalty.

**Why `_ESBMC_arrcpy_2d` dispatch still stays — deeper root cause identified (2026-04-20, A1 attempt).** The walker-level per-slot expansion (alloc + N concrete `dst[K] = src[K]` writes instead of a single `_ESBMC_arrcpy` library call) **was implemented and verified at the goto level** — the emitted sequence uses entirely constant indices, feeding cleanly into the per-constant-index VSA scheme. All 723 Solidity regression still pass with the expansion in place.

But 2D/3D multi_dim clone tests **still fail** after removing the `_ESBMC_arrcpy_2d` dispatch. Investigating the VSA dump under the expanded walker reveals:

- Every `_ESBMC_alloc_array(...)` call across the whole program — outer, inner, any arrcpy from any context — resolves to the *same* `dynamic_object52` in VSA's numbering. VSA treats `_ESBMC_alloc_array`'s internal `calloc` call as **one irep2 allocation site**, so all its returns point to the same dyn_obj. Writes through those returns all land on `dynamic_object52[K]` with K being the offset within that shared heap region.
- `c->grid[0]`, `c->grid[1]`, `c->grid[2]` at the outer level and `c->grid[i][0]`, `c->grid[i][1]` at the inner level all resolve to *the same* `dyn_obj_52` at various offsets (8, 16, 24, …). The self-referential `<DYNAMIC_OBJECT(52, 0), 8, …>` entries in `dynamic_object52[8]` are literal — "offset 8 of dyn_obj_52 contains a pointer to offset 8 of dyn_obj_52", because the alloc_array return VSA gets merged across callers.
- Reads at concrete offset K end up chasing the self-referential pointer set, which eventually brings in the `*` sitting in some other `[K']` entry of the same shared dyn_obj.

`_ESBMC_arrcpy_2d` sidesteps this because its internal `void *dst_outer = _ESBMC_alloc_array(...)` is a **function-local stack pointer**, and VSA tracks stack locals per-function-frame — so each arrcpy_2d invocation gets its own clean `dst_outer` entry even though all their allocator calls still resolve to the same dyn_obj_52. The per-slot writes `dst_outer[i] = _ESBMC_arrcpy(...)` then populate the local's own `[K]` entries, and reads through the returned `dst_outer` pointer hit those clean entries. The helper frame is an isolation boundary VSA honours.

To really close this requires **heap-allocation-site sensitivity in VSA** — each `_ESBMC_alloc_array` call context should produce a distinct `dynamic_object` instance. That is a VSA architectural change (instrument allocators with call-site tags, threaded through `get_reference_set` + `assign_rec`), separate in scope from the per-constant-index scheme. Until then, `_ESBMC_arrcpy_2d` is truly load-bearing — the helper-frame local pointer is the only abstraction that gives VSA a fresh allocation identity per clone call.

## `__ESOL_deep_copy` per-type semantics

The `__ESOL_deep_copy(C src)` intrinsic lowers to `_ESBMC_clone_<C>(src)` (see `build_tod_clone_helper` in `src/solidity-frontend/solidity_convert_constructor.cpp`). Body shape: `C *c = new C(); *c = *base; c->$address = nondet; emit_clone_deep_copy_fixup(...)` — the fixup walker recurses through struct components and (a) reallocates each pointer-backed fixed-size array via `_ESBMC_arrcpy`, (b) retargets every mapping's `.addr` to the clone's fresh $address (including mappings nested inside user structs, when those exist in the model).

What this means for each field type (verified by the `esol_clone_*` regression stress tests):

- **Primitive scalars** (uint/bool/address/bytes32 at top level): fully copied, clone is isolated from base — post-clone mutation of either instance is invisible to the other.
- **Fixed arrays at top level** (`uint256[N]`): isolated. The walker emits `c->arr = _ESBMC_arrcpy(base->arr, N, sizeof(E))`, giving the clone its own heap slab. Writes to `base.arr[i]` after clone are NOT visible via `clone.arr[i]`. See `esol_clone_fixed_array_isolation_pass` (pass) / `esol_clone_fixed_array_isolation_fail` (fail dual).
- **Array-of-struct-of-primitives** (`P[N]` where `P` has only scalar fields): fully isolated. `needs_clone_deep_fixup(P)` is false, so the walker uses a single `_ESBMC_arrcpy` that memcpy's all N struct slots into the clone's fresh buffer. See `esol_clone_array_of_struct_{pass,isolation_pass}`.
- **Array-of-struct-with-mapping / array-of-nested-fixed-array**: the walker's non-scalar path unrolls a compile-time per-element copy and recurses into each element, so mapping `.addr` retargeting reaches array-nested mappings.
- **Multi-dim fixed arrays of scalar leaves** (`uint256[M][N]`): fully isolated. The walker calls the library helper `_ESBMC_arrcpy_2d(base->grid, N, M, sizeof(elem))` as a SINGLE function call, which internally memcpy's the outer pointer array then element-copies each inner row into a fresh allocation.
- **Inherited fixed arrays**: isolated via the walker traversing the flat merged struct (ESBMC emits one struct per derived contract with base-class fields inlined). See `esol_clone_inherited_fixed_array_pass`.
- **Dynamic arrays** (`uint256[]`): isolated — dynarray state vars live as global infinite arrays keyed outside the contract struct, so `*c = *base` doesn't alias them.
- **Structs of primitives**: copied (like scalars). Isolation holds via struct-level value copy.
- **Structs containing nested fixed arrays**: **KNOWNBUG**, the ctor does not recursively calloc the nested array pointer, so `base.bx.cells` is NULL and `_ESBMC_arrcpy(NULL, ...)` trips the element-null-check. Fix requires extending contract construction to recursively initialise nested pointer-backed fields before the clone walker can arrcpy them.
- **Mappings**: retargeted to the clone's fresh `$address`, so writes on the clone live in a disjoint keyspace from base. Pre-clone mapping contents on base are NOT mirrored to clone (clone's mapping starts empty).
- **Inheritance-merged state**: covered by the walker traversing the flat merged struct.
- **Strings (stored)**: copied via `$dynamic_pool`.

The `__ESOL_nondet_state_forward(C c)` intrinsic drives `*c` through a nondet dispatch over the contract's public/external methods (`build_esol_state_forward_helper` in `src/solidity-frontend/solidity_convert_constructor.cpp`). Internal and private functions are NOT invoked. Verified by `esol_state_forward_invariant_pass` (monotonic invariant preservation), `esol_state_forward_reaches_nontrivial_fail` (coverage — can reach non-initial states), and `esol_state_forward_internal_not_exposed_pass` (visibility filter correctness).

## keccak256 / sha256 on bytes-struct arguments

When the argument to `keccak256` / `sha256` is a raw source-level bytes value (`t_bytes_*` typeIdentifier: `t_bytes_storage_ptr`, `t_bytes_memory_ptr`, or `t_bytesN`), the Solidity frontend routes through a nondet-uint256 library call and then PACKS the uint256 result into a BytesStatic via `bytes_static_from_uint` (see `src/solidity-frontend/solidity_convert_expr.cpp` `hash_needs_nondet` branch). Without the pack, symex crashes on the uint256→BytesStatic struct-shape mismatch when the hash feeds a `bytes32` return value or comparison. The pack also keeps the identity-hash equality semantics: same input uint256 → same packed bytes32, so `keccak256(x) == keccak256(x)` still holds across two calls with identical input (important for the abi.encode_call selector-consistency tests).

`ripemd160` returns `address` in Solidity, not `bytes32`, so its result stays as a scalar address_t and doesn't need the pack.

## EOA Balance Modeling (`--bound` mode)

Under `--bound`, ETH balances of non-tracked recipients (EOAs and any `address payable` value the user constructs) are tracked in a global map, so that `recipient.balance` reads see credits from prior `transfer`/`send` calls.

- **Write side**: the EOA fallback in `get_transfer_definition` / `get_send_definition` (`src/solidity-frontend/solidity_convert_call.cpp`) deducts from the sender's `$balance` AND credits the recipient via `_ESBMC_eoa_credit(addr, val)`. Tracked contract instances (`_ESBMC_Object_<C>`) still take precedence via the per-contract dispatch tree; the EOA credit only fires when the recipient address matches no tracked `$address`.
- **Read side**: `get_aux_property_function` (`src/solidity-frontend/solidity_convert_builtin.cpp`) for `property_name == "balance"` falls through to `_ESBMC_eoa_balance_of(addr)` when no tracked contract matches. Other properties (`code`, `codehash`, `address`) keep the `nondet_uint` fallback — they have no equivalent persistent map.
- **Model internals** (`src/c2goto/library/solidity/solidity_address.c`): parallel `__ESBMC_inf_size` arrays `sol_eoa_addr_array[]`, `sol_eoa_balance_array[]` plus counter `sol_eoa_max_cnt`. Linear-scan lookup via `_ESBMC_eoa_get_idx`; find-or-insert via `_ESBMC_eoa_get_or_init` (new slots get a nondet initial balance — sound over-approximation of a real EOA's pre-existing balance).
- **Unwind requirement**: `--unwind N` where N ≥ number of distinct EOA addresses touched on any path, because the lookup loop iterates over `sol_eoa_max_cnt`.
- **Unbound mode**: EOA credit still fires (since `transfer/send` always route through the bound model for value-moving builtins), but unbound-mode balance reads for unknown addresses short-circuit to a fresh `nondet_uint` before reaching `get_aux_property_function`. Enable `--bound` for end-to-end write→read round-tripping.
- **User-side pin**: to make a test deterministic, `require(addr.balance == 0)` (or any constant) before the first transfer. The first read allocates the slot with a nondet initial balance; the require collapses it.
- **Regression tests**: `regression/esbmc-solidity/eoa_balance_{credit,two_recipients}_{pass,fail}` — two PASS, two FAIL, all under `--bound`, all independent of TOD harness machinery.
