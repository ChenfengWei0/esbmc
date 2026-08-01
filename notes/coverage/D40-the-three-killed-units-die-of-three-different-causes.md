# D40 — the three killed units are three different problems, and none of them is "not enough time"

**Measured 2026-08-01** with `run_stats.py --brief` on the killed runs' own logs.
A killed run leaves no `cov-report.json` (the signal arm cannot write JSON) but it
does leave its stdout, which is enough.

| unit | paths | VCC | VCC/path | solves before the kill | keys solved >1× | keys whose outcomes DISAGREE | extcall | strloop |
|---|---|---|---|---|---|---|---|---|
| `EscrowDst.publicWithdraw` | 5 | 780 | **156** | 91 | 5 of 5 | **5** | **780** | 0 |
| `FarmingPool.rescueFunds` | 14 | 210 | 15 | 194 | 14 of 14 | 6 | 30 | **3104** |
| `St1inch.rescueFunds` | 17 | 68 | **4** | 33 | 16 of 17 | 4 | 4 | 56 |

## They do NOT share a cause, and treating them as one item was wrong

* **`publicWithdraw` is pure external-call re-entry.** `extcall` is **780** against
  **780** VCCs — every single verification condition in that unit is a re-entry
  instantiation of the same assert. Five paths, five distinct claim keys, 156
  instantiations each. It got through 91 solves of 780 in 900 s, so finishing
  needs on the order of 7800 s. **A bigger outer timeout is not the fix; the
  instantiation count is** (task #22, and #35 for the key).
* **`FarmingPool.rescueFunds` is string-loop unwinding.** `strloop` **3104**
  against `extcall` 30 — a different mechanism entirely, and the one the
  `nondet_string` / `_str_assign` truncation warnings name.
* **`St1inch.rescueFunds` is neither.** Its ratio is **4**, the lowest of the
  three, and it made only 33 solves in 300 s — so its cost is per-solve, which is
  the conditional 256-bit arithmetic D36 characterises, not instantiation count.

⇒ Task #38 bundled these as "three killed units". That grouping is an artefact of
how they failed (outer timeout), not of why. `count-instances-by-entry-condition`
applies: three phenomena, three entry conditions, and fixing the re-entry
multiplier would move exactly one of them.

## ⛔ And every one of them is ALSO carrying the duplicate-key defect

`keys solved more than once` is **5 of 5**, **14 of 14** and **16 of 17** — and in
each unit some of those repeated solves **disagree with each other**. The same
shape `withdraw` shows on the surviving run: 89 solves for 5 keys, of which
`withdraw:path:2 at` alone is solved **85 times**, 84 PASSED and 1 FAILED.

So the units that die are also the units whose results would be solve-order
dependent if they lived. That is task #35, and this measurement says it is not a
tidiness issue on the side — it is co-located with the thing that makes them
unaffordable.

## What this does NOT say

It does not say the re-entry instantiations are redundant. Each re-entry level is
a genuinely different program state, so 156 instantiations may all be necessary
*as obligations*; what is measured here is only that they collapse onto ONE claim
key, are solved separately, and disagree. Whether the right fix is fewer
instantiations, one solve per key, or per-instantiation keys is not settled by
this note — it is the question #22 and #35 have to answer together.

Nor does it say these three units would clear the gate if they finished. Their 36
enumerated paths are absent from the numerator, and D39 shows what they would
contribute to their files; whether the decisions on them are ones the baseline
also has is a separate check.
