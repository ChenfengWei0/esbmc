# `--solidity-path-coverage` x bounding strategy: the two unwind bounds never
# agree, nothing checks, and `--unwind N` is inert

**VERDICT. There is no combination of `--solidity-path-coverage` with
`--k-induction`, `--incremental-bmc`, `--falsification`, `--termination`,
`--loop-invariant` or `--inductive-step` that the collector may use. Pass the
pass's own bound — `--unwind N`, or nothing and take its default 4 — and NO
strategy. One near-miss exists (`--incremental-bmc --unwind N --base-k-step N`)
and §6 states exactly why it is still not worth taking.**

## 0. What this adds over what is already written down

`notes/path-coverage-invocation-contract.md` already establishes, **from source
only**, that `do_bmc_strategy` overwrites `unwind` with `k_step` (§6, §10.4) and
that the covered-set fingerprint therefore records a `loop_bound` the run did not
use (§13.3 item 3). None of that is re-derived here; it is cited.

What is new in this file:

1. **The measurement.** §6 of the contract note ends "Any such combination has to
   state which bound it actually ran at." Answered: under `--k-induction` it ran
   at **2**, for every `--unwind N` in {1,2,3,4,6,8}; under `--incremental-bmc`
   it ran at **1..50**, for every N. `--unwind N` never reached symex.
2. **`--k-induction` changes what is INSTRUMENTED**, not just the bound
   (§3, Fact 5). It converts the `--focus-function` unit itself into a NAMED
   OBSTACLE. This is not in any existing note and is the strongest single reason
   to refuse it. It is isolated to the GOTO transform, not the strategy loop.
3. **A second recorded-wrong value:** the report's own `summary.bound.unwind` is
   the last `k`, not the bound the goal set was built for (Fact 6). The contract
   note has the fingerprint's `loop_bound`; this is a different field in a
   different artefact.
4. **A correction to the contract note's §10.4 table**, which groups
   `--base-case` / `--forward-condition` / `--inductive-step` as "4 — agree by
   accident". Measured, they behave in three different ways and two of them lose
   the deliverable outright (§5).

## 0.1 Provenance, and a caveat that has to be read first

**The binary was snapshotted before the first run and never changed:**
`cp build/src/esbmc/esbmc <scratch>/esbmc_snapshot_unwind`, md5 `a2fa5ebf…`,
ESBMC 8.2.0. Every cell in every table below is that one file. That is the only
guarantee that the cells are comparable to each other, and it is the reason the
snapshot was taken.

**The working tree moved three times while these runs were in flight.** HEAD went
`ec85590c8f` → `adb4176371` → `e1410b160e` → `0e149c84f8` → `d09536838a`, and
`src/goto-programs/goto_coverage.cpp` grew by **+178/-15** lines over that span
(`git diff --stat ec85590c8f HEAD -- src/`: it is the *only* `src/` file that
changed). Consequences, stated rather than glossed:

* **The measurements describe the pre-`d09536838a` build.** No run was repeated
  after the tree moved and nothing was rebuilt. `d09536838a` adds the
  `UNDECIDED-TRUNCATED` third state to the stage-2/3 *gates*; none of the cells
  here uses `--path-cov-certify` / `--path-cov-outer-box` / `--path-cov-assert`,
  so the enumeration path these runs exercise is not the code that commit
  touched — but that is an argument, not a re-run. **UNVERIFIED: whether these
  numbers reproduce on a build of `d09536838a`.** Re-running the matrix against
  a fresh build is the cheap way to close it (the whole matrix is ~35 min
  excluding the `--incremental-bmc` column).
* **Every `goto_coverage.cpp` line number below was re-read by me at
  `d09536838a` immediately before writing**, because two subagents reported
  numbers for that file that were stale by +163 and by +2 respectively. Line
  numbers for the other files are valid at both ends of the range, since
  `git diff --stat` shows no other `src/` file changed.

## 0.2 Target and invocation

```
<snapshot> notes/coverage/inputs/aqua__Aqua.flat.sol.solast \
  --sol notes/coverage/inputs/aqua__Aqua.flat.sol \
  --contract Aqua --solidity-path-coverage --solidity-max-tx 1 \
  --focus-function dock --memlimit 6g --cov-report-json   [cell deltas]
```
one esbmc at a time, `setsid timeout -k 30s 600s`. `Aqua.dock` because it is the
unit with a known truncation-sensitive path set (F=2 normally, F=0 once a library
loop stops being folded — `notes/coverage/certify-vs-assert-vacuity.md`).

