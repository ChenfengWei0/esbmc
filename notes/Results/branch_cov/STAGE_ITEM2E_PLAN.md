# Item 2e — incremental covered-set persistence: plan (PLAN ONLY, no code)

User authorised "进行" 2026-05-17 after the cross-run cost measurement.
Following the Item-2 rhythm: investigate empirically → plan → surface
locked decisions → implement on confirmation
(`feedback_strict_stage_authorization`).

## Empirically established problem (this session, captured)

The algorithm doc's Item 2e note framed this as "which contract to run
first". The measurement showed the dominant lever is different:

- **ESBMC `--timeout` is inconsistent.** St1inch scoped `--timeout 40`:
  graceful — reaches `report_coverage()`, writes the JSON (but
  `Reached: 0`, see below). EscrowDst **`--coverage-whole-unit`
  `--timeout 20`**: the timeout fires *mid-SMT-solve* (claim at
  contract.sol:1500), the process dies **before `report_coverage()`** —
  **no JSON, zero edges persisted** (`ls`: file absent; output ends
  `Solving with solver Bitwuzla` → `ERROR: Timed out`).
- ⇒ The Item-2 write-back is **end-only** (bmc.cpp `report_coverage()`).
  A bounded heavy run that is killed mid-solve persists **nothing**, so
  repeated bounded runs do **not** accumulate — exactly the
  whole-unit-heavy case Item 2e must serve.
- **St1inch / Farming are OUT OF SCOPE.** Their `Reached: 0` is the
  pre-existing upstream GOTO-gen / lib-typed-receiver bug (memory
  `reference_stage2c_node_flattener_correction`): zero edges are *ever*
  witnessed, independent of budget or run count. Item 2/2e cannot
  manufacture witnesses; recording this honestly per
  `feedback_coverage_failure_is_signal` — not masked, not "fixed" here.
- The pilots that DO reach claims (EscrowDst scoped 4/4, aqua 4/3,
  aqua_full 12/8) already showed bit-identical coverage at lower wall
  cost on re-run (Item 2 measurement). Item 2e extends that benefit to
  runs that *cannot finish in one budget* by persisting partial
  progress.

## Core lever (mechanism, file:line read this session)

