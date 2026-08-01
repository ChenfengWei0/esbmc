# The invocation decision table

**This file is subgoal 1's deliverable.** Not a script that runs — a decision
per dimension, each one backed by a measurement, and each one naming the
artefact that would have to be re-run to overturn it.

Why it exists: every corpus number produced before 2026-07-31 came from ONE
configuration that had never been compared against another. A configuration with
one cell and no control is not a chosen configuration; it is the first thing
that happened to run. That is how a 0/5 gate result, a 65-unit stage-2 sweep and
every funnel ratio came to be measurements of a cell rather than of a method.

Rules for this file:
* a row may only say DECIDED if a run exists that would have changed it;
* a row that says OPEN says what experiment closes it;
* every verdict is read off `cov-report.json`, never off an exit code —
  **exit codes are not comparable across bounding strategies** (measured: tx1
  exits 1 with F=2, multi-tx+k-induction exits 0 with the same F=2);
* set comparisons, never count comparisons, when asking "does configuration A
  reach something B does not" (measured: a count comparison licensed the
  opposite conclusion on scope, see row 1).

---

## The table

| # | dimension | decision | status |
|---|---|---|---|
| 1 | scope | ~~keep `--focus-function`~~ **OVERTURNED — see row 2** | REOPENED |
| 2 | tx depth | ~~`--solidity-max-tx 1`~~ **whole contract + `--solidity-max-tx 2`** | OVERTURNED |
| 3 | bounding strategy | **none** | DECIDED |
| 4 | slicing | **default** (do not pass `--no-slice`) | DECIDED |
| 5 | simplification | **never pass `--no-simplify`** to the collector | DECIDED |
| 6 | arithmetic checks | **enumeration: do not pass them (they cannot change the path set). EMISSION: pass them — `--path-cov-arith-resolve` needs them** | AMENDED 2026-08-01 |
| 7 | solver | **auto-select as the default, but it is per-benchmark: measurably RIGHT on aqua and measurably WRONG on st1inch** | AMENDED 2026-08-01 |
| 8 | resources | **`--memlimit` sized per contract; 8g is not a default** | DECIDED |
| 9 | `--all-witnesses` | **wanted, blocked by a one-line gate** | OPEN |
| 10 | isolated-function mode | **`--function` is BANNED** | DECIDED |

---

## 1. scope — keep `--focus-function`

Whole-contract IS viable; `--memlimit 8g` was the entire obstacle. Same command,
only the limit differing:

| memlimit | wall | peak RSS | report |
|---|---|---|---|
| 8g | 312 s | — | **none** (solver OOM -> `bad_alloc` -> SIGABRT) |
| 20g | 777.8 s | 15.86 GiB | yes |

Peak is **1.98x** the 8 GiB it had been given. It could never have finished.

And it reaches nothing focus cannot. F sets compared **as sets**: 15 both sides,
0 only-whole, 0 only-per-method. `bounded-holds` sums to the same 1807 and
`not-solved-this-run` is 1024 in every report. Cost of whole-contract: 2.4x
wall, 2x memory, no parallelism, no resume.

**A count comparison would have given the opposite answer.** Against the
collection as it sits on disk it reads `whole 15 / per-method 13 / only-whole 2`,
both in `Aqua.ship` — exactly what the theory predicts. It is false: the
per-method collection has no `ship` report at all (killed at the 180 s timeout).
Re-run focused with a real budget, `ship` finds the same two `enc` values.

Caveat: one contract. Aqua's units are guarded by `msg.*` and their own
arguments, not by sibling-written state. `FarmingPool.deposit`/`withdraw` is
where this could still differ.

Evidence: `notes/coverage/scope-and-resources.md`.

## ROWS 1 AND 2 ARE OVERTURNED. A ten-line hand-written contract did it in
## five seconds, and the matrix had a hole exactly where the answer was.

`notes/coverage/poc/Tiny.sol`: `bal` starts at 0 and only `deposit` can raise
it; `withdraw`'s interesting paths sit behind `require(bal >= amt)`.

