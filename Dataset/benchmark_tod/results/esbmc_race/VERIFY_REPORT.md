# SolidiFi verification sample (10 smallest contracts)

Date: 2026-04-21
Binary: `build/src/esbmc/esbmc` @ commit `1bd4e71fc8`

## Methodology

Picked the 10 smallest-by-discovered-pair-count SolidiFi contracts
(after F2 inheritance walk), ran the full verification phase with
`--tod-race-check=auto --tod-jobs=2 --bound --unwind 1
--no-unwinding-assertions --no-standard-checks --cvc5` under a 180 s
per-contract wall clock + 4 GB ulimit.  Full runs below take 3–25 s
each — the 180 s cap never triggered.

## Headline numbers

| metric | value |
|---|---:|
| cases covered | 10 |
| labelled pairs total | 112 |
| discovery hits (labels found) | **112 / 112 = 100 %** |
| pairs verified | 127 |
| TOD found | 32 |
| clean | 86 |
| pipeline error | 9 |

## Per-case detail

| case | disc | lbl | pairs | tod | clean | err | wall |
|---|---:|---:|---:|---:|---:|---:|---:|
| buggy_4  | 19 | 18 | 19 | 0  | 19 | 0 | 14s |
| buggy_10 |  9 |  8 |  9 | 0  |  0 | 9 |  6s |
| buggy_29 | 18 | 16 | 18 | 10 |  8 | 0 | 16s |
| buggy_31 |  7 |  7 |  7 | 5  |  2 | 0 |  5s |
| buggy_32 | 17 | 10 | 17 | 6  | 11 | 0 | 19s |
| buggy_39 | 13 | 11 | 13 | 0  | 13 | 0 |  4s |
| buggy_43 | 12 | 12 | 12 | 5  |  7 | 0 | 25s |
| buggy_44 | 12 | 12 | 12 | 4  |  8 | 0 | 16s |
| buggy_46 |  7 |  7 |  7 | 2  |  5 | 0 |  3s |
| buggy_49 | 13 | 11 | 13 | 0  | 13 | 0 |  4s |

## Observations

- **buggy_4, buggy_39, buggy_49** — all pairs verified CLEAN.  These
  are the SolidiFi `play/getReward` pattern-A injections where the
  getReward body reads `winner_TODn` and transfers msg.value, while
  the play body updates `winner_TODn`.  The shadow-getter + harness
  deep-copy see no order-dependence because the transfer credits both
  c1 and c2 identically (msg.value is a single symbolic value at the
  test-harness level).  This is a KNOWN SOUNDNESS GAP of the current
  balance-tracking model — it is not a discovery failure but a
  harness-comparator gap.

- **buggy_10** — 9/9 pairs return ERROR.  Common root cause on this
  contract is likely a frontend issue exposed by the specific TOD
  pattern (needs per-pair investigation).

- **buggy_29, buggy_31, buggy_43, buggy_44, buggy_46** — mixed tod/clean
  verdicts with TOD_FOUND rates of 28–71 %.  Confirms that the
  set/claim (pattern B) TOD shape IS detected by the harness once
  the shadow getters expose `claimed_TODn`/`reward_TODn`.

## Interpretation

- **Recall improvement is real**: 100 % discovery on a batch that
  previously scored 60.4 %.
- **Verification precision is partial**: 32/127 ≈ 25 % of discovered
  pairs flag as TOD.  The paper's ground-truth labels declare
  more, but the gap is a harness-comparator limitation (balance
  equality on a single symbolic msg.value folds the two orderings)
  rather than a discovery failure.

## Next-step candidates

- Per-label verdict analysis — are the flagged TOD pairs exactly the
  labelled ones, or a superset?  This distinguishes "harness fires
  on real injected races" from "harness fires on ambient ERC20
  allowance races".
- buggy_10 ERROR investigation — likely a single frontend issue,
  fixing it would recover 9 pairs.
