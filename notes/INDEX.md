# INDEX — read this first after a `/compact`

~60 of our own `.md` files under `notes/`, about 1 MB. Without an index every
context reset restarts from whichever file happens to be recalled, which is how
the same fact gets re-derived and a settled question gets re-opened. This file
exists to stop that.

## ⚠ Honesty marker, because an index that overstates is worse than none

`[read]` = I have read this file in full at some point and can vouch for the
one-liner. `[cited]` = I have read *parts* or seen it quoted, so the one-liner is
from its title/abstract and may be stale. `[unread]` = I have never opened it;
the one-liner is a guess from the filename and **must not be relied on**.

This marking is the point. A previous version of this project's habit was to
summarise a file from its name and then reason from the summary.

---

## 0. THE RESUME PATH — three files, in this order, before anything else

| order | file | why |
|---|---|---|
| 1 | `notes/SESSION_STATE.md` `[unread]` | 19 KB, the standing state doc. **I failed to read it for an entire session** — that failure is the reason this index exists. |
| 2 | `notes/coverage/INVOCATION_DECISIONS.md` `[cited]` | 584 lines. The **authority on which ESBMC options we run and why**, as numbered decision rows. Row 7 = backend auto-selection; rows 1–2 overturned tx=1 for the METHOD while the GATE stays at 1. |
| 3 | `notes/path-coverage-invocation-contract.md` `[cited]` | 1053 lines. Option **semantics read out of the SOURCE**, not `--help` — and `--help` is documented WRONG on transaction depth. |

Then, for whatever you are about to touch, the topic section below.

---

## 1. Method & measurement definitions (LOCKED — do not drift)

| file | one line |
|---|---|
| `coverage/METHODOLOGY.md` `[cited]` | LOCKED 2026-05-20. §2 canonical decision; §3 scope; §4 the capping rule; §8 denominator invariants; §10 the two known baseline reach gaps. |
| `coverage/README.md` `[unread]` | entry point for the coverage subtree. |
| `coverage/commensurability-audit.md` — actually `notes/commensurability-audit.md` `[unread]` | 24 KB. Presumably the two-sides-comparable audit. |
| `coverage/gate-first-attribution.md` `[cited]` | first pass at attributing the gate gap; superseded in part by D39. |

## 2. Explosion control / options  ← the current audit surface

| file | one line |
|---|---|
| **`coverage/EXPLOSION_CONTROL_AUDIT.md`** `[read]` | Every bound, whether it DROPS paths or only weakens, and whether the report discloses it. Plus what is NOT bounded (re-entry instantiation, claim-key multiplicity, entry state). |
| **`EXPLOSION_CONTROL_OPTIONS.md`** `[read]` | What each option MEANS and how it works, with per-claim provenance. **KNOWN INCOMPLETE** — see §7. |
| `coverage/option-matrix-round1.md` `[cited]` | first option matrix round. |
| `coverage/unwind-vs-strategy.md` `[unread]` | **33 KB — the largest unread file directly on the unwind question.** Probably already answers `--incremental-bmc` / `--k-induction`. READ BEFORE re-investigating unwind. |
| `coverage/scope-and-resources.md` `[cited]` | §2 measured the loss a dying run causes and named the change sites. |
| `coverage/dying-run-keeps-its-work.md` `[read]` | The partial-report rescue, the CE journal, the per-claim budget (`--path-cov-claim-timeout`), and why `claim-budget-exceeded` is its own U token. |

## 3. The D-series — one measurement each, newest last

`[read]` for D25–D40 (this session or quoted in full); older ones `[cited]`.

| | |
|---|---|
| D14 ×2 | memlimit discriminator; what each outcome would mean. |
| D22 | outer-timeout discriminator. |
| D23 | two knobs, neither alone. |
| D24 | claim-key mismatch (`claim_sig` vs `claim_cstr`). |
| **D25** | **the locked baseline runs at ONE transaction** — measured, not inferred. Gate must stay at tx=1. |
| D26 | revert keeps the path identity (`tr` is a goto-level ghost, not contract state). |
| D27 | the gate gap is named in our own logs. |
| **D28** | **raising the call-depth bound buys 8 paths, 4 witnesses, ZERO decisions**; bound 8 unaffordable; residual goes UP 8→34. |
| D29 | st1inch degradation is real but not the operative cause. |
| ~~D30~~ | **WITHDRAWN by measurement** — the 30-deep constructor chain costs nothing. Kept only as a record of the retraction. |
| D31 | the router is right about aqua and wrong about st1inch. |
| D32 | setFeeReceiver solves each claim twice and the two disagree. |
| D33 | a constructor call to a public unit doubles its claims under one key. |
| **D34** | **the constructor-scope witness becomes a deployment that REVERTS** — forge-proven RED. Basis of the ruling: a constructor-scope execution is not a unit path. |
| D35 | VCC/path reaches 156; claim keys collide on four benchmarks. |
| **D36** | **60-line reproduction of st1inch's F=0**: conditional 256-bit arithmetic 30 deep; **bit width is the second factor**. The PoC that worked. |
| **D37** | **cvc5 decides at uint256 what z3 OOMs on**; the auto-router's stated reason is falsified on that shape. |
| **D38** | cvc5 does **not** rescue the real contract (OOM in the first solve, budget never binding); corpus audit: 48 stale reports, 3 invisible units, 4 builds. Contains two self-corrections. |
| **D39** | **every missing canonical decision named**: 31 of 31 attributed, 0 unexplained. Includes the overturn of the "crypto-inversion guard" story. |
| **D40** | the three killed units: publicWithdraw = re-entry 156×, farming = re-entry 15× (**corrected** from "string loops"), st1inch = per-solve cost. |

