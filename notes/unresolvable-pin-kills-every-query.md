# A pin the certification query cannot express refuses every path of that unit

Found by the first corpus stage-2 sweep, on its first two real units, and it is
why that sweep was stopped after twenty minutes instead of being left to run for
four hours.

## The symptom

    aqua.rawBalances  enc=7:  no verdict from the certification query
                              (ESBMC printed neither SUCCESSFUL nor FAILED)
    aqua.safeBalances enc=14: the same

True, and useless. Two things ruled out "the query was too slow":

* the driver's total wall was 190.7s, of which the geometric bracket took 180.0s
  and the refine rounds 5.8s. **The certification query itself returned in about
  three seconds.** A timeout does not return in three seconds.
* the run's own `cert.json` was still on disk and carried a fifth bound:

      {"name": "state._DOCKED", "lo": "255", "hi": "255"}

## The cause, in the tool's own words

Re-running that exact `cert.json` by hand:

    ERROR: --path-cov-certify: unit 'sol:@C@Aqua@F@rawBalances#2819' — REFUSING
    THE QUERY because coordinate 'state._DOCKED' cannot be expressed: the name
    does not resolve to an input of this unit. ... (which reaches the contract
    object's own components only — a mapping or a dynamic array does not
    resolve). Certification is not attempted: dropping the bound would certify a
    WIDER box than the one asked for

`state._DOCKED` is a contract-scope global `sol:@C@Aqua@_DOCKED`, not a
component of the contract object. The counterexample harvest reports it in
`entry_storage`; the driver reads it as a `constant` state variable, concludes
no generated test can set it, and PINS it at its counterexample value. That
instinct is right and the outcome is fatal: certification refuses the WHOLE
query on one unexpressible name -- rightly, since dropping a requested bound
would certify a wider box than the one asked about -- so **one pin returns no
verdict for every path of the unit**.

## THE FIRST FIX WAS WIRED TO THE WRONG SENTENCE, and this is the part worth keeping

The obvious reading was that the outer-box rounds must already have complained,
since `round_failure_reason` harvests `has no input named 'X'` and only reports
it when a round measured NOTHING. So the first fix harvested that wording from
every round and dropped any matching pin.

**It never fired.** The re-run was byte-for-byte the old one: the pin still in
the box, the query still refused, the report unchanged. Two reasons, and both
had to be measured rather than assumed:

| | outer box | certify |
|---|---|---|
| wording | `has no input named 'X'` | `REFUSING THE QUERY because coordinate 'X' cannot be expressed` |
| where pins go in the spec | a separate `pin` field | folded into `box` as a degenerate bound |

So a pin certify cannot express is one the outer-box rounds **never see** and
never complain about. A detector wired to the wrong sentence is never wrong and
never right -- the same shape as [[always-true-reader]] and
[[detector-conditional-on-unknown]], reached from a third direction.

Both sentences are now pinned by tests, in both directions: the outer-box
wording must NOT read as a certify refusal, and the certify wording must NOT
read as an outer-box one.

## The fix

`unexpressible_coords(log)` reads the certify branch's own refusal.
`certify()` returns it, and the loop drops those names from the pins, the box
and the holes and re-queries -- **without consuming a shrink round**, because
the tool declined to attempt the query, so nothing was measured and charging
budget for it would let one unexpressible pin exhaust the path. Bounded by
construction: each pass removes at least one name from a finite set or stops.

Dropping is sound in the only direction that matters. An unmentioned coordinate
is universally quantified, so the certificate becomes **stronger**, not wider --
the region holds for all values of that quantity instead of for one slice
through it. It is announced anyway, because it changes what the region is a
statement about.

## Why the sweep was stopped rather than left running

Four hours would have produced a corpus table whose dominant real outcome was
"no verdict", with no way to separate a refused query from a crash or a slow
one. That is a number nobody can interpret, and letting it run would have looked
like progress. Twenty minutes were enough to find the cause, because the sweep
writes incrementally and keeps each unit's `cert.json`.

## Still open, deliberately not in the same change

A `constant` can never vary, so pinning one constrains nothing even when it IS
expressible -- dropping constants statically would be cheaper than reacting to a
refusal. Not done here because the reactive fix is the general one (it also
catches mappings and dynamic arrays, which are not constants), and because two
changes in one commit make it impossible to say which produced the measured
difference.
