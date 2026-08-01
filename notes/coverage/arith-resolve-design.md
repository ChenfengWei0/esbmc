# Entry condition 3: the model wraps, so the counterexample is a value the chain rejects

**Status: PREMISE TESTED, AND IT FAILED. The capability is NOT already in the
tool; (c) requires the C++ change designed below.** See "The premise experiment"
at the end for the 12 cells and what each one rules out.

Decision context: **C1 was overturned by its own measured cost** (lowering
checked arithmetic to a two-exit branch is `2^k`, and `arith_exponent.py` measured
k = 29 on st1inch's constructor, against a 10000-path per-unit cap). The
replacement is **(c) — re-solve the one affected claim under a no-wrap
constraint** — with four conditions, recorded in
`notes/coverage/poc/D10_WrapNotPanic.sol`'s header and in `EXECUTION_PLAN.md` §10.

---

## What the failure actually is

`D10_WrapNotPanic.add`:

```solidity
function add(uint256 amt) external { require(amt > 0); bal += amt; }
```

Measured: the path claim is refuted with `amt = 2^256-1`, `bal: 500 -> 499`; the
path is classified `exit_kind: normal`; the emitted Foundry case carries
`// [asserted] path exits normally; a revert fails the test`; `forge test` says
`[FAIL: panic: arithmetic underflow or overflow (0x11)]`.

**The `require` is load-bearing.** Without it the solver picks `amt = 0` and the
test is green. So the defect is not "the solver likes extreme values": nothing in
the formula distinguishes a wrapping member of the path's domain from a
non-wrapping one, so once the cheap value is excluded the choice is unconstrained.

**It is not one failure but two flags.** Of the three measured REDs across the
PoC set, two are Panic 0x11 (overflow: `D10.add`, `Tiny2.deposit`) and one is
**Panic 0x12, division by zero** (`P18_Unchecked.div`). `--overflow-check` and
`--div-by-zero-check` are separate flags with separate `goto_check` producers, so
a fix validated on D10 alone leaves a third of the measured failures alive while
looking finished. See [[count-instances-by-entry-condition]].

---

## The mechanical facts, read from the source

### 1. `goto_check` labels its claims with a MACHINE FIELD, not prose

`goto_checkt::add_guarded_claim` (`src/goto-programs/goto_check.cpp:1001-1029`)
builds

```cpp
t->guard = guard.is_true() ? e : implies2tc(guard.as_expr(), e);
t->location.comment(comment);
t->location.property(property);
```

so every emitted check carries `location.property()`:

| producer | `property()` | `comment()` |
|---|---|---|
| `div_by_zero_check` (`:148`) | `division-by-zero` | `division by zero` |
| `overflow_check` (`:323`) | `overflow` | `arithmetic overflow on <expr-id>` |
| `cast_overflow_check` (`:255`) | `overflow` | `Narrowing cast overflow on ...` |
| `shift_check` (`:604`) | `undef-behavior` | `undefined behavior on shift ...` |
| `bounds_check` (`:932`) | `array bounds` | `array bounds violated: ...` |

⇒ the detector keys on `property()`, which is a machine field the producer sets,
**never on the comment string**. See [[detector-must-quote-its-own-branch]]: two
producers rejecting the same thing in different words is how a detector ends up
matching a sentence nothing emits.

⚠ `overflow` covers TWO producers. `cast_overflow_check` is, for Solidity, **on
by default** — it is gated only on `disable_narrowing_check` + the file being
`.sol` + not `#sol_unchecked`, NOT on `--overflow-check`. So a Solidity path-
coverage run already carries narrowing-cast claims today, and any count of "how
many arithmetic checks does this claim have" must expect them.

The claim's condition is the **SAFE** condition (`divisor != 0`,
`!overflow(a+b)`), already wrapped as `implies(path guard, safe)`.

### 2. Path coverage does NOT neutralise them, and that is documented

`INVOCATION_DECISIONS.md` row 6: "path coverage is the only coverage mode that
does NOT neutralise pre-existing asserts, so each goto_check claim becomes its
own solver job and its own counterexample block while counting in no numerator."

⇒ with `--overflow-check` on, the overflow condition IS in the equation as an
assert step. It is simply never allowed to constrain the path claim's model.

### 3. Why it constrains nothing — the exact two lines

`claim_slicer::run` (`src/goto-symex/slice.cpp:234-283`) keeps ONE assert and
sets `it->ignore = true` on every other. It does not delete them.

`symex_slicet::run` (`src/goto-symex/slice.h:102-118`):

```cpp
for (auto &step : boost::adaptors::reverse(eq)) {
  if (step.ignore) continue;      // <-- HERE
  run_on_step(step);
}
```

