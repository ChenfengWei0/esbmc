I read everything named, in full (goto_coverage.cpp and bmc.cpp in complete sequential chunks; goto_coverage.h in full because the decision-record contract lives there). I did not build, run ESBMC, or modify anything.

---

# VERDICT TABLE

| # | Question | Verdict |
|---|---|---|
| 1 | THE UNIT — same set operation on the same universe? | **NOT COMMENSURABLE** (operation identical; the two `reached` sets fed into it are not) |
| 2 | THE SCOPE — is "reconciled by the shared intersection" true? | **NOT COMMENSURABLE** (the argument is invalid as stated; its numeric effect today is UNDETERMINED) |
| 3 | WHAT COUNTS AS A DECISION | **NOT COMMENSURABLE** (source itself documents the divergence for `require`) |
| 4 | TX DEPTH AND HARNESS | **NOT COMMENSURABLE** (tx depth *is* equal; loop bound and wall-clock budget are not, in opposite directions) |
| 5 | THE DENOMINATOR / degradation | **NOT COMMENSURABLE** — and branch_gate's own claimed compensation is **described but not implemented** |
| 6 | THE F-ONLY PROJECTION | **UNDETERMINED**, with one unguarded flattering hole (`file` field ignored) |

---

# 1. THE UNIT — NOT COMMENSURABLE

**What is genuinely identical.** Both sides call the same `ast_decisions.canonical_decisions()` on the same flat, restrict with the same `collect.is_project_own_marker`, and apply the same `min(|reached ∩ c_lines|, |c_lines|)`:

- baseline: `collect.py:199-213` (own_markers, `canon_flat_lines`), `collect.py:332-334` / `:502-503` (`union_lines & c_lines`, `min(...)`)
- product: `branch_gate.py:243-252` (`canonical_in_scope`, imports `collect` rather than restating it), `branch_gate.py:226-240` (`per_file_capped`)
- and `branch_gate.py:427-455` re-derives the denominator per in-scope file and compares it to the locked JSON.

That part is clean and is the strongest thing about this gate. **The set operation is the same. The universe it is applied to is the same. The failure is upstream of it: the two `reached` sets are produced by different definitions of "a decision".**

**The three ways the inputs differ.**

(a) *Location filtering.* `branch_coverage()` refuses to instrument any instruction whose filename is not in `location_pool` (`goto_coverage.cpp:1558-1562`, `:1572-1579`). The path-coverage decision recorder has **no such test**: the Phase-1 snapshot loop (`:3686-3717`) and all three DFS fan-out sites (`:4264-4341` GOTO, `:4364-4437` RETURN, `:4442-4493` ASSIGN) branch on instruction *kind* alone. `location_pool` is consulted in `solidity_path_coverage()` only at unit granularity (`body_in_user_src`, `:2829-2835`, and `in_user_src` at `:3413-3422`) and for `is_lost_decision` (`:3895-3902`). So the product's decision set is filtered one level coarser than the baseline's.

(b) *Cap direction.* The cap is `min(reached, denom)`. The gate is `ours >= bar`. Therefore **every product-side over-count is either a free win or invisible**, and there is no set comparison that could catch it — METHODOLOGY §4 records only a *count* for the baseline, never per-decision identity, which `branch_gate.py:31-36` states explicitly. The gate is structurally incapable of detecting the failure mode that would flatter it most.

(c) *Spec/implementation drift, latent.* METHODOLOGY §4 (`METHODOLOGY.md:91-108`) defines ESBMC reach as "the number of unique flat-lines reached inside F's block, capped by the file's canonical decision count" — i.e. **all** union lines in the block. `collect.py` implements the stricter `union_lines & c_lines`. They coincide only because branch coverage's covered-set entries happen to be probe lines. Both sides today mirror the *code*, not the *spec*; anyone who ever "fixes" collect.py toward the locked spec text raises the bar and the gate silently changes meaning.

---

# 2. THE SCOPE — NOT COMMENSURABLE (the argument is invalid)

