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
