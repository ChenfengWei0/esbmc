# Plan: Foundry-test-support REGRESSION BENCHMARK (target-first)

Build this benchmark **before** implementing F0–F2 (see `foundry-test-support-plan.md`), so the
target is fixed before we shoot. Every test's expected verdict is pinned up front by an independent
oracle (`forge test`), not back-fitted to whatever ESBMC happens to output.

Scope: both features.
- **F1 (verify)**: `esbmc <flat>.sol --foundry --contract <TestC> [--foundry-test <fn>] ...` → CORRECT
  (`VERIFICATION SUCCESSFUL`) or WRONG (`VERIFICATION FAILED`).
- **F2 (emit)**: `esbmc <buggy>.sol ... --generate-foundry-testcase` → a compilable `*.t.sol` that
  `forge test` reproduces.

---

## 1. Oracle & the conservativeness contract (the whole point of target-first)

**Oracle soundness fix (Codex #1): `forge test` alone cannot decide "the test is wrong."** A red
`forge test` only means test-and-contract disagree — it can't tell a bad test from a bad reference
contract. So every case ships a **frozen spec artifact** (`spec.md` in the dir), authored before
implementation:
- a one-line property/invariant the case pins;
- an explicit classification: `correct-test` | `bad-test` | `bad-contract`;
- for FAIL cases, which side is wrong (the whole point — ESBMC-verifying-a-test assumes the contract is
  the oracle, so a `bad-contract` case is a *different* experiment and must be labelled, not fed to F1);
- the mutant relationship to its pass-sibling (one-line diff) + a minimal hand argument for the verdict.

`esbmc_expected` is derived from that frozen classification, NOT from forge. Each F1 test then carries a
**two-column oracle**, both fixed before implementation:

| column | how obtained | meaning |
|---|---|---|
| `forge_truth` | actually run `forge test` (pinned forge/solc version) on the real Foundry test | does the test PASS or FAIL on a real EVM |
| `esbmc_expected` | derived from the frozen `spec.md` classification + conservative mapping below | the verdict ESBMC MUST produce |

Conservative mapping (defines `esbmc_expected` from the test's construction):
- Test is **correct** (its expectations match the reference contract) → `forge_truth=PASS` → **CORRECT**.
- Test is **wrong** AND uses only *supported* constructs → `forge_truth=FAIL` → **WRONG**.
- Test is **wrong** BUT touches an *unsupported* construct (support matrix miss) → `forge_truth=FAIL`
  but **esbmc_expected = CORRECT** (hard-taint). ← *the never-false-WRONG invariant.*

So for non-tainted tests `esbmc_expected` tracks `forge_truth`; for tainted tests they deliberately
diverge. The benchmark records BOTH columns per test in a `ORACLE.md` table so a reviewer can see the
conservative divergences are intentional, not bugs.

**Gate granularity decision to pin now:** hard-taint is **per `--foundry-test` function**, not per file,
so one tainted test fn does not suppress WRONG on a sibling. (Feeds F1.0 in the design plan.)

---

## 2. Shared benchmark assets (build first)

1. **`forge-std` / `Vm` compile stub** (`regression/esbmc-solidity/_foundry/forge_std_min.sol`):
   the minimal declarations so `solc --ast-compact-json` compiles each test standalone — `Vm` interface
   with the cheatcode signatures we model (`assume/prank/startPrank/stopPrank/deal/warp/roll/expectRevert`),
   `Test` base with `assertEq/assertTrue/assertFalse/assertGt/assertLt/assertApproxEqAbs`, `bound(...)`,
   `vm.failed()`, and the ESBMC intrinsic stubs `__ESBMC_reverted()`/`__ESBMC_assume(bool)`. This stub is
   an *asset*, not code under test; it is inlined/flattened into each `contract.sol`.
2. **`.solast` regeneration script** (`regression/esbmc-solidity/_foundry/regen.sh`): `solc --ast-compact-json`
   per test (mirrors existing Solidity-regression `.solast` convention).
3. **`ORACLE.md`** generator: runs `forge test --json` over the real test files and records `forge_truth`.
4. Each ESBMC test dir keeps the ESBMC regression format: `contract.sol` (+ `contract.solast`) + `test.desc`
   (mode, args, expected-output regex).

---

## 3. Complexity tiers — HYBRID axis (Codex #6 + user framing)

Outer ladder stays **SLOC 20→50→70→100+** (legible, shows progression). But SLOC is metadata, not the
real difficulty measure, so **every test also carries a semantic-complexity vector** in its `spec.md`:
`{ #cheatcodes, cheatcode-families, #assertion-families, PUT-vs-concrete, call-depth, revert-data-dep,
snapshot-dep, cross-contract }`. Curate each tier so the vector actually rises with SLOC (don't let a
100-SLOC test be semantically trivial). **SLOC is counted INCLUDING the inlined stub surface the test
exercises** (Codex #6: excluding it hides real frontend complexity) — reported as `sloc_total` +
`sloc_logic`.

### Reachability-sensitive taint anchors (Codex #2 — critical)

The conservativeness anchors ("wrong test, but ESBMC must stay CORRECT") are only meaningful if the taint
is **reached**, not pre-scanned-and-pruned. Each anchor dir MUST:
- place a **supported assertion BEFORE and AFTER** the unsupported construct, both on the reachable path,
  so a passing verdict proves the harness executed reachable code (not that it bailed early);
- expect a **visible per-op taint diagnostic** in stdout (`test.desc` regex asserts the `[APPROX]`/taint
  line fired at the construct's file:line) — proves reachability, not silent suppression;
- ship a paired **unreachable-unsupported negative control** (same unsupported op behind `if(false)`) whose
  `esbmc_expected` is **WRONG** (the real bug after it is reachable and the taint must NOT fire) — this is
  the anchor that catches a lazy "any-syntactic-unsupported ⇒ SUCCESS" implementation.

### Per-tier matrices

Tiers below: SLOC = reference contract + test contract logic (`sloc_logic`); stub surface tracked separately.

### T20 — atomic semantics (~20 SLOC), one concept per test

Each row is a **pass/fail pair** (+ a **taint** anchor where the concept has an unsupported sibling).

| # | Concept (Foundry-specific ★) | pass (CORRECT) | fail (WRONG) | taint (CORRECT despite wrong) |
|---|---|---|---|---|
| 01 | `assertEq` concrete | expected==actual | expected wrong | — |
| 02 | `assertTrue`/`assertFalse` | — | — | — |
| 03 | ★`vm.expectRevert()` on `require(false)` | reverts as expected | callee does NOT revert | ★`expectRevert(Err.selector)` selector-specific → taint |
| 04 | ★`testFail_*` convention | body reverts | body succeeds | — |
| 05 | ★`vm.prank(a)` access control | pranked owner passes | wrong-sender expectation | — |
| 06 | ★`vm.deal(a,v)` set balance | balance==v after | expects credit-add (v+init) | — |
| 07 | ★`vm.warp(t)` timestamp | ts==t | ts wrong | — |
| 08 | ★Foundry **default env** (no cheatcode): `assertEq(msg.sender, DEFAULT_SENDER)` | env pinned ⇒ pass | asserts wrong default | ← guards `_foundry_init_defaults` |
| 09 | ★unsupported `vm.mockCall` | — | test expects mocked return (wrong vs real) | **taint** (must be CORRECT) |
| 10 | native `assert` inside test | holds | violated | — |

T20 also includes **≥3 pure conservativeness anchors** (the most important tests): the test's
expectation is genuinely wrong, but the reason ESBMC can't see it (unmodeled cheatcode / unpinned env
outside defaults / arithmetic-overflow-revert not flag-marked) ⇒ `esbmc_expected=CORRECT`. If v1's
coarse gate cannot yet detect one of these, pin it **KNOWNBUG** (the failure to stay CORRECT is itself
the finding — never silently accept a false-WRONG).

### T50 — small realistic contract + `setUp` (~50 SLOC)

- Contracts: a Counter, an Escrow-lite (deposit/withdraw), an ERC20-snippet (reuse `regression/.../ERC20.sol`).
- Test file: `setUp()` establishes state, then 2–3 assertions + one cheatcode.
- New corners:
  - **setUp fixture ordering**: a `test_*` that *depends* on `setUp` state (must run setUp first) →
    pass; mutant with wrong post-setUp expectation → fail. (Guards F1.a linear harness vs the old
    nondet dispatcher that could skip setUp.)
  - ★**multiple asserts after first failure** (forge-std soft-flag *continues*): test with a failing
    `assertEq` #1 followed by `assertEq` #2 — the `_ESBMC_foundry_failed` model must yield WRONG and
    must not diverge from forge on which assertions execute. A pass-variant where #1 holds and #2 holds.
  - ★**`vm.startPrank/stopPrank` range** across two calls.
  - ★**`expectRevert` with custom error (no selector)** → pass; **with selector** → taint.

### T70 — multi-function + combined cheatcodes + first PUT (~70 SLOC)

- **Combined context**: `vm.prank` + `vm.deal` + `vm.warp` in one test flow; assert final state.
- ★**PUT / `testFuzz_*`** with `vm.assume` and `bound(x,lo,hi)`:
  - pass: property holds ∀ bounded inputs (ESBMC proves it — forge only samples).
  - fail: property violated for some bounded input ⇒ **WRONG** — headline case where ESBMC beats
    forge sampling. Include one where the violating input is *rare* (e.g. a single boundary value) so a
    forge fuzz run with default depth would likely miss it, but `forge_truth` is still recorded by
    seeding that value explicitly.
  - taint: fuzz test whose body also calls an unsupported cheatcode ⇒ CORRECT.
- ★**two-contract interaction under `--bound`**: test drives contract A which calls B; verify the
  deterministic-test-call vs `--bound` nondet-dispatch boundary (design plan F1.c / Codex #5). One pass,
  one WRONG mutant, one taint (A makes a low-level `.call` whose failure the test asserts on).

### T100+ — realistic module + full Foundry test file (~100–160 SLOC)

- One real-ish 1inch-style library/module (e.g. a MakerTraits-style bit-decoder, reuse from
  `notes/coverage-comparison/limit-order-protocol/`), plus a `*.t.sol` that:
  - **multiply-inherits** `Test` + a project helper base (stresses `linearizedBaseList` ctor order,
    Codex #4);
  - has several `test_*` fns (mixed CORRECT/WRONG), one ★`invariant_*` test (ctor→setUp→bounded
    handler sequence→invariant assert), one ★`testFuzz_*`, and one fn using ★`vm.mockCall` →
    per-function taint (verifies gate granularity: siblings still get real verdicts).
  - Expected: a *vector* of per-`--foundry-test` verdicts, not a single file verdict.

### T-semantics — additional Foundry-specific coverage (Codex #5, slot into the tier matching each item's vector)

Each gets a pass/fail (+taint where an unsupported sibling exists) triple:

- ★**`vm.expectRevert` with return-data / error-arg matching** — **the top missing boundary** (the
  primitive is only a boolean `most-recent-call-reverted`, `solidity_misc.c:208`): pass (revert with the
  expected custom error+args), fail (reverts with a *different* error → real Foundry FAILs, ESBMC must too
  IF data-matching is modeled; until then this whole family is **taint**, and the taint itself is the
  pinned expectation).
- ★**`vm.expectEmit` / `vm.expectCall`** — event/call expectations (unmodeled v1 → taint; pin as taint).
- ★**`vm.snapshotState` + `vm.revertToState`** — state rollback between assertions.
- ★**prank scoping across a revert** — `startPrank`; a call reverts; does the prank persist? (matches
  Foundry semantics).
- ★**reverting `setUp()`** — Foundry aborts all tests; harness must model setUp-abort.
- ★**constructor args supplied via `setUp`** (not literal) — harness ctor-arg plumbing.
- ★**`ffi`** — external process; always **taint** (unmodelable), pin as taint.
- ★**fuzz rejection ratio** (`vm.assume` rejecting most inputs) — ESBMC prunes symbolically (no ratio
  issue); pass where forge would struggle.
- ★**invariant handler selection / `targetContract`/`targetSelector`** wiring — invariant harness scope.
- ★**`assertEq` on arrays / `bytes` / `string`** and **`assertApproxEqRel`** — assertion-family breadth.

---

## 4. F2 (emit) benchmark — separate track, same tiers

**Target-first fix (Codex #3): don't defer F2 goldens to "after F1 metadata."** For each F2 dir,
**hand-author the expected `*.t.sol` repro NOW** and run `forge test` on it to confirm it reproduces the
bug — that hand-authored repro is the frozen oracle. Post-implementation, the generated output is
compared (after normalization: whitespace, ident renaming, comment stripping) against the frozen repro /
a structural checklist. This locks F2's target before the emitter exists.

Acceptance = **round-trip**: emitted `*.t.sol` (a) compiles under `solc`, (b) `forge test` FAILS on the
same property ESBMC flagged, (c) normalized-matches the frozen hand-authored repro. Encoded as a
two-step `test.desc` + a `roundtrip.sh` per dir.

- **T20-emit**: contract with a single assertion/overflow bug → emit a concrete `test_repro()` with the
  witness input; golden-match the literal + round-trip.
- **T50/T70-emit — value formatting corners** (one dir each, must format correctly as Solidity literals):
  `uint256` max / near-overflow, `int256` negative, `address` (checksum-agnostic `0x…`), `bytesN`,
  dynamic `bytes`/`string`, fixed array, struct arg, `bool`.
- **Unsupported-shape**: a violation reachable only through the nondet dispatcher (no reconstructable
  direct public call / ambient EVM state) → emitter must output an explicit `// UNSUPPORTED: <reason>`
  stub (assert the stub is emitted), **not** a wrong/uncompilable test (design plan F2.a, Codex #7).

---

## 5. Directory & desc conventions

```
regression/esbmc-solidity/foundry_<tier>_<feat>_<topic>_<pass|fail|taint>/
  contract.sol         # reference contract + Foundry test contract + inlined stub
  contract.solast      # solc --ast-compact-json output
  test.desc            # EXACT 4-line format below
  spec.md              # frozen: property + classification + mutant-diff + complexity vector
  (F2 dirs also:) roundtrip.sh, expected.t.sol   # hand-authored golden
_foundry/forge_std_min.sol, regen.sh, ORACLE.md
```

**Exact `test.desc` format (Codex #8 — matches `regression/testing_tool.py:55` and existing
`int_overflow_check/test.desc`), four fixed leading lines then regexes:**
```
CORE                              # line 1: mode (CORE | KNOWNBUG | THOROUGH | FUTURE)
contract.solast                   # line 2: input file
--sol contract.sol --foundry --contract <TestC> [--foundry-test <fn>] --unwind N ...   # line 3: args
^VERIFICATION (SUCCESSFUL|FAILED)$   # line 4+: one expected-output regex per line
```
Args baseline mirrors existing Solidity flags; add `--bound` only for the inter-contract tier;
overflow-revert tiers drop `--no-standard-checks`. `.solast` regeneration (`regen.sh`) is part of the
frozen target, re-run whenever `contract.sol` changes.

**Stub lowering mechanism must be specified before writing tests (Codex #7).** ESBMC today hijacks only
*exact free-identifier* calls (`__ESBMC_assume/__ESBMC_assert/__VERIFIER_*/__ESBMC_reverted`,
`solidity_convert_ref.cpp:486`); ordinary `vm.foo(...)` **member-access** calls are NOT on that path. The
benchmark's `_foundry/` ships two gating tests that must pass before any semantic test is trusted:
- a **compile-only AST-acceptance test**: the full `forge_std_min.sol` stub flattened into a trivial
  contract parses via `solc --ast-compact-json` AND ESBMC ingests the AST without crash;
- a **negative hijack test**: a user-defined contract named `Vm` with a method `assume` is NOT
  accidentally intercepted (proves cheatcode recognition is scoped to the real `vm` handle, not any
  same-named member).

Rules honored: every tier ships **≥1 pass + ≥1 fail**; conservativeness anchors are first-class; any
case ESBMC can't yet satisfy is pinned **KNOWNBUG** (never simplified away); `--foundry-test` is the
sanctioned per-test scoping (analogous to the lib-coverage `--function` exemption) — NOT a general
`--function` that would hide harness bugs.

---

## 6. Build order + anti-drift freeze (Codex #4)

**Loophole closed:** "all-KNOWNBUG-until-later, flip per passing case" lets the target drift (KNOWNBUG
only fails when the regex *unexpectedly matches*, `testing_tool.py:285`, so expected regexes/oracle could
be reshaped after seeing ESBMC output). Prevent this: **freeze the oracle in git before any F0–F2 code**.

1. Shared assets (§2) + the two `_foundry/` gating tests (§5). Record `forge_truth` (with pinned
   forge/solc versions + raw `forge test --json`) for every planned test.
2. **Freeze commit**: `ORACLE.md` (both columns), every `spec.md`, and every `test.desc` with its EXACT
   expected-output regex — committed *before* implementation. Any post-freeze change to a regex/oracle is
   a reviewed "oracle repair" commit, explicitly separate from implementation commits (so drift is
   visible in history).
3. T20 pass/fail/taint dirs (KNOWNBUG until F1 lands — but the *regex is already the final target*, not a
   placeholder). Then T50 → T70 → T100 → T-semantics.
4. F2 track dirs with **hand-authored `expected.t.sol` goldens frozen now** (§4) — not deferred.
5. As F0→F1→F2 land, flip KNOWNBUG→CORE per passing case **without touching the frozen regex**. A case
   that flips to a **false-WRONG** stays KNOWNBUG and is a release blocker (violates conservativeness).

## 7. Open questions for review
- Foundry default env values are now **verified** (see the design plan's `_foundry_init_defaults` table:
  `DEFAULT_SENDER 0x1804…1f38`, `DEFAULT_TEST_CONTRACT 0x5615…b72f`, `block.timestamp/number==1`,
  `chainid==31337`, gasprice/basefee/coinbase/difficulty `0`, initial_balance `2^96−1`). They ARE
  version-sensitive ⇒ the benchmark **pins a forge version** in `ORACLE.md` and records the raw
  `forge test --json`. Remaining sub-question: does ESBMC need to distinguish `msg.sender` in-test
  (0x1804…) vs callee-observed (0x5615…) — yes; the anchor tests must exercise both.
- Is per-`--foundry-test` taint granularity implementable, or does the flag re-run the whole file?
- SLOC accounting: include or exclude the inlined stub? (Plan: exclude — count only reference+test logic.)
- Do we need a `forge`-in-CI step for round-trip (F2) and oracle regen, or record oracles statically and
  keep CI ESBMC-only?