An ignored step is skipped, so `run_on_assert` never adds its symbols to
`depends`. Two consequences, and the second is the one that would silently break
a naive fix:

* the overflow condition is not encoded, so it constrains no model;
* **the assignments defining its operands are free to be sliced away**, so
  asserting the condition on the live solver afterwards (the way `--all-witnesses`
  asserts its blocking clause) would reference symbols the formula no longer
  constrains — the solver would satisfy it by choosing them, and the answer would
  be about nothing.

⇒ any fix must convert those steps **before** `symex_slicet` runs, not after.
`run_on_assume` with `slice_assumes == false` (the default) adds the condition's
symbols to `depends`, so converting ASSERT → ASSUME at that point keeps the
operands' definitions alive by the existing machinery.

### 4. The order inside `job_function`

`src/esbmc/bmc.cpp`, per claim:

```
symex_target_equationt local_eq = eq;        // :3175   copy
claim_slicer claim(i, ...); claim.run(...);  // :3189   all other asserts -> ignore
symex_slicet slicer(options); slicer.run(...)// :3226   skips ignored steps
run_decision_procedure(...)                  // :3277
```

So the insertion point is between `claim.run` and `slicer.run`.

### 5. There is already a re-solve idiom in this file

`--all-witnesses` (`bmc.cpp:4091-4100`) does `push_ctx()` → `assert_expr(block)` →
`dec_solve()` → `pop_ctx()` on the SAME encoded instance. That is the cheap shape,
and it is unavailable here for the reason in §3: the constraint's operands may not
be in the formula. A re-solve here costs a re-encode.

### 6. The path constraint is carried for free

