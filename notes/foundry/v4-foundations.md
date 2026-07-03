# Foundry support — v4 foundations (nail these BEFORE modeling)

Supersedes the import/stub assumptions in design-plan.md. Written after the user's
correct challenge: "you can't model however you feel like — where's the evidence
of alignment, what if you hallucinate?" and "did you consider ALL Foundry details?"

Everything here is grounded in **real forge-std v1.16.2** (vendored locally, tag
bf647bd) and **real `forge 1.7.1`** as oracle — not memory. Surface catalog:
`scratchpad/foundry_surface.md` (extracted from the real source).

## 0. The scale we're actually facing (evidence, not estimate)

From the real `Vm.sol` / StdAssertions:
- **560 Vm cheatcode functions** across Crypto(15) Environment(12) EVM(102)
  Filesystem(36) JSON(31) Scripting(9) String(14) Testing(46) Toml(20)
  Utilities(25). Currently modeled: **5** (warp, roll, assume, expectRevert + the
  gate). 
- **129 assertion overloads** (~20 distinct names). Currently modeled: ~8.

We will never model all 560. The plan's SOUNDNESS must therefore NOT depend on
completeness — it must rest on the gate (below). Completeness is a USEFULNESS
axis, measured against this catalog + the forge conformance corpus.

## Foundation 1 — Anti-hallucination: real forge is the oracle, always

- Every modeled cheatcode/assertion carries a **conformance test** whose expected
  verdict comes from **actually running `forge test`** (forge 1.7.1 + forge-std
  v1.16.2 present in this env), never from my reasoning. Proven already: warp /
  assertEq / expectRevert matched real forge **5/5** (`scratchpad/conf/`).
- The old benchmark's `forge_truth` column (I *reasoned* it) is void → **re-pin
  every forge_truth by running forge**.
- **We do NOT hand-write behavior we can't cite.** The bundled forge-std model is
  the **real forge-std source, vendored and pinned** (not rewritten): cheatcodes
  stay interface-only (intercepted by the frontend), pure helper bodies run as-is
  (faithful by construction), only bodies ESBMC cannot ingest (heavy inline
  assembly) are replaced with taint stubs — and each such replacement is noted.
  Zero invented semantics.

## Foundation 2 — Gate completeness (close hole #2)

Three dispositions for the whole forge-std surface (from the catalog):

| disposition | which | mechanism | soundness |
|---|---|---|---|
| **A. Modeled** | the conformance-tested set (warp/roll/assume/expectRevert/prank/deal/warp/fee/… + assert*) | frontend interception (`handle_foundry_cheatcode`/`handle_forge_std_assert`) | verdict ≡ forge (conformance-checked) |
| **B. Run-as-is** | pure `StdUtils`/`StdCheats` helpers with executable Solidity bodies (bound, toString, computeCreateAddress, parseUint, makeAddr…) | none — ESBMC executes the real vendored body | faithful (it IS the real code) |
| **C. Taint/prune** | every OTHER `vm.*` cheatcode (all 560 minus set A), + unmodelable-by-nature (ffi, createFork/selectFork/rollFork/rpc, all Filesystem/JSON/Toml, mockCall/etch/store, broadcast, sign*, env*, sleep) | the `base_cname=="Vm"` fallback → `ASSUME(false)` prune | conservative: never false-WRONG (proven reachability-sensitive) |

Key correction: the `vm.*` namespace is **already fully gated** — `base_cname=="Vm"`
routes EVERY `vm.foo()` to interception (set A) or prune (set C). Hole #2 was only
the **non-`vm.*`** helpers (set B) + calls like `console.log`. Disposition:
- **console.log/console2.log** → the real body staticcalls the console address →
  a harmless external call (nondet return, no state effect). Safe as-is; may add a
  no-op model to silence noise.
- **Set B helpers** → run the real vendored body. If a body internally calls an
  unmodeled `vm.*`, that inner call is gated (→ taint propagates). If a body uses
  ESBMC-unsupported assembly, it fails → treat as taint (vendored stub).
- **Residual risk to verify per-helper**: a set-B helper that ESBMC executes but
  *mis*-models (e.g. an assembly path silently mis-lowered) → could false-WRONG.
  Mitigation: every set-B helper actually reached by the corpus gets a conformance
  test too; unverified set-B stays candidate-taint.

