# `--path-cov-certify` vs `--path-cov-assert`: who is right about vacuity

**Settled.** On aqua `Aqua.dock` enc=12 depth=3 over the region
`{app in [0, 2^160-2], msg.value in [0,0]}` the two gates disagreed:
certify said `RESULT: CERTIFIED` (non-vacuity witness REFUTED), assert said
`THE REGION IS VACUOUS` (same witness PASSED, all six mutually contradictory
rungs PASSED alongside it).

The **assert side is wrong**. Its vacuity verdict is an artefact of the one
option it forces and certify does not, `--no-simplify`. The must-flip fires in
one flag, and the mechanism is fully attributed to one library loop.

---

## 1. The forced-option difference, in full

All three stage-2/3 modes are read in the same `--solidity-path-coverage` block
of `src/esbmc/esbmc_parseoptions.cpp` (4118-4306). Options forced there are
**common to all three**:

| line | forced for every `--solidity-path-coverage` run |
|---|---|
| 4139 | `base-case = true` |
| 4140 | `multi-property = true` |
| 4141 | `keep-verified-claims = false` |
| 4142 | `no-pointer-check = true` |
| 4145 | `solidity-path-coverage-enabled = true` |
| 4296 | `unwind = 4` (when `--unwind` is unset) |
| 4305 | `no-unwinding-assertions = true` |

Per-mode:

| mode | site | what it sets |
|---|---|---|
| `--path-cov-certify` | 4200-4201 | `tmp.path_cov_certify_path` only — **no `options.set_option` at all** |
| `--path-cov-outer-box` | 4202-4203 | `tmp.path_cov_outer_box_path` only — no `options.set_option` |
| `--path-cov-assert` | 4206-4224 | `tmp.path_cov_assert_path` **and** `options.set_option("no-simplify", true)` at **4223** |

**There is exactly ONE forced-option difference between the two modes:
`no-simplify`, at `src/esbmc/esbmc_parseoptions.cpp:4223`. There is no second
one.** (The two modes do differ in what the *pass* instruments — certify puts
`assert(tr == enc && cnt == depth)` on **every** exit of the unit, assert puts a
candidate ladder under `tr != enc || cnt != depth` on **this path's own** exit —
but that is instrumentation, not a forced option, and it is not what flips the
verdict; see §3.)

`--no-simplify` cannot be undone from the command line: `esbmc --help` offers
`--no-simplify` and `--no-propagation` but no positive `--simplify`, and
`options.set_option` overrides the `options.cmdline(cmdline)` copy. So the
"re-run assert with simplification back on" direction is **not runnable without
a rebuild**; the equivalent single-flag isolation is run in the other direction
(certify **+** `--no-simplify`), which is what §3 does.

### What the comment at 4209-4222 actually measured

The block justifying the force says:

> MEASURED on the R1 must-flip pair. Without it:
>   0 HOLDS, 3 REFUTED, 3 no verdict (never reached the solver)
> With it:
>   3 HOLDS, 3 REFUTED, 0 no verdict

Those three numbers are **`--path-cov-assert` ladder-candidate verdicts**
(HOLDS / REFUTED / no-verdict) over 6 candidates on the R1 fixture — **not** F
counts, and not path claims. Note that **3 stayed REFUTED in both columns**, so
on that fixture the trace was *not* vacuous and the flag did exactly what the
comment claims. The comment is not falsified. What is falsified is the
assumption that discharge-during-simplification is the *only* effect the flag
has.

---

## 2. Exact argv of both queries

**ASSERT** (recorded verbatim, line 1 of
`notes/coverage/put_roundtrip/_wd/aqua_Aqua__dock__12/assert/run.log`; built by
`scripts/solidity_path_put.py:107-135` + `:924-928`):