**Concurrency guard — the one these drivers shipped with was blind.** They
originally gated on `pgrep -x esbmc`. `pgrep -x` matches `comm`, which the
kernel truncates to 15 characters, so the snapshot binary — named
`esbmc_snapshot_unwind` precisely to survive a concurrent rebuild — presents as
`esbmc_snapshot_` and **never matches**. The guard reported zero while multi-GB
solvers were live. The cells here were nonetheless safe (they run strictly one
at a time, and `MemAvailable` was checked out of band at 36-41 GB against a
6 g `--memlimit`), but the guard did not establish that. It is now
`notes/coverage/scripts/esbmc_gate.py`, which reads `/proc/<pid>/cmdline` and
matches on the flag. It corrects one thing `esbmc_watch.py` does not: a
`timeout -k 30s 600s <esbmc> … --solidity-path-coverage …` wrapper carries the
flag in its own cmdline, so a single wrapped run counts as two — observed live
(`pid 307282 RSS 2 MB` the wrapper, `pid 307283 RSS 2237 MB` the solver). Both
arms of the gate were proved to fire (exit 2, loud) rather than assumed to work
because they were silent.

Drivers `notes/coverage/scripts/unwind_vs_strategy{,_isolate,_align}.sh`;
readers `..._summarize.py` (report fields) and `..._phases.py` (whole-file scan
of each run log for its structural lines). **Verdicts are read off
`cov-report.json`, never off the exit code** — `notes/coverage/option-matrix-round1.md`
result 3 established exit codes are not comparable across strategies, and this
matrix reproduces it (every no-strategy cell exits 1 with F=2; every k-induction
cell exits 0 with F=2).

---

## 1. The six facts

### Fact 1 — the pass installs `--unwind 4` when the user gives none
`src/esbmc/esbmc_parseoptions.cpp:4288-4304`:
```cpp
      if (cmdline.isset("unwind"))
      {
        int u = atoi(cmdline.getval("unwind"));
        if (u > 0)
          tmp.path_cov_unwind = static_cast<size_t>(u);
      }
      else
      {
        options.set_option("unwind", std::to_string(tmp.path_cov_unwind));
```
Rationale at `:4274-4281` (a Solidity external call is nondet re-entry into the
contract's own dispatcher; measured 944 unwinds of `_ESBMC_Nondet_Extcall_C` and
`ERROR: Out of memory`). **Note the asymmetry that decides §6:** the
`isset("unwind")` arm moves only the ENUMERATION bound `path_cov_unwind`; the
`else` arm writes only the SYMEX option. The two are made equal at this one
point and never again.

### Fact 2 — `no-unwinding-assertions` is forced unconditionally
`src/esbmc/esbmc_parseoptions.cpp:4305` — `options.set_option("no-unwinding-assertions", true);`
No guard. Snapshotted into symex at `src/goto-symex/symex_assign.cpp:44`
(inside the `goto_symext` constructor, `symex_assign.cpp:15-166`), consumed at
`src/goto-symex/symex_goto.cpp:482`, whose `else` arm emits an **assumption**
(`:490-493`) and adds it to the state guard (`:510`). The in-tree comment at
`symex_goto.cpp:495-500` states the consequence: *"This assumption SILENTLY
discards every path that needed one more iteration"*.

### Fact 3 — the enumeration bound is fixed once, before the strategy loop
`src/goto-programs/goto_coverage.h:677-682` (unchanged across the whole range):
```cpp
  // Loop bound for path enumeration: each back-edge is followed at most this
  // many times per path, so complete paths are enumerated up to this many
  // loop iterations. Set from --unwind by the dispatch (default 4). Must
  // match the symex unwind bound, or enumerated paths and solver-explored
  // paths disagree.
  size_t path_cov_unwind = 4;
```
Enforced at exactly two live sites, both the per-path back-edge budget of the
enumerating DFS — **`goto_coverage.cpp:5205`** (unconditional backwards GOTO) and
**`:5229`** (conditional GOTO whose taken edge is a back-edge) — and it is also
the internal-call expansion depth at **`:3871`**
(`for (size_t round = 0; round < path_cov_unwind; ++round)`), justified at
`:3804-3809`.

Instrumentation runs **once per process**: `esbmc_parseoptions.cpp:4306`
`tmp.solidity_path_coverage();`, inside `process_goto_program` (`:3569`), reached
from `doit()` → `get_goto_program` (`:2038`) → `process_goto_program` (`:3275`).
`do_bmc_strategy` is entered afterwards, at `:2064`. There is no per-k-step
re-enumeration hook; the k-loop (`:2752-2906`) builds a fresh `bmct` per phase
and never re-instruments.