Gate rule (final): **anything originating from the Vm interface that is not in the
conformance-verified set A is tainted.** Set B is opt-in per-helper only after a
conformance test passes; until then it is treated as set C.

## Foundation 3 — Oracle tiering (the deepest hole: forge is NOT always ground truth)

`forge test` is authoritative ONLY for deterministic/concrete tests. Tiers:

1. **Concrete test** (no fuzz args, no fork/time/random): forge verdict = ground
   truth. ESBMC must match. `ESBMC=WRONG & forge=PASS` → **false-WRONG = blocker**.
2. **Fuzz / PUT** (`testFuzz_`, parameterized): forge SAMPLES; a PASS only means
   "no counterexample in N samples", NOT correctness. ESBMC proves ∀. So:
   - `ESBMC=WRONG & forge=PASS` here is **NOT a blocker** — ESBMC likely found a
     real input forge's sampler missed. Verify by feeding ESBMC's witness back to
     forge (a concrete replay) — if forge then FAILS on that input, ESBMC was
     right. Only if the witness does NOT reproduce in forge is it an ESBMC bug.
   - So the fuzz oracle is **forge-on-the-concrete-witness**, not forge-fuzz-PASS.
3. **Nondeterministic** (fork/RPC/time/random/ffi): no stable oracle → these tests
   are **out of scope** (tainted; reported CORRECT/inconclusive, never verified).

The blocker metric is thus: **any concrete-tier `ESBMC=WRONG & forge=PASS`, OR any
fuzz-tier witness that forge cannot reproduce.** That is the anti-hallucination
red line, and it is fully automatable with the tools present.

## What v4 still may be missing (explicit unknowns — not claiming completeness)

Named so a reviewer can attack them; each currently lands in set C (safe) until modeled:
- **Test lifecycle**: `setUp()` per-test + snapshot isolation, `testFail_` prefix,
  constructor-vs-setUp order, `beforeTestSetup`, fixtures (`fixtureX`),
  `afterInvariant`. My harness does not yet reproduce per-test isolation.
- **Invariant campaigns**: `targetContract/targetSelector/targetSender`,
  `excludeContract`, handler ghost state, `invariant_` semantics.
- **Assertion soft-flag-continue** vs my immediate-assert (diverges only for tests
  that read `vm.failed()` or expect multiple failures); decimal/approx/tolerance
  families (`assertApproxEqRel/Abs`, `assertEqDecimal`) unmodeled → set C.
- **expectRevert nuances**: selector/return-data matching (ignored → conservative),
  `expectPartialRevert`, `expectEmit`, `expectCall`, nesting, count, "which call".
- **prank/startPrank** next-call-vs-persistent, prank-across-revert.
- **Multiple inheritance** test bases, `using X for Y`, immutables in setUp.
- **Compilation**: project deps beyond forge-std (@1inch/@openzeppelin) — OZ we
  can vendor+remap like forge-std; arbitrary project deps are out of scope unless
  vendored case-by-case.

## Build order (foundations first, then measured expansion)

1. **Vendor forge-std v1.16.2 into ESBMC's tree** + `invoke_solc` remapping
   (`forge-std/=<bundled>`, `--base-path <src dir>`, `--allow-paths <bundled>`),
   OZ remap likewise. Prove: the real `test/Conf.t.sol` (import forge-std, NO
   node_modules) compiles + runs through ESBMC.
2. **Conformance harness**: driver that runs a corpus through BOTH forge (oracle,
   tiered) and ESBMC, diffs verdicts, and reports (a) alignment rate on concrete
   tier, (b) fuzz-witness reproduction, (c) taint rate. Any concrete false-WRONG
   fails the harness.
3. **Coverage map** against `foundry_surface.md`: per cheatcode/assertion →
   {A modeled+conformed | B run-as-is+conformed | C tainted}. Grow set A only with
   a passing conformance test.
4. Only then expand modeling (prank/deal/env-setters/approx-asserts/lifecycle),
   each gated by conformance.

## Review
This v4 + `foundry_surface.md` go to Codex before any modeling resumes. The two
foundations to stress hardest: (2) does the gate REALLY catch every non-set-A path
including set-B mis-execution, and (3) is the tiered oracle actually sound for fuzz
and for the "witness replay" check.
