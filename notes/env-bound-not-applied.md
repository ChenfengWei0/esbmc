# An environment bound DOES bind. The witness payload can report a value that contradicts it — and that is what made a diagnosis wrong.

Measured 2026-07-30 on esbmc `2b8f03a7ac`.

> ⚠ **This file's first version claimed the opposite** — that a certification
> bound on an environment coordinate is counted but never applied, and called it
> a fourth false-certification path. That claim is **withdrawn**; the
> discriminating experiment below refutes it. The original evidence was real but
> admitted a second reading, and the second reading is the true one. What the
> episode leaves behind is a different defect, in the witness PAYLOAD rather than
> in the query, plus a retraction of a conclusion drawn from it yesterday.

## The discriminating experiment

The first observation was on a fixture whose path does NOT read `msg.sender`
(`Gate2.send`, guarded by its parameter). Deciding anything from it required a
fixture whose path DOES:

```solidity
contract Gate3 {
    address constant BANNED = address(0x...ff);
    function send(uint256 x) external payable returns (uint256) {
        require(msg.sender != BANNED);   // the PATH depends on the sender
        ...
    }
}
```

Two certification queries for the same revert path, differing only in the bound:

| box | expected if the bound binds | measured |
|---|---|---|
| `msg.sender ∈ [255,255]` (== BANNED) | every input reverts ⇒ SUCCESSFUL | **SUCCESSFUL** (exit 0) |
| `msg.sender ∈ [0,0]` | no input reverts ⇒ FAILED | **FAILED** (exit 1) |

Different verdicts from a query differing in nothing else. **The bound binds.**
Both candidate mechanisms in the withdrawn version — "never lowered into a
constraint" and "asserted on a copy the reseed overwrites" — are refuted by this.

## What the original observation actually shows

On `Gate2`, the query asked `msg.sender ∈ [5,5]` and the refuting witness
reported `msg.sender: 0`. Since the bound binds, the explanation is on the other
side: **that path never reads `msg.sender`, so the value the payload reports for
it is not the bounded quantity** — it is an unconstrained symbol the harvest
picked up, and the solver was free to leave it at 0.

On `EscrowSrc.cancel` the same contradiction appeared with the sender pinned to
0 and the witness reporting 32509824. There the path *does* read `msg.sender`,
so the reading is the other known one, and the emitter's own code documents the
mechanism: **nested/high/low-level call wrappers overwrite `msg_sender` with the
callee's identity**, and `cancel` makes such calls. The harvested value can
therefore be a POST-wrapper sender, not the entry sender the bound constrains.

Either way the payload's `env` entry for a quantity is not guaranteed to be the
entry-time value the box talks about.

## ⚠ The conclusion this retracts

`notes` and `method_update` I17 recorded, from the 2026-07-29 level-0 run on
`EscrowSrc.cancel`:

> the witness differs on `msg.sender`, which is not a bounded coordinate because
> the environment is unpinned, so the shrink loop halves the only bounded
> coordinate it can cut while chasing a difference that lives somewhere else
> — giving "divergence landing on an unpinned environment quantity" its own cell
> beside external-call returns and cross-coordinate equalities.

**That is not supported.** The "difference" is produced by
`divergence_text`, which compares the path's ENTRY counterexample against the
witness payload — and for `msg.sender` on a unit that makes nested calls those
are not the same quantity. A reported difference there may be an artefact of the
harvest rather than a property of the path.

What survives:

* the shrink loop does bisect `state.FACTORY` on that unit — that is directly
  observed in the shrink sequence and unaffected;
* level 0 correctly reports no single-point projection there — also unaffected;
* the *third cell* ("divergence lands on an unpinned env quantity") is
  **withdrawn** until the comparison is made between comparable values.

## The actual defect, stated narrowly

`divergence_text` is the function the evaluation leans on to say WHICH quantity
separates a witness from a path. For environment quantities it can compare an
entry value against a post-wrapper or unconstrained one, and report a difference
that means nothing. That is the same failure family as the ones this project
keeps finding — a reading that is right for the case its author had in mind — and
it is worth more than the bug: **the divergence report needs to say which of its
values are entry-time and which are not**, or exclude the ones it cannot
guarantee.

Not fixed here. Recorded with the experiment that would confirm any fix: a unit
with a nested call, a pinned sender, and a check that the reported witness sender
equals the pin.

## How this went wrong, and the cheap thing that caught it

The first version reasoned from one fixture whose path did not exercise the
quantity under test, then offered two mechanisms and picked neither — but wrote
a headline that assumed the class ("counted but not applied"). The discriminator
cost two runs of two seconds each on a five-line contract.

The rule this is an instance of: **an intervention that changes nothing is
evidence only after "did the intervention happen?" is answered** — and answering
it needs a fixture where the intervention would visibly change something.
