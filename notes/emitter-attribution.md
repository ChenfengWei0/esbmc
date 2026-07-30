# Emitter method attribution: what the two run modes actually produce

Measured 2026-07-29 on esbmc `9b38281887`, aqua flat with the locked collector's
11 `--coverage-exclude-contract` flags, `--solidity-path-coverage
--solidity-max-tx 1 --generate-foundry-testcase`.

## The comparison

| | whole-contract | `--focus-function pull` |
|---|---|---|
| wall | 319.2 s | 2.7 s |
| witnessed (F) claims | **15 across 6 units** | 5, all `pull` |
| per unit | `dock` 2 `[2,12]`, `pull` 5 `[2,58,59,62,63]`, `push` 2 `[2,14]`, `rawBalances` 2 `[2,7]`, `safeBalances` 2 `[2,14]`, `ship` 2 `[2,1756]` | `pull` 5 `[2,58,59,62,63]` |
| emitted cases | **4**: `rawBalances` x1, `safeBalances` x1, `ship` x2 | **2**, both named `pull` |
| `pull` cases emitted | **0** | 2 |

The whole-contract run witnesses `pull`'s five paths with **the same path ids as
the focused run** — `[2, 58, 59, 62, 63]` in both — and emits no `pull` call at
all. `dock` and `push` are likewise witnessed and likewise absent from the
artifact. Three units' counterexamples do not reach the emitted test.

Two further differences between the modes, on the same contract:

* whole-contract emits `vm.prank(address(uint160(0)))` before every call; the
  focused run emits no prank at all;
* the same value renders as `address(0)` in one mode and `address(uint160(0))`
  in the other.

## What is established, and what is not

ESTABLISHED, by direct observation rather than inference: the whole-contract
mode witnesses counterexamples for six units and emits cases naming three, and
`pull` — which it definitely witnessed, under identical path ids — is not among
them.

NOT ESTABLISHED: that a `pull` counterexample is *renamed* as a `ship` call,
which is how S1.28 characterised it. The whole-contract artifact contains two
`ship` cases and `ship` itself has two witnessed claims, so those two cases are
equally consistent with being `ship`'s own. Dropping and renaming are different
defects pointing at different code, and this measurement does not separate them.

## Why it cannot be separated today, and the one thing that would

**The emitted test carries no claim provenance.** Each case is a bare
`test_cov_N` with a call and a comment; nothing records which path claim it was
reconstructed from. So "case 3 is `ship`" cannot be checked against "which claim
produced case 3".

The discriminating change is small and is not a design decision: emit the
originating claim identity (`<unit>:path:<id>`) as a comment on each generated
case. With that, the same run answers the question outright — a case whose
comment says `pull:path:58` and whose call says `c0.ship(...)` is a renaming; a
`pull` claim with no case at all is a drop.

That is also worth having on its own terms, independently of this bug: a
generated test that cannot say which verification obligation it came from cannot
be audited against the report, and every acceptance criterion in this project
that compares "what was measured" against "what was shipped" needs exactly that
link.

---

# RESOLVED 2026-07-30: it was RENAMING, and the cause was not the one inferred

## What the provenance link answered immediately

Emitting the originating claim identity on every case (the change this note asked
for) settled the open question in one run.

**Focused run.** Five refuted `pull` obligations, two cases:

```
// claim: pull#3153:path:63, path:62, path:2   -> test_cov_0
// claim: pull#3153:path:59, path:58           -> test_cov_1
```

Nothing was dropped: the five collapsed onto two by dedup, because their
reconstructed calls are byte-identical. ⚠ Worth its own line — **three
DIFFERENT complete paths render as the same call**, so what separates them is
not in the emitted payload. That is the reach gate showing up in the PRODUCT
rather than in a certification failure, and it was invisible before.

**Whole-contract run, before the fix.** All five `pull` obligations appeared on a
case whose call was `c0.ship(...)`; `dock`'s two and `push`'s two likewise. So
9 of 15 obligations were emitted under another unit's name — **renaming**, which
is what S1.28 said and what this note had recorded as NOT established. That
record was correct at the time and is now superseded.

## The cause, after a fix aimed at the wrong one

First attempt: restrict the method-naming override to the REFUTED assert rather
than any guard-true one. Rebuilt, re-ran: **output identical.** The mechanism had
been inferred, so the trace was added instead (`--verbosity solidity:9`):

```
refuted={...pull#3153:path:63} segs=[Aqua.ship] callable={dock pull push rawBalances safeBalances ship} emitted=[Aqua.ship]
```

Each counterexample refutes exactly ONE claim; `pull` IS dispatcher-callable; and
the segment's method was nevertheless already `ship`. The reason is visible in
the solver line itself — `✗ FAILED: 'pull:path:63 at'`, **with nothing after
`at`**. These complete-path claims carry no source location, so
`step_location_method` returns empty, the authoritative override never fires, and
the method stays whatever the "first dispatcher-callable body that executed in
this segment" fallback set — in a dispatcher, whichever body comes first.

**Fix:** attribute from the claim's own IDENTITY, which needs no location:
`sol:@C@<C>@F@<m>#<id>:path:<n>` names the unit outright. The location remains
the fallback.

## After

All 15 obligations attribute to their own unit, and the artifact goes from 4
cases naming 3 units to **7 cases covering all 6**, matching what the focused
runs produce:

| | before | after |
|---|---|---|
| cases emitted | 4 | 7 |
| units represented | 3 | 6 |
| obligations under the wrong method | **9 of 15** | **0** |

`rawBalances` 2, `safeBalances` 2, `ship` 2, `dock` 2, `pull` 5 (across two
cases), `push` 2.

## What is pinned, and what is not

* `foundry_covgen_claim_provenance_pass` pins the provenance line and its counts
  (`5 of 5 case(s) ... standing for 7 refuted path claim(s)`), and a fault
  injection disabling the refuted predicate turns **only** that test red — 40
  others stay green, so the gate is specific rather than one-size-fits-all.
* It does NOT pin the aqua fix: reproducing that needs a claim with no source
  location, and this fixture's claims have them. Stated rather than papered
  over — the regression suite covers the shapes we thought of. The evidence for
  the fix is the before/after above.
* The emitter now reports `N of M case(s) name the obligation ... standing for K
  refuted path claim(s)` so the property is countable from stdout instead of
  only visible inside the artifact.

## Attempts, and why the first pass stopped here

Two attempts tonight. The first reproduced the symptom on real input (the
minimal two-method fixture does NOT reproduce it — both modes attribute
correctly there, which is the regression suite covering the shapes we thought
of). The second tried to obtain per-claim data by rerunning whole-contract with
`--cov-report-json`, and that run was killed at 551 s: exempting 200+ symbols
from slicing makes the same run take longer than the 319 s it takes without the
report.

The per-unit verdict grouping above was then recovered from the log the FIRST
run had already written, which is section 7 item 20's second question paying off
again — the answer was on disk before the second run was started.
