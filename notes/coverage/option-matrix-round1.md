# The invocation matrix, round 1 — the tx/strategy dimension on `Aqua.safeBalances`

Every corpus number produced so far comes from ONE cell:

```
--contract C --focus-function f --solidity-max-tx 1
   (no --unwind; the path-coverage pass installs 4 itself)
   (no bounding strategy at all)
   (slicing on, protected by a 183-symbol exemption list)
   (simplification on: 6938 VCC -> 1822)
   outer timeout 180 s, --memlimit 8g
```

That cell was never compared against any other, so there was no evidence it was
the right one — only evidence that it did not crash. This is round 1 of closing
that gap. Target: `aqua__Aqua.flat.sol`, `--contract Aqua --focus-function
safeBalances`, 300 s per cell, 8 g.

| cell | exit | report | paths | units | F | U | F w/ inputs | wall s | peak child MB |
|---|---|---|---|---|---|---|---|---|---|
| `tx1` | 1 | yes | 2846 | 6 | 2 | 2844 | 2/2 | 3.3 | 276 |
| `tx2` | 1 | yes | 2846 | 6 | 2 | 2844 | 2/2 | 6.0 | 352 |
| `tx0` | 1 | yes | 2846 | 6 | 2 | 2844 | 2/2 | 3.1 | 352 |
| `--coverage-multi-tx --unwind 4` | 1 | yes | 2846 | 6 | 2 | 2844 | 2/2 | 5.1 | 353 |
| `--coverage-multi-tx --incremental-bmc` | **0** | yes | 2846 | 6 | 2 | 2844 | 2/2 | **146.7** | **2745** |
| `--coverage-multi-tx --k-induction` | **0** | yes | 2846 | 6 | 2 | 2844 | 2/2 | 9.0 | 2745 |
| `--coverage-multi-tx` alone | **-6** | no | - | - | - | - | - | 0.1 | abort |
| `--coverage-multi-tx --solidity-max-tx 1` | **-6** | no | - | - | - | - | - | 0.1 | abort |

## Four results

**1. On this unit the transaction dimension changes nothing.** Six legal cells,
identical output: 2846 paths, 6 units, F = 2, U = 2844, both F claims carrying
inputs. What differs is cost — up to 44x wall and 10x peak memory. This is the
invocation contract's §2 prediction holding in a measurement: under
`--focus-function f` every transaction is another call to `f`, so a path guarded
by state that only another public function establishes is unreachable at every
tx bound, and buying more transactions buys nothing.

**ONE UNIT, AND ITS NAME IS PART OF THE CLAIM.** `safeBalances` is a reader with
almost no state guarding it — 2 F against 9 `bounded-holds` in the corpus
collection. The units where the tx dimension COULD matter are the ones with a
large `bounded-holds` count (`Aqua.dock` 2/61, `FarmingPool.deposit` 7/147,
`FarmingPool.withdraw` 7/96). A null result here does not transfer to them and
must not be generalised until they are run.

**2. The two illegal cells are illegal by design, and the tool says why.**

```
ERROR: --coverage-multi-tx keeps the unbounded multi-transaction dispatcher loop
live; it needs a global bounding strategy. Add --incremental-bmc (recommended:
discovers the transaction depth dynamica...

ERROR: --coverage-multi-tx is incompatible with --solidity-max-tx: an explicit
tx bound forces a deterministic unroll whose Foundry reconstruction is
unreliable. Bound the live dispatcher loop with --...
```

Both abort in 0.1 s with a diagnostic. They are in the matrix rather than
omitted from it, because "this combination is refused" is a result.

**3. EXIT CODE IS NOT COMPARABLE ACROSS STRATEGIES.** `tx1` / `tx2` / `tx0` /
`multi-tx+unwind` exit **1** (`VERIFICATION FAILED`, which under coverage means
a claim was refuted, i.e. a path was witnessed — the success signal). But
`multi-tx+incremental-bmc` and `multi-tx+k-induction` exit **0** with the same
F = 2 and the same report.

Any collector that reads success from the exit code silently flips meaning when
the strategy changes. `pathcov_collect.py` records `exitCode` but keys nothing on
it; `option_matrix.py` prints it beside the report contents rather than instead
of them. Both are correct today, and this is written down so that neither is
"simplified" later into an exit-code check.

