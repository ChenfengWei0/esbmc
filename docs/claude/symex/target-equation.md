# Target Equation — The Output Side

`src/goto-symex/symex_target.h` (abstract interface) and
`src/goto-symex/symex_target_equation.{cpp,h}` (concrete
accumulator). This is where symex deposits each SSA step and where
the SMT converter later reads them from.

## Abstract interface — `symex_targett`

Defined in `symex_target.h`. The six operations symex emits:

| Method | When called | Payload |
|---|---|---|
| `assignment(guard, lhs, orig_lhs, rhs, orig_rhs, src, stack, hidden, loop_num)` | Every SSA assign (including phi, guard renamings, return-value plumbing) | lhs=rhs with guard |
| `assumption(guard, cond, src, loop_num)` | `__ESBMC_assume`, unwinding assumption, trivially-true claim converted | `guard ⇒ cond` |
| `assertion(guard, cond, msg, stack, src, loop_num)` | Every `claim` that wasn't pruned | `guard ⇒ cond`; msg surfaces in CE |
| `output(guard, src, fmt, args)` | `printf` and friends during symex tracing | fmt + args |
| `branching(guard, cond, src, hidden, loop_num)` | Taken branches when `--witness-output-yaml` is on | For witness emission |
| `renumber(guard, symbol, size, src)` | `realloc` re-numbering | Updates the pointer's stored size |

`symex_targett::sourcet` bundles `(program, pc, thread_nr)` — the
instruction location that produced the step. Used by trace builders
to recover source-level info.

`symex_targett::clone()` lets the reachability tree duplicate the
target when it duplicates an execution_state.

## Concrete accumulator — `symex_target_equationt`

`symex_target_equation.h:18`. Owns `std::list<SSA_stept> SSA_steps`.
Each of the six virtual methods appends to the list.

### `SSA_stept` shape

`symex_target_equation.h:92`:

```cpp
struct SSA_stept {
  sourcet source;
  goto_trace_stept::typet type;  // ASSIGNMENT / ASSUME / ASSERT / OUTPUT / RENUMBER / SKIP / BRANCHING
  vector<stack_framet> stack_trace;

  expr2tc guard;

  // ASSIGNMENT
  expr2tc lhs, rhs;
  expr2tc original_lhs, original_rhs;

  // ASSUME/ASSERT/BRANCHING
  expr2tc cond;
  string comment;

  // OUTPUT
  string format_string;
  list<expr2tc> output_args;

  // populated during convert():
  smt_astt guard_ast, cond_ast;
  list<expr2tc> converted_output_args;

  bool ignore;    // slicer set this to true → skip during convert
  bool hidden;    // don't surface in counter-example
  unsigned loop_number;
};
```

Two key bits every step carries:

- **`ignore`** — true means the slicer ruled this step unused. The
  converter emits a no-op AST instead of the real one.
- **`hidden`** — the trace builder skips this when constructing a
  counter-example. Phi steps, guard-identifier steps, and internal
  assignments are typically hidden.

### `original_lhs` / `original_rhs`

Same expression as `lhs`/`rhs`, but pre-rename. Used by the trace
builder to recover a human-friendly representation (e.g. turn
`x?0!0&0#7` back into `x`). The full rename path is kept because
witness replay and test generation need both views.

## `convert()` — feeding the SMT layer

`symex_target_equation.cpp:154`. The core loop:

```cpp
void convert(smt_convt &smt_conv) {
  ast_vec assertions;
  smt_astt assumpt_ast = smt_conv.convert_ast(gen_true_expr());

  for (auto &step : SSA_steps)
    convert_internal_step(smt_conv, assumpt_ast, assertions, step);

  if (!assertions.empty())
    smt_conv.assert_ast(smt_conv.make_n_ary_or(assertions));
}
```

The **or** is what makes multi-assertion BMC work: the formula is
SAT iff *any* assertion step fails, so the solver will find the
first violation (or return unsat if none can fail).

### `convert_internal_step`

`symex_target_equation.cpp:166`:

- **Ignored step** → set `cond_ast = true`, `guard_ast = false`; bail.
- **ASSUME/ASSERT/BRANCHING** → convert `cond` to AST. Assert steps
  additionally wrap as `assumpt_ast ⇒ cond` (so earlier assumes gate
  this assertion) and push the *negation* into `assertions`. Assume
  steps conjoin into `assumpt_ast` so later steps see them.
- **ASSIGNMENT** → `smt_conv.convert_assign(cond)` where `cond` is the
  equality `lhs == rhs`. The assign convention is a top-level
  equality in `cond` — not a reuse of the `lhs`/`rhs` fields — the
  assignment method fills `cond` before pushing.
- **OUTPUT** → convert every non-constant arg to a fresh
  `symex::output::N` symbol and emit an assignment; the symbol is
  what the trace builder reads.
- **RENUMBER** → `smt_conv.renumber_symbol_address(...)` — tells the
  memory model that the object the pointer refers to has a new
  size/identity after `realloc`.

