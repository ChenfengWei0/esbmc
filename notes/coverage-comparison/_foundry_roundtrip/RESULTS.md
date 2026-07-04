# Coverage → Foundry test round-trip experiment

Goal (user): take the coverage ESBMC finds (which the prior coverage-comparison
showed *exceeds* the native suite), convert it to Foundry tests, run `forge`, and
check:
  1. **Alignment** — does forge's coverage of the generated test match ESBMC's?
  2. **Supplement** — do the generated tests cover the exact paths the native
     suite misses (proving those paths reachable via a runnable test)?

Tooling: ESBMC `--branch-coverage --generate-foundry-testcase --no-assertions`;
forge 1.7.1; forge-std vendored from `aqua/src/node_modules/forge-std`.
Project: `demo/` (standalone forge project: `src/`, `test/`, `lib/forge-std`).

## Result 1 — mechanism + exact alignment (scalar-only `Vault`)

`src/Vault.sol`: two decisions, both renderable (`uint256`, `int256`).
Incomplete "native" suite (`test/Vault.native.t.sol`) only feeds small/positive
values → reaches only the FALSE side of each decision.

| suite                         | forge % Branches (Vault) |
|-------------------------------|--------------------------|
| native only                   | **50.00% (2/4)**         |
| native + ESBMC-generated      | **100.00% (4/4)**        |

ESBMC reported: `Branches: 4  Reached: 4  (100%)`.
Generated `VaultCovTest` (4 cases): `withdraw(MAX)`, `withdraw(1000)`,
`adjust(INT256_MIN)`, `adjust(0)` — one per branch outcome.

- **Goal 1 (alignment): PASS.** ESBMC 4/4 reachable ⇒ forge confirms 4/4 (100%).
- **Goal 2 (supplement): PASS.** The generated test added the two branches
  (`withdraw > 1000` TRUE, `adjust < 0` TRUE) the native inputs never reached;
  50% → 100%.

## Result 2 — `bytes32` value rendering added (`KA`)

`src/KA.sol`: `setBig(uint256)` + `setHash(bytes32)`. A `bytes32` value is
modeled as a `BytesStatic` struct `{ uint8 data[32]; size_t length; }`, so the
recovered model value is a `constant_struct`, not an int. Added
`format_bytes_static` (src/goto-symex/foundry.cpp): read `data[0..N-1]`
big-endian and emit `bytesN(0x..)`, which round-trips to the exact value solc
produces (`data[j] = val >> 8*(len-1-j)` in bytes_static_from_uint).

Generated `KACovTest` now emits:
```solidity
c0.setBig(MAX);   c0.setBig(100);                                     // both setBig branches
c0.setHash(bytes32(0x0000..0007));                                    // == bytes32(uint256(7)): setHash TRUE branch
// UNSUPPORTED: KA.setHash ...                                        // setHash FALSE branch (see below)
```

| suite                    | forge % Branches (KA) |
|--------------------------|-----------------------|
| native only              | 33.33% (1/3)          |
| native + ESBMC-generated | **100.00% (3/3)**     |

- **Round-trip proven for `bytes32`:** the `bytes32(0x..07)` literal covers the
  `setHash` branch in forge — 33% → **100%**.
- The FALSE-branch case stays `// UNSUPPORTED` by design: on that branch the
  model leaves the struct's `length`/`data` only partially constrained, so the
  value is not fully concrete — the generator degrades gracefully rather than
  emit a value that might drive the wrong branch (anti-goal: never a wrong test).

## Codex adversarial review — two defects found and fixed

A fresh Codex review of the bytesN commits found two real defects; both fixed in
`src/goto-symex/foundry.cpp` (generator-only; no frontend change):

- **Defect 1 (soundness, wrong-width literal):** a *recovered* bytesN value was
  formatted at the recovery site via the parameter symbol's type, which is
  width-degraded; it then fell to a `.length`-based width fallback that could
  emit a wrong-width literal (e.g. `bytes32(..)` for a `bytes4` param — the exact
  anti-goal). Fix: `build_call` now re-formats every recovered value against the
  **declared** type (`decl.second`, which carries the authoritative stamped
  width), and the `.length` fallback was removed entirely — a bytesN with no
  declared width degrades to UNSUPPORTED rather than guess.
- **Defect 2 (constructor coverage):** `get_method_params` (constructor / base-
  name path) didn't read the argument width stamp, so constructor `bytesN` args
  went UNSUPPORTED. Fix: route it through the shared `arg_sol_type` helper.