**4.** ~~`--focus-function` does not reduce what is INSTRUMENTED.~~ **⛔ NO LONGER
TRUE, and struck rather than deleted because the reasoning it supported is still
worth reading.** As measured here — all six cells reporting 2846 paths across 6
units, the whole contract's path set, with 2844 claims `unit-not-entered` — this
was a correct statement about the build of the day. The instrumentation
narrowing landed afterwards (task #1), and the tool now says so on stdout:

    --solidity-path-coverage: --focus-function 'setFeeReceiver' narrowed
    INSTRUMENTATION to 1 unit(s); 38 other in-scope unit(s) were not enumerated
    at all. Their paths are absent from the denominator ON PURPOSE ...

Re-measured 2026-08-01 on `notes/coverage/poc/F01_MultiFocus.sol` (three units
with three different path counts, built for exactly this):

    one = 3    two = 4    three = 6    whole contract = 13    (3 + 4 + 6)
    one,two = 7, and the run prints "2 unit(s) kept, 1 other(s) dropped"

**What survives the strike** is the warning attached to it: a per-report
`paths_total` from a collection made BEFORE the narrowing is a contract-level
number wearing a unit-level label, and any table quoting those older reports
still has to read it that way. What is withdrawn is only the present tense.

## Round 2 — `Aqua.dock`, and the flag that silently deletes every witness

`dock` was chosen because it is the opposite of `safeBalances`: 2 F against 61
`bounded-holds` in the corpus, i.e. a unit whose paths are mostly state-guarded.
If the transaction dimension ever mattered, it would matter here.

**tx dimension, `dock`:** identical to `safeBalances`. tx1 / tx2 / tx0 /
multi-tx+unwind / multi-tx+k-induction all give F = 2, and the 61 bounded-holds
paths stay bounded-holds in every one of them. multi-tx+incremental-bmc died at
357 s with `cvc5::internal::Minisat::OutOfMemoryException` at 8 g.

⇒ Two units now agree: **with `--focus-function` on, no transaction setting buys
a single extra witness.** The contract's §2 prediction is measured, not argued.

**slice x simplify, `dock`, tx1:**

| slice | simplify | exit | F | F w/ inputs | U | wall |
|---|---|---|---|---|---|---|
| default | default | 1 | **2** | 2/2 | 2844 | 9.5 s |
| default | `--no-simplify` | **0** | **0** | 0/0 | **2846** | 11.2 s |
| `--no-slice` | default | 1 | **2** | 2/2 | 2844 | 14.7 s |
| `--no-slice` | `--no-simplify` | **0** | **0** | 0/0 | **2846** | 17.8 s |

**`--no-slice` changes nothing.** Same F, same inputs, same U, in both
simplification settings. The 183-symbol exemption list is adequate here, and the
worry that counterexample INPUTS are silently sliced away is answered: 2/2 F
claims carry inputs with slicing on. (One unit. `dock` takes two scalar
coordinates; a unit with struct or dynamic-array parameters is a different
question.)

**`--no-simplify` deletes both witnesses.** F goes 2 -> 0, and it does so in the
worst possible way: 11 seconds, **exit 0**, report written normally, U = 2846,
no OOM, no timeout, no warning of any kind. The run reports calmly that all 2846
paths hold. Nothing anywhere says a query was weakened.

This is `no-verdict-is-not-no` in its most dangerous form — a run that looks like
a completely successful verification and has witnessed nothing.

### Why this is not a curiosity

`--path-cov-assert` FORCES `--no-simplify`. `--path-cov-certify` does not.

Independently of this matrix, the stage-4 wiring hit a contradiction on the SAME
contract and the SAME unit: `Aqua.dock` enc=12, one region, two gates —

* `--path-cov-certify` -> `RESULT: CERTIFIED`, its non-vacuity witness REFUTED
  (an execution in the region does walk the path);
* `--path-cov-assert` -> `THE REGION IS VACUOUS`, the same witness PASSED, and
  all six mutually contradictory ladder rungs passing beside it.

The 2x2 above supplies the mechanism: in the configuration `--path-cov-assert`
forces, this tool cannot witness `dock`'s paths at all. A witness that cannot be
refuted reads as "no execution walks this path", which is exactly the vacuity
verdict — and every assertion rung then holds for want of an execution.

⇒ **Every stage-3 assertion so far was proved under a configuration in which the
path cannot be witnessed.** That is not assertion strength; that is vacuity. The
isolation experiment (does the certify verdict flip when `--no-simplify` alone is
added?) is running separately and is the thing that decides which side is wrong.

⇒ It also means the direction of my earlier worry was backwards. I expected
simplification to SWALLOW witnesses; measured, it is switching simplification
OFF that loses them.

### Round 3 — the effect is CONTRACT-SPECIFIC, and the mechanism is not the
### obvious one

Replicating the same 2x2 on two more units settles both the scope and the
mechanism, and narrows an overclaim made from the `dock` table alone.

| unit | slice x simplify | F in all four cells |
|---|---|---|
| `Aqua.safeBalances` | all four | **2** — no effect |
| `FarmingPool.deposit` | all four | **7** — no effect |
| `Aqua.dock` | `--no-simplify` cells | **0** vs 2 — the only unit that moves |

So "`--no-simplify` deletes witnesses" is NOT a general property of the flag. It
is a property of a path that runs through a library loop which simplification
was previously folding away. Any statement of the form "stage 3's assertions are
vacuous" is therefore **withdrawn**: what stands is that this flag changes the
deliverable on some units and not others, which is exactly the kind of thing
that can only be measured.

**The mechanism, read out of the reports rather than guessed** (all three via
`report_summary.py`, so the verdict comes off the artefact and not off an exit
code):

| config | F | bounded-holds | not-solved-this-run | decision steps |
|---|---|---|---|---|
| default | 2 | 61 | 0 | 4 |
| `--no-simplify` | **0** | **63** | 0 | 0 |
| `--no-simplify --partial-loops` | 2 | 61 | 0 | 4 |

The two lost witnesses do NOT become "never asked". `not-solved-this-run` is 0
in all three; the count that grows is `bounded-holds`, 61 -> 63. **The claim was
asked, and the tool answered that the path does not hold — when it does.**

That rules out the first explanation offered for this (that simplification folds
a claim to `true` in `goto_symext::claim` and it never reaches `assertion()`).
Those claims would surface as `not_solved_this_run`, and none did. The real
chain, confirmed by `--partial-loops` restoring F=2 in one flag:

* `--no-simplify` stops `do_simplify` folding loop guards
  (`symex_goto.cpp:20`), so `__memset_impl` (`c2goto/library/string.c:298`) is
  actually entered;
* it is truncated at the coverage-forced `--unwind 4`;
* `no-unwinding-assertions` is forced unconditionally
  (`esbmc_parseoptions.cpp:4305`), so `loop_bound_exceeded` installs an
  ASSUMPTION (`symex_goto.cpp:492-493`) that assumes away precisely the
  executions that witness the path.

`--unwindset 64:512` also restores F = 2, while `1:64`, `62:16` and `64:64` do
not — so the loop's trip count is > 64 and <= 511.

**Which side of the certify/assert contradiction is wrong: the assert side.**
`--path-cov-assert` forces `no-simplify` at `esbmc_parseoptions.cpp:4223` and is
the only one of the three path-cov sub-modes that does. Its `RESULT: VACUOUS` on
`Aqua.dock` enc=12 is a false verdict, and the PUT that
`solidity_path_put.py` refused on that signal is a lost deliverable, not a
property of the region.

**The generalisation that does hold, and it is the serious one:** forcing
`no-simplify` inside a mode that also forces `no-unwinding-assertions` is unsafe
in general. On this contract any `--solidity-path-coverage` run carrying
`--no-simplify` reports `VERIFICATION SUCCESSFUL`, exit 0, 0% coverage, with
nothing but the generic under-report warning to show for it.

## What round 1 does NOT answer

* the same matrix on a state-guarded unit (`dock`, `deposit`, `withdraw`) —
  running;
* the slicing dimension: is the 183-symbol exemption list complete, i.e. does
  `--no-slice` change the report at all? The `F w/ inputs` column is in the
  table for this, and on this unit it is 2/2 in every cell — but 2 claims is not
  a test of an exemption list;
* the simplification dimension: 6938 VCC -> 1822, and what happens to a
  path-coverage claim that is simplified away is unread;
* whether any of this transfers to the whole-contract configuration, which died
  of solver OOM at 8 g and discarded everything it had already decided.

  **CORRECTION.** An earlier version of this line, and the commit message that
  introduced this note, said that run had solved "5100+ claims". It had solved
  **938**, and refuted **5**. The log carries two `✓ PASSED` shapes that mean
  opposite things — solve-time (`bmc.cpp:2888`) and symex-time simplification
  (`symex_main.cpp:82`) — and the second occurs 5116 times, which is exactly
  `6938 - 1822`, the simplification delta printed on the VCC line. I counted the
  simplified-away claims as solved ones. The direction of the finding is
  unchanged (a dying run discards real decided work, and five refuted paths is a
  third of that contract's 15 witnesses), but the magnitude is 938, not 5100.

  Both dimensions are now answered in `notes/coverage/scope-and-resources.md`:
  whole-contract completes at `--memlimit 20g` (777.8 s, 15.86 GiB peak — 1.98x
  the 8 GiB it had been given, so it could never have finished), and its F set
  compared AS A SET against the union of the per-method reports is 15 both
  sides, 0 only-whole, 0 only-per-method. **Whole-contract reaches nothing focus
  cannot, at 2.4x wall and 2x memory. `--focus-function` stays.**
