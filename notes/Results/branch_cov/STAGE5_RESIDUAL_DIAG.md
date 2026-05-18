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
