# D38 — CVC5 does NOT rescue the real st1inch; and auditing why exposed three accounting holes in the corpus

**Measured 2026-08-01.** Two things, and the second is the bigger one.

## 1. The retry D37 registered: it failed, and the budget was never the binding constraint

```
timeout 900 setsid esbmc st1inch__St1inch.flat.sol.solast --sol st1inch__St1inch.flat.sol \
  --solidity-path-coverage --contract St1inch --focus-function setFeeReceiver \
  --solidity-max-tx 1 --cov-report-json --path-cov-max-goals 10000 --memlimit 8g --cvc5
```

| | result |
|---|---|
| exit | **2**, not 124 — `timeout 900` **never fired** |
| death | `std::bad_alloc` during the per-claim solve |
| claims decided | **0 of 10** — it died inside the **first** solve, `setFeeReceiver:path:15` |
| solve time | `Decision procedure total time: 0.000s` — no solve ever completed |
| report | PARTIAL, `F 0, U 5` |

⇒ **The isolated blocker being one CVC5 handles does not carry to the real
contract.** D37 registered exactly this possibility before the run —

> "Nothing here says CVC5 will decide the real st1inch — the real contract
> carries much more than this fixture, and D31 measured it not returning at
> 180 s."

— and that is what happened. D37's §1–§2 stand unchanged: CVC5 really does decide
the 60-line reproduction at `uint256` in 3.7–5.2 s where z3 OOMs and bitwuzla does
not return, and the auto-router's stated reason really is falsified on that shape.
What does **not** follow, and is now measured not to hold, is D37 §3's hope that
st1inch's `0/86` would move.

**The cost is stated rather than absorbed**: this bought no witness. But note
*which* resource ran out. The run did not exhaust its 900 s — it exhausted 8 GiB
inside one query. So a **larger time budget buys nothing here**, and the honest
next lever is not "run it longer".

## 2. ⛔ That run's own report mislabels why it has no witnesses

The report files all five paths as

```json
"u_reason": "unit-not-entered",
"u_reason_detail": "harness never entered it (no --focus-function narrowing explains this)"
```

while the same run's stdout says

```
Solving claim 'setFeeReceiver:path:15 at' with solver CVC5 1.1.2
```

The unit **was** entered — symex produced 5 paths and 10 VCCs from it, and one of
its claims reached the solver. The bucket that is true, `run-died-before-solving`,
reads **0**.

This is not cosmetic. `unit-not-entered` is a diagnosis about the **harness** (the
dispatcher could not reach the unit ⇒ go fix the model); `run-died-before-solving`
is a diagnosis about the **run** (⇒ go change the budget or the backend). Opposite
next actions from the same cell. `dying-run-keeps-its-work.md` step 2 built the
correct token deliberately, keyed on `claims_in_solve_loop` — "the exact set of
claim comments that survived simplification and reached the equation, recorded
once before the first solve" — and even records that an earlier version of that
attribution over-attributed and had to be corrected before shipping. Something in
that keying does not match on this run.

