# Slice — Equation Pruning Before SMT

`src/goto-symex/slice.{cpp,h}`. Runs between symex and SMT conversion.
Drops SSA steps that can't influence any assertion, so the solver sees
a smaller formula. A wrong slice silently hides bugs — hence
`--no-slice` is a standard diagnostic flag.

## Three slicers

All derive from `slicer : public ssa_step_algorithm`.

| Class | Purpose |
|---|---|
| `simple_slice` | Ignore every step after the last assertion (cheap; always safe). |
| `claim_slicer` | Keep one chosen claim, ignore all others. Used for multi-property investigation. |
| `symex_slicet` | The real slicer — reverse-dataflow over SSA, keeping only steps transitively feeding an assertion. |

### `simple_slice`

```cpp
bool run(SSA_stepst &) {
  // Walk from end backwards, find the last ASSERT,
  // mark everything after it as ignore=true.
}
```

Rationale: nothing after the final assertion can affect whether any
assertion holds. The cost is a single pass; used even when
`--no-slice` is set (because it's purely a termination detail, not
dataflow pruning).

### `claim_slicer`

Takes a `claim_to_keep` index (from `--show-claims`), walks the
equation, and sets `ignore=true` on every assertion except the chosen
one. Used under `--multi-property --claim N` to investigate a
specific claim without the noise of others failing first.

### `symex_slicet` — the dataflow slicer

`slice.h:83` + `slice.cpp` (not shown above). Reverse walk:

```cpp
unordered_set<string> depends;   // symbols the remaining formula depends on

for (auto &step : reverse(eq)) {
  if (step.ignore) continue;
  run_on_step(step);
}
```

Dispatches per step type:

- **ASSERT** — never ignored. Its `cond` symbols are added to
  `depends` (the "seed"): any SSA variable appearing in an assertion
  must be kept.
- **ASSUME** (if `slice_assumes` is on) — if any of its cond's
  symbols are in `depends`, the assume is kept and its guard + cond
  symbols are added to `depends`. Otherwise `ignore=true`.
- **ASSIGNMENT** — if the lhs symbol is in `depends`, keep, and add
  the rhs's symbols + the guard's symbols. Otherwise `ignore=true`.
- **RENUMBER** — same rule as ASSIGNMENT, keyed on the renumbered
  symbol.

At the end, every step marked `ignore=true` is skipped during
`convert_internal_step` (its guard becomes false, cond becomes true —
effectively a no-op).

### "Reverse taint" over guards

When an assume is kept, all symbols in its condition get added to
`depends`. This is the "reverse taint" comment in the header: even
though an assume only constrains, its presence in the dependency
graph means its condition's inputs also have to be kept, because
otherwise the assume would never be generated in the first place —
and removing the assume changes the formula.

### `slice_nondet`

Controlled by
`!(generate-testcase || generate-ctest-testcase)`. When true, nondet
symbols that don't flow into an assertion are dropped. When false
(testcase generation), every nondet is kept so the witness / ctest
output can replay the exact nondet values that triggered the trace.

## Options

| Option | Effect |
|---|---|
| `--no-slice` | Disable `symex_slicet`. `simple_slice` still runs. |
| `--slice-assumes` | Make the dataflow slicer also drop irrelevant assumes. Default: off, because a kept assume can prune SAT state the solver would otherwise waste time on. |
| `--slice-by-trace` | (internal) Use an existing goto-trace to restrict the slice. |
| `config.no_slice_names` / `config.no_slice_ids` | Symbol allowlist that is never sliced — used by the frontend to pin specific globals. |

## When slicing hides a bug

The slicer is supposed to be sound — "no step that could affect an
assertion is dropped". But the dependency set is driven by
*syntactic* lhs-rhs tracking, not a semantic analysis. Two known
unsoundness windows:

1. **Array indexing conflation** — the slicer tracks
   `a` as a dependency, not `a[i]` per-index. Usually fine; but if
   the solver side *does* treat `a[i]` per-index, an earlier
   `a[0] = nondet_int()` might be dropped when only `a[1]` is later
   read, because `a` appears to be overwritten between.
2. **Indirect writes via pointer** — an assignment through `*p = v`
   where the slicer doesn't know which object `p` refers to can
   either be kept (pessimistic) or dropped (unsound). The current
   implementation is pessimistic here because the lhs is a compound
   expression containing `p` — `p` itself carries the transitive
   write dependency.

History in this codebase: the `feedback_silent_truncation_flags`
memory entry is about `--unwind N --no-unwinding-assertions`, a
different unsoundness — but the same category of "silent state drop
before SMT". The slicer's counterpart is whenever
`--no-slice` flips a verdict.

## Diagnostic recipes

- **"Did the slicer drop the step that produced this symbol?"**
  `--program-only --no-slice` vs. `--program-only` — diff the two
  equations. Steps present in the first but not the second were
  sliced.
- **"`--no-slice` changes my verdict."** Either (a) the bug is real
  and the slicer is wrong (rare — check if your program uses
  aliasing in unusual ways), or (b) the solver is timing out on the
  bigger formula without the slicer's help.
- **"`--slice-assumes` changes the count of assertions."** The
  multi-property mode's counter is computed post-slice. If a
  sliced-away assume was strengthening an assertion to trivially
  true, removing it makes that assertion newly survive to SMT.

## Integration with bmct

`bmct::run_thread` (`src/esbmc/bmc.cpp`) calls into the slicer before
`eq->convert(smt_conv)`. The order is:

1. `get_next_formula()` — symex produces the full equation.
2. `simple_slice` → `symex_slicet` → optional `claim_slicer`.
3. `eq->convert(smt_conv)` — emit to the solver.
4. Solve.
5. If SAT, build a counter-example from the (unsliced) trace using
   the equation's original steps (ignore flags are per-step, not a
   deletion).

Because `ignore` is a flag, not a deletion, the slicer preserves the
full trace for witness / testcase generation even when the slice
narrows the SMT formula.

## Common pitfalls

- **"New option doesn't affect slicing"** — the slicer reads from
  `optionst` via its constructor; make sure the option is read
  before the slicer is constructed, otherwise the flag default wins.
- **"Slicer runs slow on big equations"** — it's
  O(steps × avg expr size) because `get_symbols` walks every step's
  expression. A linter-style pre-pass that cached per-step symbol
  sets would help; not currently implemented.
- **"Why did a fresh nondet disappear?"** — `slice_nondet` is on.
  Turn it off with `--generate-testcase` (which forces preservation)
  or debug by comparing `--no-slice` output.
