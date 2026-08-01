# D25 — the locked baseline really is at one transaction, and that settles the tx question in the direction that costs us

**Measured 2026-08-01**, `notes/coverage/scripts/baseline_tx_depth.py`, binary
mtime `1785591755`, contract `notes/coverage/poc/Tiny.sol` (ten lines).

## The question this closes

`pathcov_collect.py` pins our side of the branch-coverage gate at
`--solidity-max-tx 1` and justifies it like this:

> "It is also what the locked branch-coverage dataset actually ran at — branch
> coverage IS in `unbounded_modes`, so it got bound 0, so one transaction. The
> two sides are at the same transaction depth."

That was an **inference from an option table**, never a measurement, and two
things made it worth checking: `collect.py` (LOCKED) never passes
`--solidity-max-tx` at all, and every baseline command carries
`--k-induction --unlimited-k-steps`, whose inductive step starts from a HAVOC'd
state — the very posture `EXECUTION_PLAN.md` §3.5 bans for our own artefact
because a witness resting on it becomes a RED test.

If the baseline's reach had included arms provable only from a havoc'd state, the
gate's bar and our numerator would be different quantities and 1/6 would not be
a comparison at all.

## The observable, and its positive control

`Tiny.withdraw` is `require(amt > 0); require(bal >= amt); if (amt > 100) {…}`
and `bal` starts at 0 with `deposit` as its only writer.

* **line 41** (`if (amt > 100)`) is reachable only after `bal >= amt > 0`, i.e.
  only after a preceding `deposit` — this is the observable.
* **line 39** (`require(amt > 0)`) is reachable in every model — this is the
  control, and a cell that misses it is reported VOID rather than as a zero.

Every cell reached the control.

## Result

| cell | configuration | Branches / Reached | covered decision lines | line 41 |
|---|---|---|---|---|
| A | baseline verbatim (k-induction, unlimited-k, no tx flag) | 8 / **5** | 34, 39, 40 | no |
| B | plain BMC, no tx flag | 8 / **5** | 34, 39, 40 | no |
| C | baseline + `--solidity-max-tx 1` | 8 / **5** | 34, 39, 40 | no |
| D | plain BMC + `--solidity-max-tx 1` | 8 / **5** | 34, 39, 40 | no |
| E | **plain BMC + `--solidity-max-tx 2`** | 8 / **8** | 34, 39, 40, **41** | **YES** |
| F | baseline Pair-2 shape (`--focus-function withdraw`) | 8 / **3** | 39, 40 | no |

## What it establishes

1. **The docstring is right: the baseline is at ONE transaction.** A, B, C and D
   are identical on reach. Naming the bound explicitly changes nothing, so the
   default really does resolve to one transaction under branch coverage.
2. **The havoc hypothesis is REFUTED, and cleanly.** A ≡ B on covered lines:
   `--k-induction --unlimited-k-steps` buys the baseline **no reach at all** on
   this contract. Whatever gives the baseline 100% on four benchmarks, it is not
   an entry state that no call sequence produces. The suspicion was worth
   testing and it is now closed against me, not for me.
3. **⛔ AND THE ONE THAT COSTS US: cell E.** The BASELINE ITSELF goes 5/8 → 8/8
   when run at `--solidity-max-tx 2`. The locked dataset's numbers are one-
   transaction numbers. So raising OUR side to tx=2 while comparing against a
   tx=1 baseline is not "using the configuration our decision table settled on"
   — it is running deeper than the thing we are being compared to, and we would
   be the ones cheating. The baseline is LOCKED and cannot be re-run at tx=2 to
   restore parity.

## The consequence for subgoal 1's deliverable

`INVOCATION_DECISIONS.md` rows 1 and 2 overturned `--focus-function` + tx=1 in
favour of whole-contract + tx=2, and printed that as **the** settled command
line. Rows 1 and 2 are about REACH and they are correct about reach. But the
file prints one command line where the project needs **two**, and the one it
prints is the wrong one for the gate:

* **the GATE run** must sit at the baseline's depth — one transaction — or the
  comparison is not a comparison;
* **the ARTEFACT / enumeration run** may use whole-contract tx=2, because there
  the question is "what can this method reach", with no second party to match.

That distinction is the same shape as the harness asymmetry already written into
`EXECUTION_PLAN.md` §5 (enumeration may be relaxed; certification must be
tightened to match the emitted artefact). It had simply never been applied to
the axis where a LOCKED opponent fixes the depth for us.

## The stronger consequence: the gap has NO transaction-depth excuse left

This does not stay on `Tiny`. Combine the measurement above with a number
already on record and the corpus follows.

The baseline's Pair 2 is a **union of per-method `--focus-function` runs**
(`collect.py:463-473`), each at one transaction. Under focus on method `m`,
`m` is the only thing the dispatcher offers, so state that another public method
`n` would have to write **cannot be written**. Cell F measured that directly and
it is the reason cell F exists: the baseline's own Pair-2 command shape, with
`--coverage-whole-unit` and everything, reaches 3 of 8 on `Tiny` and does **not**
reach line 41. `--coverage-whole-unit` does not buy cross-function reach.

⇒ **Every decision in the baseline's numerator is reachable from the constructor
within ONE call of ONE method.** A state-guarded decision could not have got in
there.

And on four of the six benchmarks the baseline's Pair-2 numerator **is the whole
denominator** — `EscrowDst 18/18`, `EscrowSrc 16/16`, `farming 26/26`,
`limit_order_protocol 3/3`. So on those four, *every decision the gate asks us
for* is one that a single focused call can reach.

⇒ **The 1/6 result has no transaction-depth or entry-state explanation left.**
Our corpus runs at exactly that envelope — `--contract C --focus-function f
--solidity-max-tx 1`, the same dispatcher narrowing at the same depth — so
whatever we are missing, we are missing it *inside* an envelope the baseline
proved sufficient.

### What this does to the bucket-(1) rollback

`EXECUTION_PLAN.md` §3's 1.3 measured "bucket (1), state guards = 0" on all four
benchmarks and I struck the inference drawn from it, correctly: the 0 was
measured in the one cell that cannot witness a state-guarded path, so it was a
measurement that cannot see X being used to conclude X does not exist. The
rollback went to **UNDECIDED**, not to true.

D25 supplies a **different argument for the same conclusion**, and this one does
not have that hole, because it reasons about the BASELINE's numerator rather than
about our own blindness: a decision the baseline reached under focus at one
transaction is by construction not state-guarded. On the four 100% benchmarks
that covers the entire denominator.

⇒ bucket (1) = 0 moves from UNDECIDED to **supported**, on this new basis only.
The old argument stays struck. §3.5's harness synthesis and §8's risk row stay
retired for the reason given here, not for the reason originally given.

⚠ The one benchmark this does NOT cover is `st1inch` (baseline 83 of 86, so
three decisions are outside the envelope) and `aqua` (7 of 8). Those two have a
residue; the four at 100% do not.

## What it does NOT establish

**One contract, and it must be named in any sentence built on this.** `Tiny` has
no library, no external call, no modifier and no mapping. It answers the tx-depth
and havoc questions because its ground truth is not in dispute; it says nothing
about why `EscrowDst` is 5/18.

But it does **remove one candidate explanation** for that gap by direct
measurement: reading 3's own consequence is that the baseline's `EscrowDst`
18/18 cannot contain a state-guarded arm either, because at one transaction it
could not have reached one. So the corpus gap is not entry state — which leaves
the residual-call/depth-bound explanation the tool names in its own run log
(`8 call site(s) are deeper than the call depth bound (4) … Raise --unwind`) as
the next thing to test, and `notes/coverage/scripts/depth_bound_sweep.py` is
already written for exactly that with its four readings pre-registered.

## Falsifier

If a later run shows any of A/C/F reaching line 41, or shows E failing to, this
note is wrong and everything built on it goes with it. The script is kept so the
six cells can be re-run against any future build.