| configuration | paths | F | bounded-holds | coverage |
|---|---|---|---|---|
| `--focus-function withdraw`, tx=1 | 5 | 3 | 2 | 60% |
| whole contract, tx=1 | 8 | 6 | 2 | 75% |
| **whole contract, tx=2** | 8 | **8** | **0** | **100%**, 8/8 with inputs |
| whole contract, tx=3 | 8 | 8 | 0 | 100%, field-identical to tx=2 |

**Whole contract at `--solidity-max-tx 2` reaches everything.** One preceding
call is enough; tx=3 adds nothing.

Two further hand-written experiments pin down what is actually going on, each
about a second:

* `Tiny2.sol` — identical except the CONSTRUCTOR sets `bal = 500`. Then
  `--focus-function withdraw` at tx=1 gives **5 of 5, 100%**. So the obstacle
  was never "the state"; it was "a call has to happen first".
* `Tiny3.sol` — the predecessor `seed()` has no `require` and no branch. Whole
  contract at tx=1: still 2 bounded-holds. At tx=2: **7 of 7**. So a preceding
  call is needed even when it carries no user-level decision of its own.

### The error, stated precisely

The matrix crossed **tx only under `--focus-function`**, where by construction
raising the bound cannot help — every transaction is another call to the same
`f`. And it crossed **scope only at tx=1**, which is where whole-contract and
per-method were found to have identical F sets.

**The cell `whole contract x tx=2` was never run.** From two individually
correct observations I concluded "the transaction dimension buys nothing", and
wrote it into this table as DECIDED.

The aqua evidence that seemed to confirm it — F sets equal as sets, 15/15, zero
either-only — holds only because aqua's units are guarded by `msg.*` and their
own arguments rather than by sibling-written state. That caveat was written down
in `scope-and-resources.md` when the measurement was made, and I did not act on
it.

### What this costs, stated rather than avoided

`--solidity-max-tx N>=2` is the configuration ESBMC itself warns about: it
"reconstructs multi-transaction sequences unreliably (methods can be
mis-attributed across transactions)" for Foundry emission
(`esbmc_parseoptions.cpp:553-565`).

So the split is: **enumerate at tx=2 whole-contract**, because it is the only
configuration that reaches cross-function state at all; and **confront the
emission attribution problem directly** rather than avoiding the configuration.
This file previously listed the tx ladder as the first thing to cut, citing that
warning. That traded away the method's only route to its own hardest paths in
order to avoid an engineering problem in the renderer.

### Superseded text, kept because its reasoning is still cited elsewhere

## 2. tx depth — the old row: `--solidity-max-tx 1`

* `--solidity-max-tx 0` is the SHALLOWEST setting under coverage, not the
  unbounded one: bound 0 emits `while(nondet){body}` and coverage rewrites the
  back-edge to a SKIP, leaving one transaction.
* `--solidity-max-tx N>=2` is the configuration the tool itself warns produces
  mis-attributed Foundry tests.
* **`--coverage-multi-tx` does nothing under path coverage.** Path coverage is
  absent from `unbounded_modes`, so its bound is 2, so `emit_tx_driver` copies
  the body twice with **no loop and no back edge** — and `--coverage-multi-tx`
  exists to exempt the back-edge neutralisation from removing a back edge that
  is not there. Measured: its cells are field-for-field identical to `tx2`.

**There is no ESBMC configuration today that gives path coverage more than 2
straight-line transactions.** Cross-function entry state is not purchasable with
a flag; it needs `__ESOL_nondet_state_forward` (implemented, never wired).

Measured on three units (`Aqua.safeBalances`, `Aqua.dock`, `FarmingPool.deposit`):
tx1 / tx2 / tx0 / multi-tx+unwind / multi-tx+k-induction all give the same F.

Evidence: `notes/coverage/option-matrix-round1.md`,
`notes/path-coverage-invocation-contract.md` §2, §10.4.