The claim is at `branch_gate.py:179-185` and mirrored at `pathcov_collect.py:98-108`: the two scoping mechanisms are reconciled "NOT by replicating the filters but by intersecting with the canonical project-own decision lines".

**The intersection is a one-sided operation.** It removes decisions *outside* canon. It cannot:
- restore a decision the baseline was **forbidden** to instrument but that **is** in canon;
- remove a decision the product counted that the baseline structurally could not.

So the claim is true only under an unstated premise:

> `exclude_contracts` ∩ {contracts declared in a project-own in-scope file with ≥1 canonical decision} = ∅

Nothing in either script checks this premise, and **`own_contracts.json:23-41` documents a violation of it by name**: `BalanceLib` is declared in aqua's own `src/libs/Balance.sol`, is excluded from branch coverage (`ownContracts: ["Aqua"]`), and the marker rule that produces canon *accepts* that file. The note asserts the instance is numerically inert because `src/libs/Balance.sol` contributes 0 canonical decisions. That is true for aqua and unverified for the other five.

**The concrete failing scenario.** A library or helper contract `L` declared inside a project-own file that has canonical decisions, with `L ∉ ownContracts`:
- baseline: `emit_decision` returns at `goto_coverage.cpp:1619-1623` before `all_claims.insert`, so `L`'s decisions are in **neither** its numerator nor any probe — permanently unreachable for the bar;
- product: `exclude_contracts` is read **nowhere** in `solidity_path_coverage()` (verified across the whole function; the only uses in the file are `branch_coverage`'s, and the invocation contract's dispatch list, `path-coverage-invocation-contract.md:151-167`, does not wire it), and `L`'s body is spliced into its callers by `expandable_callee` / `expand_into` (`goto_coverage.cpp:2850-2879`, `:2943-2980`), carrying `L`'s own source locations. `L`'s decision lines therefore enter the product's numerator, survive the canon intersection, and score.

**Favours: the product. Strictly.** This is the single cleanest "difference that flatters the product side" in the pipeline.

**A second scope divergence, favouring the baseline, but still an incommensurability.** The two sides use *different keys* for "belongs to contract C":
- branch coverage: `it->location.get("sol_decl_contract")` — the *lexically declaring* contract (`goto_coverage.cpp:1607-1610`, doc at `goto_coverage.h:683-690`);
- path coverage: `contract_of(f_it->first.as_string())` — the *mangled unit id's* contract (`goto_coverage.cpp:3428-3430`, `:3030-3033`, helper at `:6603-6615`).

Under inheritance merge-by-copy these disagree for an inherited method. The baseline sidesteps it (`--coverage-whole-unit` leaves `scope_contract` empty), so only the product applies a contract filter at all — and it applies it with the other key.

**Third, and larger in practice:** `collect_pair2` runs each own contract as *its own* dispatcher entry — `--contract <cname> --focus-function <fname>` where `cname` is the declaring contract (`collect.py:470-473`). `pathcov_collect.esbmc_cmd` always passes `--contract <primary>` (`pathcov_collect.py:286-288`, `:103-107`). For an own contract that is not a base of the primary and not a library, the product enumerates **no unit at all** for it; the baseline gives it a full harness. On farming, 14 of the 26 canonical decisions live in `Distributor.sol` / `FarmingLib.sol` / `FarmAccounting.sol` / `UserAccounting.sol` (`esbmc_farming.json:406-482`) and the product can only reach them by inlining into `FarmingPool` units. Favours the baseline — but it means the two numbers describe different harnesses, not different reach.

---

# 3. WHAT COUNTS AS A DECISION — NOT COMMENSURABLE

The source itself settles this. `goto_coverage.h:583-600`, verbatim:

> `branch coverage       "!(a != 0)"   /  "a != 0"` / `path coverage       "!(!(a != 0))"  /  "!(a != 0)"` … "The guards are one `not` apart because `--solidity-path-coverage` turns on the revert-observation gate, which lowers `require` to a different goto shape."