```
setsid timeout -k 30s 600s $ESBMC/build/src/esbmc/esbmc \
  $ESBMC/notes/coverage/inputs/aqua__Aqua.flat.sol.solast \
  --sol $ESBMC/notes/coverage/inputs/aqua__Aqua.flat.sol \
  --contract Aqua --solidity-path-coverage --solidity-max-tx 1 \
  --focus-function dock --memlimit 8g --result-only \
  --path-cov-assert <wd>/assert/spec.json --cov-report-json
```

spec.json (verbatim on disk):
`{"unit":"dock","enc":12,"depth":3,"region":[{"name":"app","lo":"0","hi":"1461501637330902918203684832716283019655932542974"},{"name":"msg.value","lo":"0","hi":"0"}]}`

**CERTIFY** (built by `scripts/solidity_path_generalise.py:62-89` +
`:1880-1921`; reproduced here):

```
setsid timeout -k 30s 900s $ESBMC/build/src/esbmc/esbmc \
  $ESBMC/notes/coverage/inputs/aqua__Aqua.flat.sol.solast \
  --sol $ESBMC/notes/coverage/inputs/aqua__Aqua.flat.sol \
  --contract Aqua --solidity-path-coverage --solidity-max-tx 1 \
  --result-only --memlimit 8g --focus-function dock \
  --path-cov-certify <wd>/cert.json --cov-report-json
```

cert.json: same `unit/enc/depth`, same two bounds (the certify schema folds pins
into `box`), plus the path's own `ce` harvested from an enumeration run of the
same configuration (`app=0`, `msg.value=0`, `state._DOCKED=255`,
`block.number = block.timestamp = 2^255-1`, everything else 0).

Apart from the mode flag and the spec schema, **the two argvs are identical**.

---

## 3. The must-flip: ONE flag, verdict flips

| run | flags added to the certify argv | `#nonvacuous` | `RESULT:` |
|---|---|---|---|
| certify, as it stands | — | ✗ FAILED (refuted) | **CERTIFIED** |
| certify + one flag | `--no-simplify` | ✓ PASSED | **VACUOUS** |

Both runs: same `cert.json`, same instrumentation
(`instrumented 1 complete path(s)`, `asserted tr == 12 && cnt == 3 at ALL 1
exit(s)`), same 2 VCCs (`Generated 2 VCC(s), 2 remaining after simplification`).

`--no-simplify` alone turns a certificate into a vacuity verdict, and it turns
it into **exactly** the signature the `--path-cov-assert` run showed: the
non-vacuity witness holding. **The flag is the cause; the assert side is
wrong.**

---

## 4. Mechanism — and it is NOT "the claim was simplified away"

The natural reading of `goto_symext::claim`
(`src/goto-symex/symex_main.cpp:63-145`) is that `do_simplify` yielding
`is_true` discharges a claim before `assertion()` (`:129`), so it never enters
`remaining_claims` (`:154`) and surfaces as `U / not_solved_this_run`. That
mechanism is real, and it **did not fire in either configuration here**.

Measured on the 2x2 matrix already on disk
(`notes/coverage/option_matrix/aqua_dock/`, `--contract Aqua --focus-function
dock --solidity-max-tx 1`):

| | simplify default | `--no-simplify` |
|---|---|---|
| `Generated N VCC(s), M remaining after simplification` | **63 / 63** | **63 / 63** |
| `Solving claim ...` lines | 63 | 63 |
| `... of 2846 instrumented path claim(s) reached the solver` | 63 | 63 |
| `U Reasons: ... not-solved-this-run` | **0** | **0** |
| `U Reasons: ... bounded-holds` | 61 | 63 |
| `Path Status` | **F 2**, U 2844 | **F 0**, U 2846 |
| symex assignments | 1121 | 1923 |
| post-slice assignments in the formula | 768 | 1486 |
| decision-procedure total | 6.011 s | 7.684 s |
| loops unwound at all | loop 62 only | loops **64**, 62, **1** |
| verdict / exit | FAILED / 1 | SUCCESSFUL / 0 |