### Fact 4 — `do_bmc_strategy` overwrites `unwind` with `k_step` at every phase
`esbmc_parseoptions.cpp`: base case **`:2975`**, forward condition **`:3039`**,
inductive step **`:3104`**, post-exhaustion diagnosis **`:5117`** (plus the
separate parallel implementation at `:2398`, `:2503`, `:2572`). Dispatch at
`:2060-2064` for `termination` / `incremental-bmc` / `falsification` /
`k-induction` / `loop-invariant`.

Symex reads the option once, at construction: `src/goto-symex/symex_assign.cpp:27`
`max_unwind(options.get_option("unwind").c_str())`, used at `symex_goto.cpp:518`.
A fresh `bmct` is built per phase (`:2982`, `:3041`, `:3106`) from the same
`optionst` the path-cov block mutated (`bmc.cpp:56` → `:94`/`:103`). So the symex
bound in phase k is exactly k.

### Fact 5 (new) — `--k-induction` changes what is INSTRUMENTED
`esbmc_parseoptions.cpp:3786-3788` and `:3812-3813`, in the same
`process_goto_program`, **before** the path-cov block at `:4118`:
```cpp
    bool is_k_induction = cmdline.isset("inductive-step") ||
                          cmdline.isset("k-induction") ||
                          cmdline.isset("k-induction-parallel");
      ...
      if (is_k_induction)
        goto_k_induction(goto_functions);
```
`goto_k_induction` rewrites loops into havoc+assume preambles. The path pass then
sees control-flow-free assumes, which is its NAMED OBSTACLE cause (a). Measured,
identical instrumentation input, the only delta being the flag:

```
nostrat: (no NAMED OBSTACLE line at all)
kind   : WARNING: --solidity-path-coverage: NAMED OBSTACLE — 2796 path(s)
         excluded, being ALL paths of every affected unit.
           (a) 2796 path(s) across 2 unit(s): the unit contains a construct
               that removes executions WITHOUT a branch ...
```
2796 = `ship` 2733 + `dock` 63 — **the `--focus-function` unit is one of the two.**
Isolated in §5: `--inductive-step` alone (which triggers `goto_k_induction` but
is *not* dispatched to `do_bmc_strategy`) reproduces the obstacle exactly;
`--incremental-bmc` (not in the `is_k_induction` disjunction) never shows it.

### Fact 6 (new) — the report's `bound.unwind` is the last k
`src/esbmc/bmc.cpp:1362` `const std::string unwind_s = options.get_option("unwind");`,
written per claim at `:1501-1502` and into the summary at `:1945-1946`. The
comment two lines above it, `bmc.cpp:1358-1360`, says what the field is for:

> *"Bound under which THIS run's verdicts were produced. Recorded on every path
> entry: a 'holds' verdict is only meaningful together with the exploration it
> was obtained under."*

Measured: `kind__unwind8` enumerated 5166 paths at loop bound 8 and reports
`"unwind": "2"`; `incr__default` enumerated 2846 at bound 4 and reports
`"unwind": "50"`. Both wrong, and both wrong in the direction that flatters the
run.

---

## 2. The matrix

`paths` = `summary.paths_total`; `bh`/`nso`/`nsr`/`une` = the `bounded-holds` /
`named-obstacle` / `not-solved-this-run` / `unit-not-entered` U-reasons;
`rep.unwind` = `summary.bound.unwind`. `—` = **no report was produced at all**.

