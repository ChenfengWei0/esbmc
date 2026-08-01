# D36 — st1inch's `F = 0` reproduced in ~60 generated lines: 30-deep CONDITIONAL 256-bit arithmetic, and the operator does not matter

**Measured 2026-08-01**, backend pinned to `--z3 --tuple-node-flattener` (the
configuration st1inch runs under), `--memlimit 4g`, serial.

## Say the failure precisely

The real `St1inch.setFeeReceiver` — an owner check, a zero check, one assignment
and an event — **enumerates 5 complete paths and witnesses none of them**:
`F 0, U 5`, of which `solver-unknown 3` with z3's own reason `out of memory`.

The paths ARE enumerated. What is missing is the WITNESSES. "Cannot get a path"
and "cannot get a counterexample for a path" are different failures, and the
imprecise version has already misled this project once.

## The ladder — bottom is the function verbatim, each rung adds ONE ingredient

| rung | paths | F | U | verdict |
|---|---|---|---|---|
| `step0_bare` — the function, an owner, the modifier, the error, the event | 4 | **4** | 0 | ✅ 0.002-0.004 s |
| `+ constructor calls the unit` | 4 | **4** | 0 | ✅ (doubles the VCCs — D33 — but solves) |
| `+ 30 immutable T_k = (T_{k-1}*T_{k-1})/1e18` (straight line) | 4 | **4** | 0 | ✅ |
| `+ name/symbol strings` | 4 | **4** | 0 | ✅ |
| **`+ constructor calls a 30-BRANCH _votingPowerAt`** | 4 | **0** | 4 | ⛔ `solver-unknown 4` |
| all four together | 4 | **0** | 4 | ⛔ `bounded-holds 1, solver-unknown 3` |

The last row's U-reason split is nearly the real contract's (`2 / 3`), on 4 paths
against its 5. **This is a minimal reproduction of st1inch's zero.**

Note the third rung: the exponent-table CHAIN alone solves. That is why D30 —
which blamed that chain — was withdrawn, and the ladder shows what it should have
blamed instead.

## Cliff, not gradient — and NON-MONOTONE

Varying only the branch count in the called function:

| branches | F | U | symex assign | VCC assign | min solve | max solve |
|---|---|---|---|---|---|---|
| 1 | **4** | 0 | 251 | 123 | 0.139 s | 0.584 s |
| 5 | — | — | 283 | 155 | **120.792 s — hit the per-claim timeout, NO REPORT** |
| 10 | 0 | 4 | 323 | 195 | 3.645 s | 17.739 s |
| 15 | 0 | 4 | 363 | 235 | 4.363 s | 15.678 s |
| 20 | 0 | 4 | 403 | 275 | 4.425 s | 16.889 s |
| 25 | 0 | 4 | 443 | 315 | 3.602 s | 16.489 s |
| 30 | 0 | 4 | 483 | 355 | 3.706 s | 16.869 s |

* **A cliff between 1 and 5.**
* **Non-monotone**: 5 branches is WORSE than 30 (times out at 120 s where 30
  bails with `out of memory` in 17 s).
* **Flat from 10 to 30**, while the formula grows linearly and stays tiny — 123
  to 355 assignments, encoded in **two milliseconds** throughout.

⇒ The cost is not the formula's size. Something changes qualitatively inside the
decision procedure.

## The operator does not matter

At 30 branches, changing only what the branch body does:

| branch body | F | U | VCC assign | min | max |
|---|---|---|---|---|---|
| `v = (v * T_k) / 1e18` | 0 | 4 | 355 | 3.612 s | 17.055 s |
| `v = v * T_k` | 0 | 4 | 355 | 3.621 s | 16.737 s |
| `v = v / 1e18` (division by a CONSTANT) | 0 | 4 | 355 | 2.140 s | 2.321 s |

All three fail. Even dividing by a compile-time constant, thirty times, behind
thirty branches, ends in `out of memory`.

⇒ **What breaks it is 256-bit arithmetic composed 30 levels deep through
CONDITIONALS** — not the operator, and not the arithmetic per se, because the
same arithmetic laid out straight-line (rung 3) solves.

## It is not the slicing configuration

Asked directly, and measured three ways on the failing rung:

| cell | symbols exempted from slicing | VCC assign | max solve | Path Status |
|---|---|---|---|---|
| `--cov-report-json` (as collected) | 59 | 355 | 16.707 s | `F 0, U 4` |
| **no** `--cov-report-json` | none | 262 | 16.862 s | `F 0, U 4` |
| `--cov-report-json --no-slice` | 59 | **472** | **4.912 s** | `F 0, U 4` |

Dropping the report — and with it the 59-symbol slicing exemption — does not help.
And `--no-slice`, which KEEPS MORE (472 assignments against 355), is **three times
FASTER**. Another datum saying the cost is not proportional to what is in the
formula.

(The no-report cell is a DIAGNOSTIC, not a proposed configuration:
`--cov-report-json` is not optional for the pipeline — without it the per-claim
slicer removes every state write and the counterexample payload comes back empty.)

## What this is, and what it is not

**It is**: a ~60-line, seconds-to-run reproduction of the failure that gives
st1inch 0/86 on the branch-coverage gate, with single-factor attribution and a
control that fires (rung 0 witnesses 4 of 4).

**It is not** a diagnosis of the encoder. Nothing here has read the SMT
conversion. "z3 runs out of memory on a 355-assignment formula it encodes in two
milliseconds" is an observation with a reproduction, not a root cause.

## Next, in the order the evidence supports

1. **Does the bit WIDTH matter?** The same ladder at `uint64` or `uint128`. If it
   solves, width is the second factor and the finding becomes "wide conditional
   arithmetic", which is far more actionable.
2. Only then, the encoder itself.

## Files

Generator `notes/coverage/scripts/gen_setfee_ladder.py` — every rung is generated
so no two cells can differ by anything but the named factor. Fixtures and logs
under `notes/coverage/poc/D36_SetFeeReceiver/`.