The claim population is **identical**. Nothing was simplified away. The two
witnesses did not become "never asked" — they became `bounded-holds`, i.e.
**asked, and the solver said the assertion holds**. And the run did *more* work,
not less: the extra ~800 symex assignments and the extra 1.7 s of solving are
the newly-unwound library loops.

### The real chain

1. `--no-simplify` makes `do_simplify` a no-op
   (`src/goto-symex/goto_symex.h:152-159`: *"Essentially is just a call to
   simplify, but is guarded by the --no-simplify option being turned off."*).
2. `symex_goto` calls `do_simplify(new_guard)` on the **loop guard**
   (`src/goto-symex/symex_goto.cpp:20`) and then tests
   `is_false(new_guard) / is_true(new_guard)` (`:22-23`). Unfolded, a guard that
   is semantically constant is neither, so symex **enters and unwinds loops it
   otherwise never enters**. Measured: `__memset_impl` (loop 64) and
   `__ESBMC_atexit_handler` (loop 1) appear in every `--no-simplify` log and in
   no simplify-default log.
3. Those loops hit the coverage-forced bound of 4
   (`esbmc_parseoptions.cpp:4296`).
4. Because `--solidity-path-coverage` also forces `no-unwinding-assertions`
   (`esbmc_parseoptions.cpp:4305`), `loop_bound_exceeded` takes the **`else`**
   branch and emits an **assumption**, not an unwinding assertion:

   ```cpp
   // src/goto-symex/symex_goto.cpp:482-493
   if (!no_unwinding_assertions)
     claim(negated_cond, "unwinding assertion loop " + i2string(loop_number));
   else
   {
     expr2tc guarded_expr = negated_cond;
     cur_state->guard.guard_expr(guarded_expr);
     target->assumption(
       cur_state->guard.as_expr(), guarded_expr, cur_state->source, first_loop);
   ```
   followed by `cur_state->guard.add(negated_cond);` (`:510`).
5. That assumption **excludes every execution needing a 5th iteration**. The
   executions that witness dock's paths 2 and 12 are among them, so their path
   claims hold *for want of an execution* — which is what the ladder's six
   mutually contradictory rungs passing at once already proved independently
   (`post == pre` and `post != pre` can both hold only at an unreachable point).

### Two independent confirmations of step 4-5

* **Suppress the assumption.** `--no-simplify --partial-loops` — `loop_bound_exceeded`
  returns at `symex_goto.cpp:474` before emitting anything. Result: **F 2**,
  `dock:path:12` and `dock:path:2` FAILED, exit 1. Everything else identical
  (same 63 VCCs, same 1920 symex assignments, same three loops truncated).
* **Raise only the offending bound.** `--no-simplify --unwindset 64:512` —
  loops 62 and 1 still truncate at 4. Result: **F 2**, exit 1. Whereas
  `--unwindset 1:64` alone, `--unwindset 64:64` alone and `--unwindset 62:16`
  alone all stay at F 0.

So the single loop responsible is **loop 64 = `__memset_impl`,
`src/c2goto/library/string.c:298` (`for (size_t i = 0; i < n; i++)`)**, whose
trip count on this input is **> 64 and <= 511**. Simplification resolves its
guard statically and the loop is never entered; without simplification it is
entered, truncated at 4, and the truncation assumption silently deletes the two
witnesses.

### The specific false claim

> "On that model, turning simplification OFF should give MORE solved claims, not
> fewer — and certainly should not remove a witness that exists."

* *"more solved claims"* — false as a prediction and as a description: the
  solved-claim count is **identical** (63 = 63) and `not-solved-this-run` is
  **0 in both** runs. The `goto_symext::claim` mechanism produced zero claims in
  either configuration, so it cannot account for the delta in either direction.
* *"should not remove a witness that exists"* — false. The line that makes it
  false is **`src/goto-symex/symex_goto.cpp:492-493`**, the unwinding
  *assumption*, reachable only because `--solidity-path-coverage` forces
  `no-unwinding-assertions` at `src/esbmc/esbmc_parseoptions.cpp:4305`. The
  model looked only at `goto_symext::claim`; `--no-simplify` also disables
  simplification of **loop guards** at `symex_goto.cpp:20`, and in a mode where
  the unwinding assertion has been replaced by an assumption, that silently
  turns a reachable exit into an unreachable one.

Note the failure is **silent by design in this configuration**: with unwinding
assertions on it would have been a visible `unwinding assertion loop 64`
violation. `--no-unwinding-assertions` converts it into an assumption, and the
run reports `VERIFICATION SUCCESSFUL`, exit 0, `Path Coverage: 0%`, with only
the generic `Coverage may be UNDER-REPORTED` warning to show for it.

---

## 5. Verdict and consequences

* **`--path-cov-assert`'s vacuity verdict on aqua `dock` enc=12 is WRONG.** The
  region is not vacuous; certify's `CERTIFIED` is correct (bounded, as always,
  by the tx/unwind bound and post-constructor entry state).
