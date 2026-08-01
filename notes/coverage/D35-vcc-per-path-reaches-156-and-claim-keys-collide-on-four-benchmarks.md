# D35 — VCCs per path reach 156, and the claim-key collision is corpus-wide, not a st1inch quirk

**Measured 2026-08-01**, `run_stats.py --brief` over every unit log of all five
collected benchmarks. **No new ESBMC run** — the logs were already on disk.

## The table that matters

| bench / unit | paths | VCCs | **VCC/path** | extcall | keys solved >1× | of those, DISAGREEING |
|---|---|---|---|---|---|---|
| `Aqua.*` (5 units) | 3–63 | = paths | 1.00 | 0 | 0 | 0 |
| **`Aqua.ship`** | 2733 | 6825 | **2.50** | **0** | 0 | 0 |
| `BaseEscrow.rescueFunds` (Dst) | 8 | 32 | 4.00 | 4 | 3 | **3** |
| `EscrowDst.cancel` | 12 | 180 | **15.00** | 30 | 9 | **9** |
| `EscrowDst.withdraw` | 5 | 425 | **85.00** | 340 | 1 | **1** |
| **`EscrowDst.publicWithdraw`** | 5 | **780** | **156.00** | 780 | 5 | **5** |
| `EscrowSrc.publicCancel` / `publicWithdraw` | 4–5 | 60 / 75 | **15.00** | 30 | 4 / 5 | **4 / 5** |
| `FarmingPool.rescueFunds` | 14 | 210 | **15.00** | 30 | 14 | **6** |
| `FarmingPool.*` (8 others) | 2–397 | = paths | 1.00 | 0 | 0 | 0 |
| `St1inch.setFeeReceiver` | 5 | 10 | **2.00** | **0** | 5 | **2** |
| `St1inch.deposit*` / `rescueFunds` | 2–17 | 8–68 | 4.00 | 4 | 2–16 | 2–4 |

## 1. Task #22 finally has a NUMBER, and it is not what the task says

`EscrowDst.publicWithdraw` is recorded as "SYMEX never finishes under the
external-call re-entry model". The log says otherwise: **5 paths, 780 VCCs,
156 verification conditions per path**, with 780 external-call re-entry lines.

⇒ It is not that one query is hard. It is that this unit has **780 queries**, and
the run is killed while working through them (91 solves before the outer
timeout). The multiplier is measurable per unit and varies enormously — 4, 15,
85, **156** — because it is the number of re-entry SITES times the level bound,
not a constant.

⇒ That reframes the task: the fix is not a bigger budget, it is the instantiation
count. And `--unwind` is the wrong knob to reach for, because D28 already showed
raising it on this contract buys zero decisions while costing everything.

## 2. ⛔ THE CLAIM-KEY COLLISION IS NOT A st1inch QUIRK

D32 found `setFeeReceiver`'s claim key solved twice with disagreeing outcomes and
treated it as one unit's anomaly. It is not:

* `EscrowDst.cancel` — **9 keys** solved more than once, **all 9 disagree**
* `EscrowSrc.publicWithdraw` — 5 of 5 disagree
* `EscrowDst.publicWithdraw` — 5 of 5 disagree
* `FarmingPool.rescueFunds` — 14 duplicated, 6 disagreeing
* `BaseEscrow.rescueFunds` — 3 of 3 disagree

**Four of the five collected benchmarks have units whose claim key receives
contradictory verdicts within a single run.** On st1inch the second identity
comes from a constructor call (D33); on the Escrows and farming it comes from the
external-call re-entry levels. Different sources, same consequence.

⇒ **The reported U reason for any such unit depends on solve order**, and on which
instantiation the verdict-preservation logic happened to see first. Wherever a
number in this project is broken down by U reason on those units, that breakdown
is order-dependent.

⇒ This is the same shape as the already-recorded rule that one fact kept in two
ledgers diverges — here one KEY covers many queries.

## 3. `Aqua.ship` is a THIRD shape and is not explained

2733 paths, 6825 VCCs, ratio **2.50**, and **zero** external-call lines and zero
duplicated keys among the 1709 solves that ran before it was killed. Neither
re-entry (extcall 0) nor the constructor identity (2.50 is not 2). Recorded as
unexplained rather than folded into either mechanism.

⚠ The 1709 is a killed run, so the duplicate count is a LOWER bound: keys that
would have repeated may simply never have been reached.

## What is NOT claimed

The 4.00 / 15.00 / 85.00 / 156.00 group is attributed to external-call re-entry
because the `extcall` column moves with it, not because the mechanism was
re-derived here. `strloop` is ruled out as a differentiator by counting: it is
constant within st1inch (59) and varies independently elsewhere (130–3104 on
farming, 0 on the Escrows) without tracking the ratio.
