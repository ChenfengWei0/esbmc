# Approximation Ledger (soundness & completeness trade-offs)

**Purpose.** This section records every deliberate abstraction in the
Solidity frontend that sacrifices soundness or completeness. Each entry
documents (1) where the approximation lives, (2) whether it is
over-approximate or under-approximate, (3) what kinds of false positives
or false negatives it can produce. Code sites carry an `[APPROX: OVER]`
or `[APPROX: UNDER]` marker that mirrors the entries here — grep for
`\[APPROX:` to find every in-source warning.

## Terminology

- **Over-approximation (sound, incomplete)**: the model admits *more*
  behaviours than the real system. Counterexamples may be spurious
  (false positives). Proofs of safety carry over to the real system.
- **Under-approximation (unsound, may be complete)**: the model admits
  *fewer* behaviours. Bugs reachable only in the missing behaviours are
  not detected (false negatives). Counterexamples are real bugs.

"Sound for safety" = no real bug is missed. "Sound for equality" = the
abstraction is deterministic and injective; reasoning about identity
holds but reasoning about bit patterns does not.

## Ledger

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
| 21 | Free-function `bytes memory` return of a string literal | `solidity_convert_stmt.cpp::ReturnStatement` (free-function path) | OVER | When a free function returns a string literal as `bytes memory`, the conversion needs a dynamic pool, but free functions have no containing contract. The return value is replaced with `llc_nondet_bytes()`. Contract-member functions keep the precise `bytes_dynamic_from_string` path. | None for length-range / existence checks. | The actual byte content of the returned literal is discarded — two successive calls return independent samples (no `f() == f()` invariant). `stress_func_ptr_longdata_1` escapes this by using `keccak256(a) == keccak256(b)` which is already nondet under the abstract keccak model. |

## How to use this ledger

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

## In-source markers

Every approximation above has a matching code comment; `rg '\[APPROX:'`
finds the canonical list. Table rows and code comments MUST be kept in
sync. If you remove an approximation, delete the marker *and* the row.
