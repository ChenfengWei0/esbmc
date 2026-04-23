# Resolved Bugs & Incident History

## Summary Table

| Bug | Description | Root cause | Fix location |
|-----|-------------|-----------|--------------|
| **1** | Sub-256-bit overflow check missed `uint8`/`uint16` overflow | C integer promotion widens to `signed int` before arithmetic; `overflow2tc` checks at 32-bit width | `goto_check.cpp`: narrowing cast check + narrowing assignment check for `.sol` files; suppressed inside `unchecked` blocks |
| **2** | Large constants like `10**36` silently evaluated to 0 | solc truncates `typeString` with `"..."` notation; `string2integer()` returns 0 for non-alphanumeric input | `solidity_grammar.cpp:785`: skip `LiteralWithRational` when `typeString` contains `"..."`, fall through to `BO_Pow` BigInt path |
| **3** | `unchecked { }` blocks had no effect on overflow checking | `UncheckedBlock` AST nodes parsed as normal `Block` | `solidity_convert_stmt.cpp`: tag locations with `#sol_unchecked`; `goto_check.cpp`: skip overflow checks when tag present |
| **4** | `a ** b` (non-constant) crashed with "unexpected typecast to fixedbv" | Frontend called `double pow()` (floatbv) but sol64.goto compiled with `--fixedbv` → type mismatch | `solidity_builtins.c`: new `sol_pow_uint(uint256_t, uint256_t)` integer pow; `solidity_convert_expr.cpp`: call `sol_pow_uint` instead of `pow` |
| **5** | Z3 sort mismatch on mapping struct fields | c2goto padding shifted struct component indices; frontend used hardcoded `at(1)` | `solidity_mapping.c`: `__attribute__((packed))`; `solidity_convert_decl.cpp`: name-based component lookup |
| **6** | Multi-file import cycle silently drops files (e.g. `ISwapVM.sol ↔ MakerTraits.sol`) causing downstream "failed to find reference AST node" | `topological_sort()` uses Kahn's algorithm, which leaves cycle-participating nodes stuck at `in_degree > 0` and never emitted | `solidity_convert.cpp::topological_sort`: after main Kahn loop, force-drain remaining nodes by repeatedly picking the lowest-residual-`in_degree` node (commit `4461578016`) |
| **7** | Interface-nested `struct`/`enum`/`error`/`event` unresolved when a round-1 library references them as a return type (core dump on `IB.Order memory` in library signatures) | Interfaces only processed in round 2 of `convert()`; round-1 libraries look up nested types that haven't been registered yet | `solidity_convert.cpp::convert`: pre-round walk registers interface-nested type children before round 1; `solidity_convert_decl.cpp::get_noncontract_defition`: interface branch recurses into nested decls (commit `db74a7652c`) |
| **8** | `TypeMemberCall` crash on function reference used as r-value inside an inline function-pointer array | `TypeMemberCall` handler asserted `args_json.contains("arguments")`; when the parent is a `TupleExpression`/inline array it has `components`, not `arguments` | `solidity_convert_expr.cpp` (line ~691): detect non-call-target use via `find_last_parent`, emit opaque `void*` typecast tagged `#sol_func_ptr` (commit `53affdd290`) |
| **9** | **SOUNDNESS** — tuple-LHS `(x, y) = cond ? (a, b) : (b, a);` was silently dropped, leaving x/y at default zero and producing unsound `VERIFICATION SUCCESSFUL` | `construct_tuple_assigments` in the `TUPLE_RETURNS` branch only understood FunctionCall-shaped RHS; Conditional RHS hit `log_error("cannot locate function call in RHS"); return true` whose error bit was ignored | `solidity_convert_tuple.cpp`: detect Conditional RHS before function-call extraction. Both branches TupleExpression → decompose element-wise into per-slot ternaries (commit `106c0e9c22`) |
| **10** | Inline array of function pointers with constant index `[f, g][0](x)` fell through to nondet indirect-call path | `get_call_expr` resolved the callee JSON by navigating `expr["expression"]`; an `IndexAccess` on a `TupleExpression(isInlineArray)` has no `referencedDeclaration` | `solidity_convert_expr.cpp::get_call_expr`: detect `IndexAccess` on inline array literal with constant `Literal` index; redirect `callee_p` to `components[k]` in the original AST |
| **11** | `std::out_of_range` crash when reading a nested-mapping public getter | `get_new_mapping_index_access` hit its struct-shaped `val_flg == "generic"` branch with an empty identifier because the nested-mapping leaf type arrives as `MAPPING` | `solidity_convert_mapping.cpp::get_new_mapping_index_access`: early-return before the struct path when the leaf value type itself is `MAPPING` (commit `965a2a4c2a`) |
| **12** | Nested mapping writes (`m[k1][k2] = v`) and reads landed in different slots in `is_new_expr` mode | Singleton `mapping_t` model used one slot per level, but public-mapping-getter read folded keys independently and crossed a function-call boundary | `solidity_convert_mapping.cpp`: `combine_mapping_keys_256` packs xor-folded 64-bit keys into 64-bit lanes of a uint256; `get_index_access_expr` routes both GET/SET through `map_<leaf>_get/set` against the combined key (commit `965a2a4c2a`) |
| **13** | Cross-contract **single-level** mapping public-getter regressed when the combined-key scheme was applied uniformly | `get_contract_member_call_expr`'s MAPPING branch applied `xor_fold_key_to_64bit` unconditionally while in-contract writes kept the raw key | `solidity_convert_expr.cpp`: split single-level (no fold, raw key) vs nested (fold per level + combine) in the MAPPING branch (commit `d4ab3f06c0`) |
| **14** | Two structurally-identical contracts shared `_ESBMC_Object_<cname>` singleton; mapping-getter read path indexed a local pointer's mapping | When `cname_set.size() > 1`, cross-contract function calls route through the dispatcher that executes against `_ESBMC_Object_<cname>`. The mapping-getter read used the local base pointer's storage | `solidity_convert_expr.cpp`: when `cname_set.size() > 1`, route mapping reads through `get_static_contract_instance_ref` to the same singleton the dispatcher writes to (commit `d4ab3f06c0`) |
| **15** | Per-pointer polymorphism through `C(_addr)` cast missing — all `A1`-typed pointers collapsed onto shared singleton | `_ESBMC_bind_cname` was a struct field on the singleton; every `A1*` dereferenced to the same singleton. The cast `C(_addr)` additionally rewrote `_ESBMC_Object_A1.$address = _addr` globally | `solidity_convert_call.cpp`: helpers `get_or_create_bind_shadow` / `get_bind_shadow_read` — per-pointer `<var_id>$bind` shadow symbol. `new` writes BOTH singleton field AND shadow; `C(_addr)` writes ONLY shadow (shadow-propagation or address-match ladder). Mapping-getter polymorphism read emits `if_exprt` ladder on shadow. |
| **16** | 3D `__ESOL_deep_copy` fired spurious `_ESBMC_element_null_check` at top of `_ESBMC_arrcpy_2d(base->arr[i], ...)` | `value_set_domaint::transform` had a `default: // do nothing` branch that silently dropped every ASSUME, and `_ESBMC_alloc_array`'s calloc-returns-NULL branch polluted every struct-pointer field points-to set | **VSA ASSUME handler** — `value_set_domain.cpp` dispatches on ASSUME; new `value_sett::apply_assume` strips null-object / constant-zero entries for `p != 0` shapes. **Library non-null contracts** — `__ESBMC_assume(block != 0)` after calloc/malloc; `__ESBMC_assume(from_array != 0)` in `_ESBMC_arrcpy` / `_ESBMC_arrcpy_2d`. Commit `9af9744e32`. |
| **17** | Solidity user function whose name collided with C stdlib.h export (`div`, `abs`, `malloc`, `atoi`, `sort`, ...) was silently hijacked by the C decl | `get_sol_builtin_ref`'s FunctionCall branch unconditionally looked up `c:@F@<name>` in the C symbol table and bound to it when found, without checking whether the Solidity Identifier already had a positive `referencedDeclaration` | `solidity_convert_ref.cpp::get_sol_builtin_ref`: early-return when callee has `referencedDeclaration > 0`. Exception preserved for `__ESBMC_assume` / `__ESBMC_assert` / `__VERIFIER_*`. Commit `2614c484c2`. |
| **18** | `mapping(K => T[N])` (fixed-size array value) tripped "unsupported mapping value type: sol_type=ARRAY_LITERAL" in `is_new_expr` dispatch | `get_new_mapping_index_access` had branches for DYNARRAY, STRUCT, MAPPING, and scalar leaves, but no branch for ARRAY_LITERAL | **C helper** — new `map_fixed_arr_get(m, k, sz)` in `solidity_mapping.c`. **Frontend** — ARRAY_LITERAL dispatch emits the helper call with `sizeof(T[N])`. Commit `6ae0e57c3f`. |
| **19** | `map_generic_set(&m, k, v, sz)` was called with `v` in the `sz` slot (struct-typed value where `size_t` expected); worked by accident for small structs that implicitly coerced | `get_new_mapping_index_access`'s `val_flg=="generic"` SET branch pushed `symbol_expr(added_sym)` into both the value and size argument slots | `solidity_convert_mapping.cpp`: replace the duplicate value push with `size_of_expr`. Regression: `map_large_struct_set_pass/fail` |

