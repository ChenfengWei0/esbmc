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
