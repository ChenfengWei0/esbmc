# D28 — raising the call-depth bound buys 8 more paths, 4 more witnesses, and ZERO additional decisions

**Measured 2026-08-01**, `notes/coverage/scripts/depth_bound_sweep.py`, binary
mtime `1785591755`, `EscrowDst.cancel` on the real flat input, at the gate's own
cell (`--contract EscrowDst --focus-function cancel --solidity-max-tx 1`).

## Why this run happened

`D27` read the corpus's own run logs and found that `ImmutablesLib.hash#932` is
named as an **unexpanded call site past the call-depth bound (4)** in all four
EscrowDst units and all six EscrowSrc units — falsifying the recorded reason for
their `0/8 ImmutablesLib` shortfall ("in-degree 0, nothing calls it"). D27 also
recorded, in the same note, that this does NOT license the conclusion: `farming`
has eight `FarmingLib` functions past the bound and still scores 1/1 on that
file, because those decisions arrive through shallower call sites.

So the question was left as "a candidate explanation, not a demonstration", and
settling it costs a run. This is that run.

## ⛔ The first attempt did not measure what it claimed to, and it ran first

`depth_bound_sweep.py`'s reading A was "ImmutablesLib decisions appear at a higher
bound", and its observable was `decisions[].file`. **That field publishes the
FLAT input's path — one value, on every claim, at every bound.** Reading A was
unreachable by construction, and the first run duly reported B/D from a table
with a single row in it.

Fixed to the gate's own currency: flat LINE → the flat's `// File` blocks →
intersected with each in-scope file's canonical decision lines, which is exactly
what `branch_gate.canonical_in_scope` does. Then **the fixed observable was fired
once at bound 4 alone** and shown to separate four original files (two non-zero,
two zero) before any comparison was drawn from it.

(Also fixed while there: with a single bound the script used to print a verdict
about higher bounds that had never been asked for. It now refuses.)

## Result

| `--unwind` | paths | F | residual sites past the bound | BaseEscrow | Escrow | EscrowDst | ImmutablesLib |
|---|---|---|---|---|---|---|---|
| 4 | 12 | 12 | 8 | 4/7 | 1/1 | **0/2** | **0/8** |
| 6 | **20** | **16** | **34** | 4/7 | 1/1 | **0/2** | **0/8** |
| 8 | — | — | — | \_ OUTER TIMEOUT after 400 s \_ | | | |

## READING D, which the file pre-registered as "the outcome that looks like progress and is not"

> **D** the F count rises but the per-file distribution does not
>       → more witnesses inside files already saturated; the gate does not move
>          and neither explanation is supported.

Eight more complete paths and four more witnesses at bound 6, and **not one
additional canonical decision in any file**. The extra witnesses land on
decisions already counted. `ImmutablesLib` stays 0/8; `EscrowDst.sol` stays 0/2.

**And bound 8 is READING C**: it does not finish in 400 s, so the answer there is
a cost, not a scope. Per the file's own pre-registration that is **NOT A WIN and
not a reason to raise anything further.**

One more thing the table says that the hypothesis did not predict: **the residual
count went UP, 8 → 34.** Expanding deeper does not consume the frontier, it
exposes more of it.

## What this settles

**Raising the call-depth bound is not the fix for the Escrows' `ImmutablesLib`
0/8.** The bound is real, it is named in every log, and moving it from 4 to 6
buys zero decisions on this unit while 8 is unaffordable.

⇒ The operative explanation is the one D27 left standing: the baseline reaches
`ImmutablesLib` through `--function` ISOLATION (`collect.py:466-468` routes
library units that way), and this project bans `--function` because it verifies
from an arbitrary contract state and can produce a counterexample no reachable
state supports — a RED generated test. That is a **stated applicability limit of
the measurement configuration**, not a knob that was left in the wrong position.

D27's correction still stands and is not undone: the REASON recorded for it
("in-degree 0 — nothing calls it") was false. What is now also established is
that the alternative reason does not buy anything either.

## What it does NOT settle

* **One unit.** `EscrowDst.cancel`. The gate's numerator is a union over the
  benchmark's units, and this says nothing about `withdraw`, `publicWithdraw` or
  `BaseEscrow.rescueFunds` — except that D27 showed all four name the same eight
  call sites, so the mechanism is shared even if the outcome need not be.
* **Whether `ImmutablesLib`'s eight decisions are reachable through their callers
  at ANY bound.** Bound 8 did not finish; 10 and beyond were not attempted and
  there is no reason from this run to attempt them.
* **Why the bound-6 run witnessed 16 of 20 rather than 20 of 20.** Four paths
  were enumerated and not witnessed; their U reasons were not read here.

## Falsifier

If a later run at bound 6 on this unit shows any file's count above the bound-4
value, this note is wrong. The script prints every in-scope file including the
ones at zero, so a file moving off zero cannot be missed.
