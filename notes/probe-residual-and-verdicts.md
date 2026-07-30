# residual units, the I verdict, entry liveness, censuses

Read in full, sequentially, for this probe:
`src/goto-programs/goto_coverage.cpp` (6602), `src/goto-programs/goto_coverage.h` (905),
`src/esbmc/bmc.cpp` (3575), `src/esbmc/esbmc_parseoptions.cpp` (5062),
`src/goto-symex/foundry.cpp` (3105), `src/goto-symex/foundry.h` (311).
The last two were added because they are the only place a "must not become a test" rule
could actually bite, and question 1 cannot be answered without them.

---

## 1. residual_unit_fns — blocks or only names?

**Verdict: it MARKS, and the marking is DEAD on the only channel that matters.
It does not refuse to enumerate, does not refuse to instrument, does not abort — and
the emitter never reads the mark. This is an open red-test channel, and it is worse
than "only names it": the same path is separately recorded as `normal_exit_paths`,
which makes the emitter drop its try/catch and emit the call BARE with an
`[asserted]` comment.**

### Where it lives

It is a **local variable** in `solidity_path_coverage()`, not a class member:

`src/goto-programs/goto_coverage.cpp:2885-2892`
```cpp
  size_t inlined_calls = 0, residual_calls = 0;
  std::set<std::string> residual_fns;
  std::set<std::string> residual_unit_fns;
  // Per unit: which UNIT callees it still calls unexpanded. This is not a
  // reporting convenience — it is what the containment below is keyed on, so
  // the marking reaches the right unit's paths instead of being a global count
  // nobody acts on.
  std::map<std::string, std::set<std::string>> residual_unit_callees_of;
```

Filled after the expansion passes, by scanning what is still callable:

`goto_coverage.cpp:3165-3195`
```cpp
    forall_goto_program_instructions (it, b)
    {
      const goto_functiont *callee = expandable_callee(it);
      if (callee == nullptr) continue;
      if (withdrawn.count(call_point_key(it)) != 0) continue;
      ++residual_calls;
      const std::string cid = callee_id_of(it);
      residual_fns.insert(cid);
      ...
      if (is_external_entry(irep_idt(cid)))
      {
        residual_unit_fns.insert(cid);
        residual_unit_callees_of[uname].insert(cid);
      }
    }
```

`is_external_entry` (`goto_coverage.cpp:2819-2821`) is the UNIT test: the existence of
`<function-id>#_sol_save_this`, which the frontend creates for public/external/receive/fallback
and nothing else.

### What it does — all of it

1. **A warning** — `goto_coverage.cpp:3230-3249`, `log_warning(...)`, naming the callees and
   telling the user to raise `--unwind`. No return value, no exit code.

2. **A per-unit flag** — `goto_coverage.cpp:3462-3472`:
```cpp
    const auto residual_units_here =
      residual_unit_callees_of.find(f_it->first.as_string());
    const bool unit_calls_gated_unit =
      residual_units_here != residual_unit_callees_of.end();
```
   Note what does **not** happen next: there is no `continue`, no skip. Line 3451
   (`++units_enumerated;`) has already run, and the ABI gate, Phase-1 accounting and the
   whole DFS run unconditionally afterwards.

3. **A map entry per path** — `goto_coverage.cpp:5641-5651`:
```cpp
      if (unit_calls_gated_unit)
      {
        ++obstacle_paths_residual;
        named_obstacle_paths[key] =
          "unit still calls another UNIT's own body unexpanded (" +
          residual_unit_names +
          "); that body carries the ABI value gate, which models an EXTERNAL "
          "entry, so the model admits the callee reverting for carrying value "
          "inside an INTERNAL call that on-chain proceeds — an execution that "
          "does not exist on chain";
      }
```
   This sits in the loop that **still inserts the assert** for the path
   (`goto_coverage.cpp:5677 insert_assert(goto_program, pc, g, comment);`) and still puts the
   key in `all_claims` (line 5603).