| cell | rc | wall | paths | F | bh | nso | nsr | une | rep.unwind | k phases run |
|---|---|---|---|---|---|---|---|---|---|---|
| no strategy, no `--unwind` | 1 | 10 s | 2846 | 2 | 61 | 0 | 0 | 2783 | **4** | – |
| `--unwind 1` | 1 | 2 s | 102 | 2 | 5 | 0 | 0 | 95 | **1** | – |
| `--unwind 2` | 1 | 2 s | 238 | 2 | 13 | 0 | 0 | 223 | **2** | – |
| `--unwind 3` | 1 | 5 s | 766 | 2 | 29 | 0 | 0 | 735 | **3** | – |
| `--unwind 4` | 1 | 10 s | 2846 | 2 | 61 | 0 | 0 | 2783 | **4** | – |
| `--unwind 6` | 1 | 53 s | 1326 | 2 | 253 | 0 | 0 | 1071 | **6** | – |
| `--unwind 8` | 2 | 287 s | — | — | — | — | — | — | — | OOM, no report |
| `--k-induction` | 0 | 14 s | 2846 | 2 | **0** | **2794** | 0 | 50 | **2** | BC1, BC2, IS2 |
| `--unwind 1 --k-induction` | 0 | 3 s | 102 | 2 | 0 | 50 | 0 | 50 | **2** | BC1, BC2, IS2 |
| `--unwind 2 --k-induction` | 0 | 5 s | 238 | 2 | 0 | 186 | 0 | 50 | **2** | BC1, BC2, IS2 |
| `--unwind 3 --k-induction` | 0 | 6 s | 766 | 2 | 0 | 714 | 0 | 50 | **2** | BC1, BC2, IS2 |
| `--unwind 4 --k-induction` | 0 | 13 s | 2846 | 2 | 0 | 2794 | 0 | 50 | **2** | BC1, BC2, IS2 |
| `--unwind 6 --k-induction` | 0 | 49 s | 1326 | 2 | 0 | 1274 | 0 | 50 | **2** | BC1, BC2, IS2 |
| `--unwind 8 --k-induction` | 0 | 205 s | 5166 | 2 | 0 | 5114 | 0 | 50 | **2** | BC1, BC2, IS2 |
| `--incremental-bmc` | 134 | 295 s | 2846 | 2 | 61 | 0 | 0 | 2783 | **50** | BC 1…50 |
| `--unwind 1 --incremental-bmc` | 0 | 232 s | 102 | 2 | 5 | 0 | 0 | 95 | **50** | BC 1…50 |
| `--unwind 2 --incremental-bmc` | 134 | 306 s | — | — | — | — | — | — | — | BC 1…39, abort |
| `--unwind 3 --incremental-bmc` | 134 | 304 s | — | — | — | — | — | — | — | BC 1…26, abort |
| `--unwind 4 --incremental-bmc` | 134 | 282 s | 2846 | 2 | 61 | 0 | 0 | 2783 | **50** | BC 1…50 |
| `--unwind 6 --incremental-bmc` | 0 | 258 s | 1326 | 2 | 253 | 0 | 0 | 1071 | **50** | BC 1…50 |
| `--unwind 8 --incremental-bmc` | 134 | 218 s | — | — | — | — | — | — | — | BC 1…38, abort |

Read the `rep.unwind` column against the `paths` column: they agree only in the
no-strategy block.

### 2.1 Without a strategy the two bounds agree, and `--unwind N` moves both
`paths_total` tracks N (102 / 238 / 766 / 2846 for N = 1..4), `rep.unwind` is N,
and the log says `Not unwinding loop 62 iteration N`. This is the only block in
which the header's *"Must match the symex unwind bound"* actually holds — and it
holds by construction at one line, not by any check.

`paths_total` is **not monotone** in N (2846 at 4, 1326 at 6, 5166 at 8): at N=6
and N=8 the per-unit budget (`--path-cov-max-goals`, default 10000) triggers
DEGRADATION and the log carries
`DEGRADED unit 'sol:@C@Aqua@F@ship#3022' — … 1 call point(s) were WITHDRAWN`.
So `paths_total` is not a like-for-like count across N, and no claim here rests
on comparing it across N.

`--unwind 8` with no strategy **loses everything**: 5166 paths instrumented,
`ERROR: Out of memory` after 977 claims, no report, 287 s.

### 2.2 Under `--k-induction`, `--unwind N` never reaches symex
Every k-induction cell, N = 1..8 alike: BC k=1, BC k=2, IS k=2, then
`Solution found by the inductive step (k = 2)` and stop. The log reads
`Not unwinding loop 62 iteration 1` then `iteration 2` — never N. With
`--unwind 4` the user asked for 4 and got 2. `bounded-holds` is **0** in all
seven; `named-obstacle` is `paths − 2 − 50` in all seven.

`--unwind 8 --k-induction` is the extreme: 5166 paths enumerated for bound 8,
205 s spent, symex never above 2, report says 2.

### 2.3 `--incremental-bmc` cannot stop early, and its failures are invisible
Whole-log scan of `incr__default`: BC k = 1…15 solve normally
(`Properties: 61 verified ✓ 61 passed`, fifteen times — the same 61 claims
re-asked every round); BC k = **16…50** hit `ERROR: Out of memory` /
`ERROR: SMT solver failed` at **every single step** (35 consecutive), each
downgraded to "inconclusive and continuing" by `do_bmc`
(`esbmc_parseoptions.cpp:3201-3205`); then `VERIFICATION UNKNOWN` and the report.