## 3. bounding strategy — none

`--unwind N` (or nothing, in which case the pass installs 4 for itself) and NO
strategy. Both alternatives are actively harmful, not merely expensive:

* **`--k-induction` changes what is INSTRUMENTED.** `goto_k_induction` runs
  BEFORE the path-coverage block, and its havoc+assume preambles trip the pass's
  own NAMED OBSTACLE criterion, excluding **2796 of 2846 paths** — of which 63
  are the focused unit's entire path set. That is why every k-induction cell
  shows `bounded-holds = 0`: not "nothing held", but "the unit was not measured".
  No bound setting repairs it. Isolated: `--inductive-step` alone reproduces it;
  `--incremental-bmc` never shows it.
* **`--incremental-bmc` is pure waste**: 50 base cases re-asking the same 63
  claims, final report field-for-field identical to the 10 s no-strategy run.
* **`--overflow-check --k-induction` has no closing phase**: the flag disables
  the inductive step and Solidity auto-disables the forward condition. Measured:
  225 s, 14 base cases, OOM from k=12, no report.

Two values are RECORDED WRONG under a strategy, which is worse than absent:
`summary.bound.unwind` is the LAST k (2 under k-induction, 50 under incremental,
whatever `--unwind` said), and the covered-set fingerprint's `loop_bound` is
likewise taken from `path_cov_unwind` while `do_bmc_strategy` overwrites `unwind`
with `k_step` AFTER instrumentation. **Two runs at different k share a
fingerprint and union freely.**

And the source's declaration that the symex bound and the enumeration bound
"MUST agree" is **a comment only** — `goto_coverage.cpp` never reads the unwind
option, `config.options` or `max_unwind`. Worse, the under-report warning
(`bmc.cpp:806-823`) actively RECOMMENDS switching to `--k-induction` /
`--incremental-bmc`, and fires in every cell that produces a report.

⇒ two follow-ups, both queued: make "MUST agree" a runtime gate, and delete the
recommendation that teaches users to break the measurement.

UNVERIFIED: the matrix ran against a snapshot predating `d09536838a`; cells are
mutually comparable but describe the older build and want one re-confirmation.

Evidence: `notes/coverage/unwind-vs-strategy.md`.

## 4. slicing — default

`--no-slice` changes nothing measurable: same F, same `inputs`, same U, in both
simplification settings, on three units.

But the exemption list has a structural hole worth stating, because it explains
a loss that was previously misread. The list protects three categories —
contract objects, contract-scope stores, environment — identified by symbol id
prefix. **Function parameters cannot be in it**: their ids contain `@F@`, which
the contract-scope criterion explicitly excludes. They survive only by DATA
DEPENDENCY, because the guards that build `tr` read them.

⇒ a parameter that participates in no decision on a given path IS sliced away,
never reaches `inputs`, and the Foundry emitter fills it with a type default
marked `defaulted` — reported, not refused. The "defaulted args" counts seen
earlier are this, not a rendering gap.

Evidence: `notes/path-coverage-invocation-contract.md` §10.1,
`notes/coverage/option-matrix-round1.md`.

## 5. simplification — never `--no-simplify` from the collector

On `Aqua.dock`, `--no-simplify` takes F from **2 to 0** — with exit 0, a normally
written report, U = 2846, no OOM, no timeout and no warning. The two lost
witnesses do not become "never asked": `not-solved-this-run` stays 0 and
`bounded-holds` grows 61 -> 63. **The claim was asked and the tool answered that
the path does not hold, when it does.**

Mechanism: `--no-simplify` stops `do_simplify` folding loop guards, so a library
loop (`__memset_impl`) is actually entered, is truncated at the coverage-forced
`--unwind 4`, and the unconditionally-forced `no-unwinding-assertions` turns the
unwinding assert into an ASSUME that deletes exactly the witnessing executions.
Confirmed two ways: `--partial-loops` restores F=2 with one flag, and
`--unwindset 64:512` restores it independently.