Repeated at `bmc.cpp:1601-1606`. **The two modes do not lower `require` — the commonest canonical decision kind in these contracts — to the same goto.** That is why the projection is forced to join on line, and joining on line makes the divergence *unobservable* rather than *absent*.

Construct by construct:

| Construct | Baseline (`branch_coverage`) | Product (path DFS) | Compensated by `branch_gate.py`? |
|---|---|---|---|
| `if` / `while` / `for` header | `is_goto() && !is_true(guard)` → 2 arms (`:1676-1694`) | same shape (`:4264-4341`) | n/a — these agree |
| `require` | different lowering (see above); if it stays an **ASSUME**, the instrumenter has **no arm for it** — the chain at `:1664-1707` handles ASSERT / GOTO / ASSIGN / RETURN and falls through on ASSUME → **no probe, cannot ever be in the bar** | the revert-observation widening makes it a real branch; `is_lost_decision` (`:3895-3902`) exists precisely to detect when it *hasn't* | **No.** Nothing on either side reports it |
| short-circuit `&&`/`||`, ternary | `collect_short_circuit_decisions`, **no cap** (`:1696-1707`, `:1459-1481`) | same collector but **hard cap `SC_DECISION_MAX = 12`**; a site above it is dropped entirely (`:3706-3710`, `:4453-4456`) | **No.** `sc_sites_over_cap` is a `log_warning` only (`:5931-5938`), absent from cov-report.json, not parsed by `pathcov_collect.py`, not printed by `branch_gate.py` |
| implicit revert (overflow, div-0) | off (`no-standard-checks`) | off | excluded from canon anyway (METHODOLOGY §2) |
| modifier body | one physical copy, folded by `location.as_string()` (`:1625-1635`) | expanded copies collapse at line level | agrees |
| internal call | callee instrumented **once, in its own function**, reached through the call | **physically spliced** into every caller (`:2213-2258`), or **withdrawn** by degradation / depth bound | see §5 |
| synthesised ABI value gate | does not exist | real decision, location **copied** from the unit's first body instruction (`:3527`, `:3551`) | **Yes** — flagged at source (`sol_abi_value_gate`), emitted at `bmc.cpp:1609-1613`, dropped at `branch_gate.py:217-219`. This one is handled correctly |

---

# 4. TRANSACTION DEPTH AND HARNESS — NOT COMMENSURABLE

**Transaction depth is genuinely equal, and the contract's reasoning holds.** `path-coverage-invocation-contract.md:30-34` (`solidity-path-coverage ∉ unbounded_modes`), `:60` (the composition table), `:76-80`; corroborated by `bmc.cpp:686-725` and `bmc.cpp:1936-1945`. Branch coverage gets bound 0 → `while(nondet)` → back-edge rewritten to SKIP → one transaction; path coverage at `--solidity-max-tx 1` → one straight-line transaction. Both under `--focus-function`, so cross-function state is out for both. **This axis is commensurable.**

**Two other axes are not.**

**(a) Loop bound — favours the baseline.** Baseline: `--k-induction --unlimited-k-steps` (`collect.py:64-65`), and per `path-coverage-invocation-contract.md:209-217`, every k-induction phase *overwrites* `unwind` with the current `k_step`, growing until the timeout. Product: the dispatch installs `--unwind 4` and then sets `no-unwinding-assertions` **unconditionally** (`:165-179`), i.e. executions past 4 iterations are **assumed away**, not reported; `path_cov_unwind = 4` bounds the DFS, the call-expansion depth and the extcall re-entry depth all at once (`goto_coverage.h:676-682`, `goto_coverage.cpp:2448-2453`).
*Name the decision:* any decision reachable only at loop iteration ≥ 5 — e.g. a guard inside a `for` over an array of length > 4 — is reachable by the baseline and **structurally unreachable** by the product.

**(b) Wall-clock budget — favours the product, and is documented nowhere.**