The report is numerically identical to the 10-second no-strategy run
(F 2, bh 61, une 2783) except `bound.unwind` now reads `"50"` — and
**`not-solved-this-run` is 0.** Thirty-five base cases in which the solver failed
outright leave no trace in the report.

It cannot stop early because the forward condition is auto-disabled in Solidity
dispatcher mode (`:1015-1018` sets `disable-forward-condition`; `:3024-3025`
returns `TV_UNKNOWN` before running it) and the `incremental-bmc` branch
(`:2852-2898`) has no inductive step. The only exits are
`count_active_asserts() == 0` — which the in-tree comment at `:2727-2735` says
never happens on a program with unreachable asserts, and this run has 2783 — and
exhaustion at `--max-k-step` (default 50, `options.cpp:510-512`).

Three of the seven cells aborted mid-loop (rc 134) with **no report at all**,
which is `notes/path-coverage-invocation-contract.md` §10.5's "a caught OOM still
costs the whole report" happening in practice.

---

## 3. Isolation: which half of `--k-induction` does the damage

`--k-induction` does two independent things — **(T)** the GOTO transform
`goto_k_induction` (`:3812-3813`, gated on `is_k_induction` at `:3786-3788`) and
**(K)** the strategy loop (`:2060-2064`). These cells separate them.

| cell | (T)? | (K)? | rc | wall | paths | F | bh | nso | NAMED OBSTACLE | report? |
|---|---|---|---|---|---|---|---|---|---|---|
| `--base-case` | no | no | 1 | 11 s | 2846 | 2 | 61 | 0 | absent | yes |
| `--forward-condition` | no | no | 1 | 10 s | 2846 | – | – | – | absent | **no** |
| `--inductive-step` | **yes** | no | **134** | 2 s | 2846 | – | – | – | **present, 2796** | **no** |
| `--incremental-bmc` | no | yes | 134 | 295 s | 2846 | 2 | 61 | 0 | absent | yes |
| `--k-induction` | yes | yes | 0 | 14 s | 2846 | 2 | 0 | 2794 | **present, 2796** | yes |

**The NAMED OBSTACLE tracks (T) exactly.** `--inductive-step` alone produces it
without ever entering `do_bmc_strategy`; `--incremental-bmc` runs the whole
strategy loop and never produces it. So it is the transform, not the bound.

`--inductive-step` additionally **crashes**, in 2 seconds, on an internal
invariant:
```
esbmc: src/goto-symex/execution_state.cpp:219:
  virtual void execution_statet::symex_step(reachability_treet&):
  Assertion `k_induction && "Inductive step instructions should be set only
  for k-induction"' failed.