**It is contract-specific**: `Aqua.safeBalances` and `FarmingPool.deposit` show
no effect in any of the four cells. So this can be measured and cannot be
reasoned about.

**Not to be confused with the tool's internal force.** `--path-cov-assert`
forces `no-simplify` at `esbmc_parseoptions.cpp:4223` and MUST keep doing so:
measured with the force env-gated, the R1 ladder goes 3 HOLDS / 3 REFUTED / 0
no-verdict -> **0 / 3 / 3**, i.e. every HOLDS becomes "never reached the solver",
destroying the entire positive output of a mode whose result IS a HOLDS/REFUTED
table. The fix was not to remove the force but to stop the resulting verdict
from lying: a run that would report VACUOUS while a loop was truncated now
reports `RESULT: UNDECIDED-TRUNCATED`, pinned by four regressions including the
must-not-fire direction.

Evidence: `notes/coverage/certify-vs-assert-vacuity.md`,
`notes/coverage/option-matrix-round1.md`.

## 6. arithmetic checks — cannot change the PATH SET, but they can change WHICH
## MEMBER of a path's domain the solver returns (amended 2026-08-01)

**The row's argument is intact; its conclusion was too broad.** Everything below
about the enumerated path set remains true and measured. What is now false is the
sentence "passing them cannot help", because it silently answered a second
question the argument never addressed: *which member of the path's domain does
the counterexample take?*

`--path-cov-arith-resolve` (2026-08-01) is built on exactly that distinction. On
a WITNESSED path carrying a checked operation it re-solves that one claim with
`goto_check`'s own conditions assumed and prefers a non-wrapping witness.
Measured, must-flip pair:

| | off | on |
|---|---|---|
| `D10_WrapNotPanic.add` | `amt = 2^256-1`, `bal 500 → 499` — **wraps** | `amt = 2^256-501`, `bal = 2^256-1` — saturates exactly |
| cost | — | 3 claims re-solved in 0.008s |

Three of the PoC set's RED tests were this (two Panic 0x11, one Panic 0x12), and
a test that is red on the unmodified contract is the one outcome this pipeline
must never produce. So for the ENUMERATION run the row's advice still stands —
the checks add claims and change no path — and for a run that intends to emit
tests, `--overflow-check` and/or `--div-by-zero-check` are now a PREREQUISITE of
the flag that fixes it, which refuses to run without them rather than no-op.

⚠ **Two costs the amendment does not hide.** The checks still become their own
solver jobs (the paragraph below), and under path coverage a non-instrumented
claim now produces no Foundry case at all — a deliberate change measured on
`D16_OnlyByOverflow`, where `goto_check`'s own refuted overflow claim was
emitting a duplicate of the path claim's call and defeated the first version of
the refusal.

### The original row, unchanged below

The enumerating DFS fans out at exactly three site kinds: conditional GOTO,
folded short-circuit in ASSIGN, folded short-circuit in RETURN. Everything else
falls through to straight-line. `goto_check` produces a **single-successor
ASSERT** (`targets` never assigned; `get_successors`' assert arm pushes only
`next`).

⇒ `--overflow-check`, `--unsigned-overflow-check`, `--div-by-zero-check`,
`--bounds-check` **cannot change the enumerated path set**. An EVM revert on
overflow is not in this method's decision set under any flag combination, and no
option can put it there — that would be a frontend change.

⇒ they are not free either: path coverage is the only coverage mode that does
NOT neutralise pre-existing asserts, so each goto_check claim becomes its own
solver job and its own counterexample block while counting in no numerator.

**One exception, for a different reason.** `--div-by-zero-check` during
CERTIFICATION is still wanted — not to add a decision, but so an independent
claim excludes zero divisors from the region. With the check off, ESBMC models
`a/0` as `type(uintN).max`, a value that exists in neither real Solidity
(`Panic(0x12)`) nor bare EVM (`0`); that all-ones value would otherwise
participate in building R2 assertions.