## 4. The emitter / stage ④  (subgoal 4)

| file | one line |
|---|---|
| `foundry/TECHNICAL.md` `[unread]` | 34 KB, largest emitter doc. |
| `foundry/design-plan.md`, `v4-foundations.md`, `v5-impl-spec.md`, `roadmap.md`, `benchmark-plan.md`, `foundry_surface.md` `[unread]` | 6 files, ~90 KB total. **Strong compression candidate** — several are plan-stage docs for work now shipped. |
| `emission-loss-four-samples.md` `[cited]` | source of the RED-disabled counts the funnel prints. |
| `emitter-ce-value-loss-audit.md` `[unread]` | 31 KB. |
| `emitter-attribution.md`, `forge-roundtrip-aqua.md`, `r0-events-three-missing-layers.md` `[unread]` | |

## 5. Regions / stage ②  (subgoal 3)

| file | one line |
|---|---|
| `interval-input-scope-and-plan.md` `[unread]` | **37 KB — the subgoal-3 plan. Read before touching regions.** |
| `coverage/certify-vs-assert-vacuity.md` `[unread]` | |
| `coordinate-settability-census.md`, `the-bracket-is-the-whole-cost.md`, `s3-disjunctive-regions.md`, `s10-msg-value-is-a-fact.md` `[unread]` | |
| `probe-entry-state-havoc.md` `[unread]` | 24 KB. **Entry-state havoc is the blocker for `I` ever being non-zero** — this is the file for it. |

## 6. Probes & one-off investigations `[unread]`

`probe-arith-check-lowering.md`, `probe-enc-decode.md`, `probe-focus-function.md`,
`probe-residual-and-verdicts.md`, `pipeline-silent-default-sweep.md`,
`runnability-distribution.md`, `single_fn_loop_elision_plan.md`,
`escrowsrc-after-coordinate-fixes.md`, `escrowsrc-cancel-factory-is-msg-sender.md`,
`env-bound-not-applied.md`, `unresolvable-pin-kills-every-query.md`,
`geometric-ladder-wraps-on-narrow-types.md`, `bug-inherited-local-initializer-dropped.md`,
`trycatch_s0_preflight.md`, `s4-punch-end-to-end.md`, `s5-s7-s8-edit-sites.md`,
`arith-resolve-design.md`, `path-cov-assert-patch.md` (106 KB!),
`path-cov-assert-plan.md` (54 KB).

---

## 7. ⚠ KNOWN GAPS in the current audit — do not present it as complete

Raised and not yet answered:

1. **`--incremental-bmc` / `--k-induction` were never tried.** ESBMC's own
   truncation warning names them as the alternatives to a fixed `--unwind`. The
   bound of 4 is not chosen or argued — it is the value path enumeration uses,
   adopted so symex matches it. **We have no mechanism for knowing how many
   unwinds are needed.** `coverage/unwind-vs-strategy.md` (33 KB, unread) is the
   first place to look.
2. **Layered verification is missing from the audit.** The covered-set
   (`--coverage-covered-set`, on-disk version 3, `witnessed_in_earlier_round`)
   means an already-witnessed path is **not re-instrumented next round**. That is
   a cross-run explosion control and it was not listed.
3. **Multi-input `--focus-function`** (comma-separated set) exists — we built it —
   and the audit describes only the single-name form. See
   `coverage/scripts/multifocus_check.py`.
4. **`--solidity-max-tx` growth and "how to turn it off"** are unanswered. Note
   `0` is NOT off — it is the shallowest setting. Whether a genuinely unbounded
   mode or a progressive-deepening driver exists is unchecked.
5. **`--path-cov-max-goals` was never seen firing** and is not surfaced in any
   report read so far. If it drops paths silently that is a disclosure hole.

## 8. Compression / deletion candidates — PROPOSED, none executed

Nothing has been deleted. These are proposals, and each needs its file read
before acting, because "outdated by the filename" is exactly the inference this
index refuses to make.

| candidate | reason |
|---|---|
| `coverage/D30-*.md` | **WITHDRAWN by measurement.** Keep a 3-line tombstone, drop the 9 KB body. |
| `coverage/D14-memlimit-discriminator.md` + `D14-what-each-outcome-would-mean.md` | two files, one question, 20 KB. Merge. |
| `foundry/{design-plan,v4-foundations,v5-impl-spec,roadmap,benchmark-plan}.md` | ~76 KB of PLAN-stage docs for work that shipped. Compress to one "what was built and why" + delete the superseded plans. |
| `path-cov-assert-patch.md` (106 KB) | a patch listing; if the patch is merged this is history, not reference. |
| `coverage/gate-first-attribution.md` | superseded in substance by D39 (31/31 attributed). Verify, then tombstone. |

**Rule for whoever acts on this:** a note that records a WITHDRAWAL or a
CORRECTION is not dead weight — this project's convention is to keep the
retraction visible (D30, D38 §5, D40's correction). Compress the body, keep the
tombstone and the reason.