4. **Degradation refuses to create one** — `goto_coverage.cpp:2930-2932`:
```cpp
  auto withdrawable = [&](goto_programt::const_targett i) {
    return !is_external_entry(irep_idt(callee_id_of(i)));
  };
```
   This is real blocking, but it only stops the *budget* mechanism from opening the hole. It
   does nothing about the depth-bound route, which is exactly the route `residual_unit_fns`
   records.

### Every consumer of `named_obstacle_paths` in the tree

* `goto_coverage.cpp:203-204` — `path_u_reason_token()` returns `"named-obstacle"`.
  **Only reachable for a path that is already U.** A refuted (F) path never asks.
* `bmc.cpp:1454-1459` — writes `claim_entry["u_reason_detail"]`, and the enclosing block is
  `if (tri == "U")` (`bmc.cpp:1447`). Again F paths never reach it.
* Nowhere else. `src/goto-symex/foundry.cpp` and `foundry.h` do not mention
  `named_obstacle_paths` at all — read in full, none found.

### The consequence, stated plainly

A path of a residual-unit-calling unit is enumerated, instrumented, solved. If the solver
refutes it — the normal, expected outcome for a feasible path — then:

* it is `covered` / `tri == "F"` (`bmc.cpp:1429-1441`), so no `u_reason` is ever emitted for it;
* `foundry_generator::collect()` (`foundry.cpp:2277`) reconstructs a test from its
  counterexample, with no obstacle check anywhere in `reconstruct()`;
* and because the exit classifier fills `normal_exit_paths` from *absence of revert evidence
  only* (`goto_coverage.cpp:5624-5627`) with no obstacle term in the condition, the emitter's
  one positive read of the census (`foundry.cpp:1599-1605`) sets `normal_confirmed = true`,
  and `write_foundry_file` then emits (`foundry.cpp:2790-2800`):

```cpp
        else if (call.normal_confirmed)
        {
          f << deal_line;
          f << "    // [asserted] path exits normally; a revert fails the test\n";
          f << "    " << recv << "." << call.method << value_brace << "("
            << join_args(call) << ");\n";
        }
```

So the header's stated contract at `goto_coverage.h:388-394` —

> "a marked path must be excluded from the sibling set used for the stage-3 subtraction AND
> must not be turned into a test. Marking without excluding would be worthless."

— is **not implemented for the test-emission half**. The mark is a report field only.

---

## 2. The I verdict

**Confirmed: `I` cannot be emitted. The branch is present, uncommented, un-flagged, and
gated on a function whose entire body is `return false;`.**

### Where the verdict is produced