Also settled: `no-assertions` is set for BOTH branch and path coverage by one
shared condition, so the two metrics are not asymmetric on that axis; and
`unchecked { }` produces a byte-identical model to a normal block under every
flag combination, because all `#sol_unchecked` readers end in
`add_guarded_claim`.

Evidence: `notes/path-coverage-invocation-contract.md` §11.

## 7. solver — auto-select, WITH ONE MEASURED EXCEPTION

> **⛔ AMENDED 2026-08-01: "let it auto-select" is measurably WRONG on st1inch and
> measurably RIGHT on aqua, and the row may no longer be stated as one default.**
> `notes/coverage/D31-the-router-is-right-about-aqua-and-wrong-about-st1inch.md`
> ran five backends x two vehicles, every st1inch cell gated on reproducing
> `F = 2` on `aqua safeBalances` first — a backend that cannot produce SAT at all
> has said nothing by returning 0.
>
> | on `st1inch setFeeReceiver` | | on `aqua safeBalances` | |
> |---|---|---|---|
> | auto | **180 s, NO REPORT** | auto (=cvc5) | F=2 ✅ |
> | `--z3 --tuple-node-flattener` | **103.8 s, complete, F=0** | same | F=2 ✅, fastest cell |
> | `--cvc5` | 180 s, no return | `--cvc5` | F=2 ✅ |
> | `--bitwuzla` | 180 s, no return | `--bitwuzla` | **F=0, 4 solver-unknown** ❌ |
> | `--z3` | VOID (failed control) | `--z3` | **SIGABRT** ❌ |
>
> Three things follow, and they are different claims:
> 1. **No backend fixes the coverage.** The only cell that both passed the
>    control and returned gives `F = 0`. What auto-selection costs on st1inch is
>    the REPORT, not the reach — see D30 for the source-level mechanism (a
>    30-deep chain of 256-bit `mul`/`div` in the constructor, inherited by every
>    query).
> 2. **The router is right about aqua.** Its stated reason for avoiding bitwuzla
>    there now has its counterfactual: bitwuzla FAILS the aqua control. The row
>    previously had the reason and not the test.
> 3. **Plain `--z3` SIGABRTs on aqua** while `--z3 --tuple-node-flattener`
>    completes in 2.0 s, and `--z3 --tuple-sym-flattener` SIGABRTs too
>    (`encoder_arms.py`, same day). Two of three z3 configurations abort on aqua.
>    Unexplained, recorded as a defect with a reproduction rather than a
>    preference.
>
> ⇒ The row stays "auto-select" as the DEFAULT, because it is right where it has
> been tested except on st1inch, and the collector's `ENCODER_EXCEPTIONS` already
> overrides it there. What is withdrawn is the word DECIDED: this is a
> per-benchmark question with two measured, opposite answers.