Known-answer `KB` (bytes4 param + bytes4 constructor arg, forge-validated):
```solidity
setUp():  c0 = new KB(bytes4(0x00000000));                 // ctor bytes4 (defect 2)
test_0:   c0.poke(bytes4(0x12345678));                     // recovered value, EXACT width (defect 1)
test_1:   c0.poke(bytes4(0xffffffff));                     // false branch, still bytes4
```
Forge: KB compiles, 2/2 tests pass, **100% (1/1) branch coverage**. Regression
test `regression/esbmc-solidity/foundry_covgen_bytesN_fail` added; foundry suite
8/8.

## Sliced `bytes32` params — now rendered (Aqua `safeBalances`)

Aqua's `strategyHash` is a *sliced* param (it only indexes a mapping on an empty
map, so its value cannot change branch reachability — symex slices it away). A
sliced param has no recovered value, so it needs a **default** literal, which
needs the width N from the signature. `#sol_bytesn_size` is present on the
parameter type at frontend creation (verified: `strategyHash` → `32`) but is
**stripped during `symbol_typet`→`struct_typet` type migration** before the
generator runs. Fix: stamp the width directly on the **code_typet argument**
(get_function_params, an irep on the argument, not its type — survives
migration); `params_of_method_id` reads it back. Now Aqua generates:

```solidity
c0.safeBalances(address(0), address(0), bytes32(0x00..00), address(0), address(0));
```

and KA renders *both* `setHash` branches (the false branch defaults to
`bytes32(0)`, which correctly `!= bytes32(7)`). The stamp is verification-inert
(every existing `#sol_bytesn_size` reader reads it from a *type*; the argument
attribute is read only by the generator).

**Still open for full Aqua alignment:** Phase-2 revert fidelity — Aqua's covered
branches sit behind reverting `safeTransferFrom` (token = non-contract addr) and
empty-mapping `require`, so an assertion-free replay reverts in forge; needs a
mock ERC20 + `vm.expectRevert`. Generation is no longer the blocker for the
scalar/bytesN/address methods; the harness environment fidelity is.

**Forge validation of the current Aqua output** (flat `Aqua.sol` as a forge
`src`, generated test as `test/`): `test_cov_0` — the newly-rendered
`safeBalances(address(0), address(0), bytes32(0x00..00), address(0), address(0))`
— **compiles and runs**, and FAILs with exactly the branch ESBMC covered:
`[FAIL: SafeBalancesForTokenNotInActiveStrategy(0x0, 0x0, 0x0..0, 0x0)]`. That is
the require-FALSE (revert) branch on the empty mapping; the assertion-free replay
makes forge count the top-level revert as a failure. So the sliced-bytesN fix
closes generation, and the demonstrated next step is emitting
`vm.expectRevert(SafeBalancesForTokenNotInActiveStrategy.selector, ...)` for a
counterexample that reached a revert (Phase 2). (`ship` stays UNSUPPORTED — it
takes dynamic `bytes` + arrays, Phase 1's remaining items.)

## Why the real benchmarks (aqua/EscrowDst/LOP) don't round-trip yet

Ran the generator on the real `aqua Aqua` flat input (the cleanest ESBMC>native
case, 7/8 vs 6/8). All three reconstructed calls degraded to UNSUPPORTED:
every `Aqua` method takes a `bytes32 strategyHash` (and `ship`/`dock` also take
`bytes`/`address[]`).

Root cause (`src/goto-symex/foundry.cpp:65-94` `format_sol_value`): only
`UINT*/INT*/BOOL/ADDRESS` render; `bytes32` returns `""`.

Feasibility probe (`KA.setHash`): a `bytes32` param is modeled as a **BytesStatic
struct** (violated property `return_value$_bytes_static_equal$2`), so the
recovered model value is a `constant_struct`, not a `constant_int` — rendering it
needs struct-member extraction, not a new scalar branch.

Blocker matrix (benchmarks where ESBMC > native):

| benchmark            | gap |
|----------------------|-----|
| aqua `Aqua`          | Phase 1: `bytes32`/`bytes`/`address[]` params → UNSUPPORTED. Then Phase 2: the covered branches sit behind reverting `safeTransferFrom` (token = non-contract addr) and `require` on an empty mapping → forge needs a mock ERC20 + `vm.expectRevert` |
| cross-chain `EscrowDst` | Phase 1: methods take an `Immutables` **struct** |
| LOP `MakerTraitsLib` | pure **library** (internal fns, no deployable dispatcher) — generator has no harness to emit |

## Next step

Implement `bytes32` rendering (roadmap Phase 1, top lever): extract the packed
value from the recovered BytesStatic struct → emit `bytes32(uint256(N))`. That
converts 4/6 `Aqua` methods (`rawBalances`, `safeBalances`, `pull`, `push`) from
UNSUPPORTED to rendered. A faithful aqua forge alignment additionally needs a
Phase-2 slice (mock ERC20 + `vm.expectRevert`) because the interesting branches
revert.
