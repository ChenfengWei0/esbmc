# Coverage fix plan — FILE-LEVEL rewrite

Supersedes the per-contract Fix-B (2026-05-15). Plan/doc only, no
`src/`, no `test.desc`, no commit (`feedback_strict_stage_authorization`).
Every mechanism is a `file:line` / goto-dump read **this session**;
Claims vs Hypotheses/Residual labelled per
`feedback_information_chain_before_conclusion`.

## 0. The decision that rewrote this plan

User, 2026-05-15: **report file/project-level branch coverage**
(solidity-coverage-aligned). `--contract C_i` is ONLY a cost-
partitioning driver (whole-unit-at-once is too expensive). Denominator
= distinct source decisions in the whole unit; numerator = UNION across
the per-`--contract` runs. **Per-contract is not the reported unit —
do not scope the denominator down to a contract.**

### What this dissolves

The old Fix-B's core mechanism — *reachability BFS from the harness
entry to shrink the denominator to C's reachable set* — is **per-
contract semantics (b), now rejected**. It is **deleted**, not
re-pointed. The file-level metric needs **no call-graph reachability
for the denominator at all**: the denominator is just the
deduplicated whole-unit instrumentation; the numerator is the OR of
solver-`P_SATISFIABLE` edges across runs.

## 1. Empirical basis (Claims, goto-dump this session)

`single_contract_pass`: one `if/else` → `Branches:2 Reached:2 100%`
(coverage pipeline correct with no out-of-scope code).

| Case | source decisions (mangled id) | dup? | cur `Branches` | file-level correct |
|---|---|---|---:|---:|
| sibling | `@C@C@F@setX#36`, `@C@Other@F@setY#18` | none | 4 | **4** |
| uncalled_library | `@C@C@F@setX#36`, `@C@L@F@f#18` | none | 4 | **4** |
| modifier_crosscontract | `bumpInternal#29` ×2 (`@C@A@`,`@C@B@`), `setB_gate#0` | **#29 ×2** | 6 | **4** |
| override (diag) | `@C@A@F@f#23`, `@C@B@F@f#NN` | none | — | distinct (correct) |

Claims: (a) inherited-but-not-overridden code is physically copied per
derived contract with **identical AST node id** (`bumpInternal#29` in
both `@C@A@`/`@C@B@`); (b) override yields **distinct** ids
(`f#23`≠`f#49`), `super.f()` = direct call to the single `@C@A@F@f#23`
(no third copy); (c) modifier synthetics carry id **`#0`** (non-unique)
— their spliced branch instruction's *source location* points at the
modifier **definition** site (prior dump: line 12), so all use-site
splices share it and fold naturally.

⇒ **Dedup key = AST node id `#NN` from the mangled symbol**, with
fallback **source-location** for `#NN==0` synthetics (modifier
splices). Textual `line` in the goto comment is unreliable under
`--no-assertions` (collapses to `line 1` this session — Residual R3),
which is *why* the node id, not the line, is the primary key.

## 1b. S-D SHIPPED 2026-05-16 (one-liner, not the node-id scheme)

Mechanism fully read this session collapsed Line D to a **one-line**
change, far simpler than the AST-node-id design sketched in §2:

- `get_total_cond_assert()` (`goto_coverage.cpp:658-685`) already keys
  each claim by `(condition, location.as_string())` and returns a
  `std::set`. `locationt::as_string()` (`util/location.cpp:3-38`,
  read) uses the **source** function name + file/line — NOT the
  mangled `@C@A@F@…#NN`. So the inheritance/modifier physical copies
  produce **byte-identical** pairs and the set folds them.
  Empirically (`--show-claims`): modifier_crosscontract Claim1≡Claim3,
  Claim2≡Claim4.
- The numerator (`reached_claims` matched vs `all_claims`) already
  used this deduped set. Only the **denominator**
  (`total_branch = get_total_instrument()`, a raw per-instruction
  count over `forall_goto_functions`) was un-deduped → the
  asymmetry = the entire denominator bug.
