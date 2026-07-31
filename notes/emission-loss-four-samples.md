> **READ THIS FIRST — the word "retains" below is WITHDRAWN.**
>
> Every table in this file that says "emission retains X%" was produced with a
> denominator from a DIFFERENT set of esbmc runs than the numerator.
> `emission_loss.py` read `enumerated` from
> `notes/coverage/pathcov/<bench>/reports/` — the sweep's runs — while `emitted`
> came from the forge lcov of tests produced by `forge_roundtrip.py`'s own esbmc
> runs. The two shared a benchmark name and a 180s budget and nothing else, so a
> difference BETWEEN THE RUNS is indistinguishable from a loss in the emitter.
> A ratio across two runs cannot carry a causal word.
>
> Found by an adversarial audit; confirmed by reading `emission_loss.py:37`
> against `:56`. The numbers themselves are not withdrawn — what they are a
> ratio OF is.
>
> **Say instead:** "across four benchmarks the emitted suite's forge lcov covers
> 40–56% of the canonical decisions the enumeration's F paths walk."
>
> FIXED FOR THE NEXT MEASUREMENT: `forge_roundtrip.py` now passes
> `--cov-report-json` so each emit run writes its own report, and
> `emission_loss.py` prefers those and PRINTS `same-run` vs `cross-run` before
> any number. Re-running the four benchmarks is what turns this band into a
> retention rate. Until then the tables below are `cross-run`.

# The emission loss on two benchmarks, and what the second one narrows

aqua's numbers were a single sample and were being stated as general. The second
sample narrows almost every one of them.

