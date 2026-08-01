# z3 says `out of memory` — the readings, fixed BEFORE the discriminating run

**Recorded 2026-08-01 13:30, before the run.** Same rule as
`D14-what-each-outcome-would-mean.md`: a reading written afterwards is not a
reading.

## What was just measured

Three lines added to `z3_convt::dec_solve` (`src/solvers/z3/z3_conv.cpp:96-109`)
to ask z3 for the reason it has always had and this tree has never requested.
`st1inch --contract St1inch --focus-function setFeeReceiver --z3
--tuple-node-flattener --solidity-max-tx 1 --memlimit 4g`, binary
`1e46eac1c33177f73fdadc41398e061c`:

    WARNING: z3 returned `unknown` (reason: out of memory)   x 8, every no-verdict solve
    path:13 / path:12 first solve   0.009-0.010s   ✓ PASSED
    U Reasons: bounded-holds 2, solver-unknown 3

**Every single no-verdict on this unit is z3 failing to allocate.** Not an
incomplete fragment, not a quantifier, not a hard instance.

## Why this needs a discriminator rather than a conclusion

`--memlimit 4g` makes ESBMC call `setrlimit(RLIMIT_DATA, 4 GiB)`
(`esbmc_parseoptions.cpp:779-796`). z3's allocator hits it, throws, and z3
converts that into `unknown` rather than dying — so the limit is OURS and it is
being reported as if it were the SOLVER'S.

**This project's standing rule is that raising a limit is not an answer**
(`no-workarounds-cap-the-query`). This run is not a workaround and must not turn
into one: it is the single measurement that decides WHICH limitation st1inch's
0 % belongs to, and the two belong in different Threats entries. Whatever it
says, the fix is NOT "collect the corpus at a bigger memlimit and report the
better number" — see "what this does not license" below.

## The run

Identical in every respect except `--memlimit`, which goes 4g -> 16g. One run.
Machine has 40 GiB MemAvailable, one esbmc at a time, so 16g is inside the
project's own N x memlimit <= 60 % rule with a single process.

## The readings, fixed in advance

**A. The 8 unknowns become SAT and/or UNSAT.** Then st1inch's `solver-unknown`
is **our memory budget**, not solver capability, and three things follow that are
worse for the current write-up than they look:

  * the sentence "st1inch's 0 % is the lower bound of solver capability, not a
    measurement of reachability" — already in `EXECUTION_PLAN.md` §10 and
    slated for the paper — is **wrong** and must be withdrawn, not softened;
  * `--memlimit` becomes a controlled dimension of the invocation matrix that
    has never been controlled. Every corpus number was collected at one
    unmeasured setting of it. That is the `config-is-the-independent-variable`
    finding a second time, on a different axis;
  * the "zero SAT in 195 solves ⇒ 0 % is forced" argument loses its premise,
    because the solves that could have been SAT never finished.

**B. Still `out of memory` at 16g.** Then 4 GiB was not the binding constraint
and z3 is hitting something else it also calls "out of memory" — most likely one
of its own `memory_max_size` / `memory_high_watermark` parameters, which this
tree sets nowhere (`z3_conv.cpp:58-73` is the complete parameter list). Next
step is to read z3's defaults for those, NOT to raise the limit again. **This
outcome is not a win and it is written here so it cannot be reported as one.**

**C. The process dies (killed / bad_alloc / PARTIAL report) instead of z3
returning unknown.** Then at 16g the allocation failure lands outside z3's
catch, which means the 4g behaviour was z3 absorbing an OOM that ESBMC would
otherwise have reported honestly — and the `solver-unknown` token has been
*masking* an out-of-memory this whole time. That is a reporting defect in its
own right and outranks the coverage question.

**D. Some become decided and some stay `out of memory`.** Then the limit is
binding for part of the workload; report the split, and do not describe either
half as the property of the contract.

## OUTCOME (added after the run, labelled as such): **B**, and B is not a win

| `--memlimit` | no-verdict | z3 reason | per-solve | verdicts |
|---|---|---|---|---|
| 4g | 8 of 10 | `out of memory` | 1.98–7.08 s | bounded-holds 2, solver-unknown 3 |
| **16g** | **8 of 10** | **`out of memory`** | **19.73–24.32 s** | **identical** |

Total decision time 24.8 s → 173.7 s. The two decided claims are 0.012 s at both
limits.

**One datum B did not anticipate, and it settles B's own follow-up question.**
B said the next step would be to read z3's `memory_max_size` /
`memory_high_watermark` defaults, on the theory that 4 GiB was not the binding
constraint. **It was binding, and so is 16 GiB**: the per-solve time scales with
the limit (roughly 4× memory → 4× time), which is only possible if the extra
12 GiB was actually consumed before the failure. An internal z3 cap would have
produced the *same* time at both settings. So the failure is `RLIMIT_DATA`
(`esbmc_parseoptions.cpp:779-796`) being exhausted by z3, not a parameter z3
sets for itself — and reading those defaults is no longer the next step.

