# Loops + k-Induction Transform

Two files cooperate to produce the k-induction machinery at the
GOTO-program level:

- `src/goto-programs/loopst.{cpp,h}` — per-loop data container
  (modified vars, head/exit iterators, size).
- `src/goto-programs/goto_loops.{cpp,h}` — loop discovery +
  modified-variable analysis.
- `src/goto-programs/goto_k_induction.{cpp,h}` — the transform
  itself (havoc + invariant-assume).

This doc also carries the precise root cause of **KNOWNBUG #1** —
the pointer-through-function havoc miss that affects ~75 Solidity
regressions.

## `loopst` — per-loop data

`loopst.h` / `loopst.cpp` (49 + 90 LOC). A simple container:

```cpp
class loopst {
  loop_varst modified_loop_vars;      // set<expr2tc>
  loop_varst unmodified_loop_vars;
  goto_programt::targett original_loop_head;
  goto_programt::targett original_loop_exit;
  std::size_t size;
};
```

Populated by `add_modified_var_to_loop(expr)` /
`add_unmodified_var_to_loop(expr)`. Read by `goto_k_induction` at
transform time.

## `goto_loopst` — loop discovery

`goto_loops.{cpp,h}`. Constructor calls `find_function_loops()`:

```cpp
void goto_loopst::find_function_loops() {
  for (it : goto_function.body.instructions) {
    if (it->is_backwards_goto()) {
      loop_head = *it->targets.begin();
      loop_exit = it;

      if (loop_head->location_number == loop_exit->location_number) {
        // Self-loop: a: IF g GOTO a;
        // Rewrite as assume(!g).
        simplify(loop_head->guard);
        it->make_assumption(not2tc(loop_head->guard));
        continue;
      }

      create_function_loop(loop_head, loop_exit);
    }
  }
}
```

`create_function_loop` then walks `[loop_head, loop_exit)`, calling
`get_modified_variables(instr, loop, function_names)` on each
instruction.

## `get_modified_variables` — THE KNOWNBUG source

`goto_loops.cpp:104-171`:

```cpp
void goto_loopst::get_modified_variables(
  goto_programt::instructionst::iterator instruction,
  function_loopst::iterator loop,
  std::vector<irep_idt> &function_names)
{
  if (instruction->is_assign())
  {
    const code_assign2t &assign = to_code_assign2t(instruction->code);
    add_loop_var(*loop, assign.target, /*is_modified=*/true);    // [1]
  }
  else if (instruction->is_function_call())
  {
    code_function_call2t &function_call =
      to_code_function_call2t(instruction->code);

    if (is_dereference2t(function_call.function))
      return;                                                     // skip fn ptrs

    add_loop_var(*loop, function_call.ret, true);                 // [2]

    // [3] NOTICE: call.operands are NOT inspected here.

    irep_idt &identifier = to_symbol2t(function_call.function).thename;

    if (find(function_names, identifier)) return;                 // recursion guard

    function_names.push_back(identifier);

    auto it = goto_functions.function_map.find(identifier);
    if (it == goto_functions.function_map.end()) {
      log_error("failed to find `{}' in function_map", ...);
      abort();
    }

    if (!it->second.body_available) return;

    for (head : it->second.body.instructions)                     // [4]
      get_modified_variables(head, loop, function_names);
  }
  else if (instruction->is_goto() || is_assert() || is_assume())
  {
    add_loop_var(*loop, instruction->guard, /*is_modified=*/false);
  }
  else if (instruction->is_end_function()) {
    function_names.pop_back();
  }
}
```

And `add_loop_var`:

```cpp
void goto_loopst::add_loop_var(loopst &loop, const expr2tc &expr, bool is_modified)
{
  if (is_nil_expr(expr)) return;

  expr->foreach_operand([&](const expr2tc &e) {
    add_loop_var(loop, e, is_modified);                     // recurse into operands
  });

  if (is_symbol2t(expr) && check_var_name(expr))
  {
    if (is_modified)
      loop.add_modified_var_to_loop(expr);
    else
      loop.add_unmodified_var_to_loop(expr);
  }
}
```

### The bug

Observation [3] above: `call.operands` are **not** examined by the
modified-vars walker. The walker only:

- Point [1]: records `assign.target` for ASSIGN instructions.
- Point [2]: records `call.ret` for FUNCTION_CALL.
- Point [4]: recurses into callee body, and inside that body point
  [1] fires on `p->x = ...` → records `p` (the local pointer) as
  modified.

But `p` is a **callee-local** variable, not the caller's object.
The caller's object (e.g. `obj` in `dispatch(&obj)`) is NEVER added
to the modified set.

Result: when `goto_k_induction::make_nondet_assign` (`goto_k_induction.cpp:198`)
iterates `loop.get_modified_loop_vars()` to insert nondet ASSIGNs
at loop head, **`obj` is not in the set**. Its value is preserved
across the inductive-step havoc, so the inductive hypothesis
becomes "assume obj has its pre-loop value" — which is false in
general.

### Concrete demonstration

```c
struct S { int x; } obj;
void dispatch(struct S *p) { p->x++; }
int main() {
  while (nondet_int()) dispatch(&obj);
  assert(obj.x == 0);
}
```

- After `find_function_loops`: one loop identified, body = call.
- `get_modified_variables(call, loop, ...)`:
  - [2]: `call.ret == nil`, no-op.
  - [4]: recurse into `dispatch`. Find `ASSIGN (*p).x = p->x + 1`.
    - [1]: `add_loop_var(loop, (*p).x, true)` → walks into `p`,
      which is a symbol, adds `p` to modified set.
- Final `modified_loop_vars == { p }`.
- `make_nondet_assign` emits `ASSIGN p = nondet(...)` at loop head.
  But `p` is `dispatch`-local — the caller's `obj` is untouched.
- I(k) proceeds with `obj.x == 0` preserved. Proves.
- Base case actually catches the bug at k=1. But the k-induction
  driver's optimistic schedule often reports I(k) success before
  B(k) failure — verdict = VERIFICATION SUCCESSFUL. **Unsound.**

### Equivalent inline program is fine

```c
struct S { int x; } obj;
int main() {
  while (nondet_int()) obj.x++;
  assert(obj.x == 0);
}
```

`add_loop_var(loop, obj.x, true)` hits `obj` symbol directly → adds
`obj` to modified set → havoc inserts `obj = nondet(...)` at loop
head → I(k) correctly fails or UNKNOWN. Sound.

This is the diagnostic: **Solidity Solidity problem + equivalent C
inline program works = frontend or transform issue. Here the
transform (this file) is the cause.**

## `goto_k_induction` transform

`goto_k_induction.cpp`. Entry:

```cpp
void goto_k_induction(goto_functionst &goto_functions) {
  Forall_goto_functions(it, goto_functions)
    if (it->second.body_available)
      goto_k_inductiont(it->first, goto_functions, it->second);
  goto_functions.update();
}
```

For each function with a body, construct a `goto_k_inductiont`
(inherits from `goto_loopst`, so automatically runs loop detection).

### `convert_finite_loop(loop)`

`goto_k_induction.cpp:73-95`. 5 steps:

1. `get_entry_cond_rec(loop_head, loop_exit, guards)` — walk the
   loop body gathering branch conjuncts that lead from head to
   exit.
2. `remove_unrelated_loop_cond(guards, loop)` — drop guards whose
   variables don't overlap the modified set.
3. `assume_loop_entry_cond_before_loop(loop_head, loop_exit, guards)` —
   emit `ASSUME(entry_cond)` right before the loop. This is the
   induction hypothesis.
4. `make_nondet_assign(loop_head, loop)` — emit `ASSIGN v = nondet`
   for each `v ∈ loop.modified_loop_vars`. These get
   `inductive_step_instruction = true`.
5. `adjust_loop_head_and_exit(loop_head, loop_exit)` — fix target
   iterators after the insertions.

The `inductive_step_instruction` flag is what lets symex's
execution_state skip the havoc during base-case (B(k)) and
forward-condition (F(k)) runs. See
[docs/claude/symex/k-induction.md](../symex/k-induction.md) for the
symex side.

### `make_nondet_assign` body

`goto_k_induction.cpp:198-259`:

```cpp
void goto_k_inductiont::make_nondet_assign(
  goto_programt::targett &loop_head,
  const loopst &loop)
{
  auto const &loop_vars = loop.get_modified_loop_vars();

  goto_programt dest;
  for (auto const &lhs : loop_vars)
  {
    // Don't havoc pointers when we're trusting value-set analysis
    if (config.options.get_bool_option("add-symex-value-sets") &&
        is_pointer_type(lhs))
      continue;

    expr2tc rhs = gen_nondet(lhs->type);

    goto_programt::targett t = dest.add_instruction(ASSIGN);
    t->inductive_step_instruction = true;
    t->code = code_assign2tc(lhs, rhs);
    t->location = loop_head->location;
  }

  goto_function.body.insert_swap(loop_head, dest);
  // ... fix up iterators
}
```

The havoc ASSIGNs go **before** the loop head, flagged so symex
can see them during I(k) and skip them during B(k)/F(k).

The `add-symex-value-sets` skip for pointers is orthogonal — an
experimental feature that uses value-set analysis to avoid havoc'ing
pointers. Not on by default.

## KNOWNBUG #1 — Fix directions

Three fix directions. I've documented these in
[docs/claude/symex/k-induction.md](../symex/k-induction.md) §Bugs
already; the solver-layer reading has sharpened the cost analysis.

### Fix A — Include pointer-argument aliases in the modified set

**Scope:** extend `get_modified_variables`' FUNCTION_CALL branch to
inspect `call.operands`. For each operand that's an `address_of`
(or contains one), resolve via the static value-set analysis
(`src/pointer-analysis/value_set_analysis.{cpp,h}`) to the set of
pointed-to L0 objects, and union those into the modified set.

**Implementation sketch:**

```cpp
// In goto_loops.cpp, inside is_function_call branch, after [2]:

for (const expr2tc &arg : function_call.operands) {
  collect_addressed_objects(loop, arg);
}

// Where collect_addressed_objects walks the expr looking for
// address_of nodes, extracts the pointed-to, and adds to
// modified-set:

void collect_addressed_objects(loopst &loop, const expr2tc &arg) {
  if (is_nil_expr(arg)) return;

  if (is_address_of2t(arg)) {
    const expr2tc &target = to_address_of2t(arg).ptr_obj;
    // Walk to base symbol, or use value-set to resolve
    add_modified_var_via_addressof(loop, target);
    return;
  }

  arg->foreach_operand([&](const expr2tc &e) {
    collect_addressed_objects(loop, e);
  });
}
```

where `add_modified_var_via_addressof` either (a) directly adds the
base symbol if the target is a simple symbol/member/index chain, or
(b) queries `value_set_analysis` for the set of pointed-to L0
objects and adds them all.

**Requires:** a static value-set analysis available at this transform
point. Confirmed available: `value_set_analysis_fit` runs before
goto_k_induction in `bmct::run` when it's enabled; but the default
path does NOT run the static analysis before goto_k_induction. So
option (a) alone — walking `address_of` to its base — works for
simple cases and should be the first cut.

**Cost:** ~50-100 LOC in `goto_loops.cpp`. Low risk.

**Soundness:** conservative over-approximation. May havoc variables
that aren't actually modified by the call — regressing some
currently-provable programs to UNKNOWN. Acceptable tradeoff for
eliminating the unsoundness.

**Why this fix is right:** it's the smallest change that fixes the
root cause. Alternative fixes would require SSA-time analysis or
runtime support — bigger surface, harder to test.

### Fix B — Havoc at SSA level

Instead of inserting ASSIGN-nondet instructions at the GOTO level,
have symex emit nondet writes for all non-local symbols at I(k)
entry. Symex has the full value-set at SSA time, so can know
exactly which storage locations are written through every pointer.

**Cost:** larger. Touches `src/goto-symex/execution_state.cpp`
and the interface between goto_k_induction and symex.

**Benefit:** more precise — only actually-written vars are havoc'd.

**Complexity:** high. Requires split between "inductive-step
marker" (a pc position, set in goto-programs) and "havoc actions"
(synthesised at SSA time).

**Not the near-term fix.** Right for long-term architecture.

### Fix C — Havoc everything (over-approximate)

Unconditionally havoc every global and every addressable local at
I(k) entry, regardless of whether the walker thinks it's modified.
Trivial implementation. Massive regression in proof strength (most
currently-provable programs become UNKNOWN).

**Not acceptable.** User explicitly rejects this in
`feedback_no_soundness_escape_hatch.md` and
`feedback_no_workaround.md`.

## Recommended fix

**Fix A, option (a) first.** Directly extract `address_of` targets
and walk to base symbol. Handles the
`dispatch(&obj)` case (80%+ of Solidity failures). Later extend to
option (b) with value-set-analysis integration for more complex
patterns like `dispatch(some_expr_that_evaluates_to_ptr)`.

Both options keep the havoc set at the GOTO level (where it is
now) and only expand the walker — minimal architectural change.

## Fix applied (2026-04-24)

Fix A option (a) implemented:

- `goto_loops.h`: added `collect_addressof_targets(loopst &, const
  expr2tc &)` declaration.
- `goto_loops.cpp`: helper walks the expression recursively; when it
  finds an `address_of2t`, peels `member`/`index`/`typecast`/
  `bitcast` layers to reach the base symbol, then adds it to the
  loop's modified-var set via `loop.add_modified_var_to_loop`. In
  the FUNCTION_CALL branch of `get_modified_variables`, the helper
  is called on each actual parameter.

Regression tests under `regression/esbmc/`:
- `k_induction_ptr_through_function_fail/` — exhibits the bug:
  pre-fix wrongly `VERIFICATION SUCCESSFUL` at k=3, post-fix
  correctly `VERIFICATION FAILED (Bug found k=13)`.
- `k_induction_ptr_through_function_pass/` — confirms non-affected
  patterns still pass.

Trade-off verification: the fix is a conservative over-approximation,
so the inductive step will correctly havoc more state. State-invariant
patterns that previously relied on missing-havoc for spurious
provability now correctly return UNKNOWN. This eliminates unsoundness
but does not, on its own, make state-invariant patterns provable —
that requires loop invariants.

## Related: `contains_only_pointers`

`goto_k_induction.cpp:66`:
```cpp
if (config.options.get_bool_option("add-symex-value-sets") &&
    function_loop.contains_only_pointers())
  continue;
