# Stage I — #5 residual diagnosis: scalar deep-nested-mapping write routes to Bitwuzla

Generated 2026-05-18. **Diagnosis only — no fix, no `src/` change, no
commit** (the fix is a separate, separately-authorised stage, per
`feedback_strict_stage_authorization`). Companion to
`STAGE2C_FOLLOWUP_DIAG.md` (the structural-abort fix, landed
2026-05-15) and its "#5 residual dual matrix" appendix (Stage P
empirical capture, 2026-05-18).

## 1. The residual, precisely

TWO_TRACK finding #5's structural abort
(`irep2_expr.cpp:366` / `value_set.cpp:1258` on a deep nested-mapping
WRITE) was fixed at `solidity_convert_expr.cpp:4203` (2026-05-15);
7 aqua write pilots flipped KNOWNBUG→CORE. **The entire remaining #5
is one shape:** a **scalar `uint256`-valued ≥3-level nested-mapping
WRITE**, pinned canonically by
`cov_pilot_aqua2A_4lvl_addr_addr_bytes32_addr_uint256` (KNOWNBUG) and
isolated minimally by the two Stage-P standalone KNOWNBUG pins. It no
longer aborts; it simply never produces a verdict on the default
solver.

### Empirical wall matrix (Stage P.4, captured 2026-05-18 — not inferred)

| shape / flags | observed |
|---|---|
| 3-level scalar, **bitwuzla** (default), `--contract C --unwind 4 --no-unwinding-assertions` | `[bzla] warning: Equality over constant arrays not fully supported yet`; no `VERIFICATION` verdict — **wall (b)** at depth 3 already |
| 3-level scalar, **--cvc5**, same BMC | `VERIFICATION SUCCESSFUL` (round-trip) / `FAILED` (`==v+1` dual) |
| 4-level scalar, **--cvc5**, same BMC | `SUCCESSFUL` / `FAILED` / `SUCCESSFUL` (depth-4 key-independence `l2!=l`) |
| 4-level scalar, **bitwuzla**, same BMC | const-array-eq abort, no `SUCCESSFUL` — **wall (b)** |
| 4-level scalar in calldata-array loop, **bitwuzla**, `--branch-coverage-claims --k-induction --unlimited-k-steps --timeout 25` | k-induction → `Checking inductive step, k = 18` → terminal `ERROR: Timed out`, zero `^Branch Coverage:`, exit 1 — **wall (a)** |
| trivial `if`-only 4-level scalar (no loop), same coverage flags | converges → `Branch Coverage: 100%` (does NOT reproduce wall (a); the calldata-array loop is the k-induction amplifier) |

Two solver-layer walls, **both on Bitwuzla, both vanish on CVC5**:

- **(a) k-induction non-convergence under coverage.** Solidity's
  `while(nondet) dispatch()` harness means the forward condition never
  converges (`reference_k_induction_budget_burn`); the inductive
  step's per-step cost on the scalar deep-nested infinite mapping
  array compounds across k=1…18 past the desc `--timeout 25`, so no
  `[Coverage]` block is ever emitted.
- **(b) Bitwuzla `Equality over constant arrays not fully supported
  yet` abort under assertion BMC.** IS-havoc composes an asymmetric
  `(= ca freshsym)` over the CONST_ARRAY-initialised infinite mapping
  array; upstream `array_solver.cpp:225-241` bails, and ESBMC's
  `mk_eq` interception (`bitwuzla_conv.cpp:497-558`) covers only the
  *symmetric* `(= ca1 ca2)` case (`reference_bitwuzla_const_array_eq_trigger`).

**Load-bearing fact:** `--cvc5` cleanly handles *every* scalar depth
(3 and 4), round-trip + non-vacuous fail + depth-4 key-independence.
The fix is therefore a **solver-routing** decision, not a symex/IR
change — and not a Bitwuzla-internal or k-induction change.

## 2. Root cause of the routing miss (mechanism, file:line, read 2026-05-18)

The Solidity CVC5 auto-hint, `esbmc_parseoptions.cpp:889-1069`, sets
`nested_dyn_detected` (declared :889) **only** via:

- `.solast` mode: a sliding match of **three consecutive
  `t_array$_` markers** (`ta_marker = "t_array$_"` at :966; the
  three-in-a-row logic at :1030-1042 / :1069) in `typeIdentifier`
  JSON strings; OR
- `.sol` fallback mode: **three consecutive empty `[]`** after
  comment/string stripping (:925-936).

There is **no `t_mapping$` recognition anywhere** in the detector.

The scalar deep-nested-mapping `typeIdentifier`, verified from the
generated `.solast` this session, is:

```
t_mapping$_t_uint256_$_t_mapping$_t_uint256_$_t_mapping$_t_uint256_$_t_mapping$_t_uint256_$_t_uint256_$_$_$_$
```

— a pure `t_mapping$` / `t_uint256` chain. (Incidental `t_array$`
substrings exist in the same `.solast` only from the `uint[] calldata
ks` parameter — a 1-D array — and never form the three-*consecutive*
`t_array$_t_array$_t_array$_` run.) So `nested_dyn_detected` stays
`false`. With no `--bound`/`--reentry-check` + ≥2-contract + value-call
signal either, `kind_multi_contract_detected` is also false, and the
selector falls through to the default-preferred branch at
`esbmc_parseoptions.cpp:1300` → **`bitwuzla`** → walls (a)/(b).

