# `--solidity-path-coverage`: the invocation contract, read out of the source

Every statement here is `file:line` from the working tree, not from `--help` and
not from `Implementation_plan.md`. Where the two disagree, the source wins and
the disagreement is called out. Written because the external invocation strategy
had never been settled and kept being re-derived from memory after each context
reset.

Sources read in full: `src/esbmc/options.cpp` (991),
`src/esbmc/esbmc_parseoptions.cpp` (5062),
`src/solidity-frontend/solidity_convert_contract.cpp` (1016).

---

## 1. The transaction bound is decided in two places, and they compose

**Place one — `get_tx_bound()`, `solidity_convert_contract.cpp:617-670`:**

```
explicit --solidity-max-tx N            -> N          (user always wins)
else if mode in unbounded_modes         -> 0          (unbounded while loop)
else                                    -> 2          (default)
```

`unbounded_modes` (`:623-638`) lists `solidity-precise`, both `tod-*-check`,
`assertion-coverage{,-claims}`, `branch-coverage{,-claims}`,
`branch-function-coverage{,-claims}`, all four `condition-coverage*`, and
`k-path-coverage{,-claims}`.

> **`solidity-path-coverage` IS NOT IN THAT LIST.**

Consequence: with `--solidity-max-tx` unset, branch coverage gets bound 0 and
path coverage gets bound **2**. The two modes do not default to the same harness.

**Place two — `emit_tx_driver()`, `solidity_convert_contract.cpp:672-688`:**

```
bound <= 0  ->  while(nondet_bool) { tx_body }        // HAS a back edge
bound >  0  ->  tx_body copied `bound` times          // NO loop, NO back edge
```

**Place three — coverage neutralisation, `esbmc_parseoptions.cpp:3599-3636`:**

```cpp
if (is_coverage && !cmdline.isset("coverage-multi-tx")) {
  ... for each function whose name contains "_ESBMC_Main":
        for each instruction: if (it->is_backwards_goto()) it->make_skip();
}
```

and `is_coverage` (`:3579-3591`) **includes `solidity-path-coverage`**.

### The composition, which is the thing that matters

| `--solidity-max-tx` | driver emitted | after neutralisation | transactions actually executed |
|---|---|---|---|
| `0` | `while(nondet){body}` | back edge → SKIP | **1** |
| unset, branch cov | `while(nondet){body}` (bound 0) | back edge → SKIP | **1** |
| `1` | body × 1 | untouched | 1 |
| unset, **path cov** | body × 2 (bound 2) | untouched | **2** |
| `3` | body × 3 | untouched | 3 |

**Two consequences, both of which invert something previously believed:**

1. **`--solidity-max-tx 0` is the SHALLOWEST setting under any coverage mode,
   not the unbounded one.** The `--help` text and `options.cpp:243` both say
   "use `--solidity-max-tx 0` ... to restore the unbounded loop for an unbounded
   proof" — true for plain verification, FALSE under coverage, because coverage
   kills the very back edge that makes it unbounded.
   `Implementation_plan.md` §3.4 step 2 prescribes `--solidity-max-tx 0` for its
   "unbounded finish". **That step would run ONE transaction.**

2. **The locked branch-coverage dataset also ran at one transaction.** It used
   `--branch-coverage-claims --k-induction --unlimited-k-steps` with no
   `--solidity-max-tx`; branch coverage is in `unbounded_modes`, so bound = 0,
   so `while(nondet)`, so neutralised to a single pass. The
   `--k-induction --unlimited-k-steps` bounds **internal loops**, not the
   transaction count. So the claim "branch coverage explored more transactions
   than path coverage" is false — path coverage's `--solidity-max-tx 1` and
   branch coverage's default are the same depth on this axis.

### What one transaction actually contains

The harness shape (`solidity_convert_contract.cpp:706-735`, and the real
construction at `:736-829`):

```
void _ESBMC_Main_C() {
  C();                          // constructor, OUTSIDE the loop
  while(nondet_bool) {          // one iteration == one transaction
    _sol_per_tx_reseed();       // fresh msg.sender / msg.value / block.*
    <unbound harness call>      // if(nondet) A(); if(nondet) B(); ...
  }
}
```

Each dispatch guard is independent, so **one transaction body can call several
functions in declaration order**. `dock(); ship();` is therefore available at
`--solidity-max-tx 1` — *provided the dispatcher still offers both*.

## 2. `--focus-function` is the thing that blocks cross-function state

Declared `value<std::string>` — **one name, no list** (`options.cpp:140-145`).
Compare `--tod-balance-check f1,f2` and `--negating-property [contract:]fn[:line]`,
which do take structured values. There is no `--focus-function A,B` form.

Documented semantics (`options.cpp:142-145`): constructor and state
initialisation still run, "but the nondet dispatch loop calls only this
function".

> **Under `--focus-function f`, every transaction is a call to `f`. A path of `f`
> guarded by state that only another public function can establish is
> unreachable at EVERY value of `--solidity-max-tx`.** Raising the tx bound
> cannot fix it; only dropping `--focus-function` can.

This is the structural explanation for the aqua `ship` result (2733 unit paths →
2 F, 1707 `bounded-holds`): the T2 collector ran `--contract Aqua
--focus-function ship`, so `dock(); pull(); push();` were never callable.

## 3. `--solidity-max-tx N >= 2` is the configuration the tool warns against

`esbmc_parseoptions.cpp:553-565`:

> `--solidity-max-tx {N} with --generate-foundry-testcase` reconstructs
> multi-transaction sequences **unreliably** (methods can be mis-attributed
> across transactions). For reliable ordered sequences use
> `--coverage-multi-tx --incremental-bmc` instead.

And `--coverage-multi-tx` is a **hard error** together with `--solidity-max-tx`
(`:508-528`, `abort()`), and itself requires a global bounding strategy —
`--unwind` / `--incremental-bmc` / `--k-induction` / … — else it aborts
(`:534-546`).

So the two ways to reach a state-building call sequence are mutually exclusive:

| route | reaches `deposit(); withdraw();`? | Foundry emission | cost |
|---|---|---|---|
| `--solidity-max-tx N>=2` | yes, N transactions | **tool says unreliable** | deterministic unroll |
| `--coverage-multi-tx --incremental-bmc` | yes, depth discovered dynamically | the documented reliable path | live loop, symex time |
| whole-`--contract`, tx=1, no focus | yes, **within one transaction** | fine | cheapest |

The third row is the one nobody wrote down: dropping `--focus-function` already
buys cross-function sequences at tx=1, because the dispatch guards are
independent.

## 4. What the path-coverage dispatch block does (`:4118-4236`)

**Hard rejection.** `--multi-fail-fast` is refused outright (`:4126-4134`):
fail-fast abandons the remaining path claims and the report could not tell
"unreachable" from "never asked".

**Forced on:** `base-case`, `multi-property`, `keep-verified-claims=false`,
`no-pointer-check`, `solidity-path-coverage-enabled`.

**Wired through:**
- `--contract` and not `--coverage-whole-unit` → `tmp.scope_contract`
- `--coverage-covered-set` → `tmp.covered_set_path`. **So the covered set IS
  wired for path coverage**, even though `options.cpp:840` still describes it as
  "Cross-run persisted covered-set for `--branch-coverage`". The help text is
  stale, not the wiring.
- `--cov-report-json` → `tmp.protect_ce_symbols = true`. Without it the per-claim
  slicer removes every state write and environment read (a path claim's guard
  mentions only the ghost accumulators) and the counterexample payload comes back
  **empty** (`:4164-4173`). This is why the report flag is not optional if the CE
  payload is wanted.
- `--path-cov-max-goals` (default 10000), `--path-cov-certify`,
  `--path-cov-outer-box`, `--cov-assume-asserts`.