Bugs 1-5 targeted regression work. Bugs 6-9 found while stress-testing on 1inch swap-vm and fusion-protocol. Bug 10 was a KNOWNBUG promotion. Bugs 11-15 landed during mapping storage-sharing work. Bug 16 landed with VSA ASSUME + library null-pointer contracts. Bugs 17-18 landed after the SolidiFi full-50 rerun hit SafeMath-using contracts. Bug 19 was incidental while tracing bug 18.

## 1inch Liquidity-Protocol Scan (archived)

The `liquidity-protocol-master/` tree used for stress testing has been removed from the working copy. Empirical findings from that scan, captured here so they survive the deletion.

### Best known flags for 1inch Solidity contracts

```
esbmc <contract>.solast --sol <contract>.sol --contract <Name> \
  --no-standard-checks --unwind 1 --no-unwinding-assertions --cvc5
```

**Why these flags and not others**:
- **Z3** (default fallback) fails with `Z3 error datatype is not well-founded` on the recursive struct datatypes ESBMC emits for Solidity storage.
- **Bitwuzla** (currently auto-selected for Solidity) prints `[bzla] warning: Equality over constant arrays not fully supported yet` and then aborts with `ERROR: SMT solver failed` on any contract that touches mappings via const-array equality. Do NOT rely on bitwuzla for mapping-heavy code.
- **CVC5** is the only backend that reaches verdicts on most of this repo — pass `--cvc5` explicitly to override the bitwuzla auto-select.
- **`--unwind 1` instead of 2**: the synthesized `_ESBMC_Nondet_Extcall_*` harness forms a mutually-recursive external-call graph. Under `--unwind 2`, symex fans out and times out. Under `--unwind 1`, symex finishes in 40-100s per contract.

