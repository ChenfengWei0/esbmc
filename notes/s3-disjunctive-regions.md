# S3: a path's region is a LIST of boxes, and what four runs actually showed

`notes/interval-input-scope-and-plan.md` §6 called S3 "the biggest single yield
change and the one needing no tool support". This is what it does, and what it
does NOT yet do, from runs rather than from the design.

## What changed

On a refutation the loop used to replace the box with the tool's suggested cut
and throw the other side away. That side is not known to be outside the path's
domain -- the cut excludes ONE refuting witness, and the rest of that side may
be domain the path really has. Certification is a per-query judgement, so a
union of separately certified boxes is itself certified; only the
REPRESENTATION had to change, from one box to a list.

`--max-region-pieces`, DEFAULT 1 (OFF). Same house rule as `--level0` and
`--max-holes`: keeping both sides changes what a default run reports, and it
costs queries -- worst case pieces x shrink-rounds certification runs per path.

Three smaller decisions, each of which could have gone wrong silently:

* **C2 applies only to the piece holding the counterexample.** On every other
  piece the CE is outside BY CONSTRUCTION -- that is what made it a separate
  piece -- so running C2 there would reject exactly the pieces S3 exists to
  keep. What replaces it is the tool's non-vacuity witness, and each such piece
  says on its own line that this is the guarantee it rests on.
* **C3's widening check reads the UNCLAMPED suggestion.** `split_on_cut` clamps
  the suggestion to the current interval; if that ran first, a suggestion
  reaching outside the box would be silently trimmed instead of caught as the
  invariant violation it is. The order is deliberate and is commented where it
  matters.
* **The partition check runs over ALL pairs**, including two pieces of the same
  path. They are carved as complements, so they are disjoint by construction --
  which makes an intersection between them a defect in the splitting, and
  skipping them would leave the new code as the only part of the loop with no
  partition check on it.

## The must-flip pair, end to end

Same command, same contract, `--max-region-pieces` the only difference. The
default half was diffed against a log produced by the PRE-S3 binary:

    $ diff s4_run_noholes.log s3_default.log
    1c1   < [workdir] .../wd0      > [workdir] .../wd3
    6c6   < geometric-bracket: 52.3s wall     > 52.4s wall
    10c10 < max=0.002s median=0.001s total=0.1s  > max=0.003s ...

Three lines: the working directory and two wall clocks. **Every substantive
line -- every shrink, every region, every failure reason -- is byte-identical.**

At `--max-region-pieces 4` the branch fires:

    [split enc=2 piece 1] keeping the discarded side x in [57, 57] as a separate piece
    [shrink enc=2 piece 1] ... -> x (58, 255)
    [split enc=30 piece 1] keeping the discarded side x in [56, 56] as a separate piece
    ...

and every spawned piece is certified by its own query, with its own shrink
budget, and reported under its own path.

## What the spawned pieces came back as, and why that is the right answer

On the Punch fixture the CE-less pieces came back **VACUOUS** -- e.g. `x in
[56,56]` for a path whose real domain is `{42}`. That is the non-vacuity witness
doing exactly the job S3 delegates to it: a piece with no known member is judged
by whether the query witnesses an execution walking the path, and here there is
none. On `enc=2` the pieces came back REFUTED instead, also correct.

So the guarantee S3 substitutes for C2 was observed working on a real run, in
the direction that rejects.

## THE HONEST GAP: one branch has still never fired

**No run so far has produced a CE-less piece that CERTIFIES.** The line

    [certify enc=N piece K] certified WITHOUT a known member: ...

has not appeared in any output. Every spawned piece has been VACUOUS or
REFUTED. So the half of S3 that actually BUYS yield -- a second certified box
for one path -- is implemented and unexercised, and it is labelled that way here
for the same reason the punch branch was labelled before it was run.

Two things stood in the way, both measured rather than guessed:

1. **The tool's cuts on this fixture move by one value at a time** (57 -> 58 ->
   59), so the discarded side is a single point and is almost never domain.
   The piece budget is then spent on the shape of the cut rather than on
   anything informative. On the `--max-holes 2` run of the same contract the
   cuts were much larger (57 -> 159 -> 208), so this is a property of the
   solver's choices on this shape, not of the method.
2. **A union on ONE path is harder to construct than expected.** A fixture built
   for it -- `unchecked { require(uint8(x - 40) > 4) }`, whose surviving domain
   is `[0,39] u [45,255]` behind a single source-level guard -- was enumerated as
   TWO complete paths (enc=6 and enc=7), each certified over its own interval,
   [0,27] and [57,255]. The path granularity had already split the union, so S3
   was not needed there at all.

Point 2 is worth more than the failed attempt: it suggests complete-path
enumeration already decomposes many of the unions S3 was designed to recover,
which would mean the yield S3 buys is smaller than the plan assumed. That is ONE
sample and is written as a question, not a finding -- the honest next step is to
count, on the real benchmarks, how often a refutation's discarded side is larger
than a single point.