```

`--forward-condition` instruments correctly and runs symex at the right bound
(`Not unwinding loop 62 iteration 4`), solves all 63 claims — and then emits **no
`[Coverage]` block and no `cov-report.json`**, because `report_coverage`'s
in-`bmct` call site is gated on base-case-and-not-FC-and-not-IS
(`bmc.cpp:3661-3663`) and no strategy loop runs to call it from the other side.

> **Correction to `notes/path-coverage-invocation-contract.md` §10.4.** Its table
> groups `--base-case` / `--forward-condition` / `--inductive-step` as
> "nothing — … so the bound is whatever `--unwind` is, i.e. the pass's own 4 —
> **4 — agree by accident**". The bound statement is right for all three, but the
> row reads as though the three are interchangeable and benign. Measured, only
> `--base-case` is: `--forward-condition` produces no report, and
> `--inductive-step` poisons the instrumentation *and* aborts.

### 3.1 `--overflow-check --k-induction`
`esbmc_parseoptions.cpp:663-665` sets `disable-inductive-step`;
`is_inductive_step_violated` then returns `TV_UNKNOWN` at `:3093-3094` and
`diagnose_unknown_properties` returns at `:5093-5094`. With the forward condition
already auto-disabled for Solidity, k-induction has **no closing phase left**.

Measured: rc 134, 225 s, base case k = 1…14, then `ERROR: Out of memory` from
k=12 on, **no report**. Also visible: the check flags add claims but not paths —
k=1 solved `180 verified ✓ 176 passed, ✗ 4 failed` against 63/61/2 without the
flag, and the claim count grows +4 per k step (83, 87, 91, 95, …) while
`paths_total` never moves. That is
`notes/path-coverage-invocation-contract.md` §11.2's "extra solver work and extra
stdout noise and zero extra paths", measured, with the addition that it also ends
in no deliverable.

---

## 4. Can `--base-k-step` repair the bound? (and the controls for A)

`--base-k-step N` starts the k-loop at N (`:2658`, `:2752`), so the FIRST base
case runs with symex unwind = N. `--max-k-step` must exceed it (`:2669-2675`).

| cell | rc | wall | paths | F | bh | nso | une | rep.unwind | k phases |
|---|---|---|---|---|---|---|---|---|---|
| `--unwind 4` (baseline, no strategy) | 1 | 10 s | 2846 | 2 | 61 | 0 | 2783 | 4 | – |
| `--unwind 4 --incremental-bmc --base-k-step 4 --max-k-step 5` | 0 | 20 s | 2846 | **2** | **61** | **0** | **2783** | **5** | BC4, BC5 |
| `--unwind 4 --k-induction --base-k-step 4 --max-k-step 5` | 0 | 10 s | 2846 | 2 | **0** | **2794** | 50 | 4 | BC4, IS4 |
| `--unwind 4 --unwindset 62:1` | 1 | 6 s | 2846 | **2** | 61 | 0 | 2783 | 4 | – |
| `--unwind 4 --no-simplify` | 0 | 12 s | 2846 | **0** | **63** | 0 | 2783 | 4 | – |
| `--unwind 4 --no-simplify --k-induction` | **124** | 600 s | — | — | — | — | — | — | BC1…IS8, **timeout, no report** |

**Row 2 is the one near-miss in this whole file.** `--incremental-bmc` with
`--base-k-step` equal to the enumeration bound reproduces the no-strategy report
field for field. It is the only strategy cell that does. It still costs 2x the
wall time for an identical deliverable, and it still misreports
`bound.unwind = 5`.

**Row 3 shows the k-induction defect is not a bound defect.** Aligning the bound
fixes `rep.unwind` (now 4) and nothing else: `bounded-holds` is still 0 and 2794
paths are still named obstacles. (T) is untouched by (K)'s knobs.

**Rows 4-5 are the controls question A needs.** The positive control reproduces:
`--no-simplify` still collapses F 2 → 0 on this snapshot, with the two lost
witnesses becoming `bounded-holds` (61 → 63) and `not-solved-this-run` staying 0,
and three loops truncating (1, 62, 64) instead of one. The negative control
`--unwindset 62:1` — enumeration at 4, `dock`'s own loop 62 capped at 1 — changes
**nothing**: F stays 2, bh stays 61.

---

## 5. Answers

### A. k < 4 — does the truncation assume away witnesses?
**The mechanism is present and demonstrated. The F-count collapse does not
reproduce on `Aqua.dock`, and that is a property of `dock`, not a refutation.**

Present: under `--unwind 4 --k-induction` the goal set is the 2846 paths
enumerated for four back-edge traversals, and all 63 of `dock`'s claims are put
to the solver under a symex bound of 1 (k=1) and 2 (k=2). Sixty-one come back
`61 passed` — asked, and answered *"the assertion holds"*, i.e. *"this path is
not feasible"*, under a strictly weaker unwinding than the one that defined them.
That is the shape from `notes/coverage/certify-vs-assert-vacuity.md`: not
`not-solved-this-run`, but answered wrongly-favourably.

Not reproduced as a number: F is 2 in every cell from `--unwind 1` up, with and
without a strategy, and `--unwindset 62:1` at enumeration bound 4 also leaves F=2
and bh=61. `dock`'s two witnessed paths do not need a second traversal of loop 62.
**On this unit F is insensitive to the symex bound over 1..8 and cannot exhibit
the 2 → 0 collapse by starvation of that loop.** The `--no-simplify` control shows
the collapse is still producible on this snapshot — but through loop 64
(`__memset_impl`), a loop simplification normally folds away entirely, not
through the bound.

So the claim that carries is the narrow one: **under a strategy, path claims
defined at enumeration depth D are answered under symex depth k < D, and the
answer they get is `holds`.** Whether that flips a particular F is a property of
the unit, and finding a unit where it does is the obvious follow-up.

### B. k > 4 — does the extra k buy anything?
**No. The goal set is frozen before the strategy loop starts.**

From source: `solidity_path_coverage()` is called once
(`esbmc_parseoptions.cpp:4306`) inside `process_goto_program`, which `doit()`
runs at `:2038` — strictly before the dispatch at `:2064`. No re-enumeration
hook exists.

From measurement: `incr__default` ran base case k = 1…50 and asked the **same 63
claims** every round; its final report is identical in every coverage field to
the 10-second no-strategy run. 285 s of extra solving, zero extra paths — and 35
of those rounds were solver OOMs that the report does not mention.

### C. does anything detect the disagreement?
**No. It is a comment, in two places, and nothing else.**

* `src/goto-programs/goto_coverage.h:679-681` — *"Must match the symex unwind
  bound, or enumerated paths and solver-explored paths disagree."*
* `src/esbmc/esbmc_parseoptions.cpp:4269-4271` — *"The two MUST agree, or 'this
  path is feasible' as enumerated and 'this path is feasible' as explored are
  answers to different questions."*

No assert, no runtime comparison, no warning. `goto_coverage.cpp` never reads
`options.get_option("unwind")`, `config.options` or `max_unwind` at all — the
only `optionst` in the file is the default-constructed `inline_opts` at `:3810`,
which is never queried. This is `propositions-are-runtime-checks` verbatim: a
proposition the method depends on, false in most of the cells measured here, and
nothing fires.

**Three artefacts record a bound, and under a strategy two of them are wrong:**

| artefact | field | source | under a strategy |
|---|---|---|---|
| `cov-report.json` summary + per claim | `bound.unwind` | `bmc.cpp:1362` ← `options["unwind"]` | **wrong — the last k** (Fact 6) |
| covered-set fingerprint | `loop_bound=` | `goto_coverage.cpp:3224` ← `path_cov_unwind` | **wrong — records the pre-strategy number**, so two runs at different k share a fingerprint and union freely (already documented: invocation contract §13.3 item 3) |
| the log line at `goto_coverage.cpp:7726-7731` | `(loop bound = N iterations)` | `path_cov_unwind` | correct *for the enumeration*, and never reconciled with what symex did |

> **Line-number drift note for `notes/path-coverage-invocation-contract.md`
> §13.1/§13.3.** Those sections cite the fingerprint at `goto_coverage.cpp:3057-3094`
> with the hash lines at `:3067-3069`. At HEAD `d09536838a` the block is
> **`:3214-3251`** and the three `path_cov_unwind` hashes are **`:3224-3226`**
> (re-read immediately before writing). §13's other citations were not re-checked.

**And the one warning that fires recommends the broken combination.** The
under-report warning, `src/esbmc/bmc.cpp:806-823`, ends:

> *"Raise --unwind, use --unwindset/--unwindsetname for the specific loop, or
> **switch to --k-induction / --incremental-bmc**."*

Measured, both halves of that advice are wrong: `--k-induction` *lowers* the
symex bound to 2 and adds 2794 named-obstacle exclusions; `--incremental-bmc`
changes no coverage number and costs 20-30x. The warning fired in **every cell
that produced a report at all**, including all seven k-induction cells where its
own remedy is what is being done. A warning that fires identically whether or not
its advice has been taken carries no information.

### D. k-induction specifically
**The inductive step does not depend on unwinding assertions, and the forced
`no-unwinding-assertions` is not what breaks k-induction — each phase overwrites
it. What breaks is the GOTO transform (Fact 5) and the bound (Fact 4).**

* Each phase sets what it wants, so `:4305` is overwritten before any phase runs:
  base case sets it **true** (`:2973`), forward condition **false** (`:3030`),
  inductive step **true** plus `partial-loops = true` (`:3102-3103`). With
  `partial_loops` on, `loop_bound_exceeded` returns at `symex_goto.cpp:474` and
  emits neither an assertion nor an assumption — the inductive step is untouched
  by the coverage force.
* The phase that genuinely depends on unwinding assertions is the **forward
  condition** (it *is* the unwinding-assertion question), and it re-enables them
  itself at `:3030`. But in Solidity dispatcher mode it is auto-disabled
  (`:1015-1018`, short-circuit at `:3024-3025`), so **under
  `--solidity-path-coverage` the forward condition never runs.** k-induction is
  reduced to base case + inductive step.
* The coverage deliverable comes from the **base case**, which sets
  `no-unwinding-assertions = true` itself (`:2973`) — the same configuration the
  coverage block forces. The silent-truncation hazard is therefore present in the
  phase that produces the numbers, strategy or no strategy.
* **Is the inductive step still able to conclude anything?** On this target it
  concluded at k=2 in every cell (`Solution found by the inductive step (k = 2)`)
  and that is what ends the k-loop — so it concludes, and concluding early is
  precisely what caps the symex bound at 2. **UNVERIFIED: whether that conclusion
  is *sound* on a coverage-instrumented program** (it would be asserting an
  inductive invariant over the ghost `tr`/`cnt` accumulators). What is measured
  is only that it returns UNSAT and stops the loop.
* **`--overflow-check` sets `disable-inductive-step`** (`:663-665`), read at
  `:3093-3094` and `:5093-5094` (symex can also set it at runtime,
  `src/goto-symex/symex_function.cpp:98`). Combined with the auto-disabled
  forward condition this leaves k-induction with **no closing phase at all** —
  base case to `--max-k-step` and nothing else. Measured in §3.1: 225 s, 14 base
  cases, OOM from k=12, no report.

### E. what should be passed
**The pass's own bound, and no strategy:**

```
esbmc <c>.solast --sol <c>.sol --contract C --focus-function f \
  --solidity-max-tx 1 --solidity-path-coverage --cov-report-json [--unwind N]
