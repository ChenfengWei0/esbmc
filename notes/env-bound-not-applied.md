# A certification bound on an environment coordinate is COUNTED but not APPLIED

Measured 2026-07-30 on esbmc `2b8f03a7ac`.

## The observation

Minimal fixture (`regression/esbmc-solidity/solidity_path_cov_level0_single_point/contract.sol`,
`Gate2.send`, one parameter `to`, one `require(to != BANNED)`).

Query sent to `--path-cov-certify`:

```json
{ "unit": "send", "enc": 2, "depth": 1,
  "box": [ { "name": "to",         "lo": "0", "hi": "1" },
           { "name": "msg.sender", "lo": "5", "hi": "5" } ] }
```

The tool acknowledges both:

```
--path-cov-certify: unit 'sol:@C@Gate2@F@send#29' — assumed 2 input bound(s) at entry
```

The refuting witness it returns, from that same run's own report:

```json
"inputs": { "to": "1" },
"env":    { ..., "msg.sender": "0", ... }
```

`to = 1` respects its bound. **`msg.sender = 0` violates the bound the same query
said it assumed.**

The harvest is not the suspect: `to` is reported faithfully in the same payload,
and in runs with no sender bound at all `msg.sender` is likewise 0 — i.e. 0 is
simply the unconstrained default, which is what a bound that does not bind would
leave.

## Why this is a soundness matter, not a yield one

On a FAILED verdict an ignored bound only wastes a shrink round. On a
**SUCCESSFUL** verdict it produces a certificate about a region **wider than the
one requested** — the query answers "every input in THIS box walks the path"
about a box the caller did not ask for. That is the same failure family as the
three false-certification paths already closed:

* substring-matched prose (`e6f80fb64f` era),
* `lo > hi` certifying vacuously (`bc72da6417`),
* signed coordinates making a decimal-nonempty box empty in the solver
  (`b1177e8b84`).

This is a fourth, and its distinguishing feature is the worst one: **the tool
reports the bound as assumed.** The count in the message is what makes it look
applied. Section 5.1c's ruling was explicit that certification must refuse the
whole query rather than proceed with a coordinate it cannot express, precisely so
that a wider box can never be silently answered.

## What is NOT established

Two mechanisms fit the observation and this measurement does not separate them:

1. **Not resolved.** `msg.sender` is accepted into the bound count but never
   lowered into a constraint on the quantity the path reads.
2. **Resolved but stale.** The bound is asserted at entry on a copy that the
   harness's per-transaction reseed (`_sol_per_tx_reseed` writes `msg_sender`)
   overwrites before the body reads it — so the constraint binds something no
   guard consults.

Both are defects and both fail the same way, but they need different fixes, so
neither is asserted here. Discriminating them needs a fixture whose PATH depends
on `msg.sender` (Gate2's does not), which is the next step and is not done.

## Two things downstream that must be re-checked, not assumed wrong

* **The `--pin-env` measurement.** The recorded effect — "without it a
  non-payable function certifies nothing (0/3 vs 1/3)" — is cited as the reason
  the flag exists. If an environment bound does not bind, that measurement rests
  on something that may not have been doing what it appeared to. It is not
  hereby wrong; it is now unverified.
* **The EscrowSrc.cancel diagnosis from 2026-07-29.** The level-0 run concluded
  the shrink loop bisects `state.FACTORY` while chasing a divergence that lives
  in `msg.sender`. Pinning `msg.sender=0` explicitly did NOT change the outcome,
  and the witness still reported `msg.sender = 32509824` — consistent with this
  defect. So the diagnosis stands as a description of what the loop does, and
  the intervention that would have tested it could not run.

## How it was found

By trying the intervention the previous diagnosis called for: pin the quantity
the witness diverges on, and see whether the failure reason leaves the budget
bucket. It did not — and the reason it did not is that the pin never took. The
useful shape here is that **an intervention which changes nothing is evidence**,
provided the next question asked is "did the intervention actually happen?"
rather than "what else could explain the outcome?".
