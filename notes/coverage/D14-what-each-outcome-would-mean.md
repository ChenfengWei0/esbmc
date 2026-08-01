# D14: what each reduction outcome would mean — written BEFORE the result

**Recorded 2026-08-01 12:45, while the reduction is running.** A prediction
written afterwards is not a prediction, and this project has already lost a
result to reading an outcome as confirming whichever way it went. So the
readings are fixed here first.

## What is being reduced, and why it is licensed now

`notes/coverage/inputs/st1inch__St1inch.flat.sol`, 4874 lines, against the
predicate `solver-unknown ≥ 1` under `--focus-function setFeeReceiver
--z3 --tuple-node-flattener --solidity-max-tx 1`, i.e. the exact configuration
the corpus was collected under.

**A source-level reduction was NOT licensed until today.** st1inch was the only
benchmark run with `--z3 --tuple-node-flattener` and the only one with any
`solver-unknown`, so the backend and the contract were fully confounded — nothing
on disk separated "st1inch's source does this" from "that flag pair does this".
The three-arm experiment (`encoder_arms.py`, on `aqua --focus-function
safeBalances`, whose SAT answer was already recorded) broke the confound:

    arm A  default backend               F=2   positive control reproduces
    arm B  --z3 --tuple-node-flattener   F=2   SAME, solves even faster
    arm C  --z3 --tuple-sym-flattener    SIGABRT (a separate defect, not this)

Arm B did not reproduce the signature, so **the encoder is exonerated on that
vehicle** and the 47 unknowns belong to st1inch's source. That is what makes
reducing the source the right next action rather than a guess.

## What is already ruled out — do not re-propose without new evidence

| suspect | how it died |
|---|---|
| the encoder | `encoder_arms.py`, arm B, above |
| the `_EXP_TABLE_0..29` fixed-point chain (k = 29, the corpus's largest arithmetic depth) | `D17_ExpChain.sol`, a 30-step contract against a 3-step control, everything else identical — refuted in 1.2 s |
| the outer timeout / claim budget | every one of the 21 reports prints `0 claim(s) abandoned over budget`; the longest no-verdict solve is 13.554 s against a 120 s cap |
| accumulated solver state | in `withdraw`, nine 0.01 s DECIDED solves come **after** a 10.37 s no-verdict solve in the same process |
| formula size | 844 to 4315 assignments — a 5× range — all give the same ~10 s |
| "this claim is hard" | `deposit:path:2` was solved four times in one run: 0.016, 0.015, 0.015 s PASSED, then 10.722 s with no verdict |

## The one fact any explanation has to fit

Solve time is a **perfect separator with no overlap**, and it is two flat bands
rather than a gradient:

    decided (UNSAT)   106 solves   0.010 – 0.026 s
    SAT                 0 solves   —
    no verdict         89 solves   8.435 – 13.554 s

Corpus-wide there are 464 SAT solves across four other benchmarks and **zero** on
st1inch. Coverage requires SAT, so its 0 % is forced rather than observed.

**No log line names a limit, an rlimit, a memout, or a z3 unknown-reason.**
Whether the flat 8–14 s band is a z3-internal bound or a genuine search that
gives up is NOT RECORDED anywhere, and the reduced file is the artefact that
makes it cheap to find out.

## The readings, fixed in advance

**A. It reduces to something small (say under 200 lines) and the remaining code
is recognisably one construct.** Then that construct is the cause and the next
step is a hand-written single-factor pair around it, exactly as `D13` went from
4874 lines to a 17-line pair of same-named library structs. This is the outcome
the reducer is for.

**B. It stalls with most of the file still present.** Then the cause is
DISTRIBUTED — no single removable unit carries it — and the reducer has said so
rather than failed. That is a real answer and it changes the next step: stop
reducing, and instrument the solver side instead (which query, what z3 reports as
its unknown-reason, whether the 8–14 s is a bound). It is NOT a reason to raise a
limit and try again.

**C. It reduces to something small and the remaining code is NOT recognisable as
one construct.** Then continue by hand, one factor at a time — D13 needed exactly
this, going 4874 → 1182 automatically and then by hand, and it refuted eight
hand-written candidates before the real one. "Reduce until nothing remains" is
the project's own rule; the shapes everyone's first instinct reaches for were
scaffolding every time.

**D. The predicate stops holding partway.** Then the reduction is finished at the
last checkpoint that did hold, and the removal that killed it is itself the
answer — that element is part of the cause. The reducer keeps the element and
records this by construction.

## What the result does NOT settle, whichever way it goes

* **It is one unit of one benchmark.** `setFeeReceiver` is the unit the failure
  was observed on; the 47 unknowns span thirteen units (the four `deposit*` units
  are at 0 %). A cause found here is a cause for this unit until a second unit
  says otherwise — this project has had the same generalisation narrowed by a
  second and third sample twice already.
* **It does not tell us the ceiling.** Even if every one of the 47 became
  decidable, the other 81 claims are fast UNSATs — real decisions — so st1inch's
  gate score would rise by at most those 47 paths.
* **It says nothing about whether the encoder flags are needed.** They were
  exonerated on aqua, not on st1inch, where bitwuzla still never returns and
  plain `--z3` core-dumped before the struct-tag fix.

## Provenance of the run

Binary `36bd85abe1` (mtime 2026-08-01 12:13:59), unchanged for the whole run and
enforced: `reduce_to_poc.py::_assert_same_binary` hard-fails if esbmc is rebuilt
mid-reduction, after that mistake invalidated an earlier attempt at 4573 lines.
Pass order: functions → statements → types → state-vars, swept to a GLOBAL
fixpoint (a sweep that removes nothing), because each pass reaching its own
fixpoint once left an earlier pass's rejects unretried.