⇒ **What st1inch's 0 % is:** a solver **memory blow-up** on an
875-assignment formula, unbounded in the useful sense — 4× the budget buys 4×
the wall clock and the same answer. It is a real limitation and it is NOT
fixable by raising the limit. But it is a *different* limitation from the one
the plan currently records.

### The wording that has to change, and it is not a softening

`EXECUTION_PLAN.md` §10 says st1inch's 0 "是「求解器能力的下界」，不是「到达能力的
测量」" — the lower bound of SOLVER CAPABILITY. That is wrong in a way that
matters: "the solver cannot decide this fragment" and "the solver ran out of
memory building it" are different claims with different Threats entries and
different fixes. The measured statement is the second. Nothing about
reachability changes (`unit-not-entered` is still 0).

### What is now testable that was not before

The corpus's confound was that st1inch is the ONLY benchmark run with
`--z3 --tuple-node-flattener` and the ONLY one with any `solver-unknown`. That
pair was added because bitwuzla never returns, cvc5 throws `std::bad_alloc`, and
**plain `--z3` core-dumped in the encoder** — until the non-well-founded datatype
fix (`b22932b627`), after which §10 records plain `--z3` completing st1inch.

So the encoder arm can now be run **on st1inch itself** rather than on aqua as a
proxy. Same unit, same everything, drop `--tuple-node-flattener`:

* **default (z3 native tuple sort) decides them** ⇒ the OOM belongs to the
  flattener's eager encoding (per source: it removes z3's tuple sort entirely
  and hands struct-arrays to `array_convt`, which fully expands bounded arrays
  and emits **quadratic** Ackermann constraints — `array_conv.cpp:1306-1333`).
  Then st1inch's 0 % is an artefact of the flag we added to make it runnable at
  all, which is the strongest possible version of this finding and the worst for
  the current write-up.
* **default also OOMs** ⇒ the blow-up is not the flattener; it is the formula or
  the harness, and the encoder is exonerated on st1inch as it already was on
  aqua.
* **default core-dumps again** ⇒ the struct-tag fix does not cover this unit.
  Record it and stop; do not retry with more memory.

## ENCODER ARM, on st1inch itself — the second branch: the encoder is EXONERATED

Same unit, same everything, `--tuple-node-flattener` dropped so z3's native
tuple sort is used (`solve.cpp:162-179` → `z3_conv.cpp:1007-1047`,
`Z3_mk_tuple_sort`):

| arm | memlimit | no-verdict | reason | decided | total solve |
|---|---|---|---|---|---|
| `--z3 --tuple-node-flattener` | 4g | 8/10 | `out of memory` | 13, 12 @0.010s | 24.8 s |
| `--z3 --tuple-node-flattener` | 16g | 8/10 | `out of memory` | 13, 12 @0.012s | 173.7 s |
| **`--z3` (native tuples)** | **8g** | **8/10** | **`out of memory`** | **13, 12 @0.012s** | **79.4 s** |

`U Reasons: bounded-holds 2, solver-unknown 3` in **all three**. Total solve time
tracks `--memlimit` and not the encoder.

⇒ **Both encodings fail the same way on the same claims.** The corpus's
standing block — "st1inch is the only benchmark on that flag pair and the only
one with `solver-unknown`, so backend and contract are fully confounded;
attribution is forbidden until a split experiment" (`EXECUTION_PLAN.md` §10) — is
**lifted**, and the attribution it was guarding is: the unknowns are a z3 memory
blow-up, independent of the tuple encoding.

This is a stronger result than the aqua proxy gave. The earlier three-arm
experiment (`encoder_arms.py`) ran on `aqua safeBalances` because plain `--z3`
core-dumped on st1inch; the non-well-founded-datatype fix (`b22932b627`) removed
that obstacle, so the arm could finally be run on the contract in question rather
than on a stand-in.

**What it does NOT say.** It does not explain *why* an 875-assignment formula
exhausts 16 GiB. Two claims out of ten are decided in 0.012 s on the same
harness, so whatever blows up is specific to paths 15/14/2 and not to the
contract as a whole. That is the next question and it is open.

## What this does NOT license, whichever way it goes

* **Not a re-collection at a bigger memlimit to get a better number.** If A
  holds, the honest move is to report that the corpus was collected under a
  memory budget that suppressed witnesses, and to say so with the number of
  affected claims — not to quietly re-run and print the improved coverage.
* **It is one unit of one benchmark.** `setFeeReceiver` has 5 paths and 10 VCCs.
  The other twelve st1inch units, and the four benchmarks that ran on the
  default backend with **zero** no-verdicts, are not covered by this at all.
* **It says nothing about the duplicate-key defect.** 10 VCCs for 5 paths is a
  separate finding, already named by the tool's own `Verdicts Preserved` line.