| | per focused method | source |
|---|---|---|
| baseline (the bar, Pair 2) | `--timeout 60` inner, 90 s outer | `collect.py:74-78` |
| product | **no inner timeout**, 300 s outer | `pathcov_collect.py:69`, `:29-33` |

That is 5× the solver budget per unit, on the side being measured. It is not hypothetical: aqua's `dock` run consumed `"wallSeconds": 60.04, "exitCode": 1` (`esbmc_aqua_Aqua.json:69-79`) — the baseline was cut off mid-solve on a real method. Nothing in `branch_gate.py`, `METHODOLOGY.md` or `pathcov_collect.py` mentions this asymmetry, and `pathcov_collect.py:29-33` justifies "no `--timeout`" purely on the partial-result gate, never noting that it also removes the budget cap the baseline had.

**(c) A binary asymmetry that subsumes both.** The locked baseline was produced by an older ESBMC; the product runs on today's. METHODOLOGY §8.3 makes "the binary changed" the *only* legitimate reason for reach to move — and the pathcov tree still holds `index.prefix-buggy-frontend.json` / `reports.prefix-buggy-frontend/` in every benchmark directory, i.e. the frontend changed *during this very collection*. **The bar and the product were not measured with the same tool.** `branch_gate.py` never compares `meta["flatInput"]` against `base["flat"]`, and there is no build identity recorded on either side.

---

# 5. THE DENOMINATOR — NOT COMMENSURABLE, and the compensation is **not implemented**

The denominator itself is fine: AST-derived, identical on both sides, cross-checked per file (`branch_gate.py:427-455`). Degradation and the depth bound do not touch it.

What they touch is the **product's numerator**, and `branch_gate.py:187-192` admits it:

> "STILL NOT HANDLED … internal calls withdrawn by degradation or the call-depth bound remove those decisions from every path of the unit while branch coverage still counts them. `degraded_call_sites` names them in the log; **the count is reported beside the gate** rather than folded into it."

**That last clause is false.** Tracing it end to end:

- `degraded_call_sites` is populated at `goto_coverage.cpp:3129` and surfaced **only** by `log_warning` at `:3133-3146` / `:3203-3214` / `:4562-4602`. It appears **nowhere** in the cov-report.json emission (`bmc.cpp:1321-1965`).
- `pathcov_collect.one_run` (`pathcov_collect.py:112-179`) records only: exit code, `killedByOuterTimeout`, `reportPresent`, the `instrumented N complete path(s) across M unit(s)` regex, `"are internal/private and are therefore not units"`, `"No verification targets"`, and five summary fields from the report. **It does not capture the DEGRADED warning, the residual-call warning, or `sc_sites_over_cap`.**
- `branch_gate.main`'s "What the product side actually saw" table (`branch_gate.py:399-406`) prints runs / reports / no-report / killed / F claims / F-without-sequence / steps / unrecorded / ABI-gate-dropped. **No degradation column. `degraded_call_sites` is not read anywhere in branch_gate.py.**

So the one hazard the gate explicitly says it discloses is invisible in its output. **It is not the only one.** The same is true of:
- `sc_sites_over_cap` (`:5931-5938`) — decisions dropped from the product's set;
- residual unexpanded **unit** callees (`:3234-3253`) — which mark **every path of the affected unit** a named obstacle;
- `named_obstacle_paths` and `truncation_weakened` (`:5723-5751`, `:5844-5883`).

**And the last one is a flatterer.** In `bmc.cpp`, the obstacle detail is emitted **only inside `if (tri == "U")`** (`:1447-1475`). An **F** claim in an obstructed unit gets `status: "F"`, a full `decisions` array, and **no obstacle marker anywhere in its entry**. `branch_gate.pathcov_reached_flat_lines` counts it like any other F. So the product's numerator credits reach from paths that the tool's own `named_obstacle_paths` says "must not be turned into a test" (`goto_coverage.h:385-403`) — and the report gives the gate no way to know.

