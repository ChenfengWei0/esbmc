# The already-verified skip is dead code under path coverage: written with one key, read with another

**Written BEFORE the repaired binary was run.** The must-flip pair is fixed here
first, including what would count as the repair being wrong.

## The defect, from source

`src/esbmc/bmc.cpp` — the INSERT discriminates between two spellings of the key,
the LOOKUP did not:

    :4657      if (is_goto_cov)
    :4658        reached_claims.emplace(claim_sig);        // msg + "\t" + loc
    :4660      else reached_claims.emplace(claim.claim_cstr);

    :3315      is_verified = reached_claims.count(claim.claim_cstr) ...
                                                          // msg + " at " + loc

`claim_sig` is built at `:3305`; `claim_cstr` at `slice.cpp:253-260`. Under
`--solidity-path-coverage` (`is_goto_cov` includes `is_path_cov`, `:3295-3296`)
the run stores `"withdraw:path:31\t"` and asks for `"withdraw:path:31 at "`.
Those can never be equal, so `is_verified` is ALWAYS false and the skip at
`:3325` is dead code. Every symex instantiation of one instrumented assert is
solved again from scratch.

Path coverage does NOT ask to keep verified claims — it sets
`keep-verified-claims` to **false** (`esbmc_parseoptions.cpp:4276`), i.e. it asks
for the skip. So this was never a policy choice; it is a key mismatch. The line
is copied verbatim into all six coverage dispatches with no comment of its own.

## What it costs, measured before the repair

    EscrowDst.withdraw   5 distinct claim keys, 425 VCCs, ~85 solves per path
                         all 4 obtainable witnesses in hand after 46 solves
                         the run then died: std::bad_alloc at 8 GiB
                         PARTIAL report, F 4 of 5, 80 %

    st1inch setFeeReceiver   5 keys, 10 VCCs, every key solved exactly twice

The tool has been reporting this all along. `Verdicts Preserved` is printed on
every path-coverage run and its own header (`goto_coverage.h:750-753`) says a
non-zero value "means the same claim key reached the solve loop more than once
-- which is itself a defect (duplicate instrumentation)". Nothing acted on it.

## The premise was checked BEFORE the change, not after

The repair only helps if the repeats share the key. If the copies differed in
their location suffix, `claim_sig` would differ per copy and the corrected
lookup would fire ZERO times while costing a string build per job — the repair
would be in the wrong place entirely. `notes/coverage/scripts/claim_key_identity.py`
is that check, run on the two logs already on disk:

    Solving claim lines read : 56
    distinct claim COMMENTS  : 10
    every comment -> exactly ONE distinct full key

Also visible there and worth recording: `claim_loc` is the EMPTY STRING for a
path claim (the lines read `withdraw:path:31 at ` with nothing after), which is
why the report's claims carry `file: ""` and `line: 0`.

## Scope of the repair, and why it is narrow on purpose

Fixed for `is_path_cov` only, not for `is_goto_cov`. Repairing it for the whole
family would newly enable skipping under branch / condition / k-path coverage,
and the branch-coverage dataset this project compares against is LOCKED. Moving
its numbers as a side effect of a path-coverage fix is exactly the kind of change
that cannot be attributed afterwards.

## Why it is sound for coverage

`reached_claims` receives only REFUTED keys — the insert sits inside the
`P_SATISFIABLE` arm. For coverage, F is monotone: a path with one witness is
covered, and further witnesses of the same path are `--all-witnesses` material,
which is enumerated INSIDE a single solve (`bmc.cpp:4602-4619`: one `push_ctx`,
a blocking clause per witness, one `pop_ctx` — no second encoding, no second
symex). No `P` / `U` / `B` verdict can reach the skip.

## MUST-FLIP, fixed in advance

| observation | before | after, required |
|---|---|---|
| `claim_key_identity.py` on a fresh `setFeeReceiver` run | each of 5 keys solved **2x** | each solved **1x** |
| `EscrowDst.withdraw`, 8 GiB | `std::bad_alloc`, PARTIAL, 46 of 425 | **completes**, report not PARTIAL |
| `Verdicts Preserved` on those runs | 2 and 8 | **0** |
| targeted regressions `solidity_path_cov` + `foundry_covgen` | 138/138 | **138/138** |

## TWO DECLARED BEHAVIOUR CHANGES — not hidden, and either could be the thing that breaks

1. **`path_ce` / `path_ce_all` will hold the FIRST copy's payload, not the
   LAST.** `bmc.cpp:4644-4645` is an assignment, so today the last physical copy
   wins. Different instantiations sit at different re-entry depths, so their
   `entry_storage` / `env` / `final_state` can genuinely differ. This is an
   observable change in the published payload, not a no-op.
2. **`decided_claims` falls and `skipped_properties` rises.** The skip returns
   before `bmc.cpp:3577`. That moves the PARTIAL report's "N of M claim(s)
   decided", `summary.claims_decided`, the `"mid-solve after claim N of M"`
   label, and the meaning of `--path-cov-fault-after N`.

## WHAT WOULD MEAN THE REPAIR IS WRONG

* **`Path Status: F` drops on any run.** Then some path was being witnessed only
  by a LATER copy, the copies are not interchangeable, and the skip loses
  coverage. This is the falsifier that matters and it is checked on every run
  below.
* Any of the 138 targeted regressions turns red.
* `Verdicts Preserved` stays non-zero — then the repeats are not reaching this
  branch at all and the fix is in the wrong place.

