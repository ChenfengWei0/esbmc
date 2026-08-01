# The ESBMC options that bound path/state explosion — definitions and mechanism

Companion to `notes/coverage/EXPLOSION_CONTROL_AUDIT.md`, which lists *which*
bounds exist and what each costs. This file says what each option **means** and
**how it works**, because the audit alone gives a reviewer names and values with
no semantics.

## ⚠ Provenance rules for a reviewer, stated before any definition

1. **`--help` is wrong about at least two of these.** It is documented in this
   project that the help text and an earlier plan both had the transaction-depth
   semantics **backwards**. Do not check these definitions against `--help`.
2. **The authority is `notes/path-coverage-invocation-contract.md`** (1053 lines),
   which was written by reading the *source*, not the help text. **I did not
   re-read it while writing this file** — so where a definition below is marked
   `[contract]` rather than `[seen]`, the reviewer should verify against that
   file, not against me.
3. `[seen]` = I read this line in a real run's stdout, or in the source/docstring,
   during this session. `[contract]` = carried from the project's prior reading of
   the source. `[inferred]` = my reading of behaviour, not a quoted definition —
   treat as a claim to check.

---

## 1. `--solidity-max-tx N` — transaction-sequence depth

**Definition.** How many transactions the harness drives against the contract
from the post-constructor state. The Solidity harness synthesises a driver that
dispatches externally-callable methods; `N` bounds how many dispatches it makes.

**⛔ The counter-intuitive part, and it is the reason `--help` misleads.**
`--solidity-max-tx 0` is **NOT** "unbounded" — it is the **SHALLOWEST** setting
under coverage. `[contract]`

Mechanism: bound 0 emits an unbounded driver `while (nondet) { dispatch(); }`.
Solidity **coverage** instrumentation then rewrites that loop's **back-edge to a
SKIP**, which leaves exactly **one guarded transaction** — strictly fewer than
`--solidity-max-tx 2`. The report says this in its own `note` field:

> "In particular `--solidity-max-tx 0` is NOT an unbounded run … Use a larger
> `--solidity-max-tx N` to explore deeper, not 0" `[seen — every cov-report.json]`

**Path-coverage's own default is 2**, because path coverage is absent from
`unbounded_modes`. `[contract]`

**What the corpus runs at, and why it may not be raised.** `1`. Not a free
choice: D25 measured the LOCKED branch-coverage baseline on `poc/Tiny.sol`, whose
line 41 needs a preceding call — baseline verbatim reaches 5 of 8 and misses line
41; plain BMC at `--solidity-max-tx 2` reaches 8 of 8. So the baseline sits at
one transaction and **gains** at two, and it is locked and cannot be re-run.
Running the product side at 2 would be exploring **deeper than the thing it is
compared against**. `branch_gate.py` enforces this in code (`GATE_SOLIDITY_MAX_TX
= 1`) and refuses a collection made in another cell. `[seen]`

**Cost.** Any path whose guard needs state that an earlier transaction would have
to establish is unreachable and reported `U / bounded-holds`. The report states
this itself as `known_limitation_entry_state`. `[seen]` Measured instance: aqua's
`require(tokensCount1 > 0 …)` — all nine U paths of that unit sit one decision
deeper than the witnessed ones.

---

## 2. `--unwind N` — loop unwinding bound

**Definition.** Maximum iterations symbolic execution unrolls any loop.

**Mechanism under path coverage.** If `--unwind` is **not** given, path coverage
sets it to **4 by itself**, to match the bound its own path enumeration uses.
The run announces this: `[seen]`

> "no `--unwind` given; bounding symbolic execution at 4 to match the path
> enumeration's own loop bound. Without it an external call (modelled as nondet
> re-entry into this contract's dispatcher) or any loop runs unbounded until the
> memory limit."

**Cost, and it is SILENT unless you read the warning.** With
`--no-unwinding-assertions` active, paths needing more iterations are **assumed
away** rather than reported. The run prints: `[seen]`

> "Coverage may be UNDER-REPORTED: 3 loop(s) hit the unwind bound while
> `--no-unwinding-assertions` was active, so the paths that needed more
> iterations were silently assumed away."

The three loops named on every Solidity run are in **ESBMC's own string library**
(`solidity_string.c`: `nondet_string` line 245, `_str_assign` lines 206/209) —
i.e. the truncation is usually in the *harness*, not the contract. `[seen]`

---

## 3. Call-depth bound (= 4) — internal-call expansion

**Definition.** How deep internal (non-unit) calls are **inlined into the calling
unit's path identity**. A unit's path is the decision sequence of one call; a
callee expanded at this bound contributes its decisions to that sequence.

**Mechanism when exceeded — this one does NOT drop paths.** `[seen]`

