# D27 — the gate's gap is named in our own run logs, and the one benchmark that PASSES is the only one with nothing named

**Measured 2026-08-01**, `notes/coverage/scripts/residual_call_census.py`, over
the run logs the corpus collection already wrote. **No new ESBMC run.** The
evidence has been on disk since the collection and nothing had read it.

## Why this was worth doing before raising anything

`branch_gate.py`'s own docstring lists four mechanisms that move our numerator
and says none of them is visible in the gate's output — three DEFLATE it
(degradation withdrawal, the call-depth bound, the short-circuit cap) and one
inflates it. It then says, in terms:

> THE PREVIOUS VERSION OF THIS PARAGRAPH CLAIMED THE FIRST ONE WAS "reported
> beside the gate rather than folded into it". That was false. […] A disclosure
> that is promised in a docstring and not implemented is worse than an
> acknowledged gap, because it reads as handled.

The counters really are absent from `cov-report.json`. But the tool **prints
them**, and `pathcov_collect.one_run` writes every run's whole stdout to
`work/<unit>/run.log`. So the disclosure exists; it just was never read.

The census reads each log in full and buckets EVERY `WARNING:`/`ERROR:` line by
a key derived from the line itself, so a category nobody thought to look for
still appears with a count. Only after that tally are the depth-bound lines
pulled out for their callee names.

## Result

| bench | gate | call sites past the depth bound | degradation |
|---|---|---|---|
| `aqua_Aqua` | **PASS 7/8** | **none named** | none |
| `cross_chain_swap_EscrowDst` | FAIL 6/18 | **8** | — |
| `cross_chain_swap_EscrowSrc` | FAIL 6/16 | **20** | — |
| `farming` | FAIL 18/26 | **38** (42 on `exit`) | — |
| `st1inch_St1inch` | FAIL 0/86 | none named | **12 units DEGRADED** |
| `limit_order_protocol` | REFUSED | — | — |

Named callees, identical across every unit of a benchmark:

* **EscrowDst** — `ImmutablesLib.hash#932`, `EscrowDst._ethTransfer#1708`,
  `EscrowDst._withdraw_onlyValidImmutables#0`, `SafeERC20.safeTransfer#1141`
* **EscrowSrc** — the same plus `AddressLib.get#35`,
  `EscrowSrc._validateImmutables#1782`,
  `EscrowSrc._withdrawTo_onlyValidImmutables#0`, `IBaseEscrow.EscrowCancelled#639`
* **farming** — `FarmingLib` ×8, `UserAccounting.updateBalances/updateFarmedPerToken`,
  `FarmAccounting.farmedSinceCheckpointScaled`, `FarmingPool._approve`,
  `Math.min`, `SafeCast.toUint`, `IERC20.Transfer`

## What this settles, and what it does NOT

### ⛔ It falsifies a recorded attribution

`EXECUTION_PLAN.md` §3's 1.3 reads:

> EscrowSrc 10 条差距里 8 条是 `ImmutablesLib`，**in-degree 0**——合约里没有任何
> 东西调用它 […] ⇒ **这不是覆盖差距，是度量口径差异**

`ImmutablesLib.hash#932` is named as an unexpanded CALL SITE in **all six**
EscrowSrc units and **all four** EscrowDst units. Something calls it. **In-degree
is not 0**, and the sentence that says nothing calls it is wrong.

The rest of that entry survives and must not be thrown out with it: the baseline
does reach ImmutablesLib through `--function` isolation (`collect.py:466-468`
routes library units that way), and `--function` is banned here on soundness
grounds. So the SCOPE difference is real. What was wrong is that it was offered
as the ONLY cause, when a second one — a call-depth bound we chose — sits in
every run log.

### ✅ A correlation worth stating, across all five measurable benchmarks

**The one benchmark that clears the gate is the only one with no truncation
named.** aqua: no depth-bound line, no degradation, PASS. Every FAIL has one or
the other, and st1inch — the 0/86 — is the one with twelve DEGRADED units rather
than a depth bound.

Five points. A correlation, not a mechanism proof, and it is written as one.

### ⚠ AND THE COUNTER-EXAMPLE THAT KEEPS IT HONEST — farming

Being named past the depth bound does **not** imply that file's decisions are
lost. `farming` has **eight** `FarmingLib` functions and both `UserAccounting`
entry points past the bound, and the gate still scores it **1/1 FarmingLib, 6/6
UserAccounting, 5/5 FarmAccounting**. Those decisions are reached through OTHER,
shallower call sites.

⇒ So "the depth bound names X" is a candidate explanation for a per-file zero,
never a demonstration of one. For EscrowDst/EscrowSrc's `ImmutablesLib` 0/8 the
question — does raising the bound reach those eight decisions — still costs a
run, and farming says the answer may well be no.

`notes/coverage/scripts/depth_bound_sweep.py` is written for exactly that, with
four readings pre-registered including "the higher bound does not finish ⇒ the
answer is a COST, not a scope; NOT A WIN, and not a reason to raise anything
further."

## Three other things the same tally printed

* **`--function`'s removal cost nothing, re-confirmed from the old logs.** Every
  stale library run under the pre-ban route reports `instrumented 0 path(s)
  across 0 unit(s)` — exactly what `INVOCATION_DECISIONS` §10 recorded.
* **The ambiguous-name deaths are real and counted**: `claim`, `startFarming`,
  `stopFarming`, `farmed`, `updateBalances` on farming; `get` on both Escrows.
  `main symbol '<name>' is ambiguous` → `CONVERSION ERROR`, no instrumentation
  line. The collector already records these as `ambiguousEntryName`.
* **`work/` keeps directories for units the current collection SKIPPED.**
  `one_run` clears a workdir per run, but a skipped unit never calls it, so
  pre-ban logs from 2026-07-30 sit beside 2026-08-01 ones. Harmless for the gate
  (`branch_gate` reads `reports/`, reconciled against the journal) and a trap for
  any future reader of `work/` — which is why the census prints each log's own
  line count and instrumentation line rather than assuming they are one vintage.

## What is still not disclosed in the gate's own output

The counters are still absent from `cov-report.json`, so the gate table cannot
show them without either a producer-side change (add them to `summary`) or a
collector-side one (`pathcov_collect.one_run` capturing these lines into
`runs.jsonl`). This note is a census of the logs, not that fix.