`bmc.cpp:2770-2787` (inside `multi_property_check`'s `job_function`) — the per-claim
solver outcome, keyed by `claim_sig`:

```cpp
    if (is_path_cov)
    {
      char verdict;
      if (solver_result == smt_convt::P_SATISFIABLE)
        verdict = is ? 'U' : 'F';
      else if (solver_result == smt_convt::P_UNSATISFIABLE)
        verdict = 'P';
      else
        verdict = 'U';
      ...
      // 'F' is final: a witness stays valid ...
    }
```

`'P'` = UNSAT at this exploration. It is the **only** candidate for `I`.

### Where F/I/U is mapped — two places, identical logic

stdout, `bmc.cpp:1228` and `bmc.cpp:1248-1263`:
```cpp
        const bool unb = path_cov_can_prove_unreachable();
        ...
          if (path_witnessed_earlier(k) || reached_claims.count(sig))
            ++nF;
          else if (v == 'P' && unb)
            ++nI;
          else
          { ++nU; ... }
```

JSON, `bmc.cpp:1359` and `bmc.cpp:1439-1445`:
```cpp
    const bool unbounded_run = path_cov_can_prove_unreachable();
    ...
        std::string tri;
        if (witnessed)          tri = "F";
        else if (v == 'P' && unbounded_run) tri = "I";
        else                    tri = "U";
```

### Why it currently cannot fire

`bmc.cpp:722-725`:
```cpp
static bool path_cov_can_prove_unreachable()
{
  return false;
}
```

Not `#if 0`, not option-gated, not conditioned on `--solidity-max-tx`. A constant `false`.
So `v == 'P' && unb` is `... && false` — the `++nI` and `tri = "I"` arms are unreachable by
construction. The JSON summary duly reports `I_proven_unreachable: 0` always
(`bmc.cpp:1777`), and every `'P'` is instead reported U with
`claim_entry["bounded_holds"] = true` (`bmc.cpp:1479-1480`) and U-token `bounded-holds`
(`goto_coverage.cpp:229-232`).

### Exactly what would have to be true for `I` to fire

Two conjuncts, and only the second is a code change:

1. the claim's recorded outcome must be `'P'` — i.e. the solver returned
   `P_UNSATISFIABLE` for `assert(tr != enc || cnt != depth)`; **and**
2. `path_cov_can_prove_unreachable()` must return `true`.

Its comment (`bmc.cpp:686-721`) states the intended precondition for (2): the exploration
must over-approximate every reachable state — which today it does not, because
(a) `--solidity-max-tx N` emits N straight-line transactions, (b) `--solidity-max-tx 0`
looks unbounded but coverage rewrites every `_ESBMC_Main*` back-edge to a SKIP
(`esbmc_parseoptions.cpp:3611-3636`), leaving **one** guarded transaction, and (c) the entry
state is whatever the constructor left — state variables are never havoc'd.

The header adds a third, non-code precondition (`goto_coverage.h:279-284`): entry liveness
must be checked first, or a never-entered unit's vacuously-holding claims would all be
reported PROVEN INFEASIBLE. That precondition is now partly met (see §3), but nothing in the
code links the two — flipping `path_cov_can_prove_unreachable()` to `true` is sufficient on
its own to start emitting `I`.

---

## 3. Entry liveness

### INTERNAL DEFECT guard

**File:line:** `src/goto-programs/goto_coverage.cpp:1199-1217`, inside
`goto_coveraget::audit_entry_liveness(const std::string &focus_function)`
(defined `goto_coverage.cpp:1100-1218`).

**It is BOTH per-run and per-unit — one function, two messages, one shared `abort()`:**

```cpp
  if (total_decided == 0)
    log_error(
      "--solidity-path-coverage: INTERNAL DEFECT — NOT ONE of the {} "
      "instrumented path claim(s) reached the solver. ...",
      total_instrumented);
  else
    log_error(
      "--solidity-path-coverage: INTERNAL DEFECT — {} unit(s) had claims "
      "instrumented but NONE of them reached the solver, i.e. the harness "
      "never entered them: {}. ...",
      dead.size(), names);
  abort();
```

**Trigger.** Claims are grouped by unit (the part of the claim comment before `":path:"`,
`goto_coverage.cpp:1130-1151`). A claim counts as *instrumented* unless it was skipped by the
cross-run covered set; it counts as *decided* iff `claim_outcome` holds an entry for it. A
unit with `instrumented > 0 && decided == 0` goes into `dead` — unless `--focus-function`
narrowed the dispatcher away from it, in which case it goes into `dead_by_design` and is
merely logged (`goto_coverage.cpp:1164-1190`). `abort()` fires whenever `dead` is non-empty.
The whole-run message is just the `total_decided == 0` special case of the same event.

**Note the witness is indirect**: "a claim produced a solver verdict" ⇒ the unit executed.
There is no direct probe.

### Per-unit liveness check

**Exists, in the indirect form above only** — `audit_entry_liveness` is per-unit
(`per_unit` tally, `dead` vector, per-unit `units_not_entered` entries at
`goto_coverage.cpp:1174-1179`).

**The intended per-unit WITNESS does not exist.** There is no `assert(false)` inserted at the
head of a unit body anywhere in `solidity_path_coverage()`. I read all 6602 lines; the only
`gen_false_expr()` "function entry" probe in the file is in a different mode entirely —
`branch_function_coverage()`, `goto_coverage.cpp:1385-1390`:

```cpp
          insert_assert(
            goto_program, it, gen_false_expr(),
            "function entry: " + id2string(f_it->first));
```

That is branch-function coverage, not path coverage; `solidity_path_coverage()` never calls it.
The planned witness is referred to as future work in two comments
(`goto_coverage.h:309-314`, `goto_coverage.cpp:1169-1173`) and is not implemented.

### `audit_entry_liveness` — HAS A CALL SITE

**Yes. `src/esbmc/bmc.cpp:1134`**, inside `report_coverage()`, first statement of the
`else if (is_path_cov)` branch, before any coverage figure is printed:

```cpp
  else if (is_path_cov)
  {
    // BEFORE any number is printed: does this run establish anything at all?
    ...
    goto_coveraget::audit_entry_liveness(options.get_option("focus-function"));
```

This is the only call site found after reading `goto_coverage.cpp`, `goto_coverage.h`,
`bmc.cpp`, `esbmc_parseoptions.cpp`, `foundry.cpp` and `foundry.h` in full. So this one is
**wired**, not another write-and-forget.

Reachability of that call site: `report_coverage()` is invoked from
`bmc.cpp:3516` (end of `multi_property_check`, gated `bs && !fc && !is && !k-induction &&
!incremental-bmc`), from `bmc.cpp:2356` (the zero-remaining-claims early return, same gating),
and from six sites in `esbmc_parseoptionst::do_bmc_strategy`
(`esbmc_parseoptions.cpp:2783, 2807, 2828, 2870, 2888, 2942`, all gated on `is_coverage`).
The default `--solidity-path-coverage` dispatch sets `base-case` + `multi-property`
(`esbmc_parseoptions.cpp:4139-4141`), so the plain-BMC route reaches it.

One residual gap worth naming: `audit_entry_liveness` runs inside `report_coverage`, and
`report_coverage` is also where certification / outer-box modes short-circuit
(`bmc.cpp:1150-1168`) — the audit runs *before* that split, so it applies to all three modes.
But a run that never reaches a `report_coverage` call at all (crash, timeout, signal path)
gets no audit; the signal handlers only emit the branch-coverage snapshot
(`esbmc_parseoptions.cpp:130-219`).

---

## 4. Censuses

The code calls these "the exit census" and "the decision-set census" at
`goto_coverage.cpp:5300`, but "all three censuses" at `goto_coverage.cpp:4930`. The third is
the `tr`-completeness invariant. All are listed below in the order they execute per unit.

### (a) `tr` completeness — `goto_coverage.cpp:4710-4756`. HARD FAIL.

**Asserts:** every decision site the DFS fanned out on was also snapshotted into the runtime
accumulator, i.e. `dfs_decision_sites ⊆ phase1_decision_sites` (keyed by
`(location string, operand index)`).

**Hard fail:** yes — `log_error(... "INTERNAL DEFECT ...")` then `abort()` at
`goto_coverage.cpp:4740`. The reverse inclusion (snapshotted but never traversed) is dead code,
reported at `log_debug` only (4749-4755).

### (b) Exit census — `goto_coverage.cpp:4758-4924`. Two halves, both HARD FAIL (conditionally).

**(b1) goto-level reachability scan** — `goto_coverage.cpp:4783-4813` builds `reachable_exits`
by a forward worklist keyed on *instruction kind*
(`i->is_return() || i->is_end_function() || is_error_call(i)`), deliberately sharing none of
the DFS's enc/depth bookkeeping; `4815-4817` collects `enumerated_exits` from `to_insert`.

**Asserts:** every reachable exit is the exit of some enumerated path.

**Hard fail:** yes, *unless a bound was hit*. `goto_coverage.cpp:4890-4922`:
```cpp
        const bool bounded_out =
          loop_truncated || capped || dropped_paths > dropped_before_unit;
        if (bounded_out)
          log_warning(... "Reported as a bound obstacle, not as coverage ...");
        else
        {
          log_error(... "INTERNAL DEFECT ... The enumeration is swallowing a "
                        "class of exit ...");
          abort();
        }
```

**(b2) AST-level count of value-returning functions** — `goto_coverage.cpp:4827-4854`. Reads
`#sol_ast_return_sites` off the unit's symbol (the frontend's count of source-level `return`
statements) and requires that if it is `> 0`, at least one enumerated path ends at a RETURN.