### Scan result (post-fix)

Of 8 business contracts: 6 reach a verdict, 2 hit independent ESBMC bugs unrelated to the frontend.

| Contract | Outcome | Notes |
|----------|---------|-------|
| BalanceAccounting | ✅ verdict | |
| FarmingVoter | ✅ verdict | required commit `94013c6517` (get_line_number OOB) |
| FarmingRewards | ✅ verdict | same fix |
| MooniswapFactoryGovernance | ✅ verdict | required `4c4f1a57e9` (bitwuzla auto-select under k-induction) |
| ReferralFeeReceiver | ✅ FAILED | reports `function call: not enough arguments` — real frontend bug (stub called with wrong arity), worth chasing but NOT a crash |
| MooniswapDeployer | ✅ FAILED | same arity-mismatch pattern |
| Mooniswap | ❌ crash | SMT-encoding SIGSEGV (release path) |
| MooniswapFactory | ❌ crash | CVC5ApiException: `Given sort is not associated with the node manager of this solver` |

Both crashes are deterministic, not slow runs — widening timeouts or adjusting `--unwind` / `--slice-formula` / `--no-*-check` does NOT help.

### Debug-only frontend fixes landed during the scan

These were masked in release builds by `NDEBUG` and only fired under Debug / Sanitizer builds, but each was a real bug that made the sanitizer useless on the 1inch codebase:

- `94013c6517` — `get_line_number` heap-buffer-overflow: unconditionally did `contract_contents.begin() + (stoul(pos)+1)` and handed the result to `std::count`. Clamp `byte_position` to `contract_contents.size()` before the count.
- `537c5d6b07` — three asserts on solc 0.6.x AST nodes (ModifierInvocation missing "kind", move_inheritance_to_ctor bare access, Return assertion on stmt.functionReturnParameters).
- `ddc7a84712` — `get_high_level_member_access` crashed on using-for library attachment (`structureTypingMap[_cname]`'s `cname_set` can include a `using-for` library with no `$address` field).

### Never do

- Do NOT reintroduce the `incremental_mode` guard in `esbmc_parseoptions.cpp`'s bitwuzla auto-select — `4c4f1a57e9` removed it because k-induction on recursive Solidity datatypes was pinned to Z3 and thus ALWAYS crashed with "not well-founded".
- Do NOT try to fix the Mooniswap/MooniswapFactory crashes by tuning timeouts or flag combinations — they are crashes in ESBMC code, not verification timeouts.
