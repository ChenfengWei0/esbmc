# `bmct` — The BMC Orchestrator

`src/esbmc/bmc.cpp` (1990 LOC). The top-level driver that glues
together goto-programs, goto-symex, slicing, and SMT. Entry
(`start_bmc`) is called from `esbmc_parseoptions.cpp` after
command-line parsing and goto-program setup.

## Pipeline

```
esbmc main
  └── parse options, load/build goto_functions
  └── run goto transforms (goto_k_induction, goto_check, ...)
  └── bmct(goto_funcs, options, context)              ← ctor
        └── configure algorithms[] (simple_slice / symex_slicet / assertion_cache)
        └── instantiate symex = reachability_treet(...)
        └── maybe instantiate runtime_solver (for --smt-during-symex)
  └── bmct::start_bmc()
        └── run(eq)
              └── loop over thread interleavings:
                    run_thread(eq)
                      └── symex->get_next_formula()  ← produces eq
                      └── for a in algorithms: a->run(eq->SSA_steps)   ← slice
                      └── run_decision_procedure(runtime_solver, *eq)
                            └── generate_smt_from_equation (calls eq->convert)
                            └── smt_conv.dec_solve() → SAT/UNSAT
                    maybe bidirectional_search on SAT
              until symex->setup_next_formula() returns false
        └── report_trace(res, *eq)
        └── report_result(res)
        → return result
```

## `bmct` construction — `bmc.cpp:49`

```cpp
bmct::bmct(goto_functionst &funcs, optionst &opts, contextt &ctx)
  : options(opts), context(ctx), ns(context)
{
  // Init counters
  interleaving_number = 0;
  interleaving_failed = 0;

  // Algorithms (slicers) to run over the SSA equation before SMT
  if (!options.get_bool_option("no-cache-asserts") && ... )
    algorithms.emplace_back(new assertion_cache(config.ssa_caching_db));

  if (opts.get_bool_option("no-slice"))
    algorithms.emplace_back(new simple_slice);
  else
    algorithms.emplace_back(new symex_slicet(options));

  if (opts.get_bool_option("ssa-features-dump"))
    algorithms.emplace_back(new ssa_features);

  // Symex + maybe runtime solver
  if (options.get_bool_option("smt-during-symex")) {
    runtime_solver = create_solver("", ns, options);
    symex = new reachability_treet(funcs, ns, options,
      shared<runtime_encoded_equationt>(ns, *runtime_solver), ctx);
  } else {
    symex = new reachability_treet(funcs, ns, options,
      shared<symex_target_equationt>(ns), ctx);
  }
}
```

Two modes by `--smt-during-symex`:

- **Off** (default) — symex emits into a `symex_target_equationt`;
  solver is created lazily at `run_thread` time.
- **On** — symex emits into a `runtime_encoded_equationt`, which
  converts each step to SMT immediately. The runtime_solver is
  kept for the whole run, enabling `--smt-symex-guard` /
  `--smt-symex-assert` / `--smt-symex-assume` pruning during
  symex.

## `start_bmc` — the single public entry

`bmc.cpp:1133`:

```cpp
smt_convt::resultt bmct::start_bmc() {
  shared<symex_target_equationt> eq;
  resultt res = run(eq);
  if (!options.get_bool_option("multi-property"))
    report_trace(res, *eq);    // normal CE trace; multi-property does its own
  report_result(res);
  return res;
}
```

## `run` — thread-interleaving outer loop

`bmc.cpp:1144`. If `--schedule` is set, directly call `run_thread`
and return. Otherwise:

```cpp
do {
  ++interleaving_number;
  config.ssa_caching_db.clear();     // unless --no-cache-asserts

  res = run_thread(eq);

  if (res == SAT) {
    if (smt-model option) runtime_solver->print_model();
    if (bidirectional option) bidirectional_search(*runtime_solver, *eq);
  }

  if (res && !all-runs) return res;  // early out on first CE

} while (symex->setup_next_formula());  // advance to next interleaving
```

`symex->setup_next_formula()` walks the reachability tree back to
an unexplored context-switch and returns true if more interleavings
remain.

