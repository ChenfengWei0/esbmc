# k-induction — Proof Mode on Top of BMC

The k-induction driver is in `src/goto-programs/goto_k_induction.cpp`
(loop transformation) + `src/esbmc/bmc.cpp` (phase scheduling). Symex
and target-equation stay mostly unchanged — they just run three times
per k, once each for the base case, forward condition, and inductive
step.

## The proof rule

Standard k-induction for safety property `P` in a loop:

```
Base case B(k):     P holds for the first k iterations.
Forward F(k):       If P holds for k iterations, the loop has exited or
                    is at step k+1 (no "runaway" more than k).
Inductive I(k):     Assuming P held at k consecutive iterations,
                    it holds at iteration k+1.
```

If all three hold for some `k`, `P` holds at every iteration. ESBMC
schedules `k = k_step .. max_k_step` and declares success at the
first k where all three pass.

## Flag surface

| Flag | Meaning |
|---|---|
| `--k-induction` | Run B/F/I with k = 1, 2, …, max_k_step. |
| `--max-k-step N` | Ceiling. Default 50. Above N, give up and report UNKNOWN. |
| `--k-step N` | Starting k. Default 1. Use 2+ to skip small k. |
| `--k-induction-parallel` | Run B, F, I in separate processes. |
| `--base-case` / `--forward-condition` / `--inductive-step` | Run only that phase. For debugging / understanding. |
| `--disable-inductive-step` | BMC mode — run only B(k) up to max_k_step. |

Parsing lives in `src/esbmc/esbmc_parseoptions.cpp:382–470`.
`bmct::run_k_induction` (not shown) orchestrates the three sub-runs.

## What each phase actually looks like

Each phase rebuilds the SSA from scratch. The goto-program is
transformed by `goto_k_inductiont::convert_finite_loop` *before*
symex sees it, then the per-phase option flags (`base-case`,
`forward-condition`, `inductive-step`) change symex's behaviour.

### `convert_finite_loop`

`goto_k_induction.cpp:73`. For each finite loop:

1. `get_entry_cond_rec` — walk the loop body collecting guard
   conjuncts (branch conditions that lead from loop head to loop
   exit).
2. `remove_unrelated_loop_cond` — drop guards whose variables don't
   overlap with the loop's modified set. Avoids spuriously narrowing
   the entry condition.
3. `assume_loop_entry_cond_before_loop` — insert an `assume` of the
   entry condition right before the loop. In the inductive step, this
   is the induction hypothesis.
4. `make_nondet_assign` — insert `ASSIGN lhs = nondet` for every
   variable in the loop's modified set, right at the loop head.
   Each inserted instruction is flagged
   `inductive_step_instruction = true`.
5. `adjust_loop_head_and_exit` — re-point the loop head/exit
   iterators after the insertions.

The net effect in the GOTO IR (conceptually):

```
BEFORE:               AFTER k-induction transform:
                      assume(entry_cond);           <-- induction hypothesis
                      havoc x, y, ...;              <-- nondet every modified var
                      <<original loop body>>        <-- the I(k) body
```

### Phase selection

`execution_state.cpp:214`:

```cpp
if ((base_case || forward_condition) && instruction.inductive_step_instruction) {
  // Skip the k-induction-inserted havoc when running B(k) or F(k).
  cur_state->source.pc++;
  return;
}
```

- **Base case B(k)**: the `inductive_step_instruction` assigns are
  skipped, so the state starts at the loop head's original values.
  Loop is unwound `k` times, unwinding assertion emits at exit —
  violates iff a real bug is reachable in ≤ k iterations.
- **Forward condition F(k)**: same skip, but the unwinding-assertion
  semantics is what matters: `assert(!can_take_one_more_iteration)`.
  Holds iff the loop terminates within k iterations.
- **Inductive step I(k)**: `inductive_step_instruction` assigns are
  executed — they assign nondet values to every var in the modified
  set. The preceding assume acts as the induction hypothesis.
  Assertions are then converted to assumes on all but the final
  iteration (see `symex_main.cpp:134`).

### Assert-to-assume on non-last iterations

`src/goto-symex/symex_main.cpp:134`:

```cpp
if (inductive_step && first_loop && !cur_state->source.pc->inductive_assertion) {
  BigInt unwind = cur_state->loop_iterations[first_loop];
  if (unwind < max_unwind - 1) {
    assume(claim_expr);
    return;                 // don't emit as assertion
  }
}
```