- **Fix (`goto_coverage.cpp:291`):**
  `total_branch = static_cast<size_t>(all_claims.size());`
  (was `= get_total_instrument();`). Other coverage modes
  (assertion/k-path/branch-function) keep `get_total_instrument()` by
  design — only branch coverage is file-level-aggregated.
- The AST-node-id key, `location_pool` removal and reachability BFS
  in §2/§3 are **not needed** — `location.as_string()` is already
  the file-level source-identity key. `location_pool` (`:255-260`)
  left untouched (it gates *which files* are instrumented, orthogonal
  to denominator dedup; removing it is unrelated scope creep).

**Verified delta (old `release-bundle` vs new binary, final verdict):**

| case | OLD | NEW | Line-D effect |
|---|---|---|---|
| single_contract_pass (CORE) | 2/2/100% | 2/2/100% | no-op (control intact) |
| sibling_contract | 4 / final 2 / 50% | identical | no-op (no dup) |
| uncalled_library | 4 / final 2 / 50% | identical | no-op (no dup) |
| modifier_crosscontract | **6** / final 2 / — | **4** / 2 / 50% | **6→4 dedup** |

`ctest -R cov_scope_` → **4/4 PASS, 1.39 s**, all CORE.

**Misdiagnosis corrected (honest):** the `Reached:0` recorded in
`KNOWNBUGS.md` / `COVERAGE_SCOPING_PINS.md` as a "separate Line-N
numerator bug" was a **non-final k-iteration line**. The *final*
verdict was always `Reached:2` even on the OLD binary. ⇒ **there is no
Line-N numerator bug for the cov_scope cases**; `sibling`/`uncalled`
were never buggy under the file-level metric (their KNOWNBUG status was
purely the rejected per-contract `Branches:2` expectation);
`modifier_crosscontract` only ever needed Line D. All 3 KNOWNBUG → CORE
(`^Branches : 4$`/`^Reached : 2$`/`^Branch Coverage: 50%$`).

## 2. Three orthogonal fix lines (the bifurcation)

The single old plan splits into three independent lines; each its own
separately-authorised stage.

### Line D — denominator dedup (the only real denominator defect)