> "8 call site(s) are deeper than the call depth bound (4) and were NOT expanded
> (…named…); paths through them are **MERGED rather than enumerated**."

So the callee still **executes**; it just stops contributing decisions, and paths
that differed only inside it collapse into one class. Sound, coarser.

**Cost, and it is bigger than "merged" sounds.** A merged callee's branches can
**never appear in any witnessed path's `decisions` array**. Measured: this is why
`EscrowDst._withdraw`, `BaseEscrow._ethTransfer` and the `onlyValidSecret`
modifier (applied by `_withdraw`) are absent from the gate numerator. `[seen]`

**Raising it is measured NOT to help** (D28): 4→6 buys **8 more paths, 4 more
witnesses, ZERO additional canonical decisions**; bound 8 does not finish in
400 s; and the residual frontier goes **UP** 8→34, because expanding deeper
exposes more call sites than it consumes. `[seen — D28]`

---

## 4. `--path-cov-max-goals N` (10000) — per-unit path budget

**Definition.** Maximum complete paths instrumented for a single unit. `[contract]`

**Mechanism.** A cap on the enumeration. **It DROPS paths that exist in the
model** — which is why it is the *last* resort, see §5.

**Audit gap I must flag to the reviewer:** I did not see this cap fire in any run
I read, and I did not find it surfaced in any `cov-report.json` I read. If it
fires silently, that is a disclosure hole comparable to §9 of the audit.
`[inferred — needs checking]`

---

## 5. Degradation — the mechanism that runs *before* the cap

**Definition.** When a unit fully expanded would exceed the goal cap, ESBMC
**withdraws call points from that unit's path identity** and treats them as black
boxes, instead of dropping paths.

**Mechanism, quoted from the run:** `[seen]`

> "DEGRADED unit '…votingPowerOf…' — fully expanded it enumerates more paths than
> the per-unit budget (10000), so 1 call point(s) were **WITHDRAWN from its path
> identity** and are now treated as black boxes … The callees still **EXECUTE**
> (the call is still there), they just stop contributing decisions, so the path
> classes get coarser **while still partitioning the input space** — sound, with
> weaker assertions, and weaker exactly at the call points named here. **This is
> tried BEFORE the goal cap on purpose: the cap would instead DROP paths that
> exist in the model.**"

**This ordering is the best-designed thing in the whole scheme** and the reviewer
should note it: the tool prefers a *weaker assertion over a lost path*, and says
so in the artefact rather than in a comment. Measured: st1inch degrades 12 units,
12 call points, and **0** units could not be made to fit. `[seen]`

---

## 6. `--focus-function <name>` (and `--contract <C>`)

**Definition.** `--contract C` selects which contract's dispatcher harness is
built. `--focus-function f` narrows **which units are INSTRUMENTED**. `[seen]`

**⛔ What it does NOT narrow, which matters for reading any number:**
internal-call **expansion still runs for every unit**, so the focused unit's path
identity is unchanged. The run says exactly this: `[seen]`

> "`--focus-function 'setFeeReceiver'` narrowed INSTRUMENTATION to 1 unit(s); 38
> other in-scope unit(s) were not enumerated at all. Their paths are absent from
> the denominator ON PURPOSE: the dispatcher cannot enter them in this run, so no
> exploration could ever witness them and counting them made the reported
> coverage a contract-level number wearing a unit-level label. **Internal-call
> EXPANSION still ran for every unit**."

⚠ **Consequence a reviewer must not miss:** the whole contract is still
converted. `--contract St1inch --focus-function setFeeReceiver` still spent
15.8 s building the GOTO program, expanded 136 internal calls and degraded 12
units — for a 4-line function. So `--focus-function` bounds the *denominator*,
not the *cost*. `[seen]`

**`--function` is BANNED in this project** (not the same option): it verifies a
function in isolation from an **arbitrary** contract state, so a counterexample
may rest on a state no `constructor → tx sequence` can reach, and the emitted
test is RED on the unmodified contract. This is why library units are refused
rather than measured. `[seen — pathcov_collect.py]`

---

## 7. `SC_DECISION_MAX = 12` — short-circuit operand cap

**Definition.** A `&&`/`||` chain with more than 12 operands has its decision
**site dropped entirely** rather than enumerated. `[contract]`

**Cost.** Drops paths, and unlike §3 it is not a merge — the site contributes
nothing. Reported only as a count (`short-circuit site(s) … cap`). `[seen — the
counter exists in pathcov_collect.py]` Measured 0 on every corpus run I read.

---

## 8. `--path-cov-claim-timeout N` (default 120 s) — per-claim solver budget