* `scripts/solidity_path_put.py:966-979` refuses the PUT on `vacuous`, citing
  this disagreement and saying "Until it is settled, the conservative direction
  is forced: refuse." **It is now settled**, and the refusal is firing on a
  false signal. `notes/coverage/put_roundtrip/_wd/aqua_Aqua__dock__12/put.json`
  (`"refused": "ladder-vacuous"`) is a lost PUT, not a property of the region.
* The forced `no-simplify` at `esbmc_parseoptions.cpp:4223` is **not safe as an
  unconditional force** in a mode that also forces `--no-unwinding-assertions`.
  It buys a HOLDS verdict on a fixture with no >4-trip library loop and costs
  the entire trace on one that has one. Any fix has to keep the two forces from
  interacting — e.g. raise the unwind bound for library loops when
  `no-simplify` is forced, restore unwinding assertions so the truncation is
  loud, or make `--path-cov-assert` refuse when `truncated_loops` is non-empty
  rather than print `THE REGION IS VACUOUS`.
* **The same defect reaches beyond this mode.** `--no-simplify` is a
  user-facing flag; any `--solidity-path-coverage` run carrying it silently
  reports 0% path coverage and `VERIFICATION SUCCESSFUL` on this contract.

## Reproduction

All runs: `setsid timeout -k 30s 900s`, `--memlimit 8g`, one at a time, against
`build/src/esbmc/esbmc` (ESBMC 8.2.0), input
`notes/coverage/inputs/aqua__Aqua.flat.sol{,.solast}`, `--contract Aqua
--focus-function dock --solidity-max-tx 1 --result-only --cov-report-json`.
Deltas from that base:

| # | added flags | outcome |
|---|---|---|
| 1 | `--path-cov-certify cert.json` | `RESULT: CERTIFIED`, `#nonvacuous` REFUTED, exit 1 |
| 2 | `--path-cov-certify cert.json --no-simplify` | `RESULT: VACUOUS`, `#nonvacuous` PASSED, exit 1 |
| 3 | (enumeration only) | F 2 (`dock:path:12`, `dock:path:2`), exit 1 |
| 4 | `--no-simplify` | F 0, exit 0 |
| 5 | `--no-simplify --partial-loops` | F 2, exit 1 |
| 6 | `--no-simplify --unwindset 1:64` | F 0, exit 0 |
| 7 | `--no-simplify --unwindset 62:16` | F 0, exit 0 |
| 8 | `--no-simplify --unwindset 64:64` | F 0, exit 0 |
| 9 | `--no-simplify --unwindset 64:512` | F 2, exit 1 |
| 10 | `--no-simplify --unwindset 1:64,62:16,64:512` | F 2, exit 1 |

Rows 3-4 and the `--no-slice` cells are also on disk as
`notes/coverage/option_matrix/aqua_dock/work/focus__tx1__*`; `--no-slice`
changes nothing in any cell.