`reached_claims.emplace(claim_sig)` fires **per claim, as soon as it is
proven `P_SATISFIABLE`**, at **bmc.cpp:2217-2225** ("Store claim
signature" block, mutex-guarded by `reached_claims_mutex`), long before
the run ends. `claim_sig = claim.claim_msg + "\t" + claim.claim_loc`
(bmc.cpp:1886) — the SAME key Item 2 already uses. So an **incremental
append of each newly P_SATISFIABLE edge at this point** makes every
witnessed edge survive a mid-solve kill, and repeated bounded runs
monotonically accumulate. The end-only write-back stays as the
final/merge pass.

## Design

**Incremental flush.** At bmc.cpp:2217-2225, for `is_goto_cov` branch
coverage with a non-empty `goto_coveraget::covered_set_outpath`: if
`(msg,loc)` parsed from `claim_sig` is not already in
`goto_coveraget::covered_set`, insert it and **persist immediately**,
under the existing `reached_claims_mutex`.

Soundness is inherited from Item 2 unchanged: only true `P_SATISFIABLE`
is ever written (monotone-∃); denominator is the no-skip static
universe (Item 2c), independent of what any run instrumented; union is
commutative/associative ⇒ order- and crash-position-independent. The
only new property to guarantee is **crash-safe persistence** (a kill
between two writes must not corrupt the file).

**"Ordering" sub-item — descoped (recommended).** With per-contract
semantics A already shipped, the natural partition is per-contract and
*which order to run partitions* is an external-orchestrator concern —
consistent with Item 2a ("an external orchestrator passes a path").
ESBMC's job is only to (a) skip already-covered edges and (b) persist
new ones crash-safely; the orchestrator decides the run schedule. No
in-ESBMC scheduler proposed unless the user wants one.

## Tests (paired-fixture, deterministic — no timing dependence)

Killing mid-run at a fixed wall-clock is machine-dependent and would
flake under ctest, so the tests assert the *invariants*, not a timer:

1. **`cov_jsonset_incremental_monotone_pass`** — commit a *partial*
   covered.json (subset of the universe). Run once. Assert: the
   written-back set ⊇ the committed set (monotone — never shrinks) and
   the newly witnessed edges are added. Branches = full universe
   (2c), % = the correct full value. This is the accumulation
   invariant a sequence of bounded runs relies on, tested in one
   deterministic shot.
2. **`cov_jsonset_partial_seed_no_inflation_pass`** — commit a partial
   covered.json whose edges are a strict subset; assert the run neither
   drops a committed edge nor lets % exceed the universe-based value
   (crash-position independence ⇒ any partial prefix is sound).
3. **`cov_jsonset_incremental_fail` / KNOWNBUG** — pin the symptom of a
   *non-crash-safe* or non-monotone regression (e.g. covered.json
   truncated to fewer edges than committed ⇒ a shrink), so a future
   regression that loses partial progress is caught by a flip.

Fixtures captured via the exact test `.solast` (the Item-2 pitfall:
stale `.solast` shifts the loc line and breaks the fixpoint). All must
be write-back fixpoints / monotone supersets and byte-stable under
ctest (pre==post sha for the committed fixture is impossible if the run
*adds* edges, so these tests use a committed *input* fixture that is
already a superset-closed set ⇒ run is a no-op append ⇒ byte-stable;
the monotone-growth property is verified by a separate scripted
capture, not a ctest pin — same split Item 2 used for the write-back
property).

## Files (implementation stage, when authorised)

- src/esbmc/bmc.cpp:2217-2225 — incremental flush hook (guarded by
  `reached_claims_mutex`, gated on `covered_set_outpath` non-empty,
  `is_goto_cov` branch coverage only)
- crash-safe writer helper (atomic tmp+rename, or NDJSON append) —
  location per Decision 1
- regression/esbmc-solidity/cov_jsonset_incremental_* — 2 pass + 1
  fail/knownbug
- notes + memory update

## Decisions LOCKED (user 2026-05-17)
1. **Atomic full rewrite (tmp + rename)** on each newly-witnessed edge.
2. **Descope ordering** — external orchestrator drives the schedule;
   no in-ESBMC scheduler.
3. **St1inch / Farming OUT OF SCOPE** — separate upstream Reached:0
   bug, left KNOWNBUG-pinned, noted honestly, untouched here.

## STATUS: SHIPPED 2026-05-17 (user 进行 → plan → decisions locked → impl)

- `goto_coveraget::write_covered_set_atomic()` (goto_coverage.cpp):
  serialize `covered_set` → `<path>.tmp` → `std::rename` (atomic
  publish; a kill between writes keeps the prior valid file).
- Incremental hook bmc.cpp:2225-2237 (under `reached_claims_mutex`,
  `is_branch_cov`, non-empty outpath): each newly P_SATISFIABLE edge
  `covered_set.emplace` + atomic flush. End write-back refactored to
  the same writer (`covered_set` = single live accumulator).
- **Empirical proof** (notes/Results/branch_cov/STAGE_ITEM2E_DEMO.md):
  bounded EscrowDst `--coverage-whole-unit --timeout 20`, killed
  mid-solve every run — **before**: 0 edges / NOFILE every run;
  **after**: 31 → 37 → 37 → 37, JSON valid after every kill.
- Deterministic ctest tripwire `cov_jsonset_unreachable_seed_pass`
  (no timing): covered-set seeded with harness-unreachable edges that
  can only stay credited if load never drops + write is atomic
  (Reached:4/100%; a monotone/truncation regression ⇒ 2/50%).
- Non-regression: cov_jsonset (5) + cov_scope (4) + cov_whole_unit (3)
  = 12/12; goto-coverage C/C++ 109/109; all fixtures byte-stable
  (atomic writer bit-identical to the prior end-writer). clang-format
  clean; no solidity-frontend file touched.

## Out of scope
- The upstream `Reached: 0` GOTO-gen / lib-typed-receiver bug (St1inch,
  Farming) — a distinct, separately-authorised investigation.
- Any change to ESBMC's timeout/signal handling itself.
