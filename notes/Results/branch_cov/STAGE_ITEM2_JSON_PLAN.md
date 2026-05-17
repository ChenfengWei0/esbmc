# Item 2 — cross-run covered-set JSON: implementation plan (PLAN ONLY, no code)

User decision 2026-05-17: "Plan first, no code." Residuals R2/R3/R4
from COVERAGE_ALGORITHM.md resolved by in-session file:line reads
below. One proposed **deviation** from the algorithm doc is flagged for
sign-off (it removes a whole prerequisite — Item 1).

## Residuals resolved (Claim / file:line read this session)

- **R2 — numerator write-back.** `reached_claims`
  (static `std::unordered_set<std::string>`, bmc.cpp:44) is
  `.emplace(claim_sig)`'d at **bmc.cpp:2181-2182** iff a coverage
  claim's `dec_solve()` returns `P_SATISFIABLE`. Key
  `claim_sig = claim.claim_msg + "\t" + claim.claim_loc`
  (bmc.cpp:1886); `claim.claim_loc = it->source.pc->location.as_string()`
  (slice.cpp:258), `claim.claim_msg` = guard expr string (slice.cpp:253)
  or comment (slice.cpp:257). For instrumented branch asserts
  claim_msg == the assert's `location.comment()`.

- **R3 — option parse + % site.** Coverage option site
  esbmc_parseoptions.cpp:3545-3569 (`branch_coverage()` call;
  `--coverage-whole-unit` already wired here). % computed in
  **bmc.cpp:890-915** (`is_branch_cov`): `total =
  goto_coveraget::total_branch`, `tracked = reached_claims.size()`,
  `Branch Coverage = tracked*100.0/total` at **bmc.cpp:914** (second
  copy bmc.cpp:1167-1190). Existing `cov-report-json` writer
  bmc.cpp:989-1088 already iterates `all_claims`, computes
  `covered = reached_claims.count(claim_msg+"\t"+claim_loc)`, emits
  per-claim `{condition,file,line,column,function}` to
  `cov-report.json` — **this is the Item-2 output skeleton already.**

- **R4 — static universe / denominator.** `get_total_cond_assert()`
  (**goto_coverage.cpp:690-716**) scans only **instrumented** asserts
  (`property=="instrumented assertion" && user_provided`), keys a
  `std::set` on `(location.comment(), location.as_string())`.
  `total_branch = all_claims.size()` (**goto_coverage.cpp:322-323**).
  `insert_assert` stamps `comment = from_expr(guard)` and copies
  `it->location` (**goto_coverage.cpp:643-649**). ⇒ denominator is
  derived from the *instrumented* set today — the exact 2c hazard.

## Key-identity finding (decisive — proposed deviation)

All three sites — denominator (`get_total_cond_assert`), numerator
(`reached_claims`), existing `cov-report-json` — already key on the
**same** `(guard_str, location.as_string())` pair. `as_string()` =
source func + file:line and **excludes custom irep fields**
(`location.set("sol_decl_contract",…)` is invisible to it; this is
exactly why 628ebad61f was copy-invariant and why goto_coverage.cpp:
312-318 says as_string folds inheritance copies to one source
identity). The two edges of a decision already have **distinct**
first components (`from_expr(g)` vs `from_expr(!(g))`), same loc ⇒ the
pair already encodes `(decision, edge)`.

**Deviation from COVERAGE_ALGORITHM.md Item 2a/2e:** the doc keys the
JSON on Item-1 `sol_decision_id`. `sol_decision_id` is **not stamped
anywhere** (grep: only `sol_decl_contract`, solidity_convert_util.cpp:
90/116/142). Item 1 was never implemented. Since
`(guard_str, as_string())` is *already* the cross-copy-stable identity
and *already* what numerator/denominator/cov-report-json compare on,
Item 2 can key the JSON on it directly. **This removes Item 1 entirely
as a prerequisite.** Risk: two textually-identical guards on the same
source line in different functions could collide — but `as_string()`
includes the source function, so this is the same (already-accepted)
identity the live numerator uses; no new unsoundness introduced. ⇒
recommend keying on `(guard_str, as_string())`; needs user OK.

## The 2c soundness requirement (made concrete)

If 2b skips inserting the assert pair for an already-covered edge:
`get_total_cond_assert()` no longer sees it → `total_branch` shrinks →
% inflated (possibly >100%). Therefore **the universe must be computed
WITHOUT the skip.** Concretely: in `branch_coverage()`
(goto_coverage.cpp:279-302), for every *in-scope* `IF g GOTO`,
unconditionally record both universe keys
`{(from_expr(g), loc.as_string()), (from_expr(!(g)), loc.as_string())}`
into a new no-skip universe set, **then** consult the covered-set to
decide whether to actually `insert_assert`. `total_branch` = universe
size (skip-independent). Stop deriving `all_claims` from
`get_total_cond_assert()` for branch mode; derive it from the no-skip
enumeration. Numerator = (this-run `reached_claims` ∪ JSON
covered-set) ∩ universe. This is option (A) of R4 (self-contained,
no Item 1). Option (B) frontend-id universe is rejected (needs Item 1).