*Direction of each:* degradation, depth bound and the SC cap **deflate** the product; obstacle-F counting **inflates** it. None of the four is measured. The net is unknown, which is exactly why the disclosure was promised.

---

# 6. THE F-ONLY PROJECTION — UNDETERMINED

**Genuinely reached but not counted** (deflating, all real):
- decisions inside withdrawn / unexpanded calls — executed by symex, absent from the path identity (`goto_coverage.cpp:2959-2966`);
- SC sites over the 12-operand cap;
- paths dropped at the goal cap or the `enc >= 2^62` length cap (`:4286-4290`, `:3987-3992`);
- an F whose `path_depth` is 0, or whose unit's table is missing: `bmc.cpp:1553-1564` requires `dp->second > 0`, otherwise **no `decisions` key at all** → counted in `f_without_sequence`, which `branch_gate.py:157-160` says "Must be 0" but which only downgrades the verdict to `"PASS (partial)"` (`:386-388`).

**Counted but not genuinely reached** — three routes, in ascending order of concern:

1. **The ABI gate.** Correctly handled, both ends. Not a hole.
2. **`unrecorded_prefix_enc`.** `bmc.cpp:1571-1584` emits a hole entry; `branch_gate.py:214-217` skips it. Correct as far as it goes. But note `solidity_path_coverage()`'s clear-block (`:2410-2428`) clears `path_decision_depth` and 14 other statics and does **not** clear `path_decision_table` / `path_decision_index`. If the pass ever runs twice in one process (the header itself warns "report_coverage can run more than once (k-induction phases)", `:1106-1109`), a stale per-unit table maps prefix `enc` values to **sites from an earlier instrumentation** — a *wrong* site, not a missing one, and therefore not a hole and not visible. Not reachable under the current invocation (base-case forced, no k-induction), but one flag away.
3. **The unguarded one, and it flatters the product.** `bmc.cpp:1589-1593` emits `file`, `line`, `column`, `function` per decision step. `branch_gate.pathcov_reached_flat_lines` (`:220-222`) reads **`e.get("line")` and nothing else** — no `file` check — and pools every line from every report of a benchmark into one flat set. That is safe only if every decision step's `file` is the flat. Per §1(a), the product's decision recorder applies **no `location_pool` filter**, and `body_in_user_src` (`:2829-2835`) admits a callee body if *at least one* instruction is in the pool — so a spliced body's non-flat instructions come along. Any such decision at, say, line 137 of a c2goto model scores against canonical flat-line 137. The baseline's parser is equally file-blind (`collect.py:167-177`), but the baseline is protected in C++ by `location_pool`; the product has no equivalent, and `branch_gate` declines the one-line check that would substitute for it.

---

# WHAT WOULD HAVE TO BE MEASURED

Each item names the run and the exact number to compare.

**A. Re-collect the baseline with the current binary (blocking, and it subsumes several others).**
Run `collect.py esbmc <bench>` for all six on today's build. Compare `per_function.total.esbmcReached` old vs new, per file. Anything that moves means the locked bar is stale and the gate has been comparing two binaries.

**B. Equalise the solver budget.** Re-run the baseline's Pair 2 with `--timeout 300` and outer 330 s (matching `pathcov_collect`'s per-run budget). Compare `per_function.total.esbmcReached` at 60 s vs 300 s. If it rises, the current bar is a timeout artefact and the gate's margin is spent buying 5× the solver time.

**C. Test §2's unstated premise, per benchmark.** For each bench, compute the set difference {top-level contracts in the flat} − `ownContracts` (the exclude list), intersect it with {contracts declared inside a marker that `is_project_own_marker` accepts}, and sum `canonical_decisions()` over the lines those contracts own. **If that sum is > 0 for any bench, the product can score decisions the bar cannot.** `own_contracts.json:23-41` already asserts it is 0 for aqua/BalanceLib; the other five are unmeasured. This is a pure AST computation — no ESBMC run needed.