Collapse physical copies of one source decision. Needed **only where
inheritance/modifier duplication occurs** (`modifier_crosscontract`
6→4; EscrowDst's per-derived + base-spliced copies). `sibling` /
`uncalled_library` need **no** D change — their whole-unit denominator
(4) is *already* the correct file-level value (this is the key finding:
the user's decision dissolves the "scoping bug" for the non-duplicated
cases).

- Where: `goto_coverage.cpp` instrumentation/count path
  (`branch_coverage()` `:246-289`, `get_total_instrument()` `:623-645`,
  `filter()` `:1201-1217` — all read this session).
- Mechanism: when instrumenting/counting a branch, derive its dedup
  key from the owning function symbol's AST-node id (`#NN`); for
  `#NN==0` use the branch instruction's source location. Count each
  distinct key **once** (numerator and denominator must use the same
  key — `get_total_instrument()` already shares `filter()`).
- `location_pool` (`:255-260`, maintainer-flagged "unsound"
  `goto_coverage.h:136-138`): **removed**. Under file-level it is
  neither needed (no scoping) nor sound (filename-keyed, defeated by
  flattened single-file `.sol`). Its only legitimate descendant —
  source identity — is subsumed by the dedup key.
- Soundness/completeness: dedup **reduces** the denominator by exactly
  the artifact-duplication factor; never drops a genuine distinct
  decision (distinct source ⇒ distinct `#NN`, Claim b). Soundness: no
  feasible branch hidden. Completeness: removes phantom obligations ⇒
  reported % becomes the true file-level %, not an artifact-deflated
  one.

### Line N — numerator / cross-run union (the dominant remaining defect)

`Reached:0` whenever a 2nd contract/library is present (vs `Reached:2`
on `single_contract_pass`). This is **not** a denominator problem —
`sibling`/`uncalled_library` already have the correct denominator (4)
and still report `Reached:0`. Two sub-parts:

- **N1 (single-run driving):** the in-scope decision stops being
  driven once out-of-scope code coexists. Distinct symex/k-induction
  completeness bug (budget-burn family, `reference_k_induction_*`).
  Direction **unchanged** by the file-level decision.
- **N2 (cross-run union):** a source decision is file-level *covered*
  if **any** per-`--contract` run proves either edge
  `P_SATISFIABLE` (`bmc.cpp:2000-2012,2172-2185`, read). Requires a
  union of `reached_claims` keyed by the **Line-D dedup key** across
  the cost-partition runs. Not yet implemented (no multi-run
  aggregation exists today).

### Line C — crashes (orthogonal, block measurement)

- aqua `bare smt_sort`→ post-2C `value_set base_type_eq` /
  `with2t is_array_type` on deep nested mapping-of-struct.
- EscrowDst: GOTO-gen abort `nonexistant member "$balance" in
  "Create2"` — **hit this session**, the `release-bundle` binary is
  stale vs the working-tree crash-fix; blocks even `--show-claims`.
  Pure frontend/backend; no coverage-semantics content.

## 3. Re-derived `cov_scope_*` file-level targets

All three: **`^Branches : 4$` / `^Reached : 2$` / `^Branch Coverage:
50%$`** (in-scope decision both edges feasible = 2; out-of-scope
decision unreached = 0; of 4 edges, 2 reached).

| pin | denominator today | needs Line D? | needs Line N? | flip-on |
|---|---:|---|---|---|
| `single_contract_pass` (CORE) | 2 ✓ | no | no | — (control) |
| `sibling_contract` | 4 ✓ | **no** | **yes (N1)** | N1 lands |
| `uncalled_library` | 4 ✓ | **no** | **yes (N1)** | N1 lands |
| `modifier_crosscontract` | 6 ✗ | **yes (6→4)** | yes (N1) | D **and** N1 |

⇒ the 3 KNOWNBUG pins must be re-pinned to the file-level triple
(separate authorised stage — not changed here). `sibling`/`uncalled`
become **Line-N falsifiers**; `modifier_crosscontract` is the **Line-D
+ Line-N** falsifier. Self-correction logged: an earlier note said
`modifier_crosscontract → Branches:2`; correct is **4** (dedup removes
the duplicate `#29` copy, not the legitimate uncovered `bumpInternal`
decision).

## 4. Staged plan (per-stage authorisation)

- **S0 ✅** cov_scope pins + EscrowDst walk-back + override/dedup
  diagnostics (`OVERRIDE_DEDUP_DIAG.md`). Symbol inventory captured.
- **S-D (待授权):** implement Line D (node-id dedup key in
  `goto_coverage.cpp`, remove `location_pool`). Solidity-only verify;
  `modifier_crosscontract` denominator 6→4 is the construct-time
  falsifier. Re-pin the 3 pins to the file-level triple.
- **S-N1 (待授权):** diagnose the "out-of-scope code suppresses
  in-scope driving" numerator bug (`sibling`/`uncalled` `Reached:0`
  with correct denominator 4 — clean minimal repro of N1, no D
  confound).
- **S-N2 (待授权):** cross-run union aggregator keyed by the dedup
  key (the actual file/project metric over cost-partition runs).
- **S-C (待授权):** the two Line-C crashes (separate, each its own
  plan).
- Success metric per stage = KNOWNBUG→CORE flips + dual-axis report.

## 5. Residuals (unmeasured — must not fabricate)

- **R1 EscrowDst post-dedup distinct count.** Documented decomposition
  `90 = 48 lib + 38 base-spliced + 4 own` (prior-session
  `--show-claims`). The dedup of it is **UNMEASURED** — current
  binary SIGABRTs at GOTO gen (Line C). Needs a rebuilt/ current
  binary; do not state a post-dedup number until measured.
- **R2** Line-N2 has no existing aggregation scaffold; multi-run
  orchestration design is a HYPOTHESIS until S-N2.
- **R3** goto-comment textual `line` collapses to `line 1` under
  `--no-assertions` — measurement-tool quirk; reinforces using the
  AST node id, not the line, as the primary dedup key. Not chased
  (out of scope).