The assumption chain makes forward-implication natural: if an
earlier assume said `x > 0`, and a later assertion asks `x > -1`,
the solver sees `(x > 0) ⇒ (x > -1)` which is trivially unsat when
negated.

### What "assignment cond is an equality" means

The `assignment(guard, lhs, ..., rhs, ...)` method pushes an
`SSA_stept` whose `cond` field is set to `equality2t(lhs, rhs)`. The
converter hands that to `smt_conv.convert_assign(cond)`. Inside the
SMT backend, `convert_assign` knows it's a pure equality and can use
a native "make a new AST variable aliased to this value" primitive
instead of a general boolean equality — which matters for solver
performance.

## Bookkeeping helpers

- **`check_for_duplicate_assigns()`** — iterates the equation and
  flags any two assignments with the same lhs. Used under
  `--double-assign-check` as a sanity probe.
- **`clear_assertions()`** — zeros out every assertion (used by some
  incremental modes that want to re-run without the previous claim
  set).
- **`count_ignored_SSA_steps()`** — size of `{s : s.ignore}`; used by
  the slicer's progress banner.
- **`get_SSA_step(n)`** — O(n) positional lookup; for debugging only.
- **`output(ostream)`** / **`short_output(ostream)`** — pretty-print
  the equation. `--program-only` triggers the long form;
  `--ssa-trace` prints one step at a time as they're converted.
- **`push_ctx()` / `pop_ctx()`** — maintain a stack of (size,
  output_count) pairs so incremental mode can snapshot the equation,
  explore a path, and roll back.

## `reconstruct_symbolic_expression`

`symex_target_equation.cpp:428` with helper `replace_rec` at `:407`.
Walks an expression, replaces SSA-renamed symbols by the expression
that was last assigned to them (chasing back through the SSA_steps).
Used for:

- Counter-example building — reconstructs "the actual expression
  that flowed into this assertion" from the renamed version.
- `keep_local_variables` mode — decide whether to walk past function
  boundaries or stop at the nearest frame-local.

It's the reverse of SSA — given a name, find the tree of assignments
that produced it.

## Targetting patterns (when not using `symex_target_equationt`)

The abstract interface exists so alternate sinks can be plugged in:

- **`runtime_encoded_equationt`** — subclass that converts each step
  to SMT immediately, enabling `--smt-symex-guard` /
  `--smt-symex-assert` / `--smt-symex-assume` to query the solver
  mid-symex for pruning decisions. Lives alongside
  `symex_target_equationt` and owns its own smt_convt.
- **`schedule_target`** — used under `--schedule` to merge every
  explored interleaving into a single equation; `reachability_tree`
  passes this to symex as the target, each replay sharing the same
  sink.

## Step types and their phi/guard encoding

When you read an SSA dump (`--program-only --no-slice`):

```
<guard> => <cond>                    # ASSUME/ASSERT
<guard> => ( lhs = rhs )             # ASSIGNMENT (cond = equality)
```

The guard is usually a conjunction of branch conditions and the
k-induction/base-case selector. Phi assignments have the form
`lhs = if(guard_diff, taken_rhs, fallthrough_rhs)`.

Hidden assignments (L1 guard symbols, internal bookkeeping)
appear in the dump under `--program-only` but not in
counter-examples.

## Reading a dump

`--program-only` prints the equation. The typical shape is:

```
  ---SSA---
  (gurard=true) ASSIGN: y?0!0&0#1 = 5
  (guard=x>0)   ASSIGN: y?0!0&0#2 = 10           <- taken branch
  (guard=true)  ASSIGN: y?0!0&0#3 = if(x>0, y?0!0&0#2, y?0!0&0#1)   <- phi
  (guard=true)  ASSERT: y?0!0&0#3 > 0, msg="...", file=..., line=...
  ---Total assignments: 4---
```

To understand "why is a verification failing", read from top to
bottom. To understand "why is a verification succeeding", consider
whether the assertion trace path is actually reachable (often an
unwinding bound silently trims it).

## Common pitfalls

- **"I added an SSA step but the solver didn't see it"** — check
  `step.ignore` didn't get set by the slicer, and
  `SSA_steps.back().type` matches the call you made. If you called
  `output()` by mistake when you meant `assignment()`, the step won't
  constrain anything.
- **"Assignment to a global looks wrong in the CE"** — the trace
  builder uses `original_lhs`, which captures the pre-rename name
  including type. If the frontend passed the wrong type (rare but
  possible), the original is also wrong. Use `--show-goto-functions`
  to verify the frontend's output.
- **Incremental mode misses**: if `push_ctx` was called but
  `pop_ctx` wasn't, the equation retains all steps from the
  abandoned branch. `--smt-symex-*` modes push/pop around each
  query — if a probe crashes mid-query, the stack may be left in an
  inconsistent state.
- **"The number of assignments in the log doesn't match
  `--show-claims`"** — claims reported by `--show-claims` include
  trivially-true ones that never reach `assertion()`. The
  `total_claims` / `remaining_claims` counters live on
  `symex_resultt`, not on the equation.