**Hard fail:** yes — `abort()` at `goto_coverage.cpp:4853`, suppressed only by
`loop_truncated || capped`.

**(b2-adjacent, warn only)** — the `#sol_this_call_count` check at `goto_coverage.cpp:4864-4877`
declares `this.f(...)` sites a NAMED OBSTACLE. `log_warning` only; it does not even write into
`named_obstacle_paths`, so unlike §1 it leaves no machine-readable trace at all.

**(b-prologue, hard fail)** — the undetermined-exit cause accounting at
`goto_coverage.cpp:4612-4633`: the three counted causes must sum to `undetermined_exits.size()`,
else `abort()` at 4632.

### (c) Decision-set census — `goto_coverage.cpp:3823-3869`. WARN + MARK ONLY. No hard fail.

**What it scans:** ASSUME instructions at user source locations —
`goto_coverage.cpp:3845-3852`:
```cpp
    auto is_lost_decision = [&](goto_programt::const_targett i) -> bool {
      if (!i->is_assume()) return false;
      if (i->location.property().as_string() == "skipped") return false;
      return location_pool.count(
               get_filename_from_path(i->location.file().as_string())) != 0;
    };
```
A source `require(c)` inside an internal library / free function lowers to a bare `assume(c)`
with no control flow, so the `!c` execution is absent from the model entirely.