With `--all-runs`, keeps going even after finding a SAT (counts
`interleaving_failed`). Otherwise returns on first failure.

## `run_thread` — one schedule's full pipeline

`bmc.cpp:1340`. The per-interleaving driver:

1. **Symex** — call `symex->get_next_formula()` (or
   `generate_schedule_formula()` under `--schedule`). Returns a
   `symex_resultt` containing the target equation.
2. **Dump if `--program-only` / `--program-too`** — call
   `show_program(*eq)` to print the SSA. Under `--program-only`,
   return `P_SMTLIB` without solving.
3. **Algorithms** — iterate `algorithms[]`, calling
   `a->run(eq->SSA_steps)` on each. These are the slicers from
   the ctor.
4. **Count remaining asserts** — after slicing.
5. **LTL mode** — `ltl_run_thread(*eq)` for LTL verification.
6. **Create solver** (if not `--smt-during-symex`) —
   `runtime_solver = create_solver("", ns, options)`.
7. **Multi-property branch** — under `--multi-property` +
   (base-case or inductive-step with loop invariants), route to
   `multi_property_check` instead of the normal path.
8. **Main path** — `run_decision_procedure(*runtime_solver, *eq)`.
9. Exception handlers catch `std::string`, `const char*`,
   `std::bad_alloc`, each → `P_ERROR`.

## `run_decision_procedure` — SMT solve

`bmc.cpp:239`:

```cpp
resultt run_decision_procedure(smt_convt &smt_conv, ...) {
  if (enable-keep-alive option) kick off keep_alive thread;

  generate_smt_from_equation(smt_conv, eq);    // eq->convert(smt_conv)

  if (smt-formula-only / smt-formula-too) {
    dump smt_conv.dump_smt() to stdout/file;
    if smt-formula-only: return P_SMTLIB;
  }

  resultt dec_result = smt_conv.dec_solve();
  return dec_result;
}
```

