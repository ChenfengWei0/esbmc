# D26 — a reverting path keeps the identity it accumulated, and the accumulator is not in the rolled-back state

**Measured 2026-08-01**, `notes/coverage/scripts/revert_identity_check.py` over
`notes/coverage/poc/D26_RevertKeepsIdentity.sol`, binary mtime `1785591755`.

## The question, and why it is not obvious

A complete path is identified by `tr == enc && cnt == depth`, and `tr` is built
one bit per decision. Solidity's `revert` undoes every state modification the
transaction made. So: if `tr` lived in that state, a path that records three
decisions and THEN reverts would arrive at its exit assert with `tr` back at its
entry value. The claim could never match. The path would come back U.

**That failure would be invisible in the aggregate.** Reverting paths are common,
U is common, and nothing in a report distinguishes "the solver did not decide it"
from "the identity was erased before the assert could see it".

The reason to expect it is FINE is that the instrumentation is not source-level:
`solidity_path_coverage()` rewrites the goto program, and a goto-level ghost is
not a component of the contract object that `_sol_save_this` snapshots. But that
is an argument, and this project's rule is that a proposition the method rests on
gets written as a check.

## The fixture

`f(a, b, c)` has three decisions and then a reverting `require`:

```solidity
if (a > 0) t += 1;
if (b > 0) t += 2;
if (c > 0) t += 4;
require(a + b + c > 100, "too small");
sink = t;
```

`g(x)` is the CONTROL: one decision, no `revert` anywhere. A run in which `g` is
not witnessed measured nothing, and the reverting paths' status would be evidence
of nothing.

## Result

Control fired: `g` — 3 path claims, **3 F**.

`f` — 17 path claims, 16 F / 1 U:

| enc | depth | status | exit_kind |
|---|---|---|---|
| 2 | 1 | F | revert |
| 48, 50, 52, 54, 56, 58, 60, 62 | 5 | **F** | **revert** |
| 49, 51, 53, 55, 57, 59, 61 | 5 | F | normal |
| 63 | 5 | **U** | normal |

**Nine revert-exit paths, nine witnessed.**

⇒ **READING A.** A witness exists only if the exit assert saw
`tr == enc && cnt == depth`, so the accumulator was NOT rolled back with the
contract state. **Identity survives the revert, and no rollback modelling is
needed FOR THE ACCUMULATOR.**

Rollback modelling is still needed for two other things and this measurement says
nothing about either: the contract STATE (so `final_state` / R1 / R2 describe
what the chain would see), and R0's exit KIND (so the emitter renders
`vm.expectRevert` rather than a bare call).

## Two things that fell out of the same table

**The depth is 5, not 4, and `enc=2 depth=1 revert` exists in BOTH units.** That
first decision is the synthesised ABI non-payable value gate, and its revert arm
is a complete path of its own. So a function with no `revert` in its source still
has a revert-exit path — which is why `g`, written to have none, has one.

**`enc=63` is the only U, and it is `bounded-holds` — on a path that is
INFEASIBLE BY ARITHMETIC.** `report_summary.py` on the same report:

```
U_reasons   bounded-holds 1, solver-unknown 0, claim-budget-exceeded 0,
            named-obstacle 0, not-solved-this-run 0, unit-not-entered 0
```

The three `if`s are independently reachable and `require(a + b + c > 100)` splits
each combination in two — except one: with `a = b = c = 0` the sum is 0, so the
`require` cannot pass. **Exactly one of the twenty enumerated paths has an empty
domain, and it is the one that came back U.**

⇒ That is the right verdict to REACH and the wrong LABEL to give it. `I` (proven
unreachable) is hardwired off — `bmc.cpp:722-725` is a bare `return false` — so a
path the solver has genuinely refuted lands in `bounded-holds`, which reads as
"not shown within the exploration bound". `EXECUTION_PLAN.md` §2 step 0.6 says
that line must not be flipped until the exploration is confirmed to
over-approximate every reachable state, and that is still not true today.

So this is a fixture where **the correct answer is knowable by hand**: 19 feasible,
1 infeasible, and the tool has the verdict but not the vocabulary. Worth keeping
for the day 0.6 is attempted — a must-flip pair for enabling `I` needs exactly
this, a path whose infeasibility is arithmetic rather than bound-dependent.

⚠ Do not read it as "`bounded-holds` means infeasible". It is the union of
"infeasible" and "not shown within the bound", and this run cannot separate them
in general — it separates them HERE because the contract is small enough to check
by hand. The corpus-scale claim already on record stands: `bounded-holds` being
the majority U reason says nothing about coverage (aqua is 7/7 with 2831 of them).

## Falsifier

If a later build shows the revert-exit paths of `f` coming back U while `g` and
`f`'s normal-exit paths stay F, this note is wrong. The checker prints that as
READING B and explicitly refuses to call it proof of erasure on its own — it is
also what a solver limit looks like.
