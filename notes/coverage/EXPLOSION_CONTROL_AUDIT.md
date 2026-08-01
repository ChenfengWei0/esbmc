# Audit — every mechanism that bounds state/path explosion, and what each one COSTS

Written for a full audit, so each row states **where I saw it**, not what I
believe. "Seen firing" means I read the line in a real run's output or the code
this session; "read in source/notes" means I read the mechanism but did not
watch it fire.

---

## 0. The staging — four phases, deliberately ASYMMETRIC

| phase | what it does | bounding posture |
|---|---|---|
| ① enumerate complete paths per unit | goto-level instrumentation, one claim per path | **may be relaxed** |
| ② grow a CE into a certified input region | out-of-process sweep (`certify_all.py`) | **tightened to match the artefact** |
| ③ prove assertions | not connected | — |
| ④ render Foundry tests | in-process (`foundry.cpp`) | tightened |

The asymmetry is the design: ① may over-approximate because a path that turns
out to be unwitnessable is reported U, while ②③④ must not, because their output
is a test that has to be GREEN on the unmodified contract.

⚠ **The staging has an ORDER DEFECT**: ② runs *after* ④. `certify_all.py` is a
python sweep run once esbmc has exited, and `foundry.cpp` emits during the solve.
So the emitter structurally cannot read a certified region — funnel stage A→B is
0 for this reason, not for a yield reason. (Seen: `foundry.h`, `certify/` holds
only jsonl.)

---

## 1. The bounds, and which ones LOSE PATHS

Sound-but-weaker vs. actually-drops-paths is the distinction that matters, and
the tool keeps it explicitly.

| # | mechanism | value | drops paths? | visible in report? | provenance |
|---|---|---|---|---|---|
| 1 | `--solidity-max-tx` | **1** | yes — deeper states unreachable | ✅ `bound.max_tx`, `tx_exploration` | seen in every cov-report.json |
| 2 | loop unwind bound | **4** | yes, silently | ⚠ warning only + `loops_truncated` | seen firing (`Not unwinding loop 35 iteration 4`) |
| 3 | call-depth bound | **4** | **no — MERGES** | ⚠ warning names the sites | seen firing (EscrowDst: 8 sites, named) |
| 4 | per-unit goal cap | **10000** | **yes** | not seen in any report I read | read in source |
| 5 | **degradation** | before #4 | **no — weakens** | ⚠ warning per unit | seen firing (st1inch: 12 units) |
| 6 | `--focus-function` | 1 unit | narrows *denominator on purpose* | ✅ prints the excluded count | seen firing |
| 7 | short-circuit operand cap | `SC_DECISION_MAX = 12` | **yes — site dropped whole** | ⚠ warning only | read in notes |
| 8 | per-claim solver budget | **120 s** | no — reports `claim-budget-exceeded` | ✅ `bound.claim_timeout_s` + enforcement string | seen in every report |
| 9 | outer process timeout | 300/900 s | **yes — whole unit vanishes** | ❌ **nothing at all** | seen (3 units) |
| 10 | `--memlimit` | 8 g | partial report, marked | ✅ `partial: true` + reason | seen firing (cvc5 OOM) |

### The two that are ordered against each other, deliberately

**Degradation (#5) is tried BEFORE the goal cap (#4)**, and the log says why:
degradation *withdraws call points from a unit's path identity* — the callees
still execute, they just stop contributing decisions, so the path classes get
coarser **while still partitioning the input space**. The cap instead **drops
paths that exist in the model**. Weakening an assertion is preferred to losing a
path. This ordering is the single best-designed thing in the list.

### #3 is the one whose cost is understated

"MERGED rather than enumerated" sounds sound, and it is — but a merged callee
contributes **no decisions**, so its branches can never appear in any witnessed
path. Measured this session: that is why `EscrowDst._withdraw`, `_ethTransfer`
and `onlyValidSecret` are missing from the numerator (D39). And D28 measured that
raising 4→6 buys **8 more paths, 4 more witnesses and ZERO decisions**, while the
residual frontier goes **up** 8→34, and bound 8 does not finish.

### ⛔ #9 is the only one with NO disclosure at all

A run killed by the outer timeout leaves **no `cov-report.json`** — the signal
arm cannot write JSON (malloc/iostream/log-mutex are unsafe in a handler). So the
unit does not appear as a zero; **it does not appear**. Three units in the corpus
are in this state (36 enumerated paths). `branch_gate.py` does catch it — it
reads the journal, counts `killed`, and appends `(partial)` — but any consumer
walking `reports/` cannot.

---

## 2. What is NOT bounded, and this is the important half

| not bounded | consequence |
|---|---|
| **external-call re-entry instantiation** | `EscrowDst.publicWithdraw`: **780 VCCs for 5 paths, extcall 780 — every VCC is a re-entry instantiation of the same assert.** 156 per path. This is the single biggest multiplier in the corpus and there is no cap on it. |
| **claim key multiplicity** | those 780 collapse onto **5 distinct claim keys**, solved separately, **with disagreeing outcomes**. `withdraw:path:2 at` alone: 85 solves, 84 PASSED + 1 FAILED. |
| **symex assignment count** | no bound; `withdraw` symex is 166 s / 44639 assignments before any solving. |
| **string-library loop unwinding** | `nondet_string`/`_str_assign` unwind per call site; farming `exit` shows 1810. Bounded only by #2, which then reports the paths as truncated. |
| **entry state** | never havoc'd — it is the post-constructor state. This is why `I` (proved infeasible) is **structurally 0**: `path_cov_can_prove_unreachable()` returns false unconditionally, so every unwitnessed path is `U/bounded-holds`. |

**The last row is the one that decides the funnel.** X→Y is 19 %, and **all 751
non-witnessed paths are `bounded-holds`** — meaning "no input found within the
bound", which cannot be split into "infeasible" and "not found" until entry state
is havoc'd (`__ESOL_nondet_state_forward`). Flipping the boolean without that
would assert *proved infeasible* for every path a different entry state reaches.

---

## 3. esbmc-internal vs. external-strategy — the triage this audit is for

| finding | fix lives in |
|---|---|
| re-entry instantiation 156×/path | **esbmc** (instrumentation) |
| one claim key for many instantiations, disagreeing | **esbmc** (claim key) |
| dying run mislabels U-reason `unit-not-entered` | **esbmc** (bmc.cpp attribution) |
| depth bound merges callees | **esbmc**, but measured not worth raising |
| entry state not havoc'd ⇒ I ≡ 0 | **esbmc** (harness) |
| killed unit leaves nothing | **both** — esbmc can't write JSON in a handler; the *collector* can record the hole |
| stale reports for skipped units | **script** (fixed this session) |
| corpus spans 4 builds | **script** (collection discipline) |
| certification aborts undiagnosable | **script** — `certify_all.py` discards the output that names the cause |
| emitter cannot express a region | **esbmc** (`foundry.h` type), *and* the order defect is **script** |

---

## 4. ⚠ Audit finding on my own method

Reducing to a PoC is what actually worked this session: **D36's ~60-line
generated contract** isolated st1inch's blocker to *conditional 256-bit
arithmetic composed 30 deep*, with bit width as a second factor, in seconds per
cell — after a three-hour full-contract matrix had a hole exactly where the
answer was. D37's five-backend comparison was **impossible** on the real contract
(every backend takes minutes or never returns) and trivial on the fixture.

Against that, most of this session's *diagnosis* was corpus-wide scans of
artefacts already on disk. Those are cheap (no esbmc runs) but they answer
"how much" and rarely "why". The rule this audit records for itself: **a
"why" question gets a PoC before it gets a sweep.**