**Unwind handling (`:4198-4234`) — two things happen silently:**
1. If `--unwind` is unset, the pass **sets `--unwind` to its own
   `path_cov_unwind` (4)** and says so. Reason given in the source: a Solidity
   external call is modelled as nondet RE-ENTRY into the contract's own
   dispatcher, so `t.call("")` recurses without a bound — measured 944 unwinds of
   `_ESBMC_Nondet_Extcall_C` and `ERROR: Out of memory`.
2. **`no-unwinding-assertions` is then set unconditionally** (`:4234`). Per
   `symex_goto.cpp:472-511` that replaces the unwinding ASSERT with an ASSUME,
   which **silently prunes** executions beyond the bound. It is not a user
   choice under path coverage; it is forced.

## 5. Checks that create branch points are OFF by default

Solidity runs get `no-standard-checks` unconditionally
(`:3445-3460`, `:3638-3650`), which expands (`:3503-3529`, `:3683-3704`) to
`no-div-by-zero-check`, `no-bounds-check`, `no-narrowing-check`,
`no-pointer-check`, … unless the matching **positive** flag is passed.
`--overflow-check` / `--unsigned-overflow-check` are separate and default OFF.

Additionally, for any branch/condition/**path** coverage on Solidity
(`:3469-3492`, `:3656-3677`):
- `no-assertions = true` — user and library asserts are dropped
- `no-symex-pointer-check = true` unless `--symex-pointer-check`

> **Open, and it bears on soundness, not just coverage:** the method requires the
> decision-point set `B_u` to be complete, including checked-arithmetic
> overflow, division by zero and bounds. None of `--overflow-check`,
> `--unsigned-overflow-check`, `--div-by-zero-check`, `--bounds-check` has ever
> been passed in any collector. Whether the path-coverage pass creates those
> revert edges itself (making the flags unnecessary) or relies on `goto_check`
> (making them REQUIRED) is not answered by the option layer and must be read out
> of `goto_coverage.cpp` / the frontend. `Implementation_plan.md` §3.3 records
> the "C1" decision to lower checked arithmetic into real two-exit branches, and
> §8 does **not** list it as implemented.

Note also `--overflow-check` sets `disable-inductive-step` (`:663-665`).

## 6. Strategy flags and `--unwind` do not coexist

`do_bmc_strategy` runs only under `--termination` / `--incremental-bmc` /
`--falsification` / `--k-induction` / `--loop-invariant` (`:2060-2064`).
Inside it, every phase **overwrites** `unwind` with the current `k_step`
(`:2975`, `:3039`, `:3104`).

> So combining path coverage with `--incremental-bmc` or `--k-induction` throws
> away the `--unwind 4` the pass just installed for itself, and the loop bound
> becomes whatever `k_step` currently is. Any such combination has to state which
> bound it actually ran at.

Other auto-settings that fire in dispatcher mode:
- `disable-forward-condition` for Solidity when `--function` is not given
  (`:1015-1018`) — the `while(nondet)` harness is unboundable so FC can never
  prove.
- In coverage mode `conclude()` reports success regardless of violations
  (`:2696-2712`), because in coverage a violation is the success signal.

## 7. Two capabilities that exist and have never been used

- **`--all-witnesses` (+ `--max-witnesses`, default 16)** — "after a property
  fails, enumerate further input vectors that also violate it"
  (`options.cpp:374-382`); implies `--multi-property` (`:750-771`). That is
  **extra counterexamples per feasible path at no extra query round**, which is
  exactly the raw material the stage-2 ladder wants for sibling spans and the
  boundary witnesses want for ON/OFF points.
- **`--coverage-covered-set`** is wired for path coverage (§4) and is what makes
  any multi-round escalation affordable — round N only instruments paths still
  lacking a counterexample. `progress.md` records it was broken in both
  directions until recently and that the plan's 1→2→3 ladder has never actually
  been run.

## 8. Resource control

`--timeout` installs SIGALRM → `timeout_handler` → `_exit(1)` (`:670-681`,
`:182-193`). The partial-result rescue `emit_branch_coverage_on_timeout` is
gated on `goto_coveraget::branch_cov_active` (`:130-133`), so **a path-coverage
run killed by `--timeout` emits nothing at all**. External SIGTERM/SIGINT go to
`term_handler` (`:207-219`) with the same gate. This is why the collector must
bound the run from outside (subprocess timeout) and write results per unit.

`--memlimit` sets `RLIMIT_DATA` (`:691-708`).

## 9. The settled invocation, and what still has to be decided

**Settled by the source, not open to preference:**

- `--focus-function` takes one name. Multi-function focus does not exist.
- Under `--focus-function f`, no tx bound reaches cross-function state.
- `--solidity-max-tx 0` under coverage = one transaction, not unbounded.
- `--multi-fail-fast` is refused; `--coverage-multi-tx` and `--solidity-max-tx`
  abort together.
- `--cov-report-json` is required for a non-empty CE payload.
- `--unwind` defaults to 4 and `no-unwinding-assertions` is forced on.

**Still to decide, and each needs its own experiment:**

1. Whether the escalation drops `--focus-function` (cheap, reaches cross-function
   state at tx=1) or raises `--solidity-max-tx` (which the tool warns produces
   mis-attributed Foundry tests) or switches to
   `--coverage-multi-tx --incremental-bmc` (the documented reliable route, which
   `Implementation_plan.md` §3.5 ruled out before the ship data existed).
2. Whether `--overflow-check` and friends are needed for decision-set
   completeness (§5). This is a soundness question, not a tuning question.
3. Whether `--coverage-covered-set` stays correct ACROSS configurations, not just
   across rounds of the same configuration. The fingerprint includes
   `path-cov-max-goals`; whether it includes the scope/tx configuration is not
   answered by the option layer.
4. Entry liveness must be checked before any whole-`--contract` run is believed:
   St1inch under the whole dispatcher produced `Generated 0 VCC(s)` and every
   path was reported U, which is "the driver never got in", not "not proven".
5. Whether dropping `--focus-function` re-opens the unit-body double-identity
   hole (a body reachable both as a guarded ABI entry and as an inlined internal
   callee). Any change to which units the dispatcher offers must be checked
   against it.

## 10. Four more flags, read the same way

Sources read in full for this section: `src/esbmc/bmc.cpp` (3723),
`src/goto-programs/goto_coverage.cpp` (8428), `src/goto-symex/slice.cpp` (315)
and `slice.h` (237), `src/goto-symex/symex_main.cpp` (1528),
`src/goto-symex/symex_assign.cpp` (1022), `src/goto-symex/symex_function.cpp`
(765), `src/goto-symex/goto_trace.cpp` (952), `src/goto-symex/foundry.cpp`
(3499), `src/goto-symex/reachability_tree.cpp` (696),
`src/goto-symex/symex_other.cpp` (143), `src/util/cache.cpp`/`cache.h`,
`src/util/parseoptions.cpp` (73), `src/esbmc/main.cpp` (13),
`src/solidity-frontend/solidity_convert_contract.cpp` (1017),
`solidity_grammar.h` (650), `solidity_convert_stmt.cpp` (1-1400),
`solidity_convert_decl.cpp` (1-1150).

None of the four is force-set by the path-coverage dispatch. The complete forced
set is still the one in §4 plus `unwind` (`:4288-4304`) and
`no-unwinding-assertions` (`:4305`); the only one of these four that the pass
ever sets is `no-simplify`, and only under `--path-cov-assert` (`:4223`).

### 10.1 `--no-slice` does not switch slicing off

**Read in three places, all in `bmc.cpp`:**

1. `bmc.cpp:81-84` — in the `bmct` constructor:
   `no-slice` **swaps** the slicer, it does not remove it:
   `simple_slice` instead of `symex_slicet`. `simple_slice`
   (`slice.cpp:202-232`) ignores every step *after the last assertion* and
   nothing else. So the flag's real meaning is "naive slice", not "no slice";
   `options.cpp:461` ("Do not remove unused equations") overstates it.
2. `bmc.cpp:2836-2840` — inside the per-claim job of `multi_property_check`:
   `if (!options.get_bool_option("no-slice")) { symex_slicet slicer(options);
   slicer.run(local_eq.SSA_steps); }`. **This is the one that guts a path
   claim's counterexample**, because it runs on the per-claim equation after
   `claim_slicer` has kept exactly one assert.
3. `bmc.cpp:139-142` and `bmc.cpp:2970-2974` — trace shape:
   `is_compact_trace = true` unless (`no-slice` **and** not `compact-trace`).
   Note `--compact-trace` implies `no-slice` (`esbmc_parseoptions.cpp:464-465`).

The dependency slicer's exemption hook is `config.no_slice_names` /
`no_slice_ids`, consulted by `no_slice()` at `slice.cpp:4-8` from
`get_symbols<false>` at `slice.cpp:47`.

**The exemption list that prints "exempting N symbol(s) from slicing" is built
at `goto_coverage.cpp:3541-3575`**, gated on `protect_ce_symbols`, which is set
only by `--cov-report-json` (`esbmc_parseoptions.cpp:4172-4174`). It walks
`cov_context` once and inserts into `config.no_slice_names` under exactly three
mutually exclusive predicates — these are the three counters in the message:

| category | predicate | counter |
|---|---|---|
| contract object(s) | `id` starts with `sol:@_ESBMC_Object_` (`:3547-3551`) | `n_obj` |
| contract-scope store(s) | `id` starts with `sol:@C@` **and** contains no `@F@` (`:3552-3557`) — mappings and dynamic arrays, which the frontend lowers to contract-level globals rather than fields of the contract object | `n_store` |
| environment | the symbol's **base name** starts `msg_` / `tx_` / `block_` (`:3558-3564`) | `n_env` |

Nothing else is registered (`:3539-3540` names the deliberate exclusion: the
c2goto keccak/sha256/ABI tables, the address allocator, the dispatcher plumbing).

> **CRITICAL: function parameters are NOT in that list, and cannot be.** A
> Solidity parameter's symbol id is `sol:@C@<C>@F@<fn>#N@<p>`
> (`foundry.cpp:38-40`; created at `solidity_convert_stmt.cpp:63,101-117` with
> `is_parameter = true`). It begins `sol:@C@` — and is therefore *excluded by
> the second predicate's own `@F@` test* — and its base name is the source
> parameter name, so it fails the third as well.

**The mechanism by which input values survive anyway** is stated in one line at
`goto_coverage.cpp:3525-3526` ("Call arguments survive on their own: the
decisions that build `tr` are guards over them") and is a *data dependency*, not
an exemption:

- Phase 1 inserts `tr = tr*2 + guard; cnt = cnt+1` before every decision
  (`goto_coverage.cpp:4390-4413`, applied at `:4444-4475`);
- the path claim is `assert(tr != enc || cnt != depth)`
  (`goto_coverage.cpp:4762-4764`);
- `symex_slicet` walks backwards from the assert, adding every symbol of a kept
  step's guard/rhs to `depends` (`slice.cpp:51-55`, `:87-177`).

So the dependency cone of a path claim contains every decision guard on that
path and, transitively, every symbol those guards read — i.e. exactly the
parameters the path *branches on*.

> **Consequence, and it is a real hole:** a parameter that appears in **no
> decision** on that path is not in the cone and IS sliced. The harvest then
> finds no assignment step for it (`bmc.cpp:3397-3440` requires a nondet-sourced
> assignment whose lhs symbol is `is_parameter` and inside `fn_scope`), so it is
> absent from `inputs`; the Foundry emitter substitutes a type default and marks
> it `defaulted` (`foundry.cpp:1394-1401`), which is *reported and not refused*
> (`foundry.cpp:3183-3219`, with the measured aqua consequence). Read the
> report accordingly: **`inputs` is complete for the parameters the path's own
> decisions read, and silently defaulted for the rest.**

**Is `--no-slice` a strictly safer superset?** For the payload, yes: it disables
site 2 entirely (`bmc.cpp:2836`), leaving only `simple_slice`, which drops
nothing before the last assertion — so a decision-free parameter's assignment
survives and so does every state write. Two costs:

- the c2goto crypto/ABI/allocator tables stay in every per-claim formula — the
  exact reason the exemption list exists instead (`goto_coverage.cpp:3539-3540`);
- each job copies the whole equation (`bmc.cpp:2787`), so the per-claim SMT
  instance grows by everything the dependency slicer would have removed. On a
  path claim that is most of the equation.

It also changes what the report says about itself: `ce.sliced` becomes false
(`bmc.cpp:3090`), which flips the `final_state_unavailable_reason` text
(`bmc.cpp:1785-1791`). `--cov-report-json` already forces the full (non-compact)
trace for path coverage independently (`bmc.cpp:2981-2982`).

### 10.2 `--no-simplify`, and what simplification does to a path claim

Declared `options.cpp:971`. **Read exactly once**: `symex_assign.cpp:43`
(`no_simplify(options.get_bool_option("no-simplify"))` in the `goto_symext`
constructor), consumed by `goto_symext::do_simplify` at
`symex_assign.cpp:221-225` (`if (!no_simplify) simplify(expr);`). Contract
documented at `goto_symex.h:152-159`.

**The line `Generated N VCC(s), M remaining after simplification` is
`bmc.cpp:2447-2452`.** `N` is `solver_result.total_claims`; `M` is
`remaining_asserts`, counted at `bmc.cpp:2432-2437` as the SSA steps that are
asserts and not `ignore` — *after* the `algorithms` loop at `:2425-2429`. So
`N - M` has two independent sources:

1. **symex time** — `goto_symext::claim`, `symex_main.cpp:63-145`:
   `++total_claims` (`:69`), rename + `do_simplify` (`:72-75`), then
   `if (is_true(new_expr))` (`:77`): under `--multi-property` it logs
   `✓ PASSED: '<msg>' at <loc>` and `++simplified_claims` (`:82-88`), calls
   `assume(claim_expr)` and **returns** (`:92-93`). `assertion()`
   (`:147-162`) — the only place `remaining_claims++` happens (`:154`) and the
   only place a step reaches the target — is never called.
2. **equation time** — `assertion_cache` (installed at `bmc.cpp:73-79`,
   `cache.cpp:6-15`) sets `step.ignore = true` on a duplicate `(cond, guard)`
   assert. It is skipped under `--k-induction` / `--forward-condition`
   (`bmc.cpp:73-76`) but active on a plain base-case path-coverage run.

**What happens to a path claim that is simplified away — the question that
matters:** it is neither PASSED-in-the-ledger nor covered. It is dropped.

- `multi_property_check` enumerates jobs `1..remaining_claims`
  (`bmc.cpp:2712-2714`). A claim that never reached `assertion()` has no index,
  so no `claim_slicer`, no solve, and no entry in `goto_coveraget::claim_outcome`
  (written only inside the job, `bmc.cpp:2918-2935`).
- The report therefore sees `v == 0` → `status: "U"` (`bmc.cpp:1453-1459`) with
  `not_solved_this_run: true` (`:1495-1498`), and the stdout U-reason token is
  `not-solved-this-run` (`goto_coverage.cpp:249-253`).

> So simplification **cannot swallow a witness** — `is_true(tr != enc || cnt !=
> depth)` is the simplifier proving the path is never walked, which is exactly
> the case where no counterexample exists. What it *can* do is turn a would-be
> `P` (`bounded-holds`) into an undecided `U`, **while printing
> `✓ PASSED: '<unit>:path:<enc>'` on stdout** (`symex_main.cpp:82-85`). stdout
> and `cov-report.json` then describe the same claim in opposite words.

This is not a hypothesis: `esbmc_parseoptions.cpp:4209-4224` forces
`no-simplify` for `--path-cov-assert` for precisely this reason and records the
measurement (without it 0 HOLDS / 3 REFUTED / 3 no-verdict; with it 3 / 3 / 0 on
the same program).

Not everything absent is a simplification casualty: `goto_coverage.cpp:614-639`
records the measured case where a box makes two of four exits unreachable, their
asserts never become VCCs at all, and `--no-simplify` does **not** bring them
back — symex never reaches the instruction.

### 10.3 `--show-funccall-trace`

Declared `options.cpp:100-103`. **Read exactly once**: `goto_trace.cpp:615`,
from `config.options` (not the local `optionst`), inside the violated-ASSERT arm
of `show_goto_trace` (`goto_trace.cpp:580-674`). It prints `Function call
trace:` and then, walking the trace up to the violated assert, the frames newly
pushed relative to the previous step's stack — outermost-of-new first — as
`  <goto function id> at <location>` (`:621-664`). Steps with an empty stack are
skipped rather than treated as a reset (`:632-637`).

**Usable under path coverage: yes.** Per-claim counterexamples reach it through
`multi_property_check` → `report_multi_property_trace` → `show_goto_trace`
(`bmc.cpp:3595-3599`, `:520-529`/`:571`). The gate is
`is_cov_silent = is_goto_cov && claim.claim_property != "instrumented
assertion"` (`bmc.cpp:2862-2863`), and every path claim is inserted with
`location.property("instrumented assertion")` (`goto_coverage.cpp:7703`), so
`is_cov_silent` is false. **One trap:** with `--verbosity coverage:N` the other
arm is taken (`bmc.cpp:3585-3594`) and no trace is printed at all for path
coverage.

**Does it give a per-path function-call sequence? Only of the calls path
coverage did not absorb.** `--solidity-path-coverage` physically splices
internal callees into their caller before instrumenting
(`sol_path_inlinet::expand_here`, `goto_coverage.cpp:2803-2848`, driven at
`:3701-3738`): an expanded call stops being a `FUNCTION_CALL` instruction and
therefore pushes no frame. So the very calls the pass tells you it folded into
the path identity are the ones this trace can no longer show. What still appears
as frames: the calls expansion refuses — `_ESBMC_Main*` and
`_ESBMC_Nondet_Extcall_*` (`:3619-3622`), `#sol_error` revert callees
(`:3626-3628`), callees with no body or outside user source (`:3629-3636`),
calls past the depth bound `path_cov_unwind` (`:3706`, warned at `:3985-3991`),
and call points withdrawn by degradation (`:3717-3725`).

The counterexample harvest reuses this exact algorithm for entry detection and
says so (`bmc.cpp:3137-3143`).

**Events and their order are NOT observable.** `EventDef` is a no-op in *both*
declaration walkers — `get_non_function_decl`
(`solidity_convert_decl.cpp:56-61`) and `get_function_decl` (`:227-233`) both
`break` without creating a symbol — so no goto function exists for an event and
there is no frame for this flag to print. `emit E(x)` is lowered by the
`EmitStatement` arm as a call expression on `eventCall`
(`solidity_convert_stmt.cpp:1017-1029`); what that resolves to was **not
determined here** (the `eventCall` lowering in `solidity_convert_call.cpp` was
not read), but it cannot be an event-named frame. Independent corroboration that
an event is not a callable member: the Foundry mock builder filters events out
by "first argument is not `this`" (`foundry.cpp:830-836`, `:852-857`).

### 10.4 `--coverage-multi-tx` is wired for path coverage and does nothing there

**Every read site** (over the files listed at the head of this section):
`options.cpp:826-837` (declaration); `esbmc_parseoptions.cpp:508` (block entry),
`:520-528` (hard error with `--solidity-max-tx`), `:534-546` (hard error without
a bounding strategy), `:555` (suppresses the multi-tx-unreliable warning),
`:3611` (the neutralisation opt-out). Nowhere else: `goto_coverage.cpp` has no
`cmdline`/`optionst` at all (it carries only the fields the dispatch sets,
`goto_coverage.h:661-1078`), and `foundry.cpp` reads only `contract`,
`function`, `sol` and `input-file` from `config.options` (`foundry.cpp:1439`,
`:2017`, `:2321`, `:2547`, `:2805`, `:3472`).

**What the harness actually executes.** The tx count is decided by
`get_tx_bound()` (`solidity_convert_contract.cpp:617-670`), and
`--coverage-multi-tx` is not one of its inputs. Its `unbounded_modes` list
(`:623-638`) holds `solidity-precise`, both `tod-*-check`, assertion /
branch / branch-function / condition / k-path coverage — and **neither
`solidity-path-coverage` nor `coverage-multi-tx`**. Since the flag forbids
`--solidity-max-tx` (`:520-528`), `max_tx_opt` is necessarily empty, so:

| mode | `get_tx_bound()` | `emit_tx_driver` (`:672-688`) | what `:3611` opts out of |
|---|---|---|---|
| branch / condition / assertion / k-path coverage | 0 (`:653`) | `while(nondet_bool){tx_body}` (`:675-683`) — has a back edge | a real neutralisation; the loop stays live |
| **`--solidity-path-coverage`** | **2** (`:655`) | `tx_body` copied twice, straight-line (`:684-687`) — **no loop, no back edge** | **nothing: there is no backwards goto in `_ESBMC_Main_*` to rewrite** |

> **`--coverage-multi-tx` under `--solidity-path-coverage` executes exactly what
> the default already executes: two straight-line transactions, each preceded by
> `_sol_per_tx_reseed` (`:774`).** The flag is *wired* (the `is_coverage`
> disjunction at `:3579-3591` includes path coverage, so `:3611` is reached), but
> the thing it protects does not exist in this mode. `options.cpp:826-837`
> promises to "keep the multi-transaction dispatcher loop live … instead of
> neutralizing it to one call"; under path coverage there was never a loop and
> never a neutralisation. This is the mirror image of the `--coverage-covered-set`
> case in §4: there the help text was stale and the wiring real; here the wiring
> is real and the effect empty.

It is not free. The mandatory-strategy check at `:534-546` lives in
`get_command_line_options`, which runs long before the path-coverage dispatch
installs its own `--unwind 4` at `:4296`. So
`--solidity-path-coverage --coverage-multi-tx` **aborts** unless one of
`--unwind` / `--incremental-bmc` / `--k-induction` / `--k-induction-parallel` /
`--termination` / `--base-case` / `--forward-condition` / `--inductive-step` is
also given, while plain `--solidity-path-coverage` runs.

**Every legal combination, and what bounds what** (all rows execute **2**
transactions, from `get_tx_bound()`; none of the bounding strategies affects the
transaction count under path coverage):

| combination | bounds internal loops | enumeration bound `path_cov_unwind` |
|---|---|---|
| `--coverage-multi-tx --unwind N` | `N` | set to `N` (`:4288-4293`) — the two agree |
| `--coverage-multi-tx --incremental-bmc` | `k_step`, rewritten into `unwind` at every phase (`:2975`, `:3039`) | stays 4 — **disagrees at every `k != 4`** |
| `--coverage-multi-tx --k-induction` | same, plus the inductive step (`:3104`) and `diagnose_unknown_properties` (`:5117`) | stays 4 — same disagreement |
| `--coverage-multi-tx --termination` | `k_step` via `does_forward_condition_hold` (`:2840-2850`) | stays 4 |
| `--coverage-multi-tx --base-case` / `--forward-condition` / `--inductive-step` | nothing — these satisfy the gate at `:537-538` without invoking `do_bmc_strategy` (`:2060-2064`), so the bound is whatever `--unwind` is, i.e. the pass's own 4 | 4 — agree by accident |
| `--coverage-multi-tx --k-induction-parallel` | as k-induction, but `doit()` forks first (`:958-959`) and each child re-runs `get_command_line_options` and the whole instrumentation (`:2137-2155`) | 4 per child |

§6 is **confirmed**: `do_bmc_strategy` overwrites `unwind` with the current
`k_step` at `esbmc_parseoptions.cpp:2975` (base case), `:3039` (forward
condition) and `:3104` (inductive step). For path coverage this is worse than a
bookkeeping annoyance: the pass states at `:4269-4287` that the offline
enumeration bound and the symex unwind bound **MUST** agree, and the enumeration
happens once at instrumentation time and cannot be redone per `k`. So under any
strategy the first base-case phase runs at `unwind = base-k-step` (default 1)
against an enumeration done at 4.

**`--focus-function`:** no read site pairs the two, and there is no abort.
`--focus-function` is consumed by the frontend harness (`options.cpp:140-145`)
and by `audit_entry_liveness` (`goto_coverage.cpp:292`, `:1694-1711`, called
with `options.get_option("focus-function")` at `bmc.cpp:1134`). The combination
is legal and useless: by §2 the dispatcher then offers only that one entry, so
both transactions call the same function and no cross-function state is built.

### 10.5 The OOM warning claims a recovery that does not happen

The four lines of the failing run map onto three sites:

| line | site |
|---|---|
| `ERROR: Out of memory` | `bmc.cpp:2559-2563` — `run_thread`'s `catch (std::bad_alloc &) { log_error("Out of memory\n"); return smt_convt::P_ERROR; }` |
| `ERROR: SMT solver failed` | `bmc.cpp:2175-2177` — `report_result`'s `default:` arm, reached with the same `P_ERROR` |
| `WARNING: The solver could not decide this query; … continuing.` | `esbmc_parseoptions.cpp:3201-3205`, in `do_bmc`, on `res == P_ERROR` |
| `terminate called after throwing an instance of 'std::bad_alloc'` | **no handler exists** |

`std::bad_alloc` is caught in exactly four places during GOTO construction —
`esbmc_parseoptions.cpp:3297-3301`, `:3376-3380`, `:3547-3551`, `:4330-4334` —
and in exactly **one** place during verification: `bmc.cpp:2559-2563`, inside
`run_thread`. Everything above it on the stack is uncovered:
`bmct::run` (`bmc.cpp:2198-2263`, including `symex->setup_for_new_explore()` at
`:2201`), `bmct::start_bmc` (`:2187-2196`), `report_trace`/`error_trace`
(`:2193`, `:131-209`), `report_result` (`:2194`), `report_coverage`
(`bmc.cpp:768-2002`, which builds the whole JSON), each phase's `bmct`
construction (`esbmc_parseoptions.cpp:2982`, `:3041`, `:3106`, `:5120`), and
`do_bmc` itself. There is no top-level handler either:
`main` is two lines (`main.cpp:8-12`), `parseoptions_baset::main` is
`install_signal_catcher(); return doit();` with no `try`
(`util/parseoptions.cpp:58-72`), and `doit()`
(`esbmc_parseoptions.cpp:884-2070`) has none.

> **So "continuing" holds only for a `bad_alloc` thrown inside `run_thread`'s
> own `try`. The next one thrown anywhere else in the BMC loop reaches
> `std::terminate`, and the process dies with SIGABRT.** The message at
> `:3201-3205` should not be read as a promise that the run survives.

Two aggravating details:

- The retry at `:3172-3199` re-runs the **entire** BMC (`res = bmc.start_bmc()`,
  `:3197`) with CVC5 when the backend was auto-selected — at a moment when
  memory is already exhausted. It is `static bool solver_fallback_attempted`, so
  once per process.
- **A caught OOM still costs the whole report.** `multi_property_check` is
  called from inside `run_thread`'s `try` (`bmc.cpp:2541`), and its own
  `report_coverage` sits *after* the job loop (`bmc.cpp:3661-3670`). An
  exception in any job unwinds past it into the catch at `:2559`, so no
  `[Coverage]` block and no `cov-report.json` are produced, and every claim not
  yet solved has no `claim_outcome` entry — i.e. the run's entire result is lost,
  not degraded. The signal-handler rescue does not apply here either: it is
  SIGALRM/SIGTERM only (`:130-133`, `:182-219`) and is gated on
  `goto_coveraget::branch_cov_active`, which only `branch_coverage()` ever sets
  (`goto_coverage.cpp:2325`).

## 11. The decision set is complete without the check flags — because the check flags cannot enter it at all

Sources read in full for this section: `src/esbmc/options.cpp` (1012),
`src/esbmc/esbmc_parseoptions.cpp` (5133),
`src/goto-programs/goto_coverage.cpp` (8427),
`src/goto-programs/goto_check.cpp` (1313),
`src/goto-programs/goto_program.cpp` (517), `src/esbmc/bmc.cpp` (3723).
Read from the top through the cited site: `src/goto-programs/goto_convert.cpp`
(to `:988`), `src/goto-programs/builtin_functions.cpp` (to `:700`),
`src/solidity-frontend/solidity_convert.cpp` (to `:240`),
`src/solidity-frontend/solidity_convert_stmt.cpp` (to `:240`),
`src/solidity-frontend/solidity_convert_modifier.cpp` (`:840-1100`).

> **Line-number drift note:** `options.cpp` is now 1012 lines (§10 was written
> at 991) and `esbmc_parseoptions.cpp` is now 5133 (§10: 5062). Every citation
> below is from the current tree. §5's `:3445-3460 / :3503-3529 / :3469-3492`
> are now `:3445-3460 / :3503-3529 / :3469-3492` unchanged, and §5's
> `:3638-3650 / :3683-3704 / :3656-3677` are unchanged too; `options.cpp:840`
> for `--coverage-covered-set` is now `:838-843`.

### 11.1 The pass creates exactly ONE branch of its own, and it is not an arithmetic check

`solidity_path_coverage()` (`goto_coverage.cpp:3001-7674`) fans the enumerating
DFS out at exactly three kinds of site, and terminates at three:

| DFS action | site | `goto_coverage.cpp` |
|---|---|---|
| **fan out 2-way** | `pc->is_goto() && !is_true(pc->guard)` | `:5030-5108` |
| **fan out 2^K** | folded short-circuit / ternary operands in an `ASSIGN` rhs | `:5208-5259` |
| **fan out 2^K** | the same, in a `RETURN` operand | `:5130-5203` |
| terminate | `END_FUNCTION` | `:4968-5015` |
| terminate | a `#sol_error` call (`revert CustomError()`) | `:5024-5029` |
| terminate | `RETURN` | `:5130-5203` |
| **straight-line, no fan-out** | *everything else* | `:5260` (`pc = std::next(pc); // straight-line`) |

Phase 1 (the runtime `tr`/`cnt` accumulator) mirrors exactly the same three
kinds and nothing else (`:4444-4475`), and the two are cross-checked at
`:5532-5557` (a site the DFS branches on but Phase 1 does not accumulate is a
hard `abort()`).

**The only branch the pass synthesises is the ABI non-payable value gate**
(`:4271-4345`): `IF msg_value == 0 THEN GOTO <body>` / `_ESBMC_sol_mark_revert();
GOTO <END_FUNCTION>`, inserted only when `#sol_payable` is unset
(`:4272-4278`), stamped `sol_abi_value_gate` (`:4309`) and contributing exactly
one extra path (`:4338-4342`).

> **There is no code anywhere in `goto_coverage.cpp` that creates a revert or
> branch edge for checked arithmetic, division by zero, array bounds or
> narrowing.** The whole pass's vocabulary for "a decision" is
> `is_goto() && !is_true(guard)` plus `collect_short_circuit_decisions`
> (`:2049-2071`), plus the one gate above.

### 11.2 What the check flags actually produce: a single-successor ASSERT, invisible to the enumeration

`goto_check` runs at `esbmc_parseoptions.cpp:3837`, i.e. **before** the
path-coverage dispatch at `:4118`. So its output is present in the GOTO program
when the pass walks it. It does not matter:

- every check funnels through `goto_checkt::add_guarded_claim`
  (`goto_check.cpp:1001-1029`), which does
  `new_code.add_instruction(ASSERT)` and sets only `guard` / `location`
  (`:1023-1027`). **`targets` is never touched.** This confirms the prior
  finding verbatim.
- `goto_programt::get_successors`' assert arm pushes only `next`
  (`goto_program.cpp:301-308`). Confirmed verbatim. An ASSERT has one
  successor.
- the individual sites are all `add_guarded_claim` calls:
  `div_by_zero_check` `goto_check.cpp:168-173`, `bounds_check` `:988` and
  `:998`, `overflow_check` `:373-378`, `cast_overflow_check` `:319-320`,
  `shift_check` `:662-667`, the Solidity narrowing-assignment arm `:1256-1261`.
  The one exception is `input_overflow_check`, which adds a bare
  `ASSERT false` (`:593-601`) — also single-successor.

> **ANSWER TO THE SOUNDNESS QUESTION (§5, §9 item 2): NEITHER.** The pass does
> not create those edges itself, and it does not rely on `goto_check` to create
> them — because `goto_check` cannot create an edge at all, only an assertion.
> Passing `--overflow-check`, `--unsigned-overflow-check`, `--div-by-zero-check`
> or `--bounds-check` under `--solidity-path-coverage` **cannot change the
> enumerated path set**: the extra instructions are ASSERTs, the DFS walks over
> them at `goto_coverage.cpp:5260`, Phase 1 does not snapshot them, `enc`/`cnt`
> are unchanged, and `all_claims` (built only from `to_insert`,
> `goto_coverage.cpp:7357`) does not grow.
>
> So the *right* statement of §5's open item is not "the flags are required" and
> not "the flags are unnecessary". It is: **an EVM revert on overflow is not in
> the decision-point set of this method under any current flag combination, and
> no option can put it there.** `Implementation_plan.md` §3.3's "C1" decision to
> lower checked arithmetic into real two-exit branches is a *frontend* change
> (it would have to emit the branch the way `require` already is, see §11.3);
> it is not reachable from the option layer, and §8 correctly does not list it
> as implemented.

**Where the revert edges that DO exist come from.** Not `goto_check` — the
frontend, via the revert-observation gate. `--solidity-path-coverage` publishes
`solidity-path-coverage-enabled` as a real boolean before `config.options` is
captured (`esbmc_parseoptions.cpp:866-867`), and that flag is read exactly once
in the frontend, at `solidity_convert.cpp:224-230`, to set
`uses_revert_observation`. `build_revert_rollback_block`
(`solidity_convert_modifier.cpp:906-1100`) then emits, for `require(cond)`,
a **real two-exit branch** — `codet("ifthenelse")` over `not_exprt(cond)` at
`:1079-1082` — whose taken arm is
`_ESBMC_sol_mark_revert(); [*this = _sol_save_this; ...] return nondet;`
(`:1003-1011`, `:1012-1068`, `:1069`). Without the gate, a non-snapshot scope
returns `true` at `:952-955` and the caller falls back to
`__ESBMC_assume(cond)` — a control-flow-free prune, which is exactly what
`is_lost_decision` (`goto_coverage.cpp:4661-4668`) then names as a per-unit
NAMED OBSTACLE.

**A real cost that the flags DO have.** Unlike every other coverage mode,
`solidity_path_coverage()` never neutralises pre-existing asserts. Compare:
`branch_coverage` rewrites them to `assert(true)` / `assume` at
`goto_coverage.cpp:2254-2264`; `k_path_coverage` at `:2427-2437`;
`branch_function_coverage` at `:1992-2001`. Path coverage has no such arm
anywhere in `:3001-7674`. So a `goto_check` assertion survives into
`multi_property_check` as its own job (`bmc.cpp:2712-2714`), gets its own
`claim_slicer` and its own solve, is **not** in `all_claims` (so it never
appears in the coverage numerator or denominator), and is **not**
`is_cov_silent` (`bmc.cpp:2862-2863` tests
`claim_property != "instrumented assertion"`; a goto_check claim's property is
`"overflow"` / `"array bounds"` / `"division-by-zero"`), so it prints a full
`[Counterexample]` block. **Passing a check flag under path coverage buys extra
solver work and extra stdout noise and zero extra paths.**

### 11.3 `--overflow-check` sets `disable-inductive-step`, and what that forbids

Confirmed: `esbmc_parseoptions.cpp:663-665` —
`if (cmdline.isset("overflow-check") || cmdline.isset("unsigned-overflow-check"))
options.set_option("disable-inductive-step", true);`

Read sites of `disable-inductive-step`: `is_inductive_step_violated` returns
`TV_UNKNOWN` immediately (`:3093-3094`), `diagnose_unknown_properties` returns
immediately (`:5093-5094`), and the parallel k-induction child breaks out of its
loop (`:2587-2588`).

> **The forbidden combination is `--overflow-check` (or
> `--unsigned-overflow-check`) with `--k-induction` on Solidity, and it
> degenerates to base-case-only.** `do_bmc_strategy`'s k-induction arm
> (`:2757-2838`) then has: the inductive step disabled by the flag above, AND
> the forward condition disabled by `disable-forward-condition`, which
> `:1015-1018` sets for every Solidity run without `--function` (so
> `does_forward_condition_hold` returns `TV_UNKNOWN` at `:3024-3025`). What is
> left is `is_base_case_violated` looping to `max-k-step`. By §11.2 the flag
> also adds nothing to the path set, so this is pure cost.

### 11.4 `no-assertions` is set for BOTH modes, and it removes nothing from either decision set

**Both modes, one block, one condition.** `esbmc_parseoptions.cpp:3469-3492`
computes `any_branch_or_cond_cov` as a disjunction that contains **both**
`branch-coverage` / `branch-coverage-claims` (`:3474-3475`) **and**
`solidity-path-coverage` (`:3482`), then sets `no-assertions` at `:3484`. The
`read_goto_binary` mirror at `:3656-3677` / `:3671` is identical.
`assertion-coverage` is the only exempt mode (`:3471-3472`, `:3485`, and the
comment at `:3462-3465` explains why: it would self-zero).

So the two sides of the gate comparison are symmetric on this axis. But the
question behind it — does branch coverage count decisions that path coverage has
had removed? — needs the goto-level fact, not the flag:

- **`assert(...)` is never a decision in either mode.** A Solidity `assert(c)`
  resolves to the builtin `c:@F@assert` and is lowered by
  `goto_convertt::do_function_call_symbol`: `is_assert` at
  `builtin_functions.cpp:547`, and `:612` does
  `t = dest.add_instruction(is_assume ? ASSUME : ASSERT)` — **one ASSERT
  instruction**, `property("assertion")` at `:634`. (The `codet`-statement path
  `goto_convert.cpp:966-988` produces the same single ASSERT at `:981`.)
  Branch coverage's decision test is
  `it->is_goto() && !is_true(it->guard)` plus the folded short-circuit collector
  (`goto_coverage.cpp:2267`, `:2291-2297`); path coverage's is the same set
  (`:5030`, `:5208`, `:5130`) plus the ABI gate. **An ASSERT matches neither.**
- **`no-assertions` deletes the instruction outright**, before either
  instrumenter sees it: `builtin_functions.cpp:571-574` returns early, and
  `goto_convert.cpp:978-979` does the same for the `codet` form.

> **PLAIN ANSWER: no, the two sides' decision sets do NOT differ because of
> `no-assertions`.** The flag is set identically for branch coverage and for
> path coverage by one shared condition (`:3469-3492`), and what it removes —
> a single-successor ASSERT — was in neither mode's decision set to begin with.
> The methodology's "`FunctionCall(assert)` is a canonical DECISION" is a
> statement about the *source language*, and it is **not** realised at goto
> level by either metric. If the gate comparison needs asserts to count as
> decisions, that is a change to both instrumenters, not a flag.
>
> Asymmetry that DOES exist on this axis, for completeness: branch coverage
> additionally rewrites any *surviving* assert to `assert(true)` (or `assume`
> under `--cov-assume-asserts`) at `goto_coverage.cpp:2254-2264`; path coverage
> does not (§11.2). That changes what is *solved*, not what is *counted*.

### 11.5 `unchecked { }` — the conclusion holds, the stated reason is too narrow

**`#sol_unchecked` is written in exactly one place** — the frontend's block
walker, `solidity_convert_stmt.cpp:145-148` (set/save the
`in_unchecked_block` flag on `nodeType == "UncheckedBlock"`), `:157-158`
(`if (in_unchecked_block) cl.set("#sol_unchecked", "1")` on each contained
statement's location), `:221` (restore). It is a *location attribute on
top-level statements of the block*, nothing more.

**It is read in exactly three places, all in `goto_check.cpp`:**

| site | check | flag that must be ON for the read to be reachable |
|---|---|---|
| `:281-282` | `cast_overflow_check`, Solidity narrowing arm | `--narrowing-check` (the Solidity arm is gated on `disable_narrowing_check` at `:274-275`, and `no-narrowing-check` is set by the `no-standard-checks` expansion at `esbmc_parseoptions.cpp:3703`) |
| `:338-341` | `overflow_check` | any of `--overflow-check` / `--unsigned-overflow-check` / `--ub-shift-check` (early return at `:328-331`) |
| `:1233-1235` | the Solidity narrowing-**assignment** arm | `--overflow-check` or `--unsigned-overflow-check` |

> **VERIFIED, with a correction.** The claim "`#sol_unchecked` is read only by
> the overflow checks, which default off" is **too narrow**: `--narrowing-check`
> (`options.cpp:217-220`) and `--ub-shift-check` (`options.cpp:686-688`) also
> reach a read site, and neither is an overflow flag. All four are nevertheless
> OFF by default for Solidity — the umbrella expansion at
> `esbmc_parseoptions.cpp:3683-3704` sets `no-narrowing-check` (`:3703`), and
> `--overflow-check` / `--unsigned-overflow-check` / `--ub-shift-check` are
> separate opt-ins that no collector in this project has ever passed.
>
> **And the conclusion is stronger than "byte-identical by default".** By §11.2,
> every one of those three sites ends in `add_guarded_claim` → a
> single-successor ASSERT. So even with all four flags ON, `unchecked { }` and
> a normal block differ only in how many ASSERT instructions exist between the
> same two decisions. **The enumerated path set, every `enc`, every `cnt` and
> every content-addressed path id are identical with and without the
> `unchecked` keyword, under every flag combination this tool has.** There is no
> Panic modelling anywhere in the tree — no site constructs a
> `Panic(0x11)`/`Panic(0x12)` revert edge; the only synthesised revert edges are
> `_ESBMC_sol_mark_revert` from `solidity_convert_modifier.cpp:1003-1011` (for
> `require`/`revert`) and the ABI value gate at `goto_coverage.cpp:4271-4345`.

## 12. `--all-witnesses` IS wired for path coverage, and the report throws all but the first away

### 12.1 Read sites (do not trust the help text — it says nothing about coverage)

Complete list over the files read in full:

- `options.cpp:374-378` (declaration) and `:379-382` (`--max-witnesses`,
  `default_value(16)`).
- `esbmc_parseoptions.cpp:750-771` — validates `--max-witnesses >= 0`
  (`:752-760`), logs and forces `multi-property` (`:762-766`), and forces
  `base-case` unless the user picked `--forward-condition` / `--inductive-step`
  (`:769-770`).
- **`bmc.cpp:2987`** — `const bool enumerate =
  options.get_bool_option("all-witnesses");`, inside `job_function`'s
  `P_SATISFIABLE` arm in `multi_property_check`. `--max-witnesses` is read two
  lines later (`:2991-2994`; `0` means `SIZE_MAX`).
- `bmc.cpp:520-521` — `report_multi_property_trace` keeps the legacy
  single-`[Counterexample]` shape only when
  `witnesses.size() <= 1 && stop_reason == Disabled`.

There is **no coverage-mode gate on any of them.** Path coverage forces
`multi-property` and `base-case` (`esbmc_parseoptions.cpp:4139-4140`), which is
exactly the condition `run_thread` uses to route into `multi_property_check`
(`bmc.cpp:2535-2542`). Every path claim that comes back SAT therefore enters the
enumeration loop at `bmc.cpp:3028`.

> **So `--all-witnesses` is wired for `--solidity-path-coverage`, and this is a
> third case distinct from both §4 and §10.4: here the help text is silent
> (`options.cpp:374-378` says only "after a property fails"), the wiring is
> real, and the effect is real — but it does not reach the artefact this
> project reads.** See §12.2.

### 12.2 Where the extra witnesses go — Foundry yes, `cov-report.json` no

The per-witness body of the loop (`bmc.cpp:3028-3508`) emits, **once per
witness**, keyed on `w.ce_index = ce_counter++` (`:3041`):

| consumer | site | per witness? |
|---|---|---|
| `--cex-output` (`N-<name>` files) | `:3046-3050` | yes |
| GraphML / YAML witness | `:3054-3062` | yes |
| `--generate-testcase` XML | `:3063-3067` (metadata once, `:3020-3021`) | yes |
| `--generate-html-report` / `--generate-json-report` | `:3068-3072` | yes |
| pytest / ctest collectors | `:3073-3076` | yes |
| **`--generate-foundry-testcase`** | **`:3077-3080`** (`foundry().collect(...)`) | **yes** |
| stdout multi-witness block + `Summary:` footer | `:3595-3599` → `:531-611` | yes |
| **the path-coverage CE payload (`cov-report.json`)** | **`:3087`** | **NO — `if (is_path_cov && witnesses.empty())`** |
| `reached_claims` / covered-set write-back | `:3523-3535`, `:3552-3553` | no ("multiple witnesses are still one claim", `:3523`) |

> **The single line that decides this is `bmc.cpp:3087`:
> `if (is_path_cov && witnesses.empty())`.** The harvest that builds
> `goto_coveraget::path_ce` — `inputs`, `env`, `entry_storage`, `final_state`,
> the whole CE→generalisation interface the stage-2 ladder consumes — runs for
> the FIRST witness only, by explicit design (`:3082-3086`: "Only the FIRST
> witness of a claim is recorded: one CE per complete path is what the report
> contracts for"). Witnesses 2..16 are built (`build_goto_trace` at `:3031`),
> printed, and handed to the Foundry generator, and then discarded.
>
> **Consequence for stage 2 as planned:** `--all-witnesses` today gives the
> refinement ladder nothing it can read from `cov-report.json`. It gives 16
> extra *Foundry tests per path* (`:3077-3080` is unconditional on witness
> index) and 16 extra stdout blocks. Turning it into the raw material the ladder
> wants is a change at `bmc.cpp:3087` — make `path_ce` a vector, or record a
> per-witness `inputs` map — not a change to the invocation.
>
> Note also `bmc.cpp:3039-3040`: `collect_nondet_values` runs **only** when
> `enumerate` is true, and its own comment calls it "non-trivial on coverage
> runs with many claims and large arrays". So the first witness's cost also
> rises when the flag is on.

### 12.3 Cost: extra `dec_solve()` on the same instance, no re-encoding

`bmc.cpp:3490-3507`: on the first extra witness the loop does
`solver_ptr->push_ctx()` once (`:3498-3502`), then per witness
`assert_expr(make_blocking_expr(...))` (`:3505-3506`) and
`enum_result = solver_ptr->dec_solve()` (`:3507`). A single matching
`pop_ctx()` after the loop (`:3520-3521`).

> **N witnesses cost N-1 extra `dec_solve()` calls and N-1 extra
> `build_goto_trace` + `collect_nondet_values` passes, on ONE already-encoded
> solver instance.** There is no second `generate_smt_from_equation`, no second
> symex, and no second per-claim `symex_slicet` run. The comment at `:2984-2986`
> states it: "No re-encoding: we only push extra assertions onto the live
> solver." The stop reason is recorded explicitly (`Unsat` / `CapHit` /
> `NoInputs` / `Error` / `Disabled`, `:2998-3000`, `:3477`, `:3486`,
> `:3512-3516`) so "16 witnesses" and "exhausted" are distinguishable.

### 12.4 Interaction with what path coverage already forces

- **`--multi-property`:** already forced by the dispatch
  (`esbmc_parseoptions.cpp:4140`), so `--all-witnesses` setting it again at
  `:766` is a no-op — but the "auto-enabling `--multi-property`" status line at
  `:764-765` **does** print, which is misleading rather than wrong.
  `was_multi` (`:762-763`) reads `options.get_bool_option("multi-property")`
  and `cmdline.isset("multi-property")`; `options` at that point holds only what
  `options.cmdline(cmdline)` copied in at `:412`, i.e. the user's own flags, and
  `get_command_line_options` runs at `:963` — long before
  `process_goto_program`'s dispatch at `:4118`. So under
  `--solidity-path-coverage --all-witnesses` the tool announces that it is
  enabling something the mode had already forced.
- **`base-case`:** `:769-770` sets it; the dispatch sets it too (`:4139`).
  No conflict.
- **`keep-verified-claims`:** path coverage forces it false (`:4141`).
  `--all-witnesses` never touches it. The post-violation cleanup
  (`bmc.cpp:3611-3615`) runs *after* the whole enumeration loop, so it cannot
  truncate it.
- **the per-claim job loop:** enumeration is entirely inside `job_function`,
  so it is per claim. Under `--parallel-solving` each job owns its own solver
  (`bmc.cpp:2851-2855`), and under `--smt-during-symex` they share
  `runtime_solver` — which is precisely why the `push_ctx`/`pop_ctx` pair exists
  (`:3490-3496` says so).
- **`--multi-fail-fast`:** irrelevant — path coverage rejects it outright
  (`esbmc_parseoptions.cpp:4126-4134`).
- **the covered set:** written once per claim, not per witness
  (`bmc.cpp:3552-3553`), so enumeration cannot inflate it.

## 13. `--coverage-covered-set` across CONFIGURATIONS — the exact field list, and the one bound it gets wrong

### 13.1 The fingerprint, field by field

Computed once at `goto_coverage.cpp:3057-3094`, published as
`path_cov_fingerprint` (`:3093`), written into the file at `:156`, and compared
on load at `:3107-3121` with a **fail-closed** discard (no migration, `:3110-3113`).

The complete input list, in order:

| field | `goto_coverage.cpp` | value |
|---|---|---|
| seed `"path-cov-fingerprint"` | `:3060` | constant |
| `schema=` | `:3061` | `PATH_ID_SCHEMA_VERSION` = 1 (`:3058`) |
| `decisions=` | `:3062` | `DECISION_SET_VERSION` = 4 (`:3059`) |
| `sc_max=` | `:3063` | `SC_DECISION_MAX` = 12 (`:2047`) |
| `loop_bound=` | `:3067` | `path_cov_unwind` |
| `call_depth=` | `:3068` | `path_cov_unwind` (same number) |
| `reentry_depth=` | `:3069` | `path_cov_unwind` (same number) |
| `goal_cap=` | `:3070` | `path_cov_max_goals` |
| `contract=` | `:3071` | `scope_contract` |
| `src:<basename>:<full file contents>` | `:3074-3092` | every input file, **sorted** (`:3078`), by CONTENT not mtime |

**So `--path-cov-max-goals` is in it** (confirming §9 item 3's premise), **and so
is the scope**: `scope_contract` is set only by
`esbmc_parseoptions.cpp:4155-4156` — `--contract C` **and not**
`--coverage-whole-unit`. Hence `--contract C`, `--contract C
--coverage-whole-unit`, and a bare whole-unit run produce three different
fingerprints, and their covered sets can never be unioned by accident.

### 13.2 What is NOT in it

- **`--focus-function`** — no read site anywhere in the fingerprint block. The
  option is consumed by the frontend harness (`options.cpp:140-145`) and by
  `audit_entry_liveness` (`goto_coverage.cpp:1694-1711`, called with
  `options.get_option("focus-function")` at `bmc.cpp:1134`).
- **the transaction bound `--solidity-max-tx`** — absent.
- **`--coverage-multi-tx`** — absent (and by §10.4 it has no effect here anyway).
- **the bounding strategy** (`--incremental-bmc` / `--k-induction` /
  `--termination` / `--falsification`) — absent.
- **the check flags** (`--overflow-check`, `--bounds-check`, …) — absent, which
  by §11.2 is harmless: they cannot change a path.

### 13.3 Is unioning across configurations sound?

**The path IDs themselves are configuration-independent, and that is why the
fingerprint gets away with it.** A stable id is
`hex64(fnv1a("exit:" + <exit location>, idh))` (`goto_coverage.cpp:4773-4774`)
where `idh` folds, per decision, `fnv1a(site + "#" + sub)`, the polarity, and
the per-site occurrence count (`step_id`, `:4698-4712`), seeded with
`fnv1a("unit:" + <unit id>)` (`:4628`). Every one of those inputs comes from the
unit's own body. `--focus-function` and `--solidity-max-tx` change the
`_ESBMC_Main_*` dispatcher, not any unit body, so **the same path has the same
id under a per-method sweep and under a whole-contract run.** And only `'F'`
(a real counterexample) is ever persisted (`:158-169`).

So a union across those two axes is *type-correct*. It is nevertheless
**not a measurement of any single configuration**, in three concrete ways:

1. **The reported percentage is the union's, not the run's.** The numerator at
   `bmc.cpp:1198-1202` counts `path_witnessed_earlier(k) ||
   reached_claims.count(...)`, and the file stores a **flat array of ids**
   (`goto_coverage.cpp:157-172`) with no per-id record of which configuration
   witnessed it. So "Path Coverage: 82%" after a sweep + a whole-contract run is
   a statement no single invocation can reproduce, and the file cannot be
   decomposed back into the runs that produced it.
2. **The artefacts do not travel with the id.** A skipped path emits no assert
   (`goto_coverage.cpp:7446-7450` `continue`s before `insert_assert`), hence no
   Foundry test and no CE payload in this run; the JSON says so explicitly
   (`witnessed_in_earlier_round`, `bmc.cpp:1534`, and `payload_absent_reason`,
   `:1793-1801`). If the run that witnessed a path was `--focus-function f`,
   the only test for that path is the one that run emitted, under that harness.
3. **`loop_bound` records a bound the run may not have used.** `path_cov_unwind`
   is fixed at `esbmc_parseoptions.cpp:4288-4293` (explicit `--unwind`) or
   `:4296` (the pass's own 4). Under `--incremental-bmc` / `--k-induction`,
   `do_bmc_strategy` then **overwrites `unwind` with the current `k_step`** at
   `:2975` / `:3039` / `:3104` — after instrumentation, so `path_cov_unwind` and
   the fingerprint keep the pre-strategy number. Two runs that share `--unwind`
   but explore different `k_step` therefore share a fingerprint and union
   freely. This is §6/§10.4's disagreement showing up in the cache: the field is
   named `loop_bound` and does not hold the loop bound the run ran at.

> **VERDICT.** Unioning a covered set across a per-method sweep and a
> whole-contract run is **not unsound in the id sense** — the ids match by
> construction and only real witnesses are stored — but the resulting file is
> **an unattributable union**, and the coverage number computed from it belongs
> to no configuration. If the escalation ladder is going to use it that way, the
> file needs a per-id provenance field (which configuration witnessed it), or
> the sweep and the whole-contract run need separate covered-set paths and an
> explicit merge step outside the tool. The one field that is *wrong* rather
> than merely absent is `loop_bound` under any bounding strategy (item 3).
>
> One incidental: the incremental write-back at `bmc.cpp:3552-3553`
> (`if (is_path_cov && !path_covered_outpath.empty())`) is **not** gated on the
> stage-2/3 modes, while the final write-back at `:1325-1331` is (it sits inside
> the non-certification `else` block opened at `:1183`). In
> `--path-cov-certify` / `--path-cov-outer-box` / `--path-cov-assert` mode
> `path_stable_id` is empty (those branches `continue` past the insertion loop
> at `goto_coverage.cpp:7346`), so the rewrite reproduces the loaded id list
> byte for byte — harmless, but it does rewrite a file those modes have no
> business touching.