## Design

**Option:** `--coverage-covered-set <path>` (read-modify-write one
file: input covered-set at entry, merged output at exit). Distinct
from the existing one-shot `cov-report-json`. `--coverage-whole-unit`
composes orthogonally (it only toggles the scope filter at
goto_coverage.cpp:290-294).

**JSON shape (merge-stable):**
`{ "version":1, "covered":[ {"cond":"<guard_str>","loc":"<as_string>"} … ] }`
— a set of per-edge keys. Monotone-∃: never removed, only unioned.

**Flow:**
1. Parse `--coverage-covered-set` → path string to `goto_coveraget`
   (mirror the `scope_contract` plumbing, esbmc_parseoptions.cpp:3566).
2. `branch_coverage()` entry: if path exists, load JSON →
   `in_covered : set<pair<string,string>>`.
3. Instrument loop (goto_coverage.cpp:279-302), per in-scope decision:
   build the 2 keys; insert both into `universe` unconditionally
   (2c); `insert_assert` an edge **only if** its key ∉ `in_covered`.
4. `total_branch = universe.size()` (replaces goto_coverage.cpp:
   322-323 for branch mode; non-branch modes unchanged).
5. Run-end (bmc.cpp:890-915 path): `effective_reached =
   reached_claims ∩ universe ∪ (in_covered ∩ universe)`;
   `% = |effective_reached| / |universe|`. Write back
   `in_covered ∪ {this-run keys with reached_claims hit}` to path
   (merge, never truncate). Reuse the bmc.cpp:989-1088 json writer
   structure.

**Soundness (2d, now grounded):** (i) edge cover is monotone-∃ —
a `P_SATISFIABLE` witness stays valid; (ii) an instrumented assert is
a property obligation, not a path constraint (contrast
`replace_assert_to_assume`, goto_coverage.cpp:272-273) — *not
inserting* it removes one observation only, perturbs no other branch;
(iii) only real `P_SATISFIABLE` (bmc.cpp:2181) is written back;
(iv) denominator decoupled (step 4) ⇒ skip cannot raise %. ∴ final
metric bit-identical to full-instrument, strictly fewer SMT
obligations on re-runs.

## Test design (hand-written, simple→complex→adversarial)

Each test = 2 ESBMC invocations sharing one covered-set file (regression
runner: a `test.desc` runs one command, so use a wrapper-free 2-pin
pair of sibling dirs, or a `.desc` that pipes run1→run2; decide at impl
— flag for the runner). Counts measured empirically, never fabricated.

1. **simple** `cov_jsonset_idempotent` — run twice, same contract.
   Run1 produces JSON with the reached edges; run2 with that JSON as
   input must report **identical %** and **0 newly-instrumented**
   edges for covered ones. Pins cross-run idempotence + 2c (denominator
   unchanged run1 vs run2).
2. **complex** `cov_jsonset_partition_merge` — contract with two
   public fns A,B. Run1 `--focus`-style path covers A's edges only;
   run2 covers B's; merged JSON ⇒ union %. Pins commutative union +
   universe stability across partial runs.
3. **adversarial** `cov_jsonset_skip_no_inflation` — a decision whose
   `g` edge is feasible but `!g` is infeasible. Run1 covers `g`; run2
   with JSON skips `g`'s assert. Assert run2 % == run1 % (NOT inflated
   by the shrunken instrumented set) — the 2c regression guard. Also a
   `_fail` dual pinning what a *broken* (instrumented-set-derived)
   denominator would wrongly report (KNOWNBUG if needed).

cppcheck on changed solidity-frontend files: none expected (changes in
goto-programs/ + esbmc/). clang-format on changed .cpp. goto-coverage
109/109 + cov_scope_* + cov_whole_unit_* must stay green (no-path =
inert: covered-set unset ⇒ universe==instrumented ⇒ identical to today).

## Files (implementation stage, when authorised)

- src/esbmc/options.cpp — declare `--coverage-covered-set <path>`
- src/esbmc/esbmc_parseoptions.cpp:3566 — plumb path → goto_coveraget
- src/goto-programs/goto_coverage.h — `covered_set_path`,
  `universe`/`in_covered` members + load/store decls
- src/goto-programs/goto_coverage.cpp:279-323 — no-skip universe build
  + covered-set skip gate + `total_branch=universe.size()`
- src/esbmc/bmc.cpp:890-915 / 989-1088 — effective-reached union +
  merge-write-back (reuse existing json writer)
- regression/esbmc-solidity/cov_jsonset_* — 3(+1 fail) tests

## Out of scope (separate later stages)
- Item 1 `sol_decision_id` stamping — **obviated** by the key-identity
  finding (unless user rejects the deviation).
- Run-ordering optimisation to maximise early skips (algorithm Item 2e
  note).