**Not yet diagnosed.** The source has not been read; this is the observation with
its reproduction (the run's artefacts are kept), not a root cause.

## 3. The audit that had to follow — and the corpus is CLEAN of #2

If a partial run mislabels its U-reasons, every attribution built on the corpus is
suspect. So: `notes/coverage/scripts/partial_report_census.py`, which reads every
`cov-report.json` **in full** and cross-checks each fact against the other ledger
that records it.

**Result: `PARTIAL 0` across all 95 reports.** No collected run died, so the
mislabel in #2 touches nothing already concluded. The gate attribution stands.

Two ledgers were also checked against each other and **agree** everywhere:
`summary.U_reasons` against a recount of the per-claim `u_reason` strings (0
disagreements), and the per-claim `not_solved_this_run` boolean against the
`not-solved-this-run` token (0 differences on complete runs; they differ 5-vs-0
only on the dying run, which is itself a symptom of #2).

## 4. ⛔ But the audit found three holes nobody was looking for

### (a) 48 of the 95 `cov-report.json` files are STALE

Every one belongs to a unit `runs.jsonl` records as

```json
"cmd": null, "reportPresent": false, "skipped": "library-has-no-dispatcher"
```

The collector deliberately did **not** run esbmc for them — a library has no
dispatcher harness and the only other route is the banned `--function`. The JSON
in their work dir is a leftover from an earlier collection. **Any consumer that
walks the tree for `cov-report.json` counts them as this collection's output**,
and this script did exactly that until section E existed.

The same class was already fixed once, in `solidity_path_generalise.py`, by
deleting the stale report *before* the run. The collector does not do that for a
unit it skips.

### (b) Three units were KILLED and left NO report at all — 36 paths vanish from BOTH sides of the ratio

| benchmark | unit | wall | paths enumerated |
|---|---|---|---|
| `cross_chain_swap_EscrowDst` | `publicWithdraw` | 900.2 s | 5 |
| `farming` | `FarmingPool.rescueFunds` | 300.3 s | 14 |
| `st1inch_St1inch` | `St1inch.rescueFunds` | 300.6 s | 17 |

All three: `killedByOuterTimeout: true`, `reportPresent: false`. Per
`dying-run-keeps-its-work.md`, the signal arm **cannot write JSON** (malloc,
iostream and the log mutex are unsafe in a handler), so a killed unit leaves no
file.

⇒ A tree-walking consumer does not see a **zero** for that unit — **it does not
see the unit**. The paths leave the numerator and the denominator together and
the benchmark's percentage goes **UP**. `EscrowDst` reads `8/8 + 5/5 + 12/12 =
25/25 = 100 %` on the units that survived, with `publicWithdraw`'s 5 paths simply
absent. This is `missing-input-silently-rewrites-scope` in a new place: an absent
report reads as "this benchmark had fewer units" when it means "a unit was too
hard". Note two of the three are `rescueFunds` — the same shape in two different
contracts, which is a lead, not a coincidence.

(`EscrowDst.publicWithdraw` is task #22's unit. Its 780-VCC problem now has a
second face: it is not merely expensive, it is *invisible*.)

### (c) The corpus spans FOUR builds, split cleanly BY BENCHMARK

Read from `runs.jsonl`, where only `binaryMtime` identifies the executable:

| benchmark | live runs | `binaryMtime` |
|---|---|---|
| `aqua_Aqua` | 6 | 1785533017 (`srcDirty: true`) |
| `farming` | 11 | 1785533017 (`srcDirty: true`) |
| `cross_chain_swap_EscrowSrc` | 6 | 1785533017 (`srcDirty: true`) |
| `st1inch_St1inch` | 21 | 1785575260 |
| `cross_chain_swap_EscrowDst` | 3 | 1785591755 |
| `limit_order_protocol` | **0** | 1785528054 (skipped units only) |

Three builds produced live runs; a fourth appears on `limit_order_protocol`,
which produced none. **The split falls between benchmarks**, which is the worst
arrangement available: "benchmark A differs from benchmark B" and "build A
differs from build B" are the same column. Twenty-three of the runs additionally
carry `srcDirty: true`, so their `head` does not identify the source either.

### (d) `limit_order_protocol` has zero measurable units BY CONSTRUCTION

Its `index.json` says `"primary": {"name": "MakerTraitsLib", "kind": "library"}`.
The benchmark's primary target **is a library**, so all 14 of its units fall under
the `--function` ban and every one is skipped. It contributes 0 live runs. It
cannot be aligned with or beaten on the gate — it is `0/0`, and reporting it as a
benchmark row alongside five that were actually measured overstates the corpus.

## 5. A check of my own that was wrong, and how it was caught

The census's first version dated each report's **binary** from the SET of keys
under `summary.U_reasons`, arguing that the breakdown is published with every
token including the zeros, so a 5-token report must predate the two tokens added
by `dying-run-keeps-its-work.md`. It printed "MORE THAN ONE GENERATION IS
PRESENT — 48 of 95", with a clean split by benchmark. It read as confirmed.

It was wrong. A run that enumerates **no path** takes a different writer branch —
its stdout says `No complete path enumerated` and its `[Coverage]` block has no
`Report Completeness`, no `Path Status` and no `U Reasons` line at all. **A short
token dict is a SHAPE, not a DATE.**

Worth recording because of *how* it failed: the conclusion it reached was
**partly true anyway** (the corpus does span several builds — §4c), reached
through an argument that does not support it. A check that is right for the wrong
reason is not caught by looking at its output. It was caught by asking what else
could produce a 5-token dict and spending one command on the answer. Section F now
reads the recorded `binary` block instead of inferring one, and the discarded
reasoning is kept in the source as a comment.

## Files

`notes/coverage/scripts/partial_report_census.py` — sections A–G, each fact taken
from the published file and cross-checked against the other ledger that holds it.
Fired in both directions before being trusted: section D fires on the dying cvc5
run and stays dark on all 95 collected reports.