Condition 1 of the four ("the re-solve must carry the path constraint, or the
solver returns a witness of a DIFFERENT path") is satisfied **structurally** in
this architecture and needs no extra term: the query IS the path claim
`assert(!(tr == enc && cnt == depth))`, so every SAT model is on this path.
Adding a conjunct to the same claim cannot move it to another path. This was a
real hole in the original (c) proposal, where the re-solve was written as a fresh
`assume(no overflow); assert(false)` query — that form does need the conjunct.

⇒ and the UNSAT of the constrained query is then exactly the proof condition 1
names: **this path is reachable only by overflowing.**

---

## The design, and its two possible orders

Both orders are (c); they differ only in which case pays the extra query.

**Order A — solve as today, then re-solve if arithmetic is present.**
`SAT` → re-solve constrained → `SAT` use the non-wrapping witness / `UNSAT` file
the path as arith-revert-only. `UNSAT` first time → nothing extra.
Cost: **+1 query per WITNESSED path that carries a checked operation.** F is
single digits per unit, so this is the cheap order.

**Order B — solve constrained first, fall back if UNSAT.**
Cost: +1 query per **unwitnessed** path with arithmetic. On st1inch that is the
69 `bounded-holds`, i.e. the expensive side.

⇒ **Order A**, unless the premise experiment says the constrained solve is the
only one that can be built.

Note what Order A does NOT need: a way to ask "did this model wrap". Preferring a
non-wrapping witness whenever one exists is strictly better than detecting the
wrap first, and it removes the only step that would have had to re-derive
arithmetic outside the model — which is condition 2's whole point (a second
implementation of an arithmetic the model already performs is free to disagree
with it; that is the geometric ladder's own wrap defect).

### The four conditions, mapped

| # | condition | how it is met |
|---|---|---|
| 1 | the re-solve carries the path constraint | structurally — same claim, same equation (§6) |
| 2 | "wrapped" is the verifier's own check | the constraint IS `goto_check`'s condition; no decimal is ever range-tested |
| 3 | "necessarily overflows" is its own cell, not folded into U | a witnessed path stays **F**; it additionally gets its own set + its own report line + its own JSON field, and the emitter must refuse to emit it as a normal-exit call |
| 4 | the cost is printed | count of re-solves and their total wall time, on stdout and in `summary` |

### What condition 3 must NOT become

A path proven to need an overflow is **still reachable on chain** — as a
REVERTING transaction. It is not a coverage loss and must not be reported as one.
The correct eventual rendering is `vm.expectRevert(stdError.arithmeticError)`;
until that exists, the path must be REFUSED at emission (the machinery from
`a6ea07f2e9` already does exactly this for `named_obstacle_paths`, with the
refusal counted on stdout) rather than emitted as a bare call that asserts a
normal exit. Refusing without counting, or counting without refusing, is the
half-fix that reads as a whole one.

---

## The premise experiment (`scratchpad/arith_premise.py`)

Four cells per contract, on `D10_WrapNotPanic`, `Tiny2` and `P18_Unchecked`:

| cell | flags | what it decides |
|---|---|---|
| 1 | base | the wrap reproduces on THIS build |
| 2 | `--overflow-check` / `--div-by-zero-check` | control: adding the claim changes no model (D10's header recorded BYTE-IDENTICAL on an earlier build) |
| 3 | check + `--cov-assume-asserts` | **decisive.** `replace_all_asserts_to_assume` already exists and is documented as "convert assertions to assumptions in coverage mode to preserve path constraints". If the witness stops wrapping, the capability is already in the tool. |
| 4 | `--cov-assume-asserts` alone | separates "the assume did it" from "the flag has some other side effect". With no check flag there is no arithmetic assert to convert, so cell 4 MUST equal cell 1; if it does not, cells 2 and 3 cannot be read as being about arithmetic at all. |

Compared field by field on `claims[*].{path_id,inputs,entry_storage,final_state,
exit_kind}` — **not** by exit code (every cell is expected to print VERIFICATION
FAILED, because a refuted path claim is the wanted outcome) and not by a hash of
the report (wall-clock fields differ in every cell and would report them all as
changed).

**If cell 3 does not change the witness**, the reason is the result: either
`--cov-assume-asserts` does not reach `goto_check`'s asserts, or it reaches them
after slicing has already removed their operands. Both are answerable from
`goto_coverage.cpp`, and both leave the design above intact — only the
implementation site moves.

### RESULT, measured 2026-08-01: 12 of 12 cells IDENTICAL

| contract | 2_check | 3_check_assume | 4_assume_only |
|---|---|---|---|
| D10_WrapNotPanic (`--overflow-check`) | IDENTICAL | **IDENTICAL** | IDENTICAL |
| Tiny2 (`--overflow-check`) | IDENTICAL | **IDENTICAL** | IDENTICAL |
| P18_Unchecked (`--div-by-zero-check`) | IDENTICAL | **IDENTICAL** | IDENTICAL |

**THE PREMISE FAILS.** `--cov-assume-asserts` does not reach `goto_check`'s
asserts under path coverage, so the capability (c) needs is NOT already in the
tool and the design above has to be implemented rather than scheduled.

The cells that make that readable, and they are why cell 4 was in the matrix:

* **cell 4 == cell 1 in all three contracts**, so `--cov-assume-asserts` has no
  other side effect on this mode. Without that, "cell 3 == cell 1" would have
  been consistent with the flag doing something unrelated that happened to cancel
  out, and nothing could have been concluded from it.
* **cell 2 == cell 1** reproduces, on the CURRENT build, what D10's header
  recorded on an earlier one: adding the claim constrains no model.
* **the wrap itself still reproduces** — `D10.add` path 7 is witnessed at
  `amt = 2^256-1` with `bal: 500 -> 499`, and `Tiny2.deposit` path 7 the same.
  A premise experiment on a failure that had quietly gone away would prove
  nothing, so this is checked rather than assumed.

### A SECOND DEFECT FELL OUT OF IT, and it is worse than §7.1 predicted

`P18_Unchecked.div` path 6 is reported with

```
final_state {"r": "0xFFFF...FFFF / 0"}
```

— **an unevaluated division expression as a STRING, not a number.** EXECUTION_PLAN
§7.1 predicted that `a/0` would enter the report as `type(uint256).max` (the
SMT-LIB total-function value of `bvudiv`) and warned that the value would flow
into R2 assertions. What actually happens is one step earlier: the simplifier
explicitly refuses to fold a zero divisor, so the expression never becomes a
constant at all and `from_expr` renders the whole thing.

Two things follow, and the second is the one that matters:

* **`--div-by-zero-check` does not change it** (cell 2 is IDENTICAL). So this is
  not "turn the check on and it goes away". INVOCATION_DECISIONS row 6's stated
  exception — that `--div-by-zero-check` is wanted at CERTIFICATION time so an
  independent claim excludes zero divisors from the region — is about a different
  stage and is NOT refuted here; what is measured is that at ENUMERATION time the
  flag changes no witness.
* **`final_state` is contracted to hold values, and here it holds an
  expression.** Every consumer that reads it either parses it as an integer
  (`solidity_path_generalise.py`'s `coord_values` calls `parse_int` and REFUSES
  the coordinate on failure — safe, but it loses the coordinate) or renders it
  into a test. The R1/R2 assertion ladder is built from exactly this field.

---

## What this changes in `INVOCATION_DECISIONS.md`

Row 6 currently reads **"arithmetic checks — passing them cannot help"**, and its
argument is sound for what it was about: the checks are single-successor ASSERTs,
so they cannot add a decision and cannot change the enumerated path set. That
stays true. What becomes false is the conclusion "they cannot help": they can
constrain WHICH MEMBER of a path's domain the solver returns, which is a different
question from which paths exist. The row must be amended, not deleted, and the
amendment must name the mechanism rather than just flip the verdict.
