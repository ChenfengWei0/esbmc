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
- revert-branch coverage → `vm.expectRevert(...)`. **Phase A (custom error) DONE,
  forge-validated 2026-07-04.** A `revert CustomError(...)` reverting edge is now
  detected and wrapped in bare `vm.expectRevert()`. Mechanism: `get_error_definition`
  tags the compiled error function symbol `#sol_error`; `goto_coverage.branch_coverage`
  runs a conservative straight-line walk (`edge_reaches_error_revert`) from each edge
  of every GOTO decision and stamps `sol_revert_edge` on the reverting edge's probe
  (only when EXACTLY one edge reverts — nested/ambiguous → no tag); the generator
  (`foundry.cpp reconstruct`) reads that off the covered claim's kept assert and marks
  the tx segment active at that step, emitting `vm.expectRevert()` before its call.
  No bmc/slice signature change (the marker rides the SSA step location). Bare
  `vm.expectRevert()` (matches any revert) sidesteps selector qualification. Regression
  `foundry_covgen_revert_fail`; forge: R.sol `strict(42)` FAIL→PASS, M.sol `c(42)` PASS.
  **CAVEAT — Phase A does NOT cover Aqua.** Aqua's revert is
  `require(cond, SafeBalancesForTokenNotInActiveStrategy(...))` — the 2-arg
  `require(cond, ErrorInstance)` form (Solidity >=0.8.26), which the frontend lowers
  via the require rollback path (the error arg is DROPPED, no error-function call),
  i.e. shape `a` (early-return, no terminator). Verified: minimal `require(v!=7, Bad(v))`
  (scratchpad/revert_ka/Q.sol) and actual Aqua `safeBalances`
  (`--focus-function safeBalances --cvc5`, 6.25%) both generate the call with NO
  `vm.expectRevert`. So Aqua needs Phase B.
  **Phase B SOLVED via Option 3 (try/catch fallback) — DONE, forge + Aqua validated
  2026-07-04.** Rather than statically detect the require revert (a hard, edge-polarity-
  and k-induction-sensitive problem — the cond-comparison design below was DEFERRED in
  favour of this), the generator now emits, for every external call it CANNOT prove the
  outcome of (i.e. not a Phase-A detected `revert E()` edge):
  `// [revert-tolerant]` + `try cN.m(args) {} catch {}`. This swallows an undetected
  revert so the assertion-free replay stays a PASS in forge; a non-reverting call runs
  normally in the try body. Phase-A detected edges KEEP the precise `vm.expectRevert()`
  (so an ESBMC<->forge divergence still surfaces loudly). Emission-only change
  (foundry.cpp write_foundry_file) — NO frontend/goto_coverage/k-induction change.
  Counts logged ("N wrapped in vm.expectRevert (detected)" + "N in revert-tolerant
  try/catch"). Codex-reviewed (endorsed Option 3 > uniform try/catch > static detection).
  **Real Aqua `safeBalances` (`--focus-function safeBalances --cvc5`): FAIL -> PASS.**
  Forge round-trips: Q.sol (require+error) 2/2 + 100% branches; R.sol 4/4 (was 3/4) +
  100%; multi-call rollback confirmed EVM-accurate (per-call state rollback matches
  ESBMC tx-isolation). Regression `foundry_covgen_require_revert_fail`.
  KNOWN LIMIT (Codex): try/catch converts a wrong-tx-context divergence (missing payable
  `{value:v}` / sender `vm.prank` — a pre-existing gap) from a loud FAIL into a silent
  pass; the `[revert-tolerant]` comment + count keep it auditable, but value/sender-
  dependent branches are NOT claimed as faithfully reproduced.
  (DEFERRED cond-comparison design, kept for reference: reverting edge = edge taken when
  require COND is false; guard polarity shape-dependent — frontend stamps simplified cond,
  goto_coverage compares simplify(guard) to simplify(cond)/simplify(!cond). Metadata-only /
  k-induction inert, but misses side-effecting/split conditions. Superseded by Option 3.)
  Full Aqua whole-unit still times out (solver-scale, 0 reached/240s) => end-to-end Aqua
  validation needs `--focus-function` + `--cvc5`.

### Revert-branch fidelity — detection is NOT generator-local (2026-07-04 investigation)

Verified against known-answer `R.sol`
(`require(v>10)` + `revert TooSmall(v)`; see
`scratchpad/revert_ka/`). The revert is modeled strictly **downstream** of the
branch-coverage claim and is **sliced out** of the per-claim equation the
generator sees, so `foundry.cpp` cannot detect it from the SSA it walks:

```
ASSERT v == 42                 ← the coverage claim (what the counterexample proves reachable)
IF !(v == 42) THEN GOTO 1
FUNCTION_CALL: TooSmall(v)      ← the revert; TooSmall's body is `ASSUME false`
1: ASSIGN this->x = v
```

- The claim precedes the revert; per-claim slicing drops the `TooSmall` call +
  its `ASSUME false` from `local_eq`. Self-confirming: the claim solves **SAT**,
  which is impossible if an `assume(false)` were on its path.
- `require(cond)` lowers to a bare `IF !cond GOTO end` early-return
  (`build_revert_rollback_block`, solidity_convert_modifier.cpp:763) — **no**
  SSA-visible marker at all. Custom-error `revert E(...)` lowers to a
  `FUNCTION_CALL` to an error symbol (`sol:@C@<C>@F@<E>#..`, body `ASSUME false`,
  get_error_definition solidity_convert_decl.cpp:1728) — a marker, but downstream
  and therefore sliced.
- The only AST-level marker (`#sol_revert_rollback`) is consumed by the reentry
  check at frontend time and never reaches SSA; `_ESBMC_sol_reverted_flag` exists
  only under `--bound`/opt-in.

**Consequence:** faithful detection must key off the covered claim's *static*
identity ("this branch edge leads to a revert"), not the dynamic sliced trace.
Minimal design (cross-cutting):
1. Frontend: tag the revert/require branch condition (reuse `#sol_revert_rollback`
   or a coverage-specific tag) with the error name when known.
2. `goto_coverage`: propagate the tag onto the reachability assert it synthesises
   for that edge (into the claim's location/comment).
3. `collect()`: receive the *violated-claim identity* (bmc.cpp:2181 currently
   passes only `local_eq` + model — the reconstruction does not know which claim
   it covers, nor which call in a multi-call tx sequence is the reverting one).
4. Generator: when the covered claim carries the revert tag, wrap the last call of
   that tx in `vm.expectRevert(<C>.<E>.selector)` (bare `vm.expectRevert()` when
   the error is unknown — still faithful, matches any revert).

This is a real multi-component feature, not a `foundry.cpp` edit. Deferred pending
explicit go-ahead (design fork logged 2026-07-04).

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
