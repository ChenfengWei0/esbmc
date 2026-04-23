# Building & Testing Solidity

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

## Test Baseline

684+ Solidity regression tests; target = all PASS with 0 new regressions. Test flags: always use `--unwind N --no-unwinding-assertions` for bounded verification; omitting `--unwind` causes OOM on the SMT solver. After recent fixes, the full libsolidity stress sweep (1663 single-source files) shows **0 frontend crashes**.

## Slow THOROUGH tests (>60s, avoid running in tight iteration loops)

| Test | Time | Root cause |
|------|------|------------|
| `transfer_send_2` | >1200s (KNOWNBUG) | k-induction + `--bound` causes solver timeout |
| `typedef_1` | ~420s | k-induction with complex type aliasing |
| `continue_3` | ~250s | `--unwind 20` with nested control flow |
| `break_4` | ~200s | `--unwind 20` with nested control flow |
| `bytes_17` | ~175s | `--unwind 6` with `--bound` and bytes operations |

**Tip:** Use `ctest --timeout 60` to skip slow tests during development, or run targeted tests with `ctest -R "esbmc-solidity/test_name"`.

## Adversarial Tests

| Test | Type | What it verifies |
|------|------|-----------------|
| `bitwise_ops_1/2` | CORE | AND, OR, XOR, NOT, left/right shifts on uint8 |
| `int_boundary_1/2` | CORE | uint8/uint256/int8/int256 min/max boundary + overflow |
| `typeconv_3/4` | CORE | Narrowing, widening, signed↔unsigned conversions + narrowing data loss |
| `compound_assign_1/2` | CORE | All 10 compound assignment operators + overflow detection |
| `enum_boundary_1` | CORE | Enum values, uint conversion, comparison |
| `struct_nested_1` | CORE | Nested struct read/write, default values |
| `array_boundary_1` | CORE | Static array indexing, overwrite |
| `unchecked_block_3/4` | CORE | Overflow wrapping inside unchecked / checked overflow outside |
| `perf_large_uint_1` | CORE | uint256 large arithmetic, chained ops, max value |
| `prevrandao_1/2` | CORE | block.prevrandao access |
| `do_while_1/2` | CORE | do-while at-least-once execution |
| `delete_1/2` | CORE | delete resets uint/bool/uint8 |
| `free_function_1/2` | CORE | Free function call + composition; division by zero in free function |

## libsolidity Stress Tests

Hand-pulled from `solidity/test/libsolidity/semanticTests/` to exercise upstream corner cases. No `--function` / `--focus-function` cheats.

Key CORE tests exercising the calldata / free-function / fn-ptr / UDVT / super / abi-decode / inherited-ctor / struct-emit surfaces:
- `stress_calldata_struct_lib_1`, `stress_calldata_array_overload_1`, `stress_calldata_bytes_return_slice_1`, `stress_calldata_bytes_overload_inner_1`
- `stress_free_fn_longdata_asm_1/2`, `stress_func_ptr_longdata_1`
- `stress_libsol_ext_fn_to_address`, `stress_libsol_abi_decode_simple`, `stress_libsol_udvt_wrap_unwrap`
- `stress_libsol_library_struct_as_expr`, `stress_libsol_external_public_calldata`, `stress_libsol_nested_tuples`, `stress_libsol_udvt_in_paren`, `stress_libsol_udvt_via_contract_name`, `stress_libsol_address_code_length`, `stress_libsol_modifier_local_uint8_void`, `stress_libsol_consteval_array_length`, `stress_libsol_inline_array_return`, `stress_libsol_lib_internal_call_parens`, `stress_libsol_lib_attached_call_parens`, `stress_libsol_struct_event_emit`, `stress_libsol_base_access_fnptr_var`, `stress_libsol_modifier_tuple_return_ref`, `stress_libsol_modifier_tuple_return_complex`, `stress_libsol_array_mapping_struct`, `stress_libsol_super_in_ctor_assign`, `stress_libsol_super_function_deployed`, `stress_libsol_virtual_function_deployed`, `stress_libsol_uncalled_blockhash`, `stress_libsol_uncalled_blobhash`, `stress_libsol_err_named_params_shadow`, `stress_libsol_pragma_range_legacy`

KNOWNBUG stress tests: `stress_libsol_uninit_fnptr_{legacy,yul}`, `stress_libsol_fntype_inline_array_value_call`, `stress_libsol_udvt_abicodec`, `stress_libsol_try_return_function`, `stress_libsol_calldata_string_array`, `stress_calldata_slice_abi_1`.

## Mapping-in-struct tests

| Test | Type | What it verifies |
|------|------|-----------------|
| `mapping_18` | CORE | `mapping(uint => uint)` inside struct: set, get, assert (SUCCESSFUL) |
| `mapping_19` | CORE | `mapping(uint => mapping(uint => uint))` (nested) inside struct (SUCCESSFUL) |

## Coverage gaps (no tests exist)

- Bitwise operators on uint256 (OOM with default solver settings)
- Signed integer arithmetic right-shift edge cases
- ABI encoding/decoding operations (beyond the identity abstractions)
- Abstract contracts

## Solidity Documentation Examples

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