|  | aqua_Aqua | farming |
|---|---|---|
| bar (branch coverage) | 7 / 8 | 26 / 26 |
| native (project's own suite) | 6 / 8 | 26 / 26 |
| **ours (generated suite, forge)** | **2 / 8** | **10 / 26** |
| enumerated (witnessed paths walk) | 4 | 18 |
| **lost in emission** | **2** | **8** |
| empty-body refusals | 4 | **0** |
| RED tests disabled | 1 | **14** |
| defaulted args | 28 | 174 |
| defaulted, by type | ADDRESS 21, BYTES32 7 | **UINT256 170**, ADDRESS 4 |
| ambiguous entry name | 0 | 10 |

## What does NOT generalise (three claims, all narrowed)

* **"Every defaulted argument is a mapping key."** True on aqua, where all 28
  were ADDRESS or BYTES32. On farming 170 of 174 are UINT256. The de-aliasing
  change made for aqua's key types therefore does not even apply here -- and it
  had already been refuted on aqua itself.
* **"Emitted tests come out with empty bodies."** Four refusals on aqua, ZERO on
  farming.
* **"The emission loss is one unreconstructed call."** On aqua both lost lines
  were `dock`'s. On farming the eight lost lines are spread over FOUR files
  (`FarmingLib` 4024; `FarmingPool` 4159/4170/4181; `FarmAccounting` 3757/3771;
  `UserAccounting` 3824/3859), and the dominant mechanism is different: 14 tests
  that are RED on the unmodified contract, against aqua's 1.

## What DOES survive two samples

**The emitter loses roughly half of what the enumeration reaches**: aqua 2 of 4,
farming 10 of 18. Both are far below the bar, and in both the enumeration itself
is also below the bar (4 of 7; 18 of 26). So the two-stage decomposition holds on
both samples even though the mechanism inside each stage differs.

That is the claim the paper can make from two benchmarks. The mechanisms are
per-benchmark and must be reported as such.

## A near-miss worth recording

farming's first round-trip reported `forge build failed` immediately after
`emitted 12 test file(s)`, which reads exactly like "the generator produced
uncompilable tests" -- an explicit anti-goal. It was not: the error is
`Stack too deep` at `src/farming__FarmingPool.flat.sol:3777`, inside the
CONTRACT, before solc looks at any generated test. The harness's `foundry.toml`
lacked `via_ir`; aqua's flat is small enough not to trip it. Filing that as an
emitter defect would have put a plausible, entirely fictional entry on a list
that already has real ones.

---

# Third sample: EscrowSrc

| | aqua | farming | EscrowSrc |
|---|---|---|---|
| bar | 7/8 | 26/26 | 16/16 |
| native | 6/8 | 26/26 | 8/16 |
| **ours** | **2/8** | **10/26** | **3/16** |
| enumerated | 4 | 18 | 6 |
| emitted | 2 | 10 | 3 |
| **emission retains** | **50%** | **56%** | **50%** |
| RED disabled | 1 | 14 | 17 |
| empty-body refusals | 4 | 0 | 0 |
| defaulted, dominant type | ADDRESS/BYTES32 | UINT256 (170/174) | mixed, UINT32-heavy |

## The one quantitative claim that survives three samples

**Emission retains about half of what the enumeration reaches** -- 50%, 56%,
50%. Every OTHER regularity taken from aqua alone has been narrowed by a later
sample: the defaulted-argument type profile differs on all three, empty-body
refusals happen only on aqua, and the dominant loss mechanism moves from an
unreconstructed call (aqua) to RED tests (farming 14, EscrowSrc 17).

## A specific question settled

`ImmutablesLib` is 0/8 on BOTH Escrows, and it was not known whether the
enumeration missed those eight or the emitter dropped them. EscrowSrc answers
it: `enumerated 0, emitted 0, LOST IN EMISSION 0`. They were NEVER ENUMERATED.
That is an enumeration-side gap and no emitter change can touch it.

## Second near-miss of the same kind

EscrowSrc first reported `forge build failed` straight after `emitted 6 test
file(s)` -- again reading as the generator producing something unbuildable, and
again the harness: `forge_roundtrip.py` pinned `solc = "0.8.30"` while the flat
pins `=0.8.23`. `collect.py` has recorded the correct per-benchmark solc all
along. The fix is to pin NOTHING and let forge satisfy the flat's own pragma, so
there is no second place for that mapping to drift. Two such near-misses in one
evening, on the same output line, is why a build failure is now read before it
is filed.

---

# Fourth sample: EscrowDst

| | aqua | farming | EscrowSrc | EscrowDst |
|---|---|---|---|---|
| bar | 7/8 | 26/26 | 16/16 | 18/18 |
| native | 6/8 | 26/26 | 8/16 | 10/18 |
| **ours** | **2/8** | **10/26** | **3/16** | **2/18** |
| enumerated | 4 | 18 | 6 | 5 |
| emitted | 2 | 10 | 3 | 2 |
| **emission retains** | 50% | 56% | 50% | **40%** |

## The surviving claim, now with its range

Emission retains **40-56%** of what the enumeration reaches, across four
benchmarks. "About half" holds, and the fourth sample is what makes it a RANGE
rather than a number that happened to look precise three times. Quote the range.

## The `ImmutablesLib` finding now holds on both Escrows

EscrowSrc reported `ImmutablesLib: enumerated 0, emitted 0`. EscrowDst reports
the same, and additionally `EscrowDst.sol: enumerated 0`. So the eight
`ImmutablesLib` decisions that appear as 0/8 in the gate table were NEVER
ENUMERATED on either Escrow -- an enumeration-side gap that no emitter change
can touch. That was a single-sample observation one commit ago and is now a
two-sample one, which is the whole reason the fourth run was worth doing.

## What stays per-benchmark

The mechanism inside the emission loss still differs everywhere: an
unreconstructed call on aqua, RED tests on farming (14) and both Escrows (17,
and EscrowDst's own), empty-body refusals only on aqua. Report mechanisms per
benchmark; only the 40-56% band is a cross-benchmark statement.

---

# Why there is no fifth sample

Four is not where the sampling stopped for want of effort; it is every benchmark
in the corpus that can produce a retention ratio at all. The remaining two
cannot, for two different structural reasons, and both are already measured:

* **st1inch_St1inch** -- all 22 path-coverage runs were killed by the 180s outer
  bound and produced ZERO reports (`index.json`: `0/22 run(s) produced a report,
  22 killed`). The enumeration side is empty, so `enumerated / emitted` has no
  denominator. Its gate row is `ours 0` against a bar of 72, and that 0 is a
  budget artefact, not a reach measurement.
* **limit_order_protocol** -- 14 of 14 runs produced reports and every one has
  ZERO F claims across ZERO units. `MakerTraitsLib` is a pure `internal`
  library, and a UNIT is a public/external function, so complete-path coverage
  has nothing to enumerate. Numerator and denominator are both 0. The gate
  reports this as `N/A: 0 units`, deliberately, rather than as a FAIL -- it is a
  scope difference between the two metrics, not a reach difference.

So the 40-56% band rests on four benchmarks because four is all the corpus
offers. Adding a fifth needs either a larger per-run budget on st1inch (which is
its own measurement -- see `notes/coverage/scripts/budget_probe.sh`) or a
benchmark whose in-scope code is not a pure internal library.

Stated here so the next reader does not spend a run discovering it.

---

# The `dock` emission defect was REAL, and fixing it did NOT move the number

The first same-run measurement, aqua, immediately after the two `dock` emitter
defects were fixed:

    emitted test files : 5   (was 6, of which 2 had empty bodies)
    refused, empty body: 0   (was 4)
    ours   (generated suite, forge lcov)   : 2 / 8     <- UNCHANGED
    enumerated 4, emitted 2, lost in emission 2, lost lines [2258, 2260]

`dock` went from 0 emitted cases to 2, both naming their obligation. And the
coverage is identical, down to the same two lost lines — which are `dock`'s own.

**So the emission defect was not what was blocking the coverage.** The emitted
`dock` tests do not execute 2258 (the loop header) or 2260 (the `require` inside
it), and the reason is already written down elsewhere in this repo: those
branches sit behind a guard on a mapping a fresh deploy leaves EMPTY, so they
need state an earlier transaction establishes. The emitted call is
revert-tolerant, so it reverts before the loop and forge records no arm.

This is worth stating plainly because the opposite reading was available and
attractive: "the emitter lost 2 of 4, we fixed the emitter, therefore the loss
is gone." Two of four is still lost, by the same lines, for a different reason
than the one just repaired. **An emission defect and a coverage blocker are not
the same thing, and this benchmark had both on the same unit.**

What it reassigns: aqua's `enumerated -> emitted` gap is NOT a reconstruction
gap. It is the transaction bound, which is a parameter of the measurement —
`forge_roundtrip.py --max-tx N` is the measurement that separates "our tests are
weak" from "one transaction cannot get there".

## One thing the same-run denominator settled in passing

aqua's `enumerated` is 4 under the same-run denominator and was 4 under the
cross-run one. So on THIS benchmark the cross-run join was not distorting the
ratio. That is one benchmark, and it is evidence about aqua, not a licence to
quote the other three before they are re-run.

---

# The killed units are a SCALE problem, not a budget one (measured)

`notes/coverage/scripts/budget_probe.sh` was written to decide this and had not
been run. It has now:

    EscrowDst.withdraw, outer budget 1200s (the sweep used 180s)
    -> /tmp/budget_probe/ is EMPTY: no cov-report.json
    -> 109452 lines of solver output, no result

`EscrowDst.withdraw` is the SMALLEST of the four units the sweep lost to its
180s bound (30 enumerated paths). At 1200s -- 6.7x the sweep's budget and 13x
the baseline's 90s per focused method -- it still produces no report, and a
path-coverage run killed by a timeout emits nothing at all, so its contribution
stays exactly 0.

By the probe's own design the other three need not be run: `publicWithdraw` has
the same 30 paths, `FarmingPool.exit` has 1004 and `rescueFunds` 9536.

## What this settles, and what it costs

It removes the most attractive remaining explanation for subgoal 2's shortfall.
"Give it more time" is not available: the gap is not a budget artefact, so it
cannot be closed by a number the paper could defend. It belongs in the text as a
limitation of complete-path enumeration at this scale.

It also removes a temptation. Our side already runs at 180s against the
baseline's 90s -- an asymmetry that favours us and is disclosed in
`notes/commensurability-audit.md`. Had the probe finished at 1200s, raising the
sweep budget would have raised our numbers while widening that asymmetry to 13x.
It did not finish, so the question does not arise; but the reasoning should be
on record either way, because the temptation would have been to take the higher
number first and disclose second.