- Non-branch coverage modes (assertion/cond/k-path) — Item 2 is
  branch-only by the locked semantics.

## Decisions LOCKED (user 2026-05-17)

1. **JSON key = `(guard_str, location.as_string())`.** Item 1
   (`sol_decision_id`) is **dropped entirely** — not a prerequisite,
   not a stage. The edge is encoded in `guard_str` (`g` vs `!(g)`).
2. **Option = new `--coverage-covered-set <path>`**, dedicated
   read-modify-write, separate from one-shot `--cov-report-json`.
3. **Tests = paired sibling dirs + a committed fixture JSON** (see
   revised harness below — made order-independent so ctest parallelism
   / run order cannot flake it).

### Revised test harness (order-independent — supersedes the earlier
"2 invocations sharing a live file" sketch)

ctest runs each `test.desc` as one isolated command, in parallel, no
guaranteed order ⇒ a file written by run1 and read by run2 is a flake
risk. Instead, each test is a **single** ESBMC command with a
**committed input fixture**:

- `cov_jsonset_idempotent_pass/` — ships `covered.json` pre-populated
  with the exact reached-edge keys for this contract (generated once at
  authoring, committed). `test.desc` runs one command with
  `--coverage-covered-set covered.json`; pins that `Branch Coverage:`
  is **unchanged** vs the no-fixture run (idempotence + 2c: denominator
  from the no-skip universe, not the shrunken instrumented set).
- `cov_jsonset_skip_no_inflation_pass/` — fixture marks the feasible
  `g` edge covered; `!g` infeasible. Pins `%` == the no-fixture `%`
  (skip did NOT inflate). This is the 2c regression guard.
- `cov_jsonset_partition_merge_pass/` — fixture = contract A-edge keys
  only; command pins that B's edges still count in the denominator
  (universe stable) and A's are credited from the fixture (union).
- `cov_jsonset_inflation_fail/` (KNOWNBUG/FAIL dual) — pins the WRONG
  output a broken instrumented-set-derived denominator would produce
  (>100% / inflated), so a regression that reintroduces the 2c bug is
  caught by a flip, per `feedback_knownbug_tests`.

The "does run-end actually write a correct merged JSON" property is
verified separately at impl time by one manual scripted check
(captured, not pinned in ctest) — the committed fixtures decouple the
ctest pins from any run-order dependency.

## STATUS: SHIPPED 2026-05-17 (user authorised "继续")

Implemented exactly as the locked design.

- **Option** `--coverage-covered-set <path>` (options.cpp; plumbed
  esbmc_parseoptions.cpp:3570).
- **2c decoupling** goto_coverage.cpp `branch_coverage()`: the no-skip
  static universe is built in-loop into `all_claims` (every in-scope
  edge, keyed `(from_expr(guard), location.as_string())`), the
  covered-set skip gates only the two `insert_assert` calls,
  `total_branch = all_claims.size()` (independent of what was
  instrumented). New statics `covered_set` / `covered_set_outpath`
  (goto_coverage.h). `gotoprograms` now links `nlohmann_json`
  (CMakeLists).
- **Numerator + write-back** bmc.cpp `report_coverage()` is_branch_cov:
  when active, numerator = universe edges in `covered_set` ∪ this-run
  `reached_claims`; merged set rewritten to the path (monotone union).
  No-path path byte-for-byte unchanged (`cov_set_active` gate).

**Empirical results (this session, captured):**
- End-to-end: run1 (no file) writes JSON; run2 skips the covered
  edges (4→2 instrumented asserts) yet Branches stays 4 / 50% —
  bit-identical metric at lower cost.
- 4 regression tests, all PASS, fixtures **byte-stable** under ctest
  (pre==post sha — no working-tree mutation; fixtures are write-back
  fixpoints captured via the exact test invocation):
  - `cov_jsonset_idempotent_pass` 2/2/100%
  - `cov_jsonset_partition_merge_pass` 4/2/50% (universe stable under
    partial covered-set)
  - `cov_jsonset_skip_no_inflation_pass` 4/4/100% (all edges
    pre-covered ⇒ instrumented set empty; 2c keeps denom=4)
  - `cov_jsonset_inflation_knownbug` KNOWNBUG tripwire pinning the
    regressed `No branch detected`
- No-path inert: goto-coverage C/C++ **109/109**; cov_scope_* (4) +
  cov_whole_unit_* (3) unchanged; pilot scoped (EscrowDst 4/4/100%,
  aqua_full 12/8/66%, aqua 4/3/75%) unchanged.
- clang-format clean in changed regions; no solidity-frontend file
  touched (cppcheck N/A).

Pitfall recorded: the JSON loc key embeds the source line, so a
fixture must be captured **from the final contract via the exact
test's `.solast` input** — a stale .solast (regenerated before a
header-comment edit) shifts line numbers and the fixture stops being a
write-back fixpoint (the covered-set then accumulates stale∪fresh keys
and ctest mutates the committed file). All 4 fixtures verified
fixpoint by pre/post sha equality under the real ctest path.
