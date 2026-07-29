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

## Attempts, and why this stops here

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
