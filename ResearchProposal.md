# ESBMC-Solidity vs Hardhat/Foundry — Branch Coverage Comparison

## Context

**Hypothesis under test.** A verifier-based branch-coverage measurement (ESBMC's `--branch-coverage*` family driven by `--k-induction`) is computed deterministically from program semantics: as k-induction grows the bound until inductive closure (or until the step budget is exhausted), every reachable branch under that bound is reported covered. A test-driven coverage measurement — Hardhat 2.x + `solidity-coverage` for Hardhat projects, `forge coverage` for Foundry projects — is computed from whatever the existing test corpus happens to execute; gaps in the test corpus appear as uncovered branches even when those branches are reachable and safe.

**Why k-induction, not `--unwind N`.** A fixed `--unwind N` silently caps the bound. Whatever value of N you pick, you cannot know whether N was large enough to reach every instrumented branch — so an "uncovered" claim could mean either (a) the branch is genuinely unreachable, or (b) you didn't unwind far enough. The reported rate becomes a lower bound of unknown tightness. k-induction sidesteps this by *dynamically growing k*, accumulating reached claims across all completed iterations and at multiple stop-points (`bmc.cpp:2464-2616`). The existing regression tests `regression/esbmc-solidity/sol_cov_{1,2}/test.desc` already follow this convention (`--k-induction --assertion-coverage`). **`--unwind N` and `--k-induction` are mutually exclusive — one silently overrides the other — and `--unwind` must never be used for coverage.**

**Semantic asymmetry of "uncovered".** This is the most important reading-key for the experiment. The two tools' "uncovered" sets mean different things:
- **ESBMC uncovered** — the verifier proved that arm of the conditional is unreachable from any input under the configured bound (dead code), OR the solver returned UNKNOWN. This is a *strong* statement about the program.
- **Baseline uncovered** (Hardhat `solidity-coverage` or `forge coverage`) — the existing test suite never executed that arm. The arm may still be reachable; the test corpus just didn't reach it. This is a *statement about the tests*, not the program.

The experiment's value comes from this asymmetry: `ESBMC_covered \ Baseline_covered` is the set of branches that are *demonstrably reachable* (the verifier exhibited an input that reaches them) but the test suite missed.

**Predicted relation.** For any eligible contract C, ESBMC's covered-branch set should *subsume* the baseline's covered-branch set:

```
covered_branches_baseline(C) ⊆ covered_branches_esbmc(C)
```

where `baseline ∈ {hardhat, forge}` depending on the project's test framework. When the subset relation holds, the difference `covered_branches_esbmc(C) \ covered_branches_baseline(C)` is exactly the set of branches the test suite missed — and is the experiment's primary deliverable.

**Fallback.** If a clean per-branch normalisation between the two tools is not tractable, the experiment falls back to per-contract numeric branch-coverage rates, which still substantiates the determinism claim but not the subsume claim.

**Why these 6 projects.** 1inch's published mainnet protocols — `limit-order-protocol`, `farming`, `st1inch`, `vested-token` (Hardhat), and `swap-vm` (release/1.1) + `aqua` (Foundry) — are realistic non-toy targets each shipping its own test corpus the per-framework coverage tool can run against. They cover diverse Solidity surface area (AMM, order matching, staking, vault, governance). `liquidity-protocol` was initially in scope but its `master` is locked at solc 0.6.12 and fails the strict ≥0.8 rule (see [Current state](#current-state-from-download--p1-p2-eligibility-checks)); it was replaced by `st1inch` and `vested-token` (both Hardhat, both solc 0.8.x).

**Two baselines per the project mix.** Four of the six projects ship a Hardhat test corpus and use `solidity-coverage` for branch coverage (Istanbul `coverage-final.json`). The other two — `swap-vm` and `aqua` — ship a Foundry test corpus and use `forge coverage` (LCOV `lcov.info`). The same set-level matching logic in S4 works for both; the differences are (a) which parser script is used to canonicalise the baseline output, and (b) the Istanbul branchMap arm-index slot is only available on the Hardhat side.

**What this experiment is NOT.** Not a bug-finding run. Not a soundness audit of ESBMC.

**ESBMC's actual Solidity model.** ESBMC models a Solidity contract under a harness function `_ESBMC_Main_<C>()` whose body is `while (nondet_bool()) { _ESBMC_Nondet_Extcall_<C>(); }`. The inner `_ESBMC_Nondet_Extcall_<C>()` is itself a nondet if-else chain that picks one externally-callable method per call. Together they realise a multi-call non-deterministic dispatcher: each iteration of the outer while-loop nondet-picks one user function (or none) and applies it to the shared contract state. State changes persist across iterations.

**Empirical evidence (verified with `esbmc --goto-functions-only` + run logs).** The dispatcher loop is preserved under `--branch-coverage*` in the installed ESBMC: the `GOTO 1` back-edge in `_ESBMC_Main_<C>()` remains intact, and the coverage rate changes as a function of the loop unwinding budget. Multi-call sequences are reached: a 2-function contract `setFlag(); step();` where `step()` has `require(flag)` correctly reports the inside-of-step branch as covered when the bound allows ≥2 iterations.

**Consequence for the subsume claim.** With `--k-induction`, the bound grows dynamically so any multi-call sequence reachable within `--max-k-step` iterations is explored. Branches that require call-sequence depth beyond `max-k-step` remain uncovered — but unlike `--unwind N`, that limit is reached *only after* exhausting smaller bounds, and the diagnostic shows exactly which step it reached. Subsume violations classify into (a) compound-condition shape (expected, harmless), (b) call-sequence depth > `max-k-step` (rare for normal contracts, identifiable by the run log's last completed k value), (c) genuine ESBMC frontend or instrumentation gap.

---

## Background — How `--branch-coverage` Works in ESBMC

Investigation found four mechanism details that shape the methodology. Each is cited so reviewers can verify.

1. **Implicit multi-property mode.** `--branch-coverage` and `--branch-coverage-claims` auto-enable `base-case=true, multi-property=true, keep-verified-claims=false, no-pointer-check=true` (`src/esbmc/esbmc_parseoptions.cpp:3490-3509`). k-induction is *not* auto-enabled, but the experiment adds `--k-induction` explicitly so the bound grows dynamically (see Context §2). When `--k-induction` is set alongside coverage, `do_bmc_strategy` (`esbmc_parseoptions.cpp:2434-2623`) iterates k=1, 1+step, ..., running base/forward/inductive at each step and accumulating reached claims; coverage write fires at 5 distinct stop-points so partial coverage survives even when the inductive step diverges.

2. **GOTO instrumentation.** `goto_coveraget::branch_coverage()` (`src/goto-programs/goto_coverage.cpp:234-296`) walks every conditional GOTO `it->is_goto() && !is_true(it->guard)` and inserts two assertions: `assert(it->guard)` and `assert(!it->guard)`. A claim "fires" (is marked covered) when symex finds a satisfying path violating the assertion — i.e., the corresponding arm is reachable. An "uncovered" claim either has no satisfying path (provably dead arm) or the solver returned UNKNOWN.

3. **`__ESBMC_HIDE` filter.** This is a *GOTO-instruction label* (not a function name prefix). Detection at `src/goto-programs/goto_convert_functions.cpp:39-51` sets `f.body.hide = true`; the coverage pass skips such functions at `src/goto-programs/goto_coverage.cpp:1202-1217` — but only for Solidity and Python. The Solidity frontend marks synthetic helpers as hidden: auxiliary constructors, polymorphic dispatchers, builtin wrappers (assert/require/revert), mapping handlers, state-init helpers, type conversions. **User-source contracts are NOT hidden**, so the experiment's coverage numbers reflect user code only — synthetic conditionals from these helpers don't pollute the count.

4. **Per-claim live stdout (authoritative coverage source).** `report_multi_property_trace()` (`bmc.cpp:478, 2180`) prints the verdict for **each claim as it finishes** — `Claim 'X' holds up to the current K` when UNSAT (the arm is provably unreachable → ESBMC marks "uncovered"), `[Counterexample]` when SAT (the arm is reachable → "covered"), `Claim 'X' could not be solved` when UNKNOWN/error. `report_coverage_verbose()` (`bmc.cpp:1096, 2169`) additionally prints running coverage percentage and per-claim summaries when `*-coverage-claims` is set. Critically, these prints happen **inside the per-claim job loop** before any process-wide finalisation. Even on external SIGKILL / SIGTERM / internal SIGALRM mid-run, the captured stdout up to the kill point contains a verdict line for every claim already solved.

5. **JSON write trigger.** `report_coverage()` (`bmc.cpp:980-1074, called at 2247`) writes `cov-report.json` at the end of `multi_property_check()`, after `parallel_jobs.join()` / `std::for_each` completes. For plain BMC (the experiment's mode), the trigger is unconditional on end-of-run — but if the process is killed before that point, the JSON is absent. **stdout (run.log) is therefore the authoritative coverage record; cov-report.json is a convenience.** The plan parses claim verdicts out of run.log and only consults cov-report.json when available.

---

## Eligibility — Reproducible Standards

### Project eligibility (drop if false)

| # | Criterion | Source of truth |
|---|-----------|-----------------|
| P1 | All `pragma solidity` lines in source contracts parse as `>=0.8.0` or `^0.8.x` | `grep -E '^pragma solidity'` across `contracts/**/*.sol` (Hardhat) or `src/**/*.sol` (Foundry) |
| P2 | Project ships **either** `hardhat.config.{js,ts}` configured for solc `0.8.x` **or** `foundry.toml` configured for solc `0.8.x` | Read config |
| P3 | Compile succeeds: Hardhat ⇒ `npm ci` + `npx hardhat compile`; Foundry ⇒ `forge build` | Run |
| P4 | Coverage tool produces an artifact with at least one source-file record: Hardhat ⇒ `npx hardhat coverage` writes `coverage/coverage-final.json` (primary) and `coverage/lcov.info`; Foundry ⇒ `forge coverage --report lcov` writes `lcov.info` | Run |

A project failing any of P1–P4 is **dropped without rewriting source**, per the user's "strict ≥0.8.0" rule.

<a id="current-state-from-download--p1-p2-eligibility-checks"></a>**Current state (from download + P1/P2 eligibility checks, 2026-05-13):**

| Project | Branch | Framework | solc | P1 | P2 | Verdict |
|---------|--------|-----------|------|----|----|---------|
| `limit-order-protocol` | master | Hardhat 2.x | 0.8.30 + `^0.8.0` | ✓ | ✓ | **eligible** (54 .sol) |
| `farming` | master | Hardhat | `^0.8.0` | ✓ | ✓ | **eligible** (12 .sol) |
| `st1inch` | main | Hardhat | 0.8.23 + `^0.8.0` | ✓ | ✓ | **eligible** (6 .sol) |
| `vested-token` | master | Hardhat | 0.8.19 | ✓ | ✓ | **eligible** (6 .sol) |
| `swap-vm` | release/1.1 | Foundry | 0.8.30 + `^0.8.0` | ✓ | ✓ | **eligible** (40 .sol) |
| `aqua` | main | Foundry | 0.8.30 + `^0.8.0` | ✓ | ✓ | **eligible** (5 .sol) |
| ~~`liquidity-protocol`~~ | ~~master~~ | ~~Hardhat~~ | ~~`^0.6.0`/`^0.6.12`~~ | ✗ | — | **dropped** (P1 fail on master; archived upstream) |

Sources are extracted into `notes/coverage-comparison/<proj>/src/`; tarballs cached in `notes/coverage-comparison/_tarballs/` (3.5M total + 0.4M for the two replacement repos).

### Contract eligibility (the "meaningful contract" set)

Computed *before either tool runs*, from the solc AST so the filter is identical for both sides. A `ContractDefinition` node `C` in source file `S` is **verifiable** iff ALL of:

| # | Criterion | AST field |
|---|-----------|-----------|
| C1 | `C.contractKind == "contract"` (excludes `library`, `interface`) | `contractKind` |
| C2 | `C.abstract == false` (excludes `abstract contract` declarations) | `abstract` |
| C3 | `C` has at least one `FunctionDefinition` node with `visibility ∈ {public, external}` and not a constructor-only body | walk `C.nodes` |
| C4 | `S` is not under any of these path components: `test/`, `tests/`, `mock/`, `mocks/`, `helper/`, `helpers/`, `interface/`, `interfaces/`, `node_modules/` | Path |

C1+C2 mirror what ESBMC's Solidity frontend already does at `src/solidity-frontend/solidity_convert.cpp:889-895` (the `nonContractNamesList` builder), so the filter agrees with the verifier's own notion of "verifiable". C3 excludes contracts whose only entry point is internal — neither tool can exercise them externally. C4 excludes mocks/test helpers; "mock" is not a Solidity keyword so we use the project's directory convention.

**Implementation.** A single Python script `notes/coverage-comparison/scripts/list_eligible_contracts.py` consumes `solc --ast-compact-json --include-path node_modules --base-path .` output and emits per-project `eligible.json`:
```json
[{"name": "AggregationRouterV6", "file": "contracts/AggregationRouterV6.sol", "ast_id": 123}, ...]
```
Both baseline (Hardhat / Foundry) and ESBMC outputs are filtered through this list before comparison.

---

## Phase Plan

### S0 — Eligibility tooling (1 file, no source changes)

- Add `notes/coverage-comparison/scripts/list_eligible_contracts.py` implementing C1–C4 over a project's `solc --ast-compact-json` output.
- Add `notes/coverage-comparison/scripts/hardhat_cov_to_branchset.py` — **primary input is `coverage/coverage-final.json`** (Istanbul JSON), consume each file's `branchMap` (entries shape `{type, line, loc, locations: [arm0, arm1, ...]}`) joined with the `b` map (branch-id → per-arm hit counts). Output: list of branch records `{file, line, start_col, end_col, branch_type, arm_index, covered: bool}`. LCOV `lcov.info` is retained only as a sanity-check sidecar — `BRDA:<line>,<block>,<id>,<hits>` lacks source span / branch-type / arm semantics and is unsuitable as primary branch identity.
- Add `notes/coverage-comparison/scripts/esbmc_cov_to_branchset.py` — parse ESBMC's `cov-report.json` (schema at `src/esbmc/bmc.cpp:980-1074`). Output: list of claim records `{file, line, column, condition_expr, function, covered: bool}`. Note: ESBMC Solidity always reports column 0 (`src/solidity-frontend/solidity_convert_util.cpp:27-79`), so column-level intersection with Istanbul spans is not possible — match is line-level plus claim-count parity.
- Add `notes/coverage-comparison/scripts/compare.py` — see S4 for normalisation rules.

### S1 — Per-project bootstrap (6 projects)

The 6 projects already on disk (see [Current state](#current-state-from-download--p1-p2-eligibility-checks)) are:

| Project | Framework | Bootstrap |
|---------|-----------|-----------|
| `limit-order-protocol` | Hardhat | `npm ci && npx hardhat compile` |
| `farming` | Hardhat | `npm ci && npx hardhat compile` |
| `st1inch` | Hardhat | `npm ci && npx hardhat compile` |
| `vested-token` | Hardhat | `npm ci && npx hardhat compile` |
| `swap-vm` | Foundry | `forge install && forge build` |
| `aqua` | Foundry | `forge install && forge build` |

For each:
1. Verify P1–P4 (P1/P2 already passed; P3/P4 verified at bootstrap time).
2. Record verdict in `notes/coverage-comparison/<proj>/eligibility.md`.
3. Run framework-appropriate bootstrap (table above).
4. Run `list_eligible_contracts.py` → `notes/coverage-comparison/<proj>/eligible.json`. The C1–C4 filter is identical for both frameworks; the only difference is the input AST source path (`solc --ast-compact-json` for Hardhat, `forge build --build-info` exposes solc AST for Foundry).

### S2 — Baseline coverage run (per-framework)

**S2a — Hardhat projects** (`limit-order-protocol`, `farming`, `st1inch`, `vested-token`):
1. Add `solidity-coverage` as devDep if not already pinned (`limit-order-protocol` is `0.8.13`; the others verified at bootstrap).
2. Run `npx hardhat coverage` (external `timeout 1200` is fine — solidity-coverage flushes its own report periodically). Capture **`coverage/coverage-final.json` (primary)** and `coverage/lcov.info` (sanity sidecar).
3. `hardhat_cov_to_branchset.py coverage/coverage-final.json` → `notes/coverage-comparison/<proj>/baseline-branches.json`.

**S2b — Foundry projects** (`swap-vm`, `aqua`):
1. Run `forge coverage --report lcov --report summary` (external `timeout 1200`). Capture `lcov.info` (primary baseline artifact for Foundry — `forge coverage` does NOT emit an Istanbul branchMap JSON, so the parser is LCOV-only on this side) plus the console `summary` for sanity.
2. `forge_cov_to_branchset.py lcov.info` → `notes/coverage-comparison/<proj>/baseline-branches.json`. The LCOV `BRDA:<line>,<block>,<branch>,<hits>` record gives `(file, line, covered: hits > 0)` — sufficient for the line-level set match in S4 but insufficient for the per-arm tertiary match. Per-arm comparison is therefore Hardhat-only.

Both branches emit a uniform `baseline-branches.json` schema:
```json
[{"file": "contracts/...", "line": 42, "covered": true, "framework": "hardhat" | "forge",
  "branch_type": "if" | "require" | "binary-expr" | "switch" | ... | null,  /* null for forge */
  "start_col": 12, "end_col": 28, "arm_index": 0   /* null on forge side */}, ...]
```

### S3 — ESBMC coverage run

For each eligible contract `C` in each eligible project:
1. Generate `.solast`: `solc --ast-compact-json --include-path node_modules --base-path . <S>` → redirect stdout.
2. Pick solver per CLAUDE.md guidance: default `--bitwuzla`; switch to `--cvc5` if the contract uses `uint256` arithmetic-heavy patterns. Record per-contract solver choice.
3. **Bound strategy: `--k-induction` only — NEVER `--unwind N`.** Per the verified ESBMC rule, fixed `--unwind` silently caps the bound to a guess and makes the "uncovered" set undecipherable. k-induction grows k dynamically across `do_bmc_strategy` iterations and accumulates reached claims, so the coverage report tracks "reached at any k ≤ k_max" — a sound upper bound on the unreachable set. The existing `regression/esbmc-solidity/sol_cov_{1,2}/test.desc` use this combination.
4. **Timeout strategy: external `timeout 1200` is fine.** Per-claim verdicts are flushed to stdout live (Background §4) — even on SIGKILL the partial coverage is recoverable from `run.log`. `cov-report.json` may be missing on killed runs, but that's a convenience loss, not data loss. k-induction is expected to time out on many real-world contracts (the inductive step rarely closes for dispatcher-loop harnesses, per memory `reference_k_induction_budget_burn`); that's not a failure — the cumulative coverage from completed k iterations is the deliverable.
5. Command shape:
   ```
   cd notes/coverage-comparison/<proj>/esbmc/<C>/ && \
     timeout 1200 esbmc <abs-path-to-S>.solast --sol <abs-path-to-S> \
       --contract <C> --no-standard-check \
       --branch-coverage-claims --cov-report-json \
       --k-induction --bitwuzla \
       2>&1 | tee run.log
   ```
   `cov-report.json` is written to CWD; the `cd` puts it in the per-contract output dir. `run.log` is the authoritative coverage record. `--sol <abs-path>` is required so the `.sol` source is treated as the Solidity input alongside the `.solast` AST (matches the `sol_cov_*` test pattern).
6. Note ESBMC's behaviour under coverage mode: empirical testing confirms the `_ESBMC_Main_*` dispatcher loop **is NOT collapsed** in coverage mode in the installed ESBMC build — the `GOTO 1` back-edge in `_ESBMC_Main_<C>()` is preserved, and k-induction's growing k value drives more dispatcher iterations as it scales. Multi-call sequences (`setFlag(); step();`) become reachable as k grows; with k-induction we don't need to know in advance which call sequence depth is required.
7. **Coverage extraction from run.log.** Parse `run.log` for per-claim verdict lines:
   - `Claim 'EXPR' holds up to the current K` → claim *uncovered* (arm proved unreachable under bound).
   - `[Counterexample]` blocks → most recent in-flight claim is *covered* (arm reachable).
   - `Claim 'EXPR' could not be solved` → claim is *UNKNOWN*.
   When the run terminated naturally and `cov-report.json` exists, cross-check that the JSON `covered`/`uncovered` partition matches the run.log reconstruction; any mismatch is a bug to investigate. When the run was killed and `cov-report.json` is absent, the run.log reconstruction is the sole source.
8. `esbmc_cov_to_branchset.py` consumes BOTH `run.log` (primary, always parsed) and `cov-report.json` (if present, cross-check) → `notes/coverage-comparison/<proj>/esbmc-branches.json` (with `covered`/`uncovered`/`unknown` per claim, and a flag indicating whether the run was killed before natural termination).

### S4 — Normalisation and comparison

The two sides instrument *different* sets of program points and use different branch identities. Naïve numeric comparison (`B_rate` vs `E_rate`) is **denominator-incommensurable** — Istanbul's branchMap classifies `require`, `assert`, `&&`, `||`, ternary, modifier, try/catch, `unchecked`, fallback/receive each by its own policy; LCOV BRDA records simply count branch-id slots; and ESBMC instruments at the GOTO conditional level (one claim pair per conditional). The plan treats numeric rates as *descriptive* per tool only and uses set-level matching for the subsume claim.

**Known systematic divergences (from Background §3 and the frontend-fidelity investigation):**
- *Simple if/require/ternary*: 1 GOTO conditional in ESBMC → 2 claims (true-arm + false-arm). Istanbul: 1 branch entry → 2 arms. LCOV: 2 BRDA slots. **Aligned across all three.**
- *Compound conditions* (`if (a && b)`, `if (a || b)`): ESBMC's frontend emits a single `and`/`or` irep2 → ONE composite conditional → 2 claims. Istanbul: 2 `binary-expr` branches (4 arms) + 1 `if` branch (2 arms) = 6 slots. LCOV BRDA: typically 2 slots only (it tracks GOTO-block branches like ESBMC, not source-level short-circuit arms). **Hardhat over-counts vs ESBMC; Foundry aligned with ESBMC.**
- *Solidity 0.8+ implicit overflow checks*: handled by SMT bitvector semantics, not GOTO conditionals — none of the three sides emits a branch. **Aligned.**
- *Synthetic helpers* (constructors, dispatchers, builtin wrappers, mapping handlers): hidden via `__ESBMC_HIDE` label → no claims emitted. Istanbul/LCOV also don't see them (they live in C library models, not Solidity source). **Aligned by virtue of all sides ignoring.**

**Branch identity, per side:**
- *Hardhat (Istanbul).* `(file, branch_type, line, start_col, end_col, arm_index)`. `arm_index` runs over `branchMap[id].locations[]`.
- *Foundry (LCOV).* `(file, line, branch_id, arm_id, covered)`. No source span, no branch type, no per-arm semantic info.
- *ESBMC.* `(file, line, condition_expr, status)`. Column is always 0 for Solidity output; true/false arm encoded in `condition_expr` (e.g., `!(P)` denotes the false arm of `P`).

**Matching strategy (in order):**

1. **Line-level set match (primary metric, works for both baselines).** For each side, build `Lines_covered = { (file, line) : ≥1 branch/claim covered }`. Compute:
   - `|Baseline_lines \ ESBMC_lines|` — "baseline hit, ESBMC missed". Three sub-classifications, in this priority order: (a) **compound-condition shape** — the line is inside `&&`/`||`/ternary where ESBMC's single composite GOTO claim undercounts Istanbul's per-arm slots (this bucket is empty for Foundry projects since LCOV doesn't oversample compound conditions); (b) **k-induction budget exhausted** — the run hit `--max-k-step` (or timeout) before the call-sequence depth required for this line; cross-check `run.log` for the last completed `k=` line; (c) genuine ESBMC frontend or instrumentation gap (the smallest, most interesting bucket — these are the findings that justify follow-up work).
   - `|ESBMC_lines \ Baseline_lines|` — "ESBMC reached, tests missed". Subdivide into: (i) lines where ESBMC's `condition_expr` shows a Solidity branch the baseline AST also marks → real test gap; (ii) lines that don't correspond to any baseline-side entry → ESBMC instrumented a non-source-level branch (rare, given the fidelity finding). (i) is the headline finding.
2. **Per-line count parity (secondary, both baselines).** For lines where both sides report N branches/claims, check `covered_count_baseline[line] ≤ covered_count_esbmc[line]`. **Per-line parity is expected to fail on `&&`/`||`/ternary lines for Hardhat** (Istanbul reports more slots than ESBMC has claims). Skip those lines in the parity test for Hardhat projects; LCOV-side parity is expected to hold.
3. **Per-arm match (best-effort tertiary, Hardhat only).** For lines where Istanbul reports exactly 2 arms (a simple `if`/`require`/ternary) and ESBMC reports 2 claims whose `condition_expr` differ by negation, attempt `(arm0 ↔ positive-claim, arm1 ↔ negated-claim)` mapping. Skipped for Foundry projects (LCOV lacks per-arm semantic labels).

**Numeric headlines (descriptive only).** Report side-by-side as:
- *Baseline-reported branch coverage rate* — `covered_branches / total_branches` per the framework's own accounting (Istanbul's branchMap for Hardhat; LCOV's BRDA total for Foundry).
- *ESBMC-reported branch-claim coverage rate* — `covered_claims / total_claims` per ESBMC's `--branch-coverage-claims` instrumentation. Annotate with `% UNKNOWN` separately so readers can see how much of the "uncovered" set is verifier-couldn't-decide rather than verifier-proved-dead.

These rates are **internally valid within each tool but not denominator-equivalent across tools**. The report must use the phrasing "Baseline-reported X / ESBMC-reported Y" and avoid "ESBMC achieves X% higher branch coverage than Hardhat/Foundry".

**Outputs:**
- `notes/coverage-comparison/results/per-contract.csv` — columns: `project, framework, contract, baseline_rate, esbmc_rate, esbmc_unknown_pct, lines_B, lines_E, lines_B_minus_E, lines_E_minus_B, perline_parity_violations_excluding_compound, last_completed_k, solver, esbmc_runtime, baseline_runtime, esbmc_terminated_normally`.
- `notes/coverage-comparison/results/aggregate.md` — table per project, then overall.
- `notes/coverage-comparison/results/diff-examples/<proj>/<C>.md` — for each contract:
  - **Headline finding**: top-N lines in `lines_E_minus_H` with source context (test gap).
  - **Subsume diagnostic**: every line in `lines_H_minus_E`, classified as compound-condition / bound-too-small / genuine-gap, with the source line and both tools' raw records.

### S5 — Final report (`ResearchProposal.md` updated, or new doc)

Sections:
1. **Setup** — project list (6 projects: 4 Hardhat + 2 Foundry), eligibility filter (verbatim from this plan), tool versions, runtime environment.
2. **Per-project results** — table with baseline_rate, esbmc_rate, and the two diff cardinalities per contract, grouped by framework so Hardhat and Foundry numbers are visually separated.
3. **Subsume-claim verdict** — does `Baseline ⊆ ESBMC` hold across all eligible contracts? Report the verdict per framework (Hardhat-side and Foundry-side separately, since the Hardhat compound-condition over-count means the Hardhat side is the harder direction). When it doesn't, what causes the leak (k-induction not closed, bound too small, frontend gap)?
4. **Determinism evidence** — run ESBMC coverage twice on a sample of contracts; verify identical output.
5. **Threats to validity** — bound choice, harness-loop neutralisation, source-line matching heuristic, solver UNKNOWN treated as uncovered, per-framework branchset asymmetry (Istanbul branchMap vs LCOV BRDA).

---

## Critical Files

**To consult (read-only):**
- `src/esbmc/options.cpp:752-819` — coverage flag definitions
- `src/esbmc/esbmc_parseoptions.cpp:3490-3509` — `--branch-coverage` auto-enables `base-case=true, multi-property=true, keep-verified-claims=false, no-pointer-check=true`
- `src/esbmc/esbmc_parseoptions.cpp:114-120, 520` — SIGALRM handler that `_exit(1)`s without flushing coverage (why we don't use `--timeout` or external `timeout`)
- `src/esbmc/esbmc_parseoptions.cpp:3173-3204` — coverage-mode harness-loop neutralisation
- `src/esbmc/bmc.cpp:980-1074` — `cov-report.json` schema and writer
- `src/esbmc/bmc.cpp:2152-2165` — UNKNOWN-claim accounting (UNKNOWN → "uncovered" in JSON)
- `src/esbmc/bmc.cpp:2247` — plain-BMC unconditional coverage-write hook
- `src/esbmc/bmc.cpp:603-671` — `prettify_solidity_expr` (output cleaner)
- `src/esbmc/bmc.cpp:674-712` — `parse_claim_location` (file:line extractor)
- `src/goto-programs/goto_coverage.cpp:234-296` — `branch_coverage()` instrumentation (insert assert(P) + assert(!P) per conditional)
- `src/goto-programs/goto_coverage.cpp:1202-1217` — `filter()` skips functions with `body.hide == true` (Solidity + Python only)
- `src/goto-programs/goto_convert_functions.cpp:39-51` — `__ESBMC_HIDE` label detection that sets `body.hide = true`
- `src/solidity-frontend/solidity_convert.cpp:885-895` — `nonContractNamesList` filter (our C1+C2 mirror)
- `src/solidity-frontend/solidity_convert_stmt.cpp:780-813` — if-statement lowering (1:1 with source)
- `src/solidity-frontend/solidity_convert_expr.cpp:2211-2236, 5573-5582, 6232-6258` — require/&&-||/ternary lowering (1:1 at frontend; compound-cond divergence emerges in goto_convert, not here)
- `src/solidity-frontend/solidity_convert_util.cpp:27-79` — Solidity source location tracking (column always 0)
- `scripts/cov-report.py` — existing HTML coverage report generator (we *consume* its JSON sibling rather than re-implement)
- `docs/claude/solidity/coverage.md` — Solidity-specific coverage behavior
- `regression/esbmc-solidity/sol_cov_{1,2}/test.desc` — existing Solidity coverage tests (sanity-check exemplars)

**To create (new files only — no edits to ESBMC source):**
- `notes/coverage-comparison/scripts/list_eligible_contracts.py`
- `notes/coverage-comparison/scripts/hardhat_cov_to_branchset.py` (consumes Istanbul `coverage-final.json`)
- `notes/coverage-comparison/scripts/forge_cov_to_branchset.py` (consumes `forge coverage --report lcov` output `lcov.info`)
- `notes/coverage-comparison/scripts/esbmc_cov_to_branchset.py`
- `notes/coverage-comparison/scripts/compare.py`
- `notes/coverage-comparison/scripts/run_project.sh` — per-framework bootstrap + coverage driver for one project (dispatches on Hardhat vs Foundry)
- `notes/coverage-comparison/<proj>/eligibility.md` — per-project eligibility verdict + log
- `notes/coverage-comparison/<proj>/eligible.json` — output of S0 script
- `notes/coverage-comparison/<proj>/baseline-branches.json` — output of S2 (unified Hardhat / Foundry schema)
- `notes/coverage-comparison/<proj>/esbmc/<C>/cov-report.json` — raw ESBMC output
- `notes/coverage-comparison/<proj>/esbmc-branches.json` — aggregated ESBMC output
- `notes/coverage-comparison/results/per-contract.csv`
- `notes/coverage-comparison/results/aggregate.md`
- `notes/coverage-comparison/results/diff-examples/<proj>/<C>.md`

**Currently on disk (post-download):**
- `notes/coverage-comparison/_tarballs/` — cached source tarballs (3.9M total)
- `notes/coverage-comparison/{limit-order-protocol,farming,st1inch,vested-token,swap-vm,aqua}/src/` — extracted sources

---

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| <0.8 pragma blocks 3-4 of 5 projects | High | Strict ≥0.8 rule per user — drop and document the drop; do not rewrite source. |
| ESBMC crashes/UNKNOWNs on a real-world contract | Medium | Per-contract `timeout 600`, log all failures, treat UNKNOWN claims as uncovered for the baseline_rate vs esbmc_rate calc but flag separately in the report. |
| Hardhat `coverage` plugin needs old node version | Low–Medium | Use `nvm use 18` (LTS) and document the node version pinned. |
| `forge coverage` requires recent Foundry; IR-pipeline contracts may need `--ir-minimum` | Medium | Foundry 0.2.0+ supports `forge coverage`; for `via_ir = true` contracts (some 1inch swap-vm modules) the run needs `forge coverage --ir-minimum`. Document the exact `forge --version` and flags used per project. |
| Per-framework branchset asymmetry (Istanbul 6 slots for `a && b` vs LCOV 2 slots) | High (mitigated by plan) | S4 splits the compound-condition bucket to fire only on Hardhat projects. Foundry-side per-line parity is expected to hold strictly; Hardhat-side parity is checked only on non-compound lines. Both subsume verdicts are reported separately in S5. |
| ESBMC's coverage harness diverges from the baseline's per-test coverage in unexpected ways | Medium | Documented as a threat-to-validity in S5 §5. Empirically, the dispatcher loop is preserved under k-induction so multi-call sequences are reachable; remaining divergence comes from k-budget vs test-sequence-length, not from a hard-coded single-call cap. |
| `--k-induction` step budget exhausted before all reachable branches are found | Medium | Per memory `feedback_coverage_must_use_kinduction`, k-induction is the only legitimate bound strategy for coverage. Default `--max-k-step` is generous; rely on external `timeout 1200` to cap wallclock. Run log records the last completed k value; subsume violations beyond that boundary are classified as "k-budget exhausted" not "genuine gap". `--unwind` is forbidden — it makes the uncovered set semantically meaningless. |
| Eligibility filter C3 (has public/external fn) drops too many contracts | Low | Inspect S0 output for sanity; loosen to "has any FunctionDefinition" if ESBMC's contract list disagrees. |
| Hardhat coverage / `forge coverage` runs flake (test timeouts) | Low–Medium | Re-run up to 2× on transient failures; record any contract that requires re-run. |
| Numeric `B_rate` vs `E_rate` interpreted as direct comparison | High (if not framed carefully) | Denominators are non-equivalent (see S4). Rates are descriptive-per-tool only; subsume verdict comes from set-level line-matching + per-line count parity (excluding compound conditions for Hardhat projects), never from `E_rate − B_rate`. |
| `cov-report.json` may be absent on killed/timed-out runs | Low (no data loss) | Per-claim verdicts are flushed to stdout live via `report_multi_property_trace` / `report_coverage_verbose` (`bmc.cpp:478, 1096`). Parse `run.log` as the primary coverage record; treat `cov-report.json` as a convenience cross-check. External `timeout 1200` is therefore safe — partial coverage is recoverable up to the kill point. |
| UNKNOWN solver claims indistinguishable from dead-code claims in `cov-report.json` (both have `status: "uncovered"`) | Medium | The `run.log` parser uses three verdict shapes (`holds up to current K` / `[Counterexample]` / `could not be solved`), preserving the UNKNOWN distinction the JSON drops. The CSV exposes `esbmc_unknown_pct` so readers see how much of the "uncovered" set is verifier-couldn't-decide. Subsume violations on UNKNOWN lines are classified as "bound-or-solver-unknown", not "frontend gap". |
| Multi-call sequences too deep for the k budget | Medium | Empirical: dispatcher loop is preserved under `--branch-coverage-claims --k-induction` (the back-edge is NOT collapsed). k-induction grows k dynamically, so most multi-call sequences are reached. Only sequences requiring more iterations than `max-k-step` permits before timeout will be missed. Cross-check with run.log's last completed k value when subsume violations cluster. |
| Compound conditions (`&&`/`||`/ternary) systematically diverge: 6 Istanbul slots vs 2 ESBMC claims for `if (a && b)` | High (mitigated by plan) | Documented in S4 as expected divergence. Compound-condition lines are quarantined from per-line count parity. Spot-check: hand-classify 3 contracts' compound-condition lines, confirm ESBMC reports the expected 2 claims while Istanbul reports the expected 6 slots, then exclude those lines from the parity test. Subsume claim falls back to "the source-line set of a covered compound condition appears in both tools" — `Hardhat_lines ⊆ ESBMC_lines` should still hold for the composite branch even when slot-level counts diverge. |
| Forgetting `--k-induction` in a coverage command silently falls back to base-case BMC, undercounting | High | All ESBMC invocations in S3 include `--k-induction` explicitly. Add a CI-style assertion to `run_project.sh` that greps the command line for `--k-induction` before launching. |
| Solidity frontend lowers `a && b` to a single irep2 `and` — symex may explore both arms as one path-split, inflating effective branch count internally; but coverage instrumentation runs on the GOTO program before symex, so only the composite conditional is instrumented | Low | The divergence shows up exactly where S4 documents it (compound-condition row above). No additional mitigation needed — flag for the spot-check. |

---

## Verification

End-to-end correctness checks before declaring the experiment done:

1. **Schema check.** `hardhat_cov_to_branchset.py`, `forge_cov_to_branchset.py`, and `esbmc_cov_to_branchset.py` all produce JSON validating against the shared `baseline-branches.json` schema (Hardhat populates `branch_type` / `start_col` / `end_col` / `arm_index`; forge leaves them `null`). Add an assertion script that ingests (a) `regression/esbmc-solidity/sol_cov_1/`'s ESBMC output, (b) a hand-crafted Istanbul `coverage-final.json` stub (one branchMap entry with 2 arms, one covered), and (c) a hand-crafted `lcov.info` stub with one BRDA record, asserts all three shapes parse cleanly into the unified schema.
2. **Determinism check.** Pick one mid-size eligible contract (e.g. a `limit-order-protocol` router). Run ESBMC coverage twice. Assert byte-identical `cov-report.json` (modulo absolute paths and timestamps).
3. **`__ESBMC_HIDE` filter verification.** Run ESBMC with `--branch-coverage-claims --goto-functions-only` on a `regression/esbmc-solidity/sol_cov_*` test. Confirm `cov-report.json` contains NO claims from synthetic helpers (constructor wrappers, dispatchers, `_ESBMC_*` library functions). If user-source contract claims and synthetic claims both appear, the hide-set assumption is wrong and S4 numbers will be polluted — must root-cause before scaling.
4. **Frontend fidelity spot-check.** Write a 3-contract micro-suite under `notes/coverage-comparison/sanity/`:
   - `simple_if.sol`: `if (P) S` — expect 1 GOTO conditional, 2 claims, Istanbul 2 slots → parity.
   - `compound_if.sol`: `if (a && b) S` — expect 1 GOTO conditional (single composite guard), 2 claims, Istanbul 6 slots → ESBMC undercounts by design.
   - `require_chain.sol`: 3 `require()` calls — expect 3 GOTO conditionals, 6 claims, Istanbul 6 slots → parity.
   Dump GOTO with `--goto-functions-only`, count conditionals, count claims in `cov-report.json`, compare with Istanbul's `coverage-final.json` from a minimal Hardhat test. Confirms the S4 divergence model.
5. **Coverage-write-on-non-clean-state check.** Reproduce the four output behaviours empirically before relying on them in S3 and S4:
   - *Plain BMC, all-pass*: confirm `cov-report.json` written + every claim in run.log with `holds up to current K` or `[Counterexample]`.
   - *k-induction + UNKNOWN claim*: contract with deep arithmetic the solver can't resolve before timeout. Confirm `run.log` contains a `could not be solved` line for the affected claim and `cov-report.json` lists it as `uncovered`. Confirm the run.log parser tags it as UNKNOWN (not uncovered).
   - *External SIGKILL mid-run*: send `kill -9` partway through; confirm `cov-report.json` is absent (or stale/incomplete) BUT `run.log` still contains per-claim verdicts for every claim solved before the kill. Validates the run.log-primary design.
   - *Multi-call subsume cross-check*: write a 2-function contract `function setOpen(bool v) { open = v; }` + `function step() { require(open); state++; }`. Run ESBMC with `--branch-coverage-claims --k-induction` and confirm the inside of `step()` after the require is *covered* at some k ≥ 2 (validating that the dispatcher loop is preserved under k-induction). Also run with `--unwind 1` (NEGATIVE control — should NOT be used in the experiment but tested here to demonstrate why) and confirm the same branch is uncovered, illustrating the rule "never `--unwind` for coverage".
6. **Sample comparison.** Run S2–S4 end-to-end on just `limit-order-protocol` first; sanity-check the comparison output (including subsume-violation classification) before scaling to other projects.
7. **No ESBMC source changes.** Confirm by `git diff -- src/ scripts/` showing zero lines changed (all work lives under `notes/coverage-comparison/`).

---

## Out of Scope (explicit non-goals)

- Bug-finding on the 1inch contracts — that's a separate research question already tracked in `project_liquidity_protocol_scan` for liquidity-protocol; this experiment is purely about coverage measurement.
- Improving ESBMC's branch-coverage instrumentation — we *consume* what exists; any gap is documented as a threat to validity, not patched here.
- Upgrading <0.8 projects to 0.8 — strict-eligibility rule rejects them; documented as the explicit reason.
- Hardhat 3 alpha — not selected by the user.
- New Solidity coverage regression tests in `regression/esbmc-solidity/` — the seed `cover_*` tests (`cover_mapping_1`, `cover_array_1`, `cover_builtin_1`, `cover_require_1`, `cover_break_1`, `cover_continue_1`) landed 2026-05-13 as the no-phantom-branch sanity gate; further regression authoring is out of scope for *this* experiment.