```

1. **`--unwind N` cannot align the two bounds under a strategy.** It sets
   `path_cov_unwind` (`:4288-4293`) and nothing else; `do_bmc_strategy` writes
   `unwind = k` at `:2975`/`:3039`/`:3104` regardless. Measured: with
   `--unwind 4 --k-induction`, symex ran at 1 and 2.
2. **Everything in the `is_k_induction` disjunction (`:3786-3788`) — `--k-induction`,
   `--inductive-step`, `--k-induction-parallel`, and `--loop-invariant` via
   `:3803` — additionally changes what is instrumented**, disqualifying the
   focused unit as a NAMED OBSTACLE. No bound setting repairs it (§4 row 3), and
   it kills every stage-2/3 query and every PUT for that unit while `F` and
   `Path Coverage` read exactly as they do without the flag.
3. **`--incremental-bmc` / `--falsification` / `--termination` buy nothing**: the
   goal set is frozen, every k re-asks the same claims, and on this target 3 of 7
   cells ended with no report at all.

**The one condition under which a strategy is not actively wrong**, stated
precisely because the brief asked for it:

> `--incremental-bmc --unwind N --base-k-step N --max-k-step N+1` runs its first
> base case at symex bound N = `path_cov_unwind`, and reproduced the no-strategy
> report field for field (§4 row 2).

**Do not use it anyway.** It costs 2x wall time for a byte-identical deliverable,
its report still says `bound.unwind = N+1`, and it is one forgotten flag away
from the failure it is working around — the flag that matters (`--base-k-step`)
is not the flag anyone would think to check. If a run must be bounded at N, pass
`--unwind N` and no strategy; that is the same computation with fewer ways to be
silently wrong.

Choosing N:

* N bounds back-edge traversals **per enumerated path** (`goto_coverage.cpp:5205`,
  `:5229`) *and* internal-call expansion depth (`:3871`). The goal set grows fast:
  102 → 238 → 766 → 2846 for N = 1 → 4 here.
* Raising it is neither free nor monotone: N=8 OOMs at 6 g with no report, and
  N=6 enumerates *fewer* paths than N=4 because degradation fires. **Raise N only
  against a measured report at the new N.**
* If one loop is the truncating one — the warning names it — prefer
  `--unwindset <loop>:<n>`. That moves only the symex side
  (`src/goto-symex/symex_goto.cpp:530-531`), so it is a deliberate one-sided
  divergence: use it to *widen* symex past the enumeration bound (safe — a
  superset of executions), never to narrow it.

---

## 6. What this file does NOT establish

* **One contract, one unit, one solver, one memory limit.** The *source* facts
  are contract-independent; the *numbers* — "the k-loop stops at k=2", "F is
  bound-insensitive", "2796 paths excluded" — are `Aqua.dock`'s. A unit whose
  witnessed paths need several loop iterations should show the F collapse `dock`
  cannot, and finding one is the natural next step.
* **The binary predates `d09536838a`** (§0.1). UNVERIFIED whether the matrix
  reproduces on a current build.
* Why `named-obstacle` *displaces* `bounded-holds` in the U-reason histogram
  rather than being counted beside it: observed, not traced.
* Whether the inductive step's k=2 conclusion is sound on an instrumented
  program (§5 D).
* `--k-induction-parallel` (`:2075-2623`, own overwrites at `:2398`, `:2503`,
  `:2572`) and `--termination` / `--falsification` were read, not run.
* `--loop-invariant` reaches `goto_k_induction` via `:3803` and so should show
  Fact 5, but was not run. UNVERIFIED.