Equivalent to "every iteration except the final one contributes the
property as an assumption; only the final one contributes it as an
assertion". This is how the inductive hypothesis is strengthened:
the solver sees `P(0) ∧ P(1) ∧ … ∧ P(k-1) ⇒ P(k)`.

## The modified set — where the bug lives

`goto_k_inductiont::make_nondet_assign` inserts a nondet assign for
every element of `loop.get_modified_loop_vars()`. That set is built
by `goto_loopst::get_modified_variables` in
`src/goto-programs/goto_loops.cpp:104`:

```cpp
void get_modified_variables(iterator instr, loop, function_names) {
  if (instr->is_assign())
    add_loop_var(*loop, assign.target, /*is_modified=*/true);
  else if (instr->is_function_call()) {
    add_loop_var(*loop, function_call.ret, true);   // return lvalue
    // recurse into callee body:
    for (head : callee.body.instructions)
      get_modified_variables(head, loop, function_names);
  }
  else if (goto/assert/assume)
    add_loop_var(*loop, instr->guard, false);       // not modified
  else if (end_function)
    function_names.pop_back();
}

void add_loop_var(loop, expr, is_modified) {
  expr->foreach_operand(e -> add_loop_var(loop, e, is_modified));
  if (is_symbol2t(expr) && check_var_name(expr))
    loop.add_{modified,unmodified}_var(expr);
}
```

Read carefully. The walker:

1. For `ASSIGN lhs=rhs`, adds every symbol found in `lhs`'s
   expression tree to the modified set. For a simple `a = b`, adds
   `a`. For `a[i] = b`, adds `a` (and `i`). For `s.f = b`, adds `s`.
2. For `FUNCTION_CALL ret = f(args)`, adds every symbol in `ret` —
   and recursively walks the callee's body, treating writes inside
   the callee as writes inside the loop.
3. Arguments to the function call are **not** added to the modified
   set. The walker doesn't examine `call.operands`.

### The bug — pointer-passed-to-function writes

Consider:

```c
struct S { int x; } obj;
void dispatch(struct S *p) { p->x = 1; }
int main(void) {
  while (nondet_int()) dispatch(&obj);
  assert(obj.x == 0);       // should fail
}
```

Semantically, `obj.x` is written every iteration via the pointer
argument. The havoc set should include `obj` so the inductive step
treats it as nondet going into iteration k.

What actually happens:

1. `get_modified_variables` sees the FUNCTION_CALL `dispatch(&obj)`.
2. `call.ret` is nil (void return); nothing added there.
3. Recursion enters `dispatch`'s body. Finds `ASSIGN (*p).x = 1`.
4. `add_loop_var(loop, (*p).x, is_modified=true)` walks the
   expression, finds `p` (a symbol), and adds `p` to the modified
   set. But `p` is a local of `dispatch`, not `obj`.
5. `obj` is never added. When `make_nondet_assign` emits the havoc
   preamble, `obj.x` retains its pre-loop value.
6. In I(k), `obj.x == 0` by preservation through the havoc preamble,
   so the assertion passes. The inductive step reports "property
   proved".

The k-induction conclusion is **unsound for this program**. Base
case B(k) correctly fails at k ≥ 1 (one iteration is enough to
write `obj.x`). F(k) is vacuous for `nondet_int()`. I(k) incorrectly
passes. The overall verdict depends on which phase's verdict is
reported when — in the current scheduler, I(k) succeeds before B(k)
fails for large k, and ESBMC returns SUCCESSFUL.

The root cause is **syntactic vs. semantic write-set analysis**.
`get_modified_variables` walks GOTO text without a points-to
analysis. Pointer-through-function writes require a semantic
(value-set) query to resolve, which is exactly what symex does at
runtime — but by then the havoc set is already fixed.

### Minimal C repro

In `/tmp/kind_probe/c_ptr.c` during the session that discovered this:

```c
#include <assert.h>
struct S { int x; int y; };
struct S obj = {0, 0};
int nondet_int(void);

void dispatch(struct S *p) {
    int choice = nondet_int();
    if (choice == 0) { if (p->x < 5) p->x++; }
    else if (choice == 1) { if (p->y < 5) p->y++; }
    else {
        __ESBMC_assume(p->x == 5);
        __ESBMC_assume(p->y == 5);
        assert(0);         // clearly reachable
    }
}

int main() {
    while (nondet_int()) dispatch(&obj);
    return 0;
}
```