**What it asserts:** nothing, in the abort sense. It computes
`unit_has_lost_decision = !lost_decision_locs.empty()` (3867) and, if set, marks **every** path
of the unit into `named_obstacle_paths` at `goto_coverage.cpp:5632-5640`.

**Hard fail:** **no.** The only output is `log_warning` at `goto_coverage.cpp:5744-5769`
(the combined named-obstacle summary, causes (a) assume-lowered and (b) residual unit call).
And per §1, that mark reaches the JSON `u_reason` only for paths that stayed U — so the
assume-lowered obstacle has the *same* dead-on-F, invisible-to-the-emitter problem as the
residual-unit obstacle.

### (d) Not a census, but the same shape — expansion-ratio cross-check.

`goto_coverage.cpp:4570-4600`: when nothing was expanded into a unit and no bound was hit, the
flat counter `count_paths_no_instrument` and the enumerating DFS must agree after discounting
the ABI gate's one path. `abort()` at 4599. Also the rollback-RETURN classification check at
`goto_coverage.cpp:4682-4699`, `abort()` at 4698.

---

## Uncertainties

* **Call-site completeness.** "No other call site of `audit_entry_liveness`" is asserted from
  reading six files end to end (the pass, its header, the BMC driver, the option dispatcher,
  and the Foundry emitter + header). I did not read the whole `src/` tree, so a caller in a
  file I did not open would not have been seen. The same caveat applies to the claim that
  `named_obstacle_paths` has exactly three readers.
* **Whether the residual-unit hole ever fires in practice** is not settled here. It needs a
  unit whose internal call to another public/external function sits deeper than
  `path_cov_unwind` call-expansion rounds (`goto_coverage.cpp:2944`), which is a property of a
  specific contract. The mechanism is unconditional once that shape occurs; the frequency is not
  established, and I did not run the binary (per instruction).
* **`--path-cov-certify` / `--path-cov-outer-box` interaction with the obstacle marking** was
  not chased. Both modes `continue` past the normal `to_insert` loop
  (`goto_coverage.cpp:5293`, `5588`), so `named_obstacle_paths` is never written in those modes
  at all. Whether that is intended (they emit no per-path identity asserts) or a second gap is a
  design question I could not settle from the code.
* **`normal_exit_paths` and obstacles.** I report that a named-obstacle path can also be in
  `normal_exit_paths` because the condition at `goto_coverage.cpp:5624-5626` contains no obstacle
  term. I did not find any later code that removes such a key. If one exists outside the files
  read, the §1 conclusion about the bare `[asserted]` emission would soften — but the
  provenance/emission conclusion (the emitter never consults the obstacle map) would stand.