**D. Instrumentation gap, per file, all six.** Compare `no_function.perFile[i].esbmc.instrumented` against `astDecisions`. On aqua (8 vs 8, `esbmc_aqua_Aqua.json:136-142`) and farming (2/1/12/5/6, all equal, `esbmc_farming.json:406-482`) they match, which is weak evidence that no canonical decision is invisible to branch coverage on those two. Do it for EscrowDst, EscrowSrc, limit_order_protocol, st1inch. **A file where `instrumented < astDecisions` is a file where the bar is structurally handicapped and the product's number over it means nothing.** (Caveat: `instrumented` is `--show-claims` lines bucketed to the block without a canon intersection, `collect.py:336-339`, so equality is suggestive, not proof.)

**E. Turn the missing disclosure into a number.** Either add `degraded_call_sites`, `sc_sites_over_cap`, `named_obstacle_paths` and `truncation_weakened` to the `summary` block of cov-report.json, or have `pathcov_collect.one_run` regex the three existing `log_warning` lines into its `runs.jsonl` record. Then report per benchmark: *how many call points were withdrawn, in how many units, and how many F claims belong to an obstructed unit.* Until that number exists, `branch_gate.py:187-192` describes a compensation that does not occur.

**F. Close the `file` hole and see if it was firing.** Count, per benchmark, the decision steps in `reports/*.json` whose `file` differs from the flat's basename. **If that count is non-zero, the current product numerator is contaminated** and the correct numerator is the one computed after adding `e["file"]` to the intersection key.

**G. Quantify the loop-bound gap.** Re-run the product at `--unwind 8` and `--unwind 16` (fingerprint changes, so the covered set is discarded — that is fine and automatic, `goto_coverage.cpp:2491-2505`). Compare `ours` per file at unwind 4 / 8 / 16. If it rises, the bar's `--unlimited-k-steps` was exploring a strictly larger loop space and the two sides' state spaces differ measurably, not just formally.

**H. The one honest set comparison available.** The baseline's per-decision identity is unrecoverable from the locked JSON, but it is recoverable from the *runs*: `union.json` records `{cond, loc}` per covered edge (`goto_coverage.cpp:90-114`). Re-collect the baseline (item A) keeping `union_pair2.json`, and compare the **sets** `{lines(union_pair2) ∩ canon}` versus `{lines(F decisions) ∩ canon}`, per file. **This is the only measurement that can detect product-side over-count, and it costs one re-run that item A already requires.** Report the symmetric difference in both directions — not just the counts.

---

# THE PLAIN ANSWER

If the gate reports PASS on all six benchmarks tomorrow, it entitles us to say exactly this: *on six 1inch contracts, the set of canonical AST decision lines touched by complete paths that our enumerator witnessed with a counterexample is, per file and after capping, at least as large as the set touched by ESBMC's own branch-coverage probes in the locked 2026-05-20 dataset.* That is a real and non-trivial statement, and the shared AST denominator, the shared scope rule and the per-file cross-check make it defensible on the denominator side. It entitles us to nothing beyond that, and in particular not to "complete-path enumeration reaches at least as much as branch coverage": the two numbers were produced by different binaries, at different solver budgets (60 s versus 300 s per method), under different loop bounds (k-induction unbounded versus a forced `--unwind 4` with over-bound executions assumed away), from different harnesses (each own contract as its own dispatcher entry versus `--contract <primary>` throughout), and over decision sets that the source code itself says disagree on `require` — with the product side counting decisions from contracts the baseline was forbidden to instrument, counting F paths that the tool's own obstacle machinery forbids turning into tests, and losing decisions to a degradation mechanism whose disclosure `branch_gate.py` promises and does not implement. Because the numerator is capped at the denominator and only a count is comparable, a PASS is equally consistent with the product measuring more, measuring the same, and measuring something else that happens to be larger — and nothing currently in the pipeline can tell those apart. For a paper's headline claim, the minimum repairs are A (same binary), B (same budget), and H (the set comparison, not the count); C, E and F are cheap and each closes a way the current number could be flattering us.
