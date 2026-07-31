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