**Definition.** Wall-clock budget for **one claim's** satisfiability query. Path
coverage decides one *independent* claim per job, so a per-check-sat limit **is**
a per-claim limit.

**Mechanism — enforced by the SOLVER, not by killing the process:** `[seen]`

| backend | native option |
|---|---|
| bitwuzla | `BITWUZLA_OPT_TIME_LIMIT_PER` (ms, per check) |
| cvc5 | `tlimit-per` (ms, per check) |
| z3 | solver parameter `timeout` (ms, per check) |
| anything else | **`NOT ENFORCED: backend '<name>' has no per-query time limit`** |

The last row is why a *mechanism string* is published rather than a boolean: a
report carrying `claim_timeout_s: 120` for a run nothing actually bounded is the
exact shape of a guard that never fires while looking fine. `[seen —
dying-run-keeps-its-work.md]`

**cvc5 uses `tlimit-per`, not `tlimit`**, because `tlimit` is cumulative over the
solver's lifetime and under `--smt-during-symex` one solver serves every claim —
a cumulative limit would abandon every claim *after* the budget instead of each
claim that exceeds it. `[seen]`

**How an abandoned claim is recognised.** Every backend folds "unknown" into
`P_ERROR` (`smt_convt::resultt` has no `P_UNKNOWN`), so the result alone cannot
separate abandonment from genuine solver failure. The **wall clock** separates
them: SAT/UNSAT → keep the verdict however late; a non-answer that took at least
the budget (100 ms slack) → `claim-budget-exceeded`. `[seen]`

**Why `claim-budget-exceeded` is its own token** and not a shade of the other U
reasons: `solver-unknown` means the solver *answered* "I don't know" (that is
information); `bounded-holds` means it answered "no witness at this exploration";
`not-solved-this-run` means the simplifier folded the claim away; **`claim-budget-
exceeded` means we asked, it was still working, and we stopped it** — nothing at
all is known, and uniquely among them the fix is a bigger budget. `[seen]`

---

## 9. `--memlimit` (8 g) and the outer process timeout

**`--memlimit`** caps `RLIMIT_DATA`. On exhaustion the run raises `std::bad_alloc`
and a **rescue** writes a PARTIAL `cov-report.json` (`partial: true` +
`partial_reason`) instead of dying with nothing. A 128 MiB block is reserved
before the solve and released as the rescue's first act, so the report writer has
memory to work with — the reserve costs address space, not RSS. `[seen — the
cvc5 run produced exactly this]`

**Outer timeout** is imposed by the collector script, not ESBMC. ⛔ On SIGTERM the
signal arm **cannot write JSON** (malloc, iostream and the log mutex are all
unsafe in a handler), so it prints a signal-safe text block and the CE journal —
**no `cov-report.json` at all**. This is the one bound with no artefact-level
disclosure. `[seen — 3 corpus units]`

---

## 10. `--cov-report-json` — not optional, and it changes SLICING

**Definition.** Emits `cov-report.json`. **Also** the switch that turns on
decision-sequence recording.

**Mechanism that a reviewer will otherwise misread:** a path claim's guard
mentions only the ghost accumulators, so the per-claim slicer would remove every
state write and environment read and the counterexample payload would come back
**empty**. With the flag, symbols are **exempted from slicing** so the payload
survives: `[seen]`

> "exempting 511 symbol(s) from slicing so each path's counterexample values
> survive into the report (20 contract object(s), 476 contract-scope store(s), 15
> environment); slicing stays enabled for everything else."

Measured side effect worth flagging: on D36's fixture, `--no-slice` **kept more**
(472 assignments vs 355) and ran **3× faster**, so solving cost here is not
proportional to formula size. `[seen — D36]`

---

## 11. Backend selection — there is an auto-router, and it is measurably wrong on one shape

ESBMC auto-selects an SMT backend and prints its reason. Measured on D36's
60-line fixture: `[seen — D37]`

> `auto-selecting 'bitwuzla' as SMT backend (Z3 is much slower on 256-bit
> bit-vector arithmetic)`

| width | z3 | cvc5 | bitwuzla |
|---|---|---|---|
| `uint64` | **0.051 s** | 0.171 s | 10.271 s |
| `uint256` | `out of memory` | **3.667 s** | no report at 120 s |

So on this shape the router picks the **slowest** backend on the easy half
(z3 is 200× faster) and a **non-returning** one on the hard half, for a stated
reason this shape falsifies. The corpus pins `--z3 --tuple-node-flattener` for
st1inch only, recorded in `index.json` with its justification.

⚠ **Not a general claim**: on the REAL st1inch, cvc5 also fails
(`std::bad_alloc`, 0 of 10 claims decided, inside the first solve). The backend
matters on the isolated shape and does not rescue the real contract.