**Why the 7 struct-valued deep-nested-mapping pilots already flipped
CORE but the scalar one did not:** empirically the struct-valued
pilots run to a clean `Branch Coverage: 75%` on the default solver
post-4203 (`STAGE2C_FOLLOWUP_DIAG.md`), i.e. the struct leaf's
lowering does not present the const-array-eq / non-convergence wall to
Bitwuzla in the same way the bare-`uint256` infinite-array leaf does.
The **scalar `=>uint256` leaf is the uncovered shape** — it neither
trips the array-marker detector nor survives Bitwuzla.

## 3. Recommended fix lever (SKETCH — NOT implemented; separate stage)

Extend the **existing Pattern-1 detector** (do not add a new
mechanism — reuse the two-mode scaffolding already at
`esbmc_parseoptions.cpp:920-1069`) to additionally recognise a
**deep-nested-mapping** shape:

- `.solast` mode: a sliding match of **≥3 consecutive `t_mapping$_`
  markers** in a `typeIdentifier` string (exact analogue of the
  `ta_marker` three-in-a-row loop at :1030-1042 — add a parallel
  `tm_marker = "t_mapping$_"` counter in the same scan, no second
  pass).
- `.sol` fallback mode: **≥3 nested `mapping(`** after the existing
  comment/string strip (analogue of the three-`[]` rule at :925-936).

On match, set the same `nested_dyn_detected` (or a sibling flag) and
route to **plain `cvc5`** — **no `--cvc5-native-tuples`** (that flag
is array-tuple-encoding-specific; the scalar mapping needs only
CVC5's array+BV backend, exactly mirroring Pattern B's plain-`cvc5`
selection at `:1296`, not the native-tuples branch at `:1288-1289`).
Emit a distinct `log_status` line (mirror the existing one at
`:1318-1324`) naming "deep-nested-mapping shape".

**Single edit region:** the `nested_dyn_detected` computation
(`:920-1069`) + the dispatch at `:1284-1297`. No other file. Reuses
`ta_marker`-style scanning, the `scan_path` / `scanning_solast`
plumbing (:937-961), and the existing precedence guard
`!user_picked_solver` (:920).

### Soundness / scope of the lever

Pure solver-selection: changes which backend proves the *same* VCs;
cannot alter verdicts where a solver already returns one. Stage P's
`--cvc5` soundness duals (round-trip PASS, `==v+1` FAIL,
key-independence PASS at depths 3 and 4) are the evidence CVC5's
result on this shape is correct, so routing to it is sound.

## 4. Risk surface & alternatives considered