Aqua auto-selects CVC5 with a stated reason ("detected >=3-level nested-mapping
shape; Bitwuzla aborts on the CONST_ARRAY-initialised infinite mapping array").
`--z3` was deliberately NOT tried there: the fallback was contingent on CVC5
being what ran out of memory, and at 20 g it does not. Overriding an
auto-selection that carries a soundness reason needs its own evidence.

### st1inch: THE ENCODING, NOT THE SOLVER, AND IT UNBLOCKS THE BENCHMARK

st1inch produced NOTHING for the whole corpus: all 22 focused runs died at the
180 s outer timeout with no report. Narrowed with `--focus-function
setFeeReceiver` -- a unit whose body is an owner check and one assignment -- the
shape is stark: symex 0.095 s, 1526 assignments, 10 VCCs, and then ONE solver
call that does not return. Three backends, three different failures:

| configuration | result |
|---|---|
| `--bitwuzla` (auto-selected) | never returns (killed at 125 s, 118 s user) |
| `--cvc5` | `std::bad_alloc` at 4 g, 0.000 s in the decision procedure |
| `--z3` | `datatype is not well-founded`, then SIGABRT, at 16 s |
| `--bitwuzla --tuple-sym-flattener` | still never returns |
| `--bitwuzla --array-flattener` | SIGABRT at 16 s |
| `--cvc5 --cvc5-native-tuples` | SIGABRT at 16 s |
| **`--z3 --tuple-node-flattener`** | **rc 0, 43 s, complete report** |
| **`--z3 --tuple-sym-flattener`** | **rc 0, 55 s, complete report** |

z3's message names an ALGEBRAIC DATATYPE whose constructor mentions itself with
no base case, and the hand-written control settles where it does not come from:
`struct Node { Node[] kids; }` -- a genuinely self-referential Solidity struct --
is accepted by all three backends (`notes/coverage/poc/D05_RecursiveStruct.sol`).
So the recursion is built by ESBMC's own tuple encoding, not by the source, and
either explicit flattener avoids it. Per-query times are 2-7 s, not unbounded.

Two things follow, and only the first is a recommendation:

* **For st1inch, pass `--z3 --tuple-node-flattener`.** It is the only
  configuration measured to produce a report at all, and the benchmark has been
  contributing zero to every corpus number until now.
* **The default z3 tuple encoding building a non-well-founded datatype is a
  DEFECT, not a preference.** The flattener is a workaround; `notes/coverage/
  scripts/st_encoders.py` is the discriminating experiment, kept so the fix can
  be checked against the same six cells.

What that first report says is itself a result and not a success: 5 paths,
`F 0, U 5`, every U now correctly `solver-unknown` (z3 ANSWERED "unknown" in
2-7 s). Before the claim-budget unit fix in the same round they were filed
`claim-budget-exceeded`, which would have sent the next reader to raise a
timeout that was never the problem.

## 8. resources — size the limit, and expect to lose everything on death

`--memlimit 8g` was copied, never chosen; whole-contract aqua peaks at
15.86 GiB. Size per contract.

A dying run produces NOTHING, and the loss is real work: the 8 g whole-contract
run died 51.5% through the solve having decided **938 claims and REFUTED 5** —
a third of that contract's 15 witnesses — and discarded all of it.

Three mechanisms, all verified:
* `report_coverage` sits after the per-claim job loop and INSIDE `run_thread`'s
  try, so a throw unwinds past it to the only verification-phase catch. **The
  report is lost even when the OOM is caught.**
* `branch_cov_active` is written only by `branch_coverage()`;
  `solidity_path_coverage()` writes none of the signal-safe atomics, so
  SIGALRM/SIGTERM/SIGINT emit nothing for path coverage.
* A mid-solve persistence mechanism already exists (the covered-set writer) and
  the collector has never passed `--coverage-covered-set` — but it persists only
  stable path ids, **so enabling it before the payload is persisted converts a
  lost witness into a permanently payload-less `F`**. Payload first, always.

Corpus cost of this: 27 runs killed at a 180 s outer timeout plus 2 solver OOMs,
every one contributing zero.

Evidence: `notes/coverage/scope-and-resources.md`.

## 9. `--all-witnesses` — OPEN

Fully wired, no coverage gate, and cheap: extra MODELS from one solver call
(one `push_ctx`, per witness a blocking clause + `dec_solve`, one `pop_ctx`), not
extra query rounds. Foundry collects per witness.

But `bmc.cpp:3087` gates the counterexample-payload harvest on
`if (is_path_cov && witnesses.empty())`, so only the FIRST witness reaches
`cov-report.json`. **Stage 2 therefore gets nothing from it today**, although up
to 16 counterexamples per feasible path is exactly the raw material its
refinement ladder and boundary witnesses want.

Closes when: the harvest gate is lifted and the report says how many witnesses
each F claim carries. Must stay opt-in.

Evidence: `notes/path-coverage-invocation-contract.md` §12.

## 10. `--function` — banned

It verifies a function in ISOLATION from an ARBITRARY contract state, so a
counterexample may rest on a state no `constructor() -> transaction sequence`
can reach on chain. This project's deliverable is a test that must be GREEN on
the unmodified contract, so such a counterexample becomes a RED test with
nothing marking it.

Removed from `pathcov_collect.py`; the library route now REFUSES and records its
reason instead of approximating. Measured cost of the removal: zero — every
library-route run produced 0 units and 0 paths.

~~Still present at `notes/coverage/scripts/forge_roundtrip.py` and must be
removed there too.~~ **DONE, verified 2026-08-01** by reading that file in full:
`--function` appears at four places and every one of them is prose — the comment
at `:136-159` explaining the removal, and the `skippedDetail` string at
`:166-171` that the refusal records. No command list in the file constructs it;
the library branch writes `"skipped": "library-has-no-dispatcher"` and continues.

The comment there adds one thing this row did not have. The old route "never
fired: every library-route run reported 0 complete path(s) across 0 unit(s)
because the functions reached were `internal`, and internal functions are not
units. But `ImmutablesLib.protocolFeeAmountCd` and three siblings are
`external` — units by visibility, sitting on this route in the corpus today.
**The route was correct only because its inputs happened to be internal, which
nothing checks.**" So the measured cost of removal being zero was luck, not a
property of the ban.

---

## The settled command line

⛔ **THERE ARE TWO, AND PRINTING ONE WAS AN ERROR** (measured 2026-08-01,
`notes/coverage/D25-baseline-is-one-transaction.md`). Rows 1 and 2 are correct
about REACH and are not withdrawn. What they do not settle is the axis where a
LOCKED opponent fixes the depth for us:

* the **GATE** run is compared against the branch-coverage baseline, and that
  baseline is **measured** to run at ONE transaction — cells A/B/C/D of D25 are
  identical on reach, so naming the bound changes nothing and the default really
  does resolve to one. Worse, cell E shows the BASELINE ITSELF goes 5/8 → 8/8 at
  `--solidity-max-tx 2`. Running our side at 2 against a baseline at 1 is not
  "using the settled configuration", it is running deeper than the thing being
  compared to. **The gate run stays at `--solidity-max-tx 1`.**
* the **ARTEFACT / enumeration** run has no second party to match, and there the
  question is what this method can reach. **Whole contract + `--solidity-max-tx 2`,
  as rows 1 and 2 say.**

This is the same asymmetry `EXECUTION_PLAN.md` §5 already states for the harness
(enumeration may be relaxed, certification must be tightened to the artefact);
it had simply never been applied to the transaction axis.

### (a) the ENUMERATION / ARTEFACT command line

```
esbmc <flat>.solast --sol <flat>
      --solidity-path-coverage
      --contract <C>
      --solidity-max-tx 2
      --cov-report-json
      --path-cov-max-goals 10000
      --memlimit <sized for this contract, not 8g by habit>
```

### (b) the GATE command line — identical but for one value

```
      --solidity-max-tx 1
```

and it is `pathcov_collect.py` that issues it. A run of (a) may never be quoted
into the branch-coverage gate table, and a run of (b) may never be quoted as the
method's reach.

⚠ The havoc suspicion that motivated D25 was **refuted**, and is recorded so it
is not raised again: the baseline's `--k-induction --unlimited-k-steps` buys it
**no reach at all** (cell A ≡ cell B). Its coverage does not rest on an entry
state no call sequence produces, so the gate's bar is a legitimate bar.

no `--function`, no `--focus-function`, no bounding strategy, no
`--coverage-multi-tx`, no `--no-slice`, no `--no-simplify`.

**Add THREE flags when the run is meant to EMIT TESTS** (amended 2026-08-01 —
row 6):

```
      --overflow-check --div-by-zero-check --path-cov-arith-resolve
      --generate-foundry-testcase
```

Without them a witnessed path whose counterexample wraps or divides by zero is
rendered as a bare call asserting a NORMAL exit, and is RED on the unmodified
contract — measured three times across the PoC set. `--path-cov-arith-resolve`
REFUSES to run if neither check is enabled, so this cannot be half-copied into a
silent no-op. It costs at most one extra query per witnessed path that carries a
checked operation (measured: 3 claims in 0.008s on a toy, 8 in 0.024s on
another), and it changes nothing on a path with no such operation.

⚠ **This block is for the EMISSION run, not the enumeration run**, and the two
answer different questions. Row 6's original argument — the checks are
single-successor asserts, so they cannot change the enumerated path SET — is
untouched; what they change is which MEMBER of a path's domain the solver
returns.

**This block previously printed `--focus-function <f> --solidity-max-tx 1`,
i.e. the configuration rows 1 and 2 OVERTURNED**, and anyone who copied it
copied the overturned cell. Whole contract at `--solidity-max-tx 2` is the only
configuration measured to reach cross-function state at all (`Tiny` 75% -> 100%,
`Tiny3` 71.4% -> 100%, `P09_TimeLock` 71.4% -> 100%, `P20` 71.4% -> 100%; a
k-hop setup needs `tx = k+1`, pinned by `P04_Chain2` at 88.9% / 100% / 100% for
tx 2 / 3 / 4).

`--focus-function` is still WANTED, for two things it is now good at and one it
never was:

* DEBUGGING a contract that does not finish. It narrows instrumentation as well
  as dispatch, so st1inch's `setFeeReceiver` goes from 275 instrumented paths to
  5 and the failure lands on one claim instead of the whole contract.
* Splitting a large contract across runs, now that it takes a SET
  (`--focus-function deposit,withdraw`) and unions through
  `--coverage-covered-set`.

  ⚠ **This was challenged and the challenge was refuted by measurement, so the
  recommendation stands WITH its evidence attached.** `goto_coverage.cpp`
  fingerprints the covered set on unwind / cap / contract and states in a comment
  that `--focus-function` is DELIBERATELY excluded, followed by a second comment
  calling the file "an UNATTRIBUTABLE union". Since `enc` is built per unit by
  the same recurrence, two units routinely carry the same small integers — so if
  a stored id were the bare path identity, unioning two focused runs would let
  one unit's witnessed path mark another unit's covered.
  `notes/coverage/scripts/covered_set_focus_check.py` ran the two focused halves
  of `poc/Tiny.sol` through one covered set:

  ```
  after --focus-function deposit    3 ids: 20de17aabff5849f a002de3d11d390ab f273a9d2627a6d05
  after --focus-function withdraw   6 ids: (those three, plus 6309533331637c0c a4d0663fdc70c843 eff4c268fd4931af)
  ```

  Every id is a content address, not an `enc`; the union GREW and run 1's ids
  survived; and the three ids each run contributed match its own F count exactly
  (deposit 3 of 3, withdraw 3 of 5). **No cross-unit collision is possible on
  this evidence.**

  What the code's own "unattributable" comment says is still true and is a
  different point: the file records no per-id configuration, so a PERCENTAGE
  computed from a union belongs to no single invocation. That is a caveat for a
  consumer, not a collision — and `pathcov_collect.py` does not pass
  `--coverage-covered-set` at all, so the gate is not exposed to it.
* It is NOT the measurement configuration. Focused runs cannot reach
  cross-function state at any tx bound -- every transaction is another call to
  the same entries -- which is exactly the hole that made rows 1 and 2 wrong.

Bounded from OUTSIDE by a subprocess timeout — `--timeout` is useless here,
because the partial-result rescue is gated on branch coverage and a path-coverage
run killed by it emits nothing at all.

## What this table does NOT license

None of it converts the 0/5 branch-coverage gate result into a pass. It removes
the objection that the gate was measuring an arbitrary cell: eight of ten
dimensions are now decided against measurements, and the two structural losses
that remain (cross-function entry state; a dying run discarding decided work)
are named, quantified, and have identified change sites.
