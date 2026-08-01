# EscrowDst: is the gate number suppressed by OUR outer timeout? — readings fixed BEFORE the run

**Recorded 2026-08-01 14:05, before the run.**

## What the corpus actually says, once the skips are separated from the failures

`notes/coverage/pathcov/cross_chain_swap_EscrowDst/runs.jsonl`, all 18 lines:

| class | n | outcome |
|---|---|---|
| libraries (`ImmutablesLib` ×10, `TimelocksLib` ×3, `ProxyHashLib` ×1) | 14 | **deliberately skipped**, `library-has-no-dispatcher` |
| `BaseEscrow.rescueFunds` | 1 | **F = 8, U = 0** — every path witnessed |
| `EscrowDst.cancel` | 1 | **F = 12, U = 0** — every path witnessed |
| `EscrowDst.withdraw` | 1 | killed by the collector's 300 s outer timeout |
| `EscrowDst.publicWithdraw` | 1 | killed by the collector's 300 s outer timeout |

**A number I quoted earlier ("2 of 18 produced reports") is misleading and is
withdrawn.** 14 of the 18 are libraries the collector skips on purpose, with the
reason recorded in the row: a library has no dispatcher harness, so
`--contract <Lib>` finds no targets, and the only other route is `--function`,
which this project forbids because it verifies from an arbitrary state and can
produce a counterexample no reachable state supports — i.e. a RED generated
test. Of the 4 units that are actually runnable, 2 produced reports and **both
are at 100 %**.

⇒ EscrowDst's 5-of-18 on the branch gate decomposes as: 8 decisions inside
skipped libraries (a MEASUREMENT-SCOPE difference, the same shape already
recorded for EscrowSrc, and now with the mechanism stated by the collector
itself) plus two units that never finished.

## The two killed units are NOT the same failure

`killed_triage.py` already separated them and the separation is the point:

* `EscrowDst.withdraw` — **symex completes** (132.8 s of a 180 s budget) and then
  6 of 425 claims are solved before the outer kill. The cost is the claim COUNT.
  This looks like a budget outcome.
* `EscrowDst.publicWithdraw` — **never printed a VCC count at all**, so symex did
  not finish. This is the defect candidate (task #22), and its sibling `withdraw`
  in the same contract is its own control.

## What is being varied, and what is deliberately NOT

Only the **outer wall-clock timeout**, 300 s → 900 s. Everything else identical
to the corpus row, verbatim from `runs.jsonl`.

**The per-query cap stays at 120 s and `--memlimit` stays at 8g.** This project's
standing rule is that raising a limit is not an answer, and the rule is being
respected in the direction that matters: the question here is not "can we make
the number better", it is "is the reported number a property of the method or of
a collector setting nobody controlled". Those need different sentences in the
paper. If the answer is the latter, the honest report is that the collection
suppressed witnesses — not a quietly re-run better figure.

## The readings, fixed in advance

**A. It completes, with F > 0.** Then `withdraw` is budget-bound, not
intractable, and **EscrowDst's gate score was decided by the collector's 300 s
outer timeout** — a configuration axis that has never appeared in any table.
That is the `config-is-the-independent-variable` finding on a third axis (after
tx/strategy and slice/simplify). The required follow-up is to report the axis,
not to re-collect at 900 s and print the better number.

**B. It completes with F = 0 (all U).** Then the extra time bought solving but
not witnesses, and the unit belongs with st1inch's story rather than with
`cancel`'s. Check the U reasons before saying which.

**C. Still killed at 900 s.** Then the claim count really is the wall, and the
honest report is 425 claims at this unwind bound, with the per-claim cost. **This
is not a win and it is written here so it cannot be reported as one.** The next
move would be the per-unit goal cap or a reduction, NOT 1800 s.

**D. Symex does not finish this time either.** Then `withdraw` has become the
same shape as `publicWithdraw`, my reading of `killed_triage` was wrong, and the
control I was relying on is gone.

## OUTCOME: none of A/B/C/D. The run died of `std::bad_alloc`, not the timeout — and the reason it needed the memory is that it was solving FIVE claims eighty-five times

Recorded after the run. The pre-registered readings all assumed the binding
constraint was wall clock. It was not, and saying "closest to A" would be
laundering a miss into a hit, so the outcome is written as its own row.

    Report Completeness: PARTIAL — std::bad_alloc during the per-claim solve.
                         46 of 425 claim(s) had been decided.
    Complete Paths : 5      Reached : 4      Path Coverage: 80%
    Path Status: F 4, I 0, U 1   (bounded-holds 1)
    Verdicts Preserved: 8
    Solver: Bitwuzla • Decision procedure total time: 30.771s

**The unit is not intractable.** Four of its five paths are witnessed, and the
corpus row for it says `reportPresent: false` — under the collector's 300 s
outer timeout this unit produced *nothing*. The mid-solve persistence machinery
is what turned a dead run into an 80 % result.

**The cost is redundancy, and it is measurable rather than inferred.** Every
`Solving claim` / `PASSED` / `FAILED` line in all 106 059 log lines, classified:

    distinct claim KEYS actually solved: 5
    total solve events: 46

      solved 10  failed 10   withdraw:path:31
      solved  9  failed  9   withdraw:path:14
      solved  9  passed  9   withdraw:path:2
      solved  9  failed  9   withdraw:path:30
      solved  9  failed  9   withdraw:path:6

425 VCCs over **5 distinct keys** — the run intended ~85 solves per path. All
four witnesses were in hand after the first 46 solves; the remaining 379 would
have re-derived the same four and then exhausted 8 GiB.

**Mechanism, from the same log.** Recursion unwind lines, whole file:

    619  _ESBMC_Nondet_Extcall_EscrowDst
    611  _ethTransfer
    448  _uniTransfer
    155  withdraw
    155  _withdraw_onlyValidImmutables
    155  _withdraw_onlyValidSecret

An external call is modelled as nondet re-entry into this contract's own
dispatcher, which calls `withdraw` again. `withdraw`'s body is instantiated 155
times, and **every physical copy carries its own copy of the five exit
assertions**, so one instrumented claim becomes 85 VCCs sharing one claim key.

⇒ **This is the same defect the tool already names on st1inch** ("Verdicts
Preserved: 2 … the same claim key was solved more than once, which is a separate
defect", 10 VCCs for 5 paths). Two orders of magnitude apart — 2× there, 85×
here — so it is NOT an st1inch peculiarity, which is how it had been filed.

### Why this matters more than the timeout question it was asked to settle

For COVERAGE, `F` is monotone: once a path has a witness it is covered, and a
second witness of the same path is `--all-witnesses` material, not coverage.
So every solve of an already-refuted key is pure cost. On this unit that is
~90 % of the planned work, and it is what turns a finished 80 % result into an
out-of-memory death.

⚠ **Not yet established, and it must not be assumed:** that skipping
already-decided keys is safe in general. Path coverage deliberately sets
`keep-verified-claims` (`EXECUTION_PLAN` §3.6). Whether that is load-bearing for
something else, or a default carried over, has to be read before anything is
changed — this project has already shipped a mechanism that was right for one
shape out of four.

⚠ **One unit.** `publicWithdraw` is untouched and still has symex not finishing,
which is a different failure. The 8 library decisions are a scope question, not
a cost question.

## What this will not settle

One unit of one benchmark. `publicWithdraw` is untouched by this run — it is the
defect candidate and needs its own reduction. And nothing here speaks to the 8
library decisions, which are a scope question and not a timeout question.