`esbmc --k-induction --max-k-step 20 --k-step 3` reports "Solution
found by the inductive step (k = 2)" — a false proof. The twin file
with `dispatch(&obj)` replaced by inline `obj.x++` (no pointer-pass)
correctly reports UNKNOWN or FAILED at the same k.

### Impact on Solidity

Every Solidity contract method call routes through
`_ESBMC_Object_<C>` passed by pointer to the dispatch function
(`__ESBMC_main` → `_ESBMC_Main_<C>(&obj)` → method body
`method(_ESBMC_Object_<C> *self)`). So every loop containing a
method call is affected by this bug for any contract state variable.

This is why many Solidity regressions that *should* fail at
k-induction incorrectly pass, and why the MEMORY entry for
`k-induction.md §Bugs` exists. Covered by the KNOWNBUG sweep in
commit `6d53d99152` (75 tests flipped to KNOWNBUG).

## Fix options (not yet implemented)

The right fix is one of:

1. **Include pointer-argument aliases in the modified set.** When
   `get_modified_variables` sees a FUNCTION_CALL, also run a
   value-set-like analysis on `address_of` actuals to extract the
   pointed-to objects and union them into the modified set.
2. **Havoc at the SSA level.** Instead of inserting ASSIGN
   instructions at the GOTO level, have symex emit nondet writes for
   all non-local symbols at the I(k) entry — symex knows exactly
   which storage locations are written via the value-set.
3. **Conservative: havoc everything.** Treat the modified set as "all
   globals and all addressable locals" unconditionally. Sound but
   weakens the inductive hypothesis — many currently-provable
   programs would become UNKNOWN.

Fix #1 is the right unit of work; fix #2 is bigger but architecturally
cleaner; fix #3 is a one-liner but regresses proof strength.

## Other k-induction knobs

- **`inductive_assertion`** (set in `bmc.cpp:1330`) flags assertions
  added by the k-induction driver itself (as opposed to user-written
  asserts). These are excluded from the assert-to-assume conversion.
- **`goto_termination`** (`goto_k_induction.cpp:17`) is the
  termination analysis sibling — similar transform but targets
  non-termination proofs.
- **`--replace-all-contracts`** uses the k-induction scaffolding to
  replace called functions with their (user-provided) contracts
  during the inductive step.

## Diagnostic recipes

- **"Does k-induction really prove this?"** Run with `--base-case`
  only. If base case reports FAILED at small k, you have a real
  counter-example. If it returns SUCCESSFUL at max_k_step, the BMC
  side found no bug up to that depth — but that alone is not a proof.
- **"Why did I(k) succeed where B(k) failed?"** Check whether any
  variable the bug depends on is missing from the modified set.
  Diff `--show-goto-functions` between the two phases to see what
  the k-induction transform inserted.
- **"Check for the pointer-havoc bug."** Build two variants of the
  program: one inline, one via a pointer-arg helper. If k-induction
  gives different verdicts for semantically-identical programs, the
  havoc set is missing something.
- **`--show-claims`** shows every assertion the symex produced;
  useful to verify the assert-to-assume conversion is firing as
  expected (fewer user-provided claim-N entries for I(k) than B(k)).

## Common pitfalls

- **"I increased --max-k-step and the verdict flipped from UNKNOWN to
  SUCCESSFUL."** That's expected if I(k) is correct for your program:
  at higher k, the induction hypothesis is stronger, so more
  programs prove. If you suspect the flip is spurious, craft a
  pointer-through-function test to confirm havoc correctness.
- **"--k-induction times out on a simple loop."** Check `--k-step` —
  starting at 1 can burn time on small k that never prove.
- **"The inductive step reports `found a solution (k = 1)` but I
  wrote loops with obvious unbounded state."** See the pointer-havoc
  bug above.
- **"Assert-to-assume conversion hides a real bug."** The conversion
  only fires when `first_loop` is set and the assertion is not the
  synthesised inductive-assertion. If your assertion lives in a
  nested function called from inside a loop, `first_loop` is still
  set, so user asserts in callees are also converted to assumes
  until the last iteration. Unwind to an unusually high k to
  surface.