**Risk (accepted):** false-positive CVC5 routing of *shallow* (≤2-level)
or struct-valued mappings that Bitwuzla handles fine — **perf-only**,
acceptable under the detector's documented high-precision/low-recall
design (`reference_solidity_solver_auto_hint`, "Caveat — Phase-0 abort
still possible"). Must verify (future fix stage) the 7 already-CORE
struct pilots + the `mapping_*` regression family are not perturbed
(they either keep Bitwuzla if no 3-mapping run, or move to CVC5 and
still pass). Explicit `--bitwuzla/--z3/--cvc5` precedence is preserved
by the existing `!user_picked_solver` guard (:920).

**Rejected lever (i): extend Bitwuzla `mk_eq` to the asymmetric
CONST_ARRAY case.** Upstream-array-solver territory; the prior
aggressive const-array workaround was reverted for breaking 10+ tests
(`reference_bitwuzla_const_array_eq_trigger`,
`feedback_aggressive_smt_fallback_breaks_tests`). High blast radius,
wrong layer.

**Rejected lever (ii): k-induction encoding / budget change.**
Forward-never-converges is by-design for the Solidity dispatch harness
(`reference_k_induction_budget_burn`); changing k-induction to "fix"
one shape's per-step cost is the wrong layer and broad-impact.

The routing lever is the **principled root fix** (`feedback_no_lazy_fix`
— it is the solver-selection decision itself, not an assert filter, a
k-induction hack, or a Bitwuzla patch): the structural fix already
landed; only the backend choice for the scalar leaf is wrong.

## 5. Flip targets a future fix stage would own

- `cov_pilot_aqua2A_4lvl_addr_addr_bytes32_addr_uint256`
  KNOWNBUG→CORE (the canonical #5 pin; expected
  `^Branch Coverage:` once CVC5-routed).
- `cov_nested_mapping_write_uint256_kinduction_knownbug`
  KNOWNBUG→CORE (wall (a), minimal).
- `nested_mapping_write_uint256_bitwuzla_constarrayeq_knownbug`
  KNOWNBUG→CORE — note: this pin's `test.desc` has no explicit
  solver, so an auto-route would itself flip it; the fix stage must
  decide whether to keep it as a Bitwuzla-specific KNOWNBUG (pin
  `--bitwuzla` explicitly) or accept the auto-route flip. Recorded
  here as a fix-stage decision, not pre-judged.
- Full Solidity regression gauntlet (no struct-pilot / `mapping_*`
  regression).
- Soundness re-probe: deep-nested scalar write→read round-trip under
  the auto-routed solver (the Stage-P `--cvc5` duals are the oracle).

## Out of scope (unchanged)

The fix implementation itself; the FarmingPool #1 plan; the other
TWO_TRACK findings (#2 Reached:0, #4 `--function` qualifier).

---

# Stage F — fix landed (2026-05-18)

## Patch (single src file, single region)

`src/esbmc/esbmc_parseoptions.cpp` (+98 lines, no new pass/function):

- New sibling flag `deep_mapping_detected` next to `nested_dyn_detected`.
- `.solast` arm: a `t_mapping$_` sliding matcher that counts markers
  **within one typeIdentifier JSON string**, resetting only on `"`
  (NOT on the intra-string key/value gap — the asymmetry vs the `ta`
  matcher is what makes 3 *separate* 1-level mappings stay below 3).
  `tm_run >= 3` ⇒ `deep_mapping_detected = true; break;`.
- `.sol` arm: a `mapping(` token-chain matcher, resetting on `;{}`.
  `>= 3` ⇒ detected. Conservative over-approx (perf-only FPs).
- Dispatch: `else if (deep_mapping_detected) chosen = "cvc5";`
  inserted AFTER the `nested_dyn_detected` array→cvc5+native-tuples
  branch, BEFORE `kind_multi_contract_detected`. **Plain CVC5, no
  native-tuples** (that flag is array-tuple-encoding-specific). A
  matching `log_status` line names the shape + override flags.
- Precedence preserved by the existing `!user_picked_solver` /
  `cvc5_available` guards; entire block is `if (is_solidity)`-scoped.

clang-format (clang-format-18, line-confined): one cosmetic
whitespace change on an own added line, zero whole-file drift.
Code review (priorities Critical/High/Med): zero critical/high
findings; state machines in-bounds, consistent with the unchanged
`ta` matcher; the reset-on-`"` provably blocks the
3-separate-mappings false positive; `break`-vs-more-specific-route
resolved by dispatch order.

## CORRECTED OUTCOME — 1 flip, not the predicted 2

The diagnosis predicted both coverage KNOWNBUGs would flip. Empirical
reality (captured, not inferred): **the routing fix closes wall (b)
ONLY.** Wall (a) is solver-agnostic — empirically Bitwuzla climbs to
k=18, CVC5 only to k=3, NEITHER emits coverage in the budget. The
route DOES fire on the wall-(a) pins (they are ≥3-level nested
mappings) but switching solver does not make k-induction converge.

| Pin | Wall | Routing effect | Final |
|---|---|---|---|
| `nested_mapping_write_uint256_autoroute_cvc5_pass` (renamed from `..._bitwuzla_constarrayeq_knownbug`) | (b) | route fires → `VERIFICATION SUCCESSFUL` | **KNOWNBUG→CORE (the 1 real flip)** |
| `nested_mapping_write_uint256_autoroute_cvc5_fail` (new non-vacuity dual) | (b) | route fires → `VERIFICATION FAILED` | CORE (proves auto-routed path catches real violations) |
| `cov_pilot_aqua2A_4lvl_addr_addr_bytes32_addr_uint256` | (a) | route fires, k-induction still non-convergent | **stays KNOWNBUG** (header updated) |
| `cov_nested_mapping_write_uint256_kinduction_knownbug` | (a) | route fires, k-induction still non-convergent | **stays KNOWNBUG** (header updated) |
| 5× `nested_mapping_write_{3,4}lvl_uint256_*` (explicit `--cvc5`) | — | unchanged (user-picked solver, guard-skipped) | CORE, soundness oracle |

Wall (a) (`reference_k_induction_budget_burn`) is a separate,
genuinely-deferred item the routing fix does not touch — flip target
of a future k-induction stage, NOT solver routing. This is recorded
honestly per `feedback_no_silent_substitution` /
`feedback_coverage_failure_is_signal`: the predicted 2-flip outcome
did NOT happen; the 2 coverage pins are NOT faked-flipped.

## F4 — full esbmc-solidity degradation gate (1071 tests, one run)

`ESBMC_REGRESS_MEMORY_LIMIT=4096 ctest -L esbmc-solidity -j4`
→ 98% passed, 26 failed (15 Timeout + 11 Failed), ctest exit 8,
1535 s wall.

**Non-regression proof (mechanism-level, no baseline rerun needed):**
`grep -c 'detected >=3-level nested-mapping'` over the FULL F4 log
(ctest `--output-on-failure` dumps every failing test's stdout) = **0**.
The patch's route line fired in ZERO of the 26 failing tests. The new
`else if (deep_mapping_detected)` is one link in an if/else-if chain;
when the flag is false (route line absent ⇒ provably false) the
dispatch is byte-identical to the pre-patch binary. Therefore all 26
fail identically with or without the patch. Every "Failed" test's log
still shows `Solidity: auto-selecting 'bitwuzla'` (or uses user-picked
`--cvc5`, guard-skipped). Adjudication:

- 11 "(Failed)": all pre-existing verdict mismatches / a KNOWNBUG that
  now passes (`cover_iterable_mapping_1`), all on the default solver
  the patch never diverted; `narrowing_user_cast_fail` uses `--cvc5`
  (user-picked → patch inert by the `!user_picked_solver` guard);
  `#2053 "Testing"` = ctest meta-entry (0.13 s, no esbmc/solver run).
- 15 "(Timeout)": 2 = the wall-(a) pins (expected NOT to flip,
  k-induction-budget-burn); `cov_pilot_st1inch_St1inch` = documented
  OUT-of-scope upstream Reached:0; 10× `napp_*` = documented SMT-scale
  solver-hard class (`project_napp_smt_scale_bound`);
  `ssa_cost_bytes32_push_*` + `tod_harness_dep_topo_sort` =
  load-timeout class, route provably inert (not ≥3 nested mappings).

Intended-behaviour confirmations from the same log:
`nested_mapping_write_uint256_autoroute_cvc5_pass` #2726 **Passed**;
5× `nested_mapping_write_{3,4}lvl_uint256_*` **Passed**;
`cov_pilot_aqua2A_4lvl_all_addr_struct` (struct pilot) **Passed**.

**Zero patch-attributable failures.** The detector fired exactly
nowhere outside its target shape across the full 1071-test surface —
the architectural-precision claim is now empirically closed, not
just argued.

---

# Stage G — "data even on UNKNOWN": partial coverage on external kill (2026-05-18)

## Why (user directive)

"k-induction 不收敛不能成为问题 — 即使汇报 unknown 我们也要有数据".
ESBMC over-approximates, so its coverage must be ≥ a concrete test
suite's; any `test > ESBMC` is an ESBMC under-report bug. The wall-(a)
pins were a concrete instance: the per-claim numerator IS computed
(base/forward resolves all 4 branch claims under the auto-routed CVC5)
but `report_coverage` (the stdout `Branch Coverage:` line) only runs at
a normal conclude/exhaustion point; the run is killed mid-inductive-
solve first, discarding the already-known number → orchestrator saw
nothing → false `test > ESBMC`.

## Root cause (mechanism, file:line verified this session)

- `--timeout N` ⇒ `signal(SIGALRM, timeout_handler); alarm(N)`
  (esbmc_parseoptions.cpp:577-578). Old `timeout_handler`
  (esbmc_parseoptions.cpp): `log_error → cleanup → _exit(1)`, no
  coverage. `report_coverage` only at conclude points
  (esbmc_parseoptions.cpp:2562/2585/2605) or max-k exhaustion (:2714)
  — all preempted by the kill.
- The regression harness does NOT even use `--timeout`:
  `testing_tool.py:137` `UNSUPPORTED_OPTIONS=["--timeout","--memlimit"]`
  strips it; it bounds via its own `ESBMC_REGRESS_TIMEOUT` then
  `os.killpg(SIGTERM)` → `_TERM_GRACE=3s` → `SIGKILL`
  (testing_tool.py:181-188), and on `TimeoutExpired` returns
  `stdout=None` ⇒ `_add_test` `self.fail()`s **unconditionally,
  without regex-matching the partial output** (testing_tool.py:243-248).
  So a perpetually-k-induction-timing-out test is an unconditional
  ctest failure in CORE *or* KNOWNBUG — it cannot be pinned green
  there. (The CORE flip attempted earlier was reverted to KNOWNBUG —
  the honest mode; this is a harness-architecture limit, not an esbmc
  defect.)

## Fix (esbmc root fix; the only src change of Stage G)

Async-signal-safe partial-coverage emit on BOTH the internal and the
external kill paths:

- `goto_coverage.{h,cpp}`: `static std::atomic` snapshot
  `branch_cov_active` / `total_branch_atomic` (= |all_claims|, set at
  instrumentation, pre-solve) / `covered_set_mode` / `live_reached` /
  `covered_run`.
- `bmc.cpp`: TWO mode-correct numerators, each a sound LOWER BOUND
  pre-report, EXACT after any `report_coverage` (re-synced there — the
  only place reached_claims is erased). DEFAULT: `live_reached` ==
  `reached_claims.size()` (canonical bmc.cpp:901), stored under
  `reached_claims_mutex` at the per-claim hook. COVERED-SET (what
  orchestrator.py always uses): `covered_run` == universe edges newly
  witnessed+persisted this run (`covered_set.emplace().second`, Item 2e
  hook) — a sound lower bound on the covered-set authoritative
  `|all_claims ∩ (covered_set ∪ reached)|`. The earlier single-
  `live_reached` design was a code-review HIGH finding (reached_claims
  .size() is NOT the covered-set authoritative); fixed before landing,
  both modes re-verified empirically.
- `esbmc_parseoptions.cpp`: `emit_branch_coverage_on_timeout()` —
  atomic loads + stack buffer + manual unsigned→decimal + one
  `write(2)`; NO malloc / iostream / std::set touch. Called from
  `timeout_handler` (SIGALRM, esbmc's own `--timeout`) AND a new
  `term_handler` (SIGTERM/SIGINT — `timeout(1)`, CI, orchestrator,
  testing_tool's killpg, ctrl-C), installed unconditionally
  (independent of `--timeout`, which external harnesses strip).
  SIGKILL is uncatchable → for that path only the Item 2e
  `--coverage-covered-set` JSON survives.

Output (sound LOWER BOUND, honestly tagged):
`Branch Coverage: 50% (partial: run terminated before verification
concluded)` with `[Coverage] / Branches : 4 / Reached : 2`.

## Empirical verification (captured, not inferred)

- SIGALRM arm: `esbmc … --timeout 20` → wall 0:25, emits the block,
  `_exit(1)`.
- SIGTERM arm: `timeout -s TERM 8 esbmc …` (NO `--timeout`, exact
  orchestrator/CI/harness path) → `ERROR: Terminated` → emits the
  block.
- End-to-end through the orchestrator's REAL `parse_coverage`:
  exact `run_one` invocation (`--coverage-covered-set` + `--timeout`
  + outer `timeout`) → `parse_coverage(out) = (4, 2, '50')` and
  `union.json` has 2 entries. Pre-fix this contract yielded
  `(None,None,None)` → 0 denominator contribution + silent drop =
  the `test > ESBMC` soundness bug. Post-fix the orchestrator's
  denominator includes the timed-out unit's static branches and the
  numerator is the crash-safe union ⇒ the project % is a SOUND LOWER
  BOUND, never an over-claim.
- Independent mechanism test
  `notes/Results/branch_cov/esbmc/mechanism_partial_cov_test.sh`
  (+ `.expected`): external-SIGTERM, asserts the partial line; PASS.
  Deliberately NOT a testing_tool test (see Root cause).

## Orchestrator consumption (chosen strategy; no harness change)

`orchestrator.py` already passes `--timeout` + outer `timeout` and
greps `^Branch Coverage:` / `^Branches` / `^Reached` — it consumes the
partial block with ZERO parse change. Added only honesty labelling:
`run_one` flags `(partial:` runs; per-run print shows
`[PARTIAL — lower bound]`; the project summary prints how many runs
were partial and that the aggregate is a SOUND LOWER BOUND.

## Net

The user-facing "data even on UNKNOWN" requirement is delivered for
the real comparison pipeline (orchestrator/CI/manual). The two
wall-(a) regression pins STAY KNOWNBUG (testing_tool architecture, not
esbmc) with headers documenting the above; the fix is corroborated by
the end-to-end orchestrator check + the independent mechanism test,
not a (structurally impossible) green ctest pin. Sharded
instrumentation fallback NOT needed (incremental snapshot + signal
emit suffices) — matching the user's "理论上完全用不到".

---

## Stage H — frontend AST-index perf fix (2026-05-19)

**Trigger.** User observed a stall during "前端转化". Measured (not
inferred): `--goto-functions-only` on `farming__FarmingPool.flat.sol`
(1.6 MB solast) → `GOTO program creation time: 9.585s`, total wall
9.70s, ~210 MB RSS = **100% frontend conversion, zero solver/symex**.
St1inch flat: **26.114s**. This is the concrete localisation of
`reference_solidity_frontend_ssa_inflation_gap` and the up-front cost
every large-flat coverage k-step pays.

**Root cause (42-sample gdb profile + code read).** Inclusive stacks:
`find_parent_contract` 27/42, `get_current_contract_name` 25/42,
`find_decl_ref`→`find_node_by_id` 8/42. Each does a full DFS over the
*entire* src_ast_json on every call; `get_current_contract_name`
(expr.cpp:4881 → `find_parent_contract`, deep-`==` at
solidity_convert_util.cpp:1231) is invoked per-expression ⇒
O(expr × full-AST). `find_decl_ref` re-scans every top-level node
per identifier.

**What shipped (verified).** A lazy memo inside `find_parent_contract`
keyed by `target.dump()` (the node's exact serialised content).
`find_parent_contract`'s result is a pure function of target CONTENT
(deep-`==` returns the enclosing contract of the FIRST content-equal
node; `root` is invariant — both callers pass `src_ast_json["nodes"]`),
so a content key is **bug-for-bug identical** to the uncached DFS while
being robust to the content-copies / sub-references / synthetic nodes
that defeat pointer- and (src,id)-keys. 3 files, +48/−5
(solidity_convert.{h,cpp}, solidity_convert_util.cpp);
`get_current_contract_name` left pristine.

| flat | before | after | Δ |
|---|---|---|---|
| farming/FarmingPool GOTO-creation | 9.585s | 6.404s | −33% |
| st1inch/St1inch GOTO-creation | 26.114s | 17.683s | −32% |

**Correctness gate.** `esbmc-solidity -R
'inherit|override|interface|using_for|library|modifier'` (the exact
find_parent_contract/get_current_contract_name-sensitive paths):
**110/110**, identical to the pre-patch baseline. Broader
struct/mapping/clone/cov_pilot sweep: **52/52** before the 5-min cap.
162 tests, **zero regressions**. cppcheck clean (no
unreadVariable/unusedVariable/variableScope). clang-format: only
clang-format-18 available locally (whole-file v18-vs-CI-Clang-11
drift); edits hand-matched to the file's Allman/2-space/80-col style.

**Rejected approaches (the failures ARE the findings).**
- *Stage-1 find_decl_ref fast path* — crashed (`type_error.305`):
  its scope+override+first-match semantics do not reduce to a safe
  O(1) probe. Reverted; ~7% potential, not worth the risk.
- *Pointer-keyed node→contract index* — 84% MISS (frontend passes
  content-copies, not the indexed objects) + permanent invalidation
  by an early `get_inherit_ctor_definition` push_back ⇒ ≈0 gain.
- *(src,id) memo key* — crashed (`namespacet::follow`): the frontend
  mutates AST nodes **in place** during conversion (cast insertion,
  type annotation), so (src,id) is not stable content-identity; only
  full `dump()` captures content-at-call-time.
- *Reroute get_current_contract_name → current_decl_contract
  (src-span)* — 2.9s/4.3s (fast) but **8 inheritance reds**: src-span
  gives the *declaring* contract; find_parent_contract's
  deep-eq-first-match gives the *structural/merged* contract, and the
  latter is what get_current_contract_name + inheritance depend on.

**1231 re-characterised (user decision: ship Stage 2 only).** The
plan's "latent correctness bug" at solidity_convert_util.cpp:1231
(`*node == target` deep-eq where the :1230 comment says "pointer
identity") is NOT a safely-isolatable bug: the 8-inheritance-failure
evidence proves the deep-content-eq **first-match** is the
load-bearing semantics get_current_contract_name + inheritance rely
on. The :1230 comment is wrong; the code behaviour is correct and
relied upon. Changing 1231 to pointer-identity is an unvalidated
semantic change that also destroys the content-keyed memo's
equivalence proof. User chose to drop the 1231 change and ship the
verified perf fix. (The misleading :1230 comment is left untouched
to keep this PR a pure, behaviour-zero perf change; a comment-only
correction is a separate trivial follow-up if desired.)

**Residual.** The remaining ~6.4s/17.7s is the deep-`==` DFS on the
first (uncached) query of each distinct node + the dump() cost; a
faithful sub-`dump()` key was disproven (in-place AST mutation). The
Reached:0 k-induction budget-burn class (FarmingPool/St1inch) is
orthogonal and unchanged by this frontend speedup.

---

# Stage I — nondet_string spurious-prune fix (memset) + github_2564 vacuous-pass co-fix (2026-05-19)

## Claim

The FarmingPool `Reached:0` (TWO_TRACK finding #1, the last
`test > ESBMC` under-report) is NOT a solver/k-induction-budget issue:
it is a **spurious path prune inside the `nondet_string()` operational
model**. The constructor's first action builds the ERC20 base-ctor args
via `stakingToken_.name()/.symbol()` → `nondet_string()`, whose
**constant-bound-33 zero-fill loop** is k-bounded; under the pin's
`--max-k-step 15` the k-induction base case prunes the entire
post-string path, so the 24 branch claims are unreachable ⇒ false 0%.

## Mechanism (file:line, read this session)

- Prune: `src/goto-symex/symex_goto.cpp:472-500` `loop_bound_exceeded`
  — when a loop exceeds the unwind/k bound and `!partial_loops`, with
  `no_unwinding_assertions` it emits an unwinding **assumption** and
  ALWAYS `cur_state->guard.add(negated_cond)` ⇒ for the data-exit-free
  zero-fill loop `i < 33`, k<33 ⇒ `guard.add(false)` ⇒ all subsequent
  assignments (constructor body + 24 branch claims) pruned.
- The loop: `src/c2goto/library/solidity/solidity_string.c:236` (old)
  `for (i=0;i<_ESBMC_NONDET_STRING_MAX+1;++i) _rand_str[i]='\0';`
  (`_ESBMC_NONDET_STRING_MAX=32` ⇒ bound 33). The sibling content-fill
  loop (:241, bound 32) has an `if(i>=len) break` nondet exit ⇒ only
  restricts to `len<=k`, never fully prunes — so the zero-fill loop is
  the sole culprit.
- memset is loop-free for this shape:
  `src/goto-symex/builtin_functions/memory_ops.cpp:737-807`
  `intrinsic_memset` — constant byte count + writable global +
  simplify on ⇒ single-shot symbolic array write, NO per-byte loop;
  only falls back to the `__memset_impl` loop on symbolic size /
  read-only target / `--no-simplify` (none apply here).

## Evidence (captured, not inferred)

- **Experiment 1 (k-ladder ground truth, no code change):** FarmingPool
  with `--max-k-step 40`: k=30..33 ⇒ `Not unwinding loop 35 ... line
  236`, `Reached:0/0%`; **k=34** (first k > bound 33) the message
  disappears, path opens; k=40 ⇒ `Branches:24 Reached:3 ... 50%` (then
  12/24 at higher budget). Proves the bound-33 zero-fill loop is the
  exact cut; `--max-k-step 15 < 34` ⇒ permanent prune.
- **Experiment 2 (memset, pinned `--max-k-step 15`, committed
  full-body solast):** baseline(loop) `Branches:24 Reached:0 0%`
  (WALL 10.36s harness-equiv) → fixed(memset) `Branches:24 Reached:12
  Branch Coverage:50%` on `--timeout 90` self-emit. line-236 loop
  gone (only the unrelated :241 content-fill loop remains).

## Dual-axis (soundness / completeness)

- Soundness: **unchanged**. `memset(buf,0,33)` is byte-exact-equivalent
  to the loop (same post-state); `intrinsic_memset` verified loop-free
  for this constant/writable/simplify-on shape. The historical
  `25489d6fbc/0e95c83fc5` concern (symbolic-bound loop → OOM) does NOT
  apply: the bound is a compile-time constant; a constant memset does
  not reintroduce an unbounded unwind.
- Completeness: **strictly improved**. Removes a spurious prune that
  caused (a) false coverage under-report (FarmingPool 0% vs ≥50%
  reachable — exactly the standing "test > ESBMC ⇒ investigate"
  methodology violation) and (b) **vacuous verification passes**.

## github_2564 — co-fix of an unmasked vacuous CORE pass

The fix unmasked that `github_2564` (CORE, Auction contract) only
"passed" because the same zero-fill prune fired under its
`--unwind 20 --no-unwinding-assertions` (silent-truncation combo,
batch `bc80114065`): pre-fix `Symex completed 0.001s (107 assignments)
VERIFICATION SUCCESSFUL` — verified essentially nothing. Post-fix the
path opens and `--unwind 20`/`--incremental-bmc` diverge. **Co-fix
(authorized):** migrate its test.desc to the canonical suite form
`--sol contract.sol --k-induction --max-k-step 20 --k-step 3`
(matches the 531-test `bf7671ab42` migration). Verified: genuine
`VERIFICATION SUCCESSFUL`, *Solution found by the inductive step
(k = 4)*, ~2.5s / 6123 assignments — a real proof, not a pruned stub.
This is a soundness improvement: a regression test that verified
nothing now actually verifies. github_2564 = Passed in the full gate.

## FarmingPool — wall-(a), accepted (user decision 2026-05-19)

With the correct committed full-body solast, baseline(loop) is a fast
**vacuous** KNOWNBUG-pass (10.36s, Reached:0 ⇒ `^Branch Coverage:
[1-9]` no-match). Post-fix it genuinely reaches 12/24 and *would* emit
`Branch Coverage: 50%` (all KNOWNBUG regexes match ⇒ the intended
KNOWNBUG→CORE flip) — but `regression/testing_tool.py:137`
`UNSUPPORTED_OPTIONS=["--timeout","--memlimit"]` strips `--timeout`,
and `_add_test`'s `if stdout is None: self.fail()` unconditionally
fails any harness-wall timeout (KNOWNBUG incl). So in-harness it
becomes a **wall-(a) timeout** — the same documented class as its
sibling `cov_pilot_st1inch_St1inch` (already a tolerated `***Timeout`
KNOWNBUG in both baseline and gate). This is a pre-existing orthogonal
harness limitation (Stage G ledger), NOT a memset soundness issue.
**User decision: keep the fix; accept FarmingPool as a documented
wall-(a) pin (option A).** `cov_pilot_farming_FarmingPool` left at its
committed state (KNOWNBUG, full-body solast) — unchanged.

## Structural-argument no-regression gate (full 1071, one run)

Fixed full esbmc-solidity = 24 fails. 22 identical to the baseline
fail set (pre-existing). The 2 deltas vs the (stale-solast-
contaminated) prior baseline: `napp_struct_array_of_struct_fail`
(string-surface=0 ⇒ memset produces byte-identical GOTO ⇒
structurally impossible; SMT-scale -j4 jitter — earlier passed 56s
isolated) and `cov_pilot_farming_FarmingPool` (the accepted wall-(a)
above). **github_2564 = Passed.** Net memset-attributable
**CORE→KNOWNBUG = 0**; one vacuous-KNOWNBUG-pass → documented wall-(a)
(accepted). The 3 napp `napp_state_2d_dyn_address_*` Passed→Timeout in
the contaminated diff were proven -j4 jitter (passed in -j1
isolation), string-surface=0.

## Tree state / residual

Changes (uncommitted, no commit authorization):
`src/c2goto/library/solidity/solidity_string.c` (zero-fill loop →
memset), `regression/esbmc-solidity/github_2564/test.desc`
(--unwind 20 --no-unwinding-assertions → --k-induction --max-k-step
20 --k-step 3). Forbidden-3 carry only their pre-existing session
M (untouched). `solidity_convert{.cpp,.h,_util.cpp}` carry the prior
uncommitted Stage-H perf memo (constant across baseline/fixed,
cancels in the gate). Residual: FarmingPool/St1inch coverage genuine
k-induction non-convergence is unchanged (the fix corrects the
*reporting* — no longer a false 0% / vacuous pass — not the
underlying budget-burn; that is the separate Reached:0 algorithmic
class, out of scope).

# Stage J — dispatcher contract-typed param soundness fix (2026-05-20)

**Claim.** Solidity dispatcher harness allocated contract-typed function
parameters as fresh `cpp_new` heap objects (`&dynamic_N_value`), which
are statically distinct from any contract pointer state-var (which is
its own `&dynamic_M_value`). Source pattern `if (param == state_var) {
body }` therefore lowered to a guard ≡ FALSE in the symex, making the
if-body vacuously unreachable. **All claims inside the body — including
`assert(false)` — were reported safe.** This is a real soundness
violation (false negative on bug finding), not a coverage-precision
gap.

**Mechanism (VCC, file:line, read this session).**
Source `solidity_convert_call.cpp:562-604` — `assign_param_nondet`,
contract-typed branch. Before: `get_new_object_ctor_call(base_cname,
empty_json, false, new_contract)` then `call.arguments().push_back`,
which emits a `cpp_new struct C` initializer in the dispatcher harness
body. Post-cpp_new, the ctor is called on the freshly-allocated object,
giving it a unique `_ESBMC_get_unique_address` and a fresh
`_ESBMC_bind_cname`. Meanwhile the caller-contract's state-var was set
in its own ctor to a separate fresh allocation. The two `dynamic_*_
value` symbols are different memory objects ⇒ pointer-equality on them
is statically false at the SSA layer (no SAT model can make the two
distinct heap symbols equal, regardless of nondet contents).

Empirical evidence: VCC dump of V2 minimal repro (`if (x == T) { if (b
< amount) revert() }`):
- `{-27} _ESBMC_Object_C.T = (IERC20*)(&dynamic_1_value)`
- `{-47} goto_symex::guard?0!0&0#1 == ((IERC20*)(&dynamic_2_value) ==
  _ESBMC_Object_C.T)` (= the outer if's path constraint)
- Inner VC: `... ∧ goto_symex::guard => assertion_at_L10`. With guard
  ≡ FALSE, the implication is vacuously TRUE; the counterexample search
  `... ∧ guard ∧ ¬assertion` is UNSAT; reported PASSED (= NOT
  reached); body claims never enter `reached_claims`.

**Flag-combo sweep refutes "flag misuse" as cause.**
On the soundness pin `dispatcher_contract_param_eq_state_var_unsound_
fail` (real EVM: `c.f(c.T())` triggers `assert(false)`):

| combo                                | verdict (sound = FAILED) |
|--------------------------------------|--------------------------|
| `--k-induction --unlimited-k-steps`  | SUCCESSFUL ❌            |
| `--k-induction --max-k-step 5`       | SUCCESSFUL ❌            |
| `--incremental-bmc --max-k-step 5`   | UNKNOWN ❌               |
| `--unwind 5 --no-unwinding-assertions` | SUCCESSFUL ❌          |
| `--unwind 20 --no-unwinding-assertions` | SUCCESSFUL ❌         |
| `--unwind 5 + --bound`                 | SUCCESSFUL ❌          |
| `--k-ind + --bound`                    | SUCCESSFUL ❌          |
| `--k-ind + --cvc5`                     | SUCCESSFUL ❌          |
| `--k-ind + --z3`                       | SUCCESSFUL ❌          |
| `--unwind 5 + --boolector`             | (couldn't reach a result) |
| `--k-ind + --symex-pointer-check`      | SUCCESSFUL ❌          |

All 4 solvers, all unwind/k-induction modes — uniformly unsound. Not
flag-induced. Control: `dispatcher_address_param_eq_state_var_baseline
_fail` (same shape but `address` (uint160) param) — `VERIFICATION
FAILED` on ALL combos. The differential isolates the bug to contract-
typed parameter modelling.

**Fix (single edit, `solidity_convert_call.cpp:assign_param_nondet`).**
Replace the unbound branch's `get_new_object_ctor_call(...)` +
`call.arguments().push_back(new_contract)` with `get_nondet_expr(
pointer_typet(symbol_typet(prefix + base_cname)), nondet_ptr)` +
`call.arguments().push_back(nondet_ptr)`. The harness now emits a free
nondet pointer of the declared contract/interface type. The SAT solver
picks any value at evaluation time:
- pick aliasing a tracked `_ESBMC_Object_<C>` singleton (or a state-
  var's pointer of compatible type) ⇒ `if (param == T)` body
  reachable, bugs inside become visible;
- pick a value distinct from every tracked instance ⇒ preserves the
  c1/c2 distinctness scenario the original commit was guarding against
  (the SAT solver picks independent nondet values for each invocation
  of `assign_param_nondet`'s contract branch).

`is_bound` branch left unchanged: `build_bound_drive_helper` still
fresh-allocates because the dispatcher loop *needs* a real allocation
to host the state mutations it drives. Bound-mode soundness gap on
this pattern is a separate follow-up.

**End-to-end empirical validation.**

1. Soundness pin: `VERIFICATION FAILED` ✓ (was SUCCESSFUL).
2. Address-typed baseline: unchanged `VERIFICATION FAILED` ✓.
3. V0 minimal coverage (1/4 → **4/4** = 100%) ✓.
4. V1 sanity (uint-only param, no contract param): **unchanged** 2/2 ✓.
5. FarmingPool re-run (5min, whole-unit, 22-exclude): reached 40 → **50**
   (`Branch Coverage 66.67% → 83.33%`); `union.json` now contains
   BOTH directions of L4200 and L4202 (the previously-vacuous
   `NONDET < totalSupply() + amount` claims).
6. TWO_TRACK rescore on `farming` benchmark: FarmingPool **10/12 →
   12/12 = test (100%)**; Track A overall: **5= 0<** (was 4= 1<;
   originally 0= 5<). The "ESBMC < test" residual on this benchmark
   is fully eliminated.
7. 53-test candidate regression sweep (tests with contract-typed
   function parameters; cov_pilot/esol_clone/cross_contract/bound/
   reentrancy/tod): 51 PASS, 2 FAIL — `cross_contract_3` and
   `cover_iterable_mapping_1`. **Both are pre-existing failures**
   verified by stash-revert: their `^VERIFICATION` lines are byte-
   identical with and without the fix.
8. Extra category sweep (erc20/reentrancy/napp): 13 timeouts, all in
   `napp_*` (documented SMT-scale class per
   `project_napp_smt_scale_bound`); pre-existing.

**Tree state.** Working-tree changes (not committed; no commit
authorization received yet):
- `src/solidity-frontend/solidity_convert_call.cpp` (single edit)
- `regression/esbmc-solidity/dispatcher_contract_param_eq_state_var_unsound_fail/`
  (CORE; new; renamed from `_unsound_knownbug` after fix flipped it)
- `regression/esbmc-solidity/dispatcher_address_param_eq_state_var_baseline_fail/`
  (CORE; new; sound control)
- this file (notes only).

Forbidden-3 (`solidity_blockchain.c`, `solidity_misc.c`,
`solidity_language.cpp`) untouched as required. Prior Stage-H perf
files (`solidity_convert{.cpp,.h,_util.cpp}`) unchanged from pre-
session state.

