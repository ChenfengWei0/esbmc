# D29 — st1inch withdraws exactly three callees from twelve units, and that CANNOT be why it scores 0/86

**Measured 2026-08-01**, `notes/coverage/scripts/degradation_census.py`, over the
corpus's own run logs. **No new ESBMC run.**

## The question, and the thing that had to be settled first

`residual_call_census.py` (D27) found st1inch is the only FAIL with no call-depth
truncation: instead it shows **twelve DEGRADED units on every one of its
twenty-two runs**. `branch_gate.py` names degradation as the first of three
mechanisms that deflate our numerator — "internal calls withdrawn by degradation
… remove those decisions from every path of the unit while branch coverage still
counts them" — and says it is not visible in the gate's output.

Before any of that could be quantified, one thing had to be established rather
than assumed: **does the tool name the withdrawn CALL SITES, or only the degraded
UNIT?** Those support completely different next steps. D27's census truncates its
bucket keys to 90 characters, which is right for a tally and hides exactly this.

## It names everything, and there is nothing left to infer

```
WARNING: --solidity-path-coverage: DEGRADED unit 'sol:@C@St1inch@F@votingPowerOf#8484'
  — fully expanded it enumerates more paths than the per-unit budget (10000), so
  1 call point(s) were WITHDRAWN from its path identity and are now treated as
  black boxes: sol:@C@St1inch@F@_votingPowerAt#7638 at file
  …/st1inch__St1inch.flat.sol line 4615 function votingPowerOf. The callees still
  EXECUTE (the call is still there), they just stop contributing decisions …
```

plus a per-run summary line:

```
degradation summary — 12 unit(s) had 12 call point(s) withdrawn to fit the
per-unit budget (10000); 0 of those unit(s) could not be made to fit even with
every call point withdrawn.
```

**Twelve units, but only THREE distinct callees:**

| withdrawn callee | withdrawn from |
|---|---|
| `_votingPowerAt#7638` | `votingPowerOf` (4615), `votingPowerOfAt` (4630), `votingPower` (4640), `votingPowerAt` (4651), and via `_earlyWithdrawLoss` (4799) for `earlyWithdraw`, `earlyWithdrawTo`, `earlyWithdrawLoss` — **seven of the twelve** |
| `_deposit#8768` | `deposit` (4661), `depositWithPermit` (4673), `depositFor` (4683), `depositForWithPermit` (4694) |
| `_removeAllPlugins#2651` | `removeAllPlugins` (1800) |

**It fires on every run regardless of focus.** All twenty-two logs show twelve —
including `--focus-function setFeeReceiver`, which has nothing to do with voting
power. The tool says why in the same logs: `--focus-function` narrows
INSTRUMENTATION, but "internal-call EXPANSION still ran for every unit". So
degradation is computed contract-wide.

## ⛔ AND IT CANNOT BE THE OPERATIVE CAUSE OF THE 0/86

This is the ordering the census forced, and it is the opposite of what the
mechanism's prominence suggests.

Withdrawal removes a callee's decisions **from the paths that get witnessed**. It
can only cost the numerator decisions that a witness would otherwise have
carried. **st1inch has `F = 0` — not one path of 128 was witnessed** (21 reports,
every claim U; `bounded-holds 81`, `solver-unknown 47`, `unit-not-entered 0`).

⇒ With no witnesses, no `decisions` array exists for degradation to have thinned.
Undoing every withdrawal would move the numerator from 0 to 0.

⇒ **st1inch's 0/86 is gated on F = 0 first.** Degradation is a real second-order
loss that would matter the moment F > 0, and it is not what to work on now.

That is the same trap this corpus has sprung before, in the other direction:
`bounded-holds` dominating the U reasons was once read as a coverage argument,
and aqua is 7/7 with 2831 of them. A mechanism being loud is not a mechanism
being load-bearing.

## What to do instead, and it is the user's suggestion

`F = 0` on st1inch is a SOLVER outcome: measured across the whole corpus, 4372
solves gave 464 SAT and **st1inch contributed none of them** (106 UNSAT, 90
no-verdict). Coverage needs SAT — an `F` IS a SAT — so st1inch's zero is
currently forced by whatever is deciding its queries.

`INVOCATION_DECISIONS` row 7 records that its solver was auto-selected and that
the auto-selection is confounded with the contract: st1inch is the only benchmark
run with `--z3 --tuple-node-flattener`, and the only one with `solver-unknown`,
so backend and contract cannot be told apart in this corpus. D14 then showed the
47 no-verdicts are z3 reporting **`out of memory`**, unchanged between 4 g and
16 g.

⇒ Next: a solver arm (`--z3` / `--cvc5` / `--bitwuzla` / auto) measured **on the
gate's currency** — witnessed decisions, not "did it return" — with a positive
control on a unit where SAT is already on record, so "no SAT anywhere" can be
distinguished from "this cell cannot produce SAT".

## Open, and stated rather than guessed

**Where `_votingPowerAt`'s body lives is not established here.** It is the callee
withdrawn from seven of the twelve units, and `VotingPowerCalculator.sol` holds
**63 of st1inch's 86 canonical decisions** — the largest single denominator block
in the corpus. If those decisions sit behind that call point, degradation is
exactly the mechanism that would keep them out of our numerator once F > 0. The
symbol is spelled `@C@St1inch@F@_votingPowerAt`, i.e. resolved against the
contract, which says nothing about which file declares it. **That link is one
AST lookup away and has not been done.** It is written here as a question because
this project has already once read "the callee is named" as "the decisions are
lost".