## OUTCOME (added after the runs, labelled as such)

Repaired binary `34c325966957aa7acaab4b1a7dd93979`.

### `EscrowDst.withdraw`, everything else identical — better than the must-flip asked for

| | before | after |
|---|---|---|
| Report Completeness | **PARTIAL**, `std::bad_alloc`, 46 of 425 decided | **COMPLETE** |
| Reached | 4 / 5 = 80 % | **5 / 5 = 100 %** |
| Path Status | F 4, I 0, **U 1** (`bounded-holds`) | **F 5, I 0, U 0** |
| Properties | ✓9 passed, ✗37 failed | ✓84 passed, **✓336 skipped**, ✗5 failed |
| log lines | 106 059, then `ERROR: Out of memory` | 19 742, then `VERIFICATION FAILED` |
| solves per key | 9-10 each | see below |

    withdraw:path:14   1 solve   (was 9)    refuted -> skipped thereafter
    withdraw:path:30   1 solve   (was 9)    refuted
    withdraw:path:31   1 solve   (was 10)   refuted
    withdraw:path:6    1 solve   (was 9)    refuted
    withdraw:path:2   85 solves             NOT refuted early -> not skipped

`path:2`'s 85 is the multiplication factor made visible: 425 VCCs / 5 keys = 85,
predicted before the repair and now directly observed on the one key the skip
does not touch.

**The fifth path became F, which the must-flip did not predict.** `path:2` was
`bounded-holds` before. One of its 85 instantiations is SAT — the re-entry depth
is itself part of feasibility — and the old run died before reaching it. So the
unit goes from "no report at all in the corpus" (`reportPresent: false`) to
100 %.

`✓ 336 skipped` had never appeared in any run: `report_simple_summary` prints
that field only when `summary.skipped_properties > 0`, and that counter could
only be incremented by the branch this repair revives. Its appearance is the
direct evidence the dead code is alive.

**Targeted regressions: `solidity_path_cov` + `foundry_covgen`, 138/138 green.**

### A must-flip of mine was MIS-DESIGNED, and that is recorded rather than quietly dropped

The table above originally required `st1inch setFeeReceiver` to go from 2 solves
per key to 1. It did not — it is still 2 — and **the judgement was wrong, not the
repair**. `setFeeReceiver` has **F = 0**: two paths PASS and three run out of
memory, so nothing is ever refuted, `reached_claims` stays empty, and the skip
correctly never fires. I picked as a discriminator a unit that has no object to
skip. The correct discriminator is a unit with refuted paths, which is why
`withdraw` is the row that means something.

This is the mirror of the dead-positive-control failure this project has
recorded three times: there the control did not fire and the negative result was
void; here the "must-flip" could not have flipped and its failure meant nothing.

### The declared behaviour changes, observed

`decided_claims` fell and `skipped_properties` rose exactly as predicted — the
`Properties:` line now carries a `skipped` field. Nothing regressed on the 138
targeted tests. The `path_ce` first-vs-last change is real but produced no
visible difference on this unit; it remains a declared change, not a measured
non-effect.

### `Path Status: F` did not drop anywhere — the falsifier did not fire

### `EscrowDst.publicWithdraw`: it moved too, but the attribution is NOT clean and that is stated rather than claimed

    780 VCCs over 5 paths (156x), 136 claims decided, F 4
    Report Completeness: PARTIAL — terminated by signal (the 900 s outer kill)

The corpus row for this unit says symex never finished — it "never printed a VCC
count at all". It now reaches 780 VCCs and witnesses 4 paths. **But relative to
that corpus row I changed TWO things: the binary AND the outer timeout
(300 s -> 900 s).** So for this unit the repair and the budget are confounded and
neither can be credited. The clean comparison is `withdraw`, where the OLD binary
was run at the SAME 900 s and died of OOM at 80 %.

It is still PARTIAL at 900 s, so `publicWithdraw` has a real cost beyond the
duplication. `killed_triage.py`'s classification of it as the defect candidate
stands.

### A REVERSE FINDING THAT BOUNDS THIS FIX, AND IT IS THE MOST USEFUL THING HERE

`withdraw:path:2` was **PASSED** (`bounded-holds`) on its early copies and became
**F** on a later one — that is where the fifth path came from.

⇒ **An UNSAT verdict is NOT final across copies.** Different re-entry depths are
different executions, so a path that holds at depth 1 can be feasible at depth 3.

⇒ Extending the skip to already-PASSED keys — the obvious next optimisation, and
the one that would remove the remaining 85 solves — is **UNSOUND for coverage**.
It would have skipped `path:2` after its first PASS and left this unit at 4/5 and
80 %, i.e. it would have undone the very result this repair produced.

So the fix is bounded by construction: only refuted keys can be skipped, and the
residual cost sits precisely on the keys that cannot be. Any future work on the
duplication has to attack the duplication itself (why one instrumented assert
becomes 85 or 156 VCCs), not the solving of it.

## STILL UNRESOLVED, and it decides whether this is the whole fix

`bmc.cpp:3548-3549` states the cause as "the same claim key is INSTRUMENTED at
more than one site". The log's recursion-unwind counts instead suggest symex
instantiating ONE instrumented assert 155 times. **These are different defects
with different fixes**, and this repair only addresses the cost of whichever it
is — it makes the repeats cheap rather than making them not exist. Which one is
true has not been read out of `goto_coverage.cpp`'s instrumentation pass yet.