The `smt_conv.dec_solve()` is the backend-specific call (z3_convt /
cvc5_convt / bitwuzla_convt's `dec_solve` — see [solvers/backends.md](solvers/backends.md)).

## k-induction phase coordination

`start_bmc` + `run` don't directly orchestrate k-induction B/F/I
phases. Instead:

- `--k-induction` or `--k-induction-parallel` is processed at
  command-line parse time in
  `esbmc_parseoptions.cpp::do_bmc_strategy` (not in this file).
- `do_bmc_strategy` spawns three separate `bmct` runs (one per
  phase) or three subprocesses, each with different boolean options
  (`base-case`, `forward-condition`, `inductive-step`) set.
- Each `bmct` sees its phase via `options.get_bool_option("base-case")`
  etc. Symex checks `k_induction && inductive_step` flags and
  shapes its execution accordingly (see
  [symex/k-induction.md](symex/k-induction.md)).

So this file's k-induction-awareness is limited to:
- `bidirectional_search` (line 1211) — only runs under
  `--inductive-step --k-induction`.
- The k-induction-driver-inserted assertion at line 1330 (below).
- Various `assertion_cache` disabling for k-induction phases
  (line 66 of ctor).

## `bidirectional_search` — CE-driven invariant synthesis

`bmc.cpp:1211`. Only active under `--inductive-step --k-induction
--bidirectional`. When the inductive step produces a SAT result
(invariant didn't hold), try to refine the invariant from the
counter-example:

1. Walk SSA steps, find the failed assertion's loop_number +
   stack_trace.
2. For each frame in the trace, find the containing loop via
   `goto_loopst`.
3. Build a new assertion encoding the CE values.
4. Insert as a new `inductive_assertion = true` instruction at
   the loop exit.
5. Re-run symex.

This is advanced territory — most runs never enter this path.

## `multi_property_check` — multi-claim reporting

`bmc.cpp:1554`. Under `--multi-property`, each assertion is
checked independently:

- For each claim index, construct a variant of the formula with
  only that claim active.
- Solve each. Aggregate results: FAILED / PASSED / UNKNOWN per
  claim.
- Report all at the end.

Variation for k-induction: `--inductive-step --loop-invariant` uses
this path to verify loop invariants as multiple properties.

## `generate_smt_from_equation`

`bmc.cpp:198`. Short helper:
```cpp
void generate_smt_from_equation(smt_convt &smt_conv, eq) {
  eq.convert(smt_conv);   // iterate SSA_steps, emit each to solver
  // ... optional smt-formula-only dump
}
```

This is where `symex_target_equationt::convert` is called (see
[symex/target-equation.md](symex/target-equation.md) §convert()).

## Reporting

Three layers of output:

- **`report_result`** (`bmc.cpp:1058`) — emit
  "VERIFICATION SUCCESSFUL" / "FAILED" / "UNKNOWN" banner.
- **`report_trace`** (`bmc.cpp:376`) — dispatch on result:
  - SAT → `error_trace(smt_conv, *eq)` — build CE, optionally
    dump witness.
  - UNSAT → `successful_trace(*eq)` — optionally dump success
    witness.
  - ERROR → log only.
- **`report_multi_property_trace`** (`bmc.cpp:478`) — multi-property
  reporting, showing per-claim PASS/FAIL with per-claim CEs.

## The `algorithms` vector

Per-run, configurable. Two slicers:

- `simple_slice` — only runs under `--no-slice`, does "everything
  after last assert is ignored".
- `symex_slicet` — full dataflow slicer, default. See
  [symex/slice.md](symex/slice.md).

Plus optional:
- `assertion_cache(config.ssa_caching_db)` — caches
  assertion→result decisions across incremental runs. Disabled for
  k-induction because of phase-dependent context.
- `ssa_features` — feature extraction pass for ML-based solver
  selection research.

## Incremental modes

- `--incremental-bmc` — iterate `--unwind` from 1 to a bound,
  solving at each step. Stops at first violation (SAT) or when
  forward-condition proves bounded termination.
- `--bidirectional` — CE-driven invariant synthesis (see above).
- `--smt-during-symex` — mid-symex SMT queries for guard/assert/
  assume pruning. Uses `runtime_encoded_equationt`.

## Trace emission

On SAT:
- GraphML witness (`--witness-output-graphml`) — SV-COMP format.
- YAML witness (`--witness-output-yaml`) — newer SV-COMP format.
- HTML trace (`--generate-html-report`) — browser-viewable.
- Test case generation (`--generate-testcase` /
  `--generate-ctest-testcase` / `--generate-pytest-testcase`).

On UNSAT:
- Correctness witness (same formats).

## Exception safety

`run_thread` catches `std::string`, `const char*`, `std::bad_alloc`.
Other exceptions propagate — crash. The typical
"failed to find ... in function_map" path aborts in-place, doesn't
throw; those errors are terminal.

## Debug recipes

- `--program-only` — dump SSA equation after slicing, do not solve.
- `--program-too` — dump SSA AND solve.
- `--show-vcc` — show verification conditions individually.
- `--smt-formula-only` — dump SMT formula (post-convert), do not
  solve.
- `--smt-formula-too` — dump AND solve.
- `--show-claims` — list every assertion with its index.
- `--double-assign-check` — sanity assertion that the same LHS isn't
  assigned twice in the SSA (shouldn't happen in SSA form; catches
  symex bugs).
- `--ssa-features-dump` — emit per-step feature vectors.

## Common pitfalls

- **"bmct spawned multiple solvers unexpectedly"** — under
  `--all-runs` or `--multi-property`, each interleaving / claim
  gets a fresh solver. Intentional; cost is per-run
  `create_solver()` overhead.
- **"VERIFICATION FAILED but no trace"** — `--result-only` skips
  trace generation.
- **"Keep-alive thread never shuts down"** — cleared at start of
  each `run_decision_procedure` call, reset to false after solve.
  If the solver hangs, the keep-alive thread logs "keep alive"
  periodically.
- **"Under `--smt-during-symex`, solve time is huge"** — this mode
  converts each SSA step to SMT immediately, so the solver sees
  intermediate states that bulk-convert doesn't. Useful for
  debugging a stuck formula, but slow.
