# Foundry coverage-test generation — roadmap to complete support

Scope: **generation only** (ESBMC counterexample → Foundry `.t.sol` that reproduces
coverage). Verifying user-written Foundry tests is explicitly **out of scope**.

Anti-goal: never emit a wrong test. Any construct we cannot faithfully render
degrades to an `// UNSUPPORTED` comment, never an incorrect call/value.

Every phase is gated by: (1) a known-answer contract whose branch is coverable
*only* via the feature, (2) real `forge test` + `forge coverage` where the branch %
must equal ESBMC's reported coverage, (3) a `_fail` regression (and `_pass` when the
option should be inert).

## Done (committed, forge-validated)

- `--branch-coverage --generate-foundry-testcase` (sanctioned single-`bmct` mode).
- Transaction-sequence reconstruction: split SSA at dispatcher guards
  (`_ESBMC_Nondet_Extcall_<C>#..return_value$_nondet_bool$1`), take each tx's method
  from the first contract-method body executed (source-location), recovering
  parameterless / sliced-argument calls. Sliced args → type defaults (sound: a
  sliced arg cannot change which branch is reached).
- Constructor args recovered per goal → different constructions → different `setUp`.
- Emission: one file per contract-under-test; one `contract <C>CovTest[_n] is Test`
  per distinct construction signature, each with `setUp()` deploying the instance
  and the sharing cases as `test_cov_*`.
- Arg types: `uint*/int*`, `bool`, `address`.
- Tests are assertion-free by design (coverage = execution, like ctest/pytest).
- Overloaded methods resolved to the exact overload via the dispatcher-callable
  set (`_ESBMC_Nondet_Extcall_<C>` body), with explicit type casts to
  disambiguate in Solidity; unresolvable overloads degrade to UNSUPPORTED.
  (commit 15d181b74c, forge-validated Ovl 100%=100%).
- Modifier-wrapped methods: the aux helper `<method>_<modifier>` is excluded
  from the callable set, so we emit the real `<method>` entry, not the
  uncompilable aux name (commit 15d181b74c).
- Import points at the `--sol` source, not the `.solast` AST input, so forge
  can compile the generated file (commit 15d181b74c).

## Phase 1 — parameter/constructor type rendering
Biggest hit-rate lever; these currently go UNSUPPORTED.

- `bytesN`: DONE for *recovered* values (`format_bytes_static`, foundry.cpp):
  a `bytesN` value is a `BytesStatic` struct `{data[32], length}`; read
  `data[0..N-1]` big-endian → `bytesN(0x..)` (round-trips to solc's layout).
  Forge-validated (KA `setHash(bytes32(0x..07))` covers the branch, 33%→100%).
  *Sliced* `bytesN` params (value irrelevant to coverage, so not recovered) also
  DONE: the width is stamped on the code_typet argument in get_function_params
  (survives the type migration that strips it from the param type), so a sliced
  bytesN gets an exact-width default (`bytesN(0)`). Aqua `safeBalances` now
  renders; KA renders both `setHash` branches. Verification-inert (argument
  attribute, read only by the generator).
- `bytes`: model dynamic bytes → `hex"..."`.
- `string`: recover from `$dynamic_pool` → `"..."` (non-printable → UNSUPPORTED).
- `enum`: `EnumName(v)` or `uint8`.
- array / struct params: `[a,b]` / `T({...})`; nested-unrenderable → UNSUPPORTED.
- payable ctor/method value: `new C{value:v}()` / `c.f{value:v}()`.

## Phase 2 — environment cheatcode fidelity  ⭐ (top fidelity risk)
Real-contract branches gate on `msg.sender`/`block.timestamp`/`balance`, which
ESBMC pins nondet-ly but forge runs under defaults → coverage diverges.

**Confirmed gap (known-answer `Owned`, msg.sender==owner):** ESBMC reports 100%
branches, forge reaches only 75% lines — the `!=owner` path is dead, because
`msg.sender` is not recovered (both counterexamples dedup to one test) and there is
no `vm.prank`.

**The naive fix is UNSOUND.** `owner = msg.sender` at deploy time makes `owner`
deployment-relative: ESBMC's concrete owner address ≠ forge's (the test contract).
Pranking ESBMC's *literal* sender against forge's different owner can flip the
branch.

**Sound design — address equality-class canonicalization:** in the model, partition
all address-typed values (per-tx `msg.sender`, stored `owner`, `this`, args…) into
equality classes. Map each class to a canonical Foundry actor
(`deployer`/`actorA`/…). Deploy via `vm.prank(deployer)` so `owner==deployer`, then
per tx `vm.prank(<class-of-sender>)` — preserving `==`/`!=` relations, not literals.
Include the sender class in the dedup fingerprint so distinct-sender cases don't
collapse.

- `msg.sender`   → `vm.prank(<canonical actor>)` (relational, see above).
- `msg.value`    → `c.f{value:v}()` + `vm.deal(sender, v)`.
- `block.timestamp`/`number` → `vm.warp`/`vm.roll` (from `_sol_per_tx_reseed`).
- `address.balance` → `vm.deal(addr, bal)` (from the EOA balance map).
- revert-branch coverage → `vm.expectRevert(...)` (a reached revert currently makes
  the assertion-free replay FAIL in forge instead of counting as covered).

## Phase 3 — coverage-mode robustness  ⭐ (architectural must-fix)
Project discipline requires coverage under **k-induction** (fixed unwind is a guess).
The generator today only collects under single-`bmct` `--branch-coverage`;
k-induction / incremental-bmc rebuild `bmct` per k-step, destroying the member
generator (parseoptions-local stays empty — a gap shared with ctest/pytest).

- Fix generator-instance ownership so collection survives k-induction/incremental.
- Extend beyond branch coverage to assertion- and condition-coverage modes.

## Phase 4 — reconstruction robustness
- ~~Overloaded methods~~ DONE (commit 15d181b74c): resolved via the
  dispatcher-callable set + recovered param names, casts to disambiguate.
- ~~Modifier-wrapped methods~~ DONE (commit 15d181b74c): aux helpers excluded
  from the callable set; the real public entry is emitted.
- Nested low-level/external call misread as a top-level tx: investigated, does
  NOT reproduce (known-instance call = direct call; unknown-external = nondet
  havoc; neither drives the dispatcher guard). Locked by
  foundry_covgen_nested_call_fail.
- Inheritance / `fallback` / `receive`: known-answer coverage.
- Consecutive same-method calls in one tx sequence: currently merged (parameterless
  double-call indistinguishable); split via guard counting.

## Phase 5 — parameterization (deferred by product choice)
- `testFuzz_*(...)` + `vm.assume(<path constraints>)` folding a family of
  counterexamples into one fuzz test. Depends on Phase 1/2 value + constraint
  recovery.

## Suggested order
Phase 2 (fidelity, or coverage silently diverges) → Phase 3 (k-induction ownership,
architectural) → Phase 1 (hit-rate, incremental) → Phase 4 → Phase 5.
