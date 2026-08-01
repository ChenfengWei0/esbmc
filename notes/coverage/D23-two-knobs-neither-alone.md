# The state-guarded paths need `tx >= 2` AND `focus off`. Neither knob alone does anything, and the whole corpus sits in the cell that gives nothing.

**Measured 2026-08-01, binary `1e46eac1c33177f73fdadc41398e061c`.**

## What was run, and why these cells

`notes/coverage/poc/Tiny3.sol` was written to separate two explanations for a
state-guarded path staying `bounded-holds`, and its own header records that it
**had never been run**. It was run today because the obvious next move —
wiring the (now verified) multi-name `--focus-function` into the corpus
collector — is only worth making if breadth of focus actually unlocks anything.

| contract | focus | tx | paths | F | U |
|---|---|---|---|---|---|
| Tiny | withdraw | 1 | 5 | 3 | 2 `bounded-holds` |
| Tiny | (none) | 1 | 8 | 6 | 2 `bounded-holds` |
| Tiny2 | withdraw | 1 | 5 | **5** | 0 |
| Tiny2 | (none) | 1 | 8 | **8** | 0 |
| Tiny3 | withdraw | 1 | 5 | 3 | 2 `bounded-holds` |
| Tiny3 | (none) | 1 | 7 | 5 | 2 `bounded-holds` |
| **Tiny3** | **(none)** | **2** | **7** | **7** | **0** |
| Tiny3 | (none) | 3 | 7 | 7 | 0 |

## Result 1 — accumulator pollution is REFUTED as the mechanism

`Tiny3.seed()` writes the balance with **no user-level decision** in its body:
no `require`, no branch. Its only decision is the synthesised ABI non-payable
value gate that every unit has. Its presence therefore contributes essentially
nothing to the path-identity accumulator.

The hypothesis recorded in Tiny3's header was that `tr` accumulates across the
whole transaction, so an execution "seed() then withdraw()" would carry seed's
bits and could not match withdraw's own `enc`. If that were the mechanism, a
**decision-free** predecessor would slip through and the guarded paths would
become F.

**They did not.** Tiny3 whole-contract at tx=1 has the same 2 `bounded-holds` as
the focused run. So the obstacle is not the predecessor's decisions.

Tiny2 — identical except the constructor puts `bal = 500` in place — is 5/5 and
8/8. So the state at unit entry is the whole of it.

## Result 2 — a claim in EXECUTION_PLAN §1.4 is refuted

> "不给 focus 时 tx=1 一笔交易内就能跨函数（各 dispatch guard 相互独立）"
> — *without focus, one transaction can already cross functions at tx=1, because
> the dispatch guards are independent.*

At tx=1 Tiny3's guarded paths stay `bounded-holds` even with `seed` available
and free of decisions. At tx=2 they are all F. **A state-establishing
predecessor requires a second transaction; it does not happen inside one.**

## Result 3 — the 2x2 nobody had assembled

| | tx = 1 | tx >= 2 |
|---|---|---|
| **focus ON** (where the entire corpus was collected) | no gain | **no gain** — measured on `aqua safeBalances` and `aqua dock`, six cells each, identical output: under `--focus-function f` every transaction is another call to `f`, so a path guarded by state another function establishes is unreachable at every tx bound (`option-matrix-round1.md`) |
| **focus OFF** | **no gain** — measured here | **full gain** — measured here, 5 -> 7, 100 % |

**Neither knob alone does anything. Only the pair does.** Every corpus number
was collected at `--focus-function <one name> --solidity-max-tx 1`, i.e. in the
cell that cannot produce the witnesses. This is the fourth axis on which the
invocation configuration has turned out to be the independent variable rather
than scaffolding (after tx/strategy, slice/simplify, and memlimit).

## Why this is licensed rather than a workaround

`EXECUTION_PLAN` §5 already states the harness asymmetry as a design rule:
stage ① (enumeration) **may** be relaxed — multi-function focus, higher tx —
while stages ②③ must be tightened to match the artefact (single focus, tx=1).
The branch-coverage gate measures enumeration. So using `tx >= 2` for the
coverage number is the plan's own design, not a way around a limit. What was
missing was any measurement showing it buys something; now there is one.

## The qualifier that travels with it, and it is not small

`--solidity-max-tx N >= 2` is the setting **the tool itself warns produces
Foundry tests with mis-attributed methods**, and it is item 1 on §8's cut list
for exactly that reason. So this raises the ENUMERATION number and must not be
carried into the emitted artefact. The asymmetry was a paper rule until now; it
now has a concrete price attached.

## What this does NOT establish

* **Three toy contracts.** The sentence carries their names until a real
  benchmark repeats it. This project has had the same generalisation narrowed by
  a second and a third sample twice already.
* **It does not say whether `focus = {unit, its setters}` + `tx >= 2` is as good
  as whole-contract + `tx >= 2`.** That is the cheap version of the winning cell
  and the only reason multi-name focus would be worth wiring — Tiny3 cannot tell
  them apart because it has only two units. Deciding it needs a PoC with at
  least three units where the setter is known, or a real benchmark.
* **Cost is unmeasured at tx >= 2 on real contracts.** aqua whole-contract at
  tx=1 already needed 20 GiB and 778 s (`scope-and-resources.md`). Whole-contract
  at tx=2 may be out of reach, which is precisely what would make the focus-set
  version necessary rather than merely cheaper.