```

If the whole loop only modifies pointers (under
`--add-symex-value-sets`), skip k-induction entirely. Different
question from the bug, but related machinery.

## Interaction with bmct

The bmc driver (`src/esbmc/bmc.cpp`) orchestrates the three phases:

- **Base case**: run symex with `base-case` option; all
  `inductive_step_instruction` ASSIGNs are skipped by
  `execution_state.cpp:214`. Loop runs k times; if a property
  fails, the bug is real.
- **Forward condition**: same transform but unwind-exceed asserts
  at k — proves the loop can't run more than k iterations.
- **Inductive step**: don't skip `inductive_step_instruction`;
  so havoc fires; assertion at I(k) loop-exit checks invariant
  preservation.

If all three pass, the property holds for all k ≤ max_k_step. If
I(k) succeeds but B(k) fails, I(k)'s success is spurious — which
is exactly what happens under KNOWNBUG #1.

## Debug recipes

- `--show-goto-functions` before and after k-induction transform:
  diff to see what havoc was inserted. If a variable you expect
  to be havoc'd is absent from the diff, the walker missed it.
- `--show-claims`: list every assertion including inductive.
- Run each phase in isolation (`--base-case`, `--forward-condition`,
  `--inductive-step`) to see which one is giving the (possibly
  wrong) success.
- Small repro pattern: two C programs differing only in inline
  (`obj.x++`) vs. via-ptr-function (`dispatch(&obj)`). If verdicts
  differ, KNOWNBUG #1 is involved.

## Common pitfalls

- **"Havoc set missing a modified variable"** — either KNOWNBUG #1
  (pointer-through-function) or the variable is filtered out by
  `check_var_name` in `goto_loops.cpp:5-40` (skips `__ESBMC_*`,
  `return_value___`, `pthread_lib`, `$`, `__func__`, etc.). Check
  the filter first.
- **"Infinite recursion in get_modified_variables"** — the
  `function_names` vector is the recursion guard. If a callee
  recursively calls its caller, the guard should fire; if not,
  check that `is_end_function` properly pops.
- **"loop_number 0 on a loop instruction"** — `loop_numbers.cpp`
  must run before `goto_k_induction`. Check pass ordering in the
  bmc driver.
- **"I(k) success but B(k) failure on the same program"** — this
  is the canonical KNOWNBUG #1 signature. Build a C-equivalent
  inline version to confirm.
