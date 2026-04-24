# Main Loop (`symex_main.cpp` + `reachability_tree.cpp`)

Who calls `symex_step`, and how a single formula is produced.

Read [state.md](state.md) first — this page uses the terms
`reachability_treet`, `execution_statet`, `goto_symex_statet`, and
"frame" without re-explaining them.

## The two-level loop

```
bmct::run  (src/esbmc/bmc.cpp)
  └── reachability_treet::get_next_formula()    ← one formula per schedule
        └── loop until interleaving complete:
              loop until context-switch point or thread stops:
                execution_statet::symex_step(*this)
                  └── override for END_FUNCTION/ATOMIC/RETURN,
                      otherwise delegate to:
                      goto_symext::symex_step(art)   ← the big switch
              decide context-switch, clone ex_state, continue
        return symex_resultt (= target equation + claim counts)
```

- The **outer** `get_next_formula` (`reachability_tree.cpp:536`) stops
  when one complete interleaving has been emitted into the target.
  `bmct` then solves, and if unsat, asks for the next interleaving by
  calling `setup_next_formula()` which walks back to an unexplored
  cswitch point.
- The **inner** two `while`s run `symex_step` on the active thread until
  either (a) a context-switch point occurred (shared-var access
  detected by `analyze_assign`/`analyze_read`), or (b) the thread
  stopped (stack empty / `thread_ended`).
- Single-threaded programs just take the inner while once to
  completion — no cswitch ever fires and the outer loop returns
  immediately.

## `execution_statet::symex_step`

`execution_state.cpp:197`. Invoked once per instruction. The shape:

1. Pick the active thread; fetch its current `instructiont`.
2. Call `merge_gotos()` — drain the frame's `goto_state_map` of states
   queued for *this* pc, emit phi assignments (see
   [goto-branching.md](goto-branching.md)).
3. Check the k-induction *section guard* (`inductive_step_instruction`):
   if we're running base-case or forward-condition and the instruction
   was synthesised for the inductive step, skip it.
4. Optional diagnostics (`--show-symex-value-sets`, `--break-at`, etc.).
5. Dispatch on `instruction.type`. Three instruction types are handled
   here: `END_FUNCTION` for ending `__ESBMC_main`, `ATOMIC_BEGIN/END`
   for atomic sections, and `RETURN` (to also feed `analyze_assign`).
   Everything else falls through to `goto_symext::symex_step(art)`.

## `goto_symext::symex_step` — the big switch

`symex_main.cpp:214`. This is **1514 LOC, the biggest single file in
the subsystem**, because every GOTO instruction type has its lowering
inline (or inlined dispatch to `symex_*.cpp` helpers).

Shape (simplified):

```cpp
void goto_symext::symex_step(reachability_treet &art) {
  const instructiont &instruction = *cur_state->source.pc;

  // depth limit check (--depth N → false guard past the limit)
  if (depth_limit && cur_state->num_instructions > depth_limit)
    cur_state->guard.add(gen_false_expr());
  cur_state->num_instructions++;

  // remember first loop entered (for k-induction --k-step scheduling)
  if (inductive_step && instruction.loop_number && !first_loop)
    first_loop = instruction.loop_number;

  switch (instruction.type) {
    case SKIP / LOCATION:        pc++; break;
    case END_FUNCTION:           symex_end_of_function();    // pops frame
    case GOTO:                   symex_goto(guard);
    case ASSUME:                 symex_assume(); pc++;
    case ASSERT:                 symex_assert(); pc++;
    case RETURN:                 symex_assign(ret); symex_return();
    case ASSIGN:                 symex_assign(code);
    case FUNCTION_CALL:          dereference args; run_intrinsic or symex_function_call
    case DECL:                   symex_decl(code);
    case DEAD:                   symex_dead(code);
    case OTHER:                  symex_other(code);
    case CATCH / THROW / THROW_DECL / THROW_DECL_END:  exception handling
    case ATOMIC_*:               handled in execution_statet override
    default:                     log_error + abort
  }

  // feed the instruction into the optional interval domain
  if (interval_domain_state && !cur_state->guard.is_false())
    interval_domain_state->process_instruction(pre_step_pc);
}
```

Each `case` is a short arm that either inlines a small action or calls
out to one of the `symex_*.cpp` helpers:

| Instr. | Helper | File |
|---|---|---|
| ASSIGN | `symex_assign` | `symex_assign.cpp` |
| GOTO | `symex_goto` | `symex_goto.cpp` |
| FUNCTION_CALL | `symex_function_call` / `run_intrinsic` / `run_builtin` | `symex_function.cpp` |
| RETURN | `symex_return` / `make_return_assignment` | `symex_function.cpp` |
| DECL / DEAD | `symex_decl` / `symex_dead` | `symex_function.cpp` |
| THROW / CATCH | `symex_throw` / `symex_catch` | `symex_catch.cpp` |
| OTHER | `symex_other` | `symex_other.cpp` |

## Pre-step expression rewrites

Before handing an expression to a helper, the switch arms run a canonical
pipeline of local rewrites (all defined elsewhere):

1. `replace_nondet(tmp)` — nondet symbols in the expr get a fresh
   `symex::nondet$N` name, so two textually-identical nondets pick
   different values.
2. `volatile_check(tmp)` — flag volatile reads (for thread-interference
   bookkeeping).
3. `dereference(tmp, READ/WRITE)` — resolve `*p` to a chain of
   `if p==&a then a else if p==&b then b ...` via the value-set.
   See [dereference.md](dereference.md).
4. `replace_dynamic_allocation(tmp)` — rewrite `IS_DYNAMIC_OBJECT(p)`,
   `ALLOC_SIZE(p)` etc. to reads of the backing infinite arrays.
5. Language-specific hooks (`replace_races_check` for TOD,
   `simplify_python_builtins`, etc.).

If you're diagnosing "why didn't my pointer get dereferenced", the
arm's dereference call is the place to set a breakpoint.

## `symex_assume` / `symex_assert` / `claim`

Three closely-related functions at the top of `symex_main.cpp`
(`claim` at line 63, `assertion` at 147, `assume` at 188):

- **`assume(expr)`** — renames, simplifies, and adds `g ⇒ expr` to the
  global_guard. If `expr` is a trivial `sym == const`, it is also
  written as an SSA assignment so later uses can be constant-propagated.
- **`assertion(expr, msg)`** — unconditionally emits an assertion step
  on the target equation, wrapped with the current guard.
- **`claim(expr, msg)`** — the high-level entry. Handles trivially-true
  claims (counted and strengthened to `assume`), the interval-domain
  pruning (`--interval-symex-assert`), the incremental SMT pruning
  (`--smt-symex-assert`), then falls through to `assertion`. Also does
  the k-induction "convert asserts to assumes on non-last iterations"
  dance — the documented trick to strengthen the induction hypothesis.

### The k-induction assert-to-assume detail

`symex_main.cpp:134`:

```cpp
if (inductive_step && first_loop && !cur_state->source.pc->inductive_assertion)
{
  BigInt unwind = cur_state->loop_iterations[first_loop];
  if (unwind < max_unwind - 1) {
    assume(claim_expr);
    return;                    // don't add as an assertion
  }
}
```

In the **inductive step**, on every loop iteration except the last, a
user assertion is converted to an assumption. Only the last iteration's
occurrence is kept as an assertion. This is what lets the inductive
step's SMT formula ask "if the property held at every step so far, does
it still hold now?" without multiplying constraints. See
[k-induction.md](k-induction.md).

## Loop handling in the dispatch

The GOTO representation has already inserted BACKWARDS_GOTO at loop
back-edges. `symex_goto.cpp` detects a back-edge, looks up
`cur_state->loop_iterations[loop_id]`, compares against
`max_unwind`, and either unwinds once more or stops. The counter
lives on the per-thread state. See
[goto-branching.md](goto-branching.md) for the full unwinding logic
and the unwinding-assertion emission.

## Instruction counters and bounds

- `cur_state->num_instructions` — per-thread monotonic counter, used for
  `--depth N` (once exceeded, the thread's guard is forced false —
  quiet truncation, but because it's guard-based not pc-based, any
  reached assertion under that guard degenerates to `true`).
- `cur_state->loop_iterations[loop_number]` — per-loop unwind counter.
- `cur_state->function_unwind[id]` — per-function recursion counter,
  bounded by `--unwind` (for function recursion) not `--unwindset`.

## `symex_resultt`

Returned from `get_next_formula`:

```cpp
struct symex_resultt {
  std::shared_ptr<symex_targett> target;   // the concrete equation sink
  unsigned total_claims;
  unsigned remaining_claims;               // total - trivially-true
  unsigned simplified_claims;
};
```

`bmct::run` pulls `target` and hands it to `smt_convt`.
`total_claims`/`remaining_claims`/`simplified_claims` are for the
`--show-claims` and progress-banner reporting.

## Debugging the main loop

| Flag | Effect |
|---|---|
| `--symex-trace` | Print each instruction as it's about to be executed — the go-to "am I even reaching this line?" test. |
| `--show-symex-value-sets` | Dump the value-set at every step. Very verbose. |
| `--break-at N` | `break_insn = N` in the loop; fires a breakpoint when `instruction.location_number == N`. |
| `--depth N` | Hard truncate a path after N instructions via guard=false. Beware: silent quieting, not a loud stop. |
| `--max-k-step`/`--k-step` | Bound base/forward/inductive k. |
| `--show-claims` | Print the claim set generated; doesn't run symex beyond what's needed to enumerate them. |
| `--program-only` / `--program-only --no-slice` | Print SSA (after / before slicer). |

## Common pitfalls

- **"My change to `symex_step` isn't firing"** — confirm the override
  in `execution_statet::symex_step` isn't absorbing your instruction
  type (END_FUNCTION, ATOMIC_*, RETURN are intercepted there).
- **"Claim shows up in `--show-claims` but not in the SSA"** —
  `claim()` simplified it to trivially-true (counted in
  `simplified_claims`) and strengthened it to an assume instead. Look
  for `✓ PASSED` lines under `--multi-property`, or check the counter
  delta.
- **"k-induction inductive step's SSA has no assertion where I expect
  one"** — the assert-to-assume conversion fires on every non-last
  iteration. Only the last occurrence is an assertion. If the last
  iteration's pc is not reached (e.g. the loop exits early on that
  iteration via a guarded path), no assertion lands on the formula.
  This is distinct from the havoc-missing-writes bug discussed in
  [k-induction.md](k-induction.md).
- **"Cswitch happens too often / never"** — the predicate is "the
  instruction touched a shared variable since the last switch"
  (`thread_last_reads`/`thread_last_writes` populated by
  `analyze_assign`/`analyze_read`). An instruction that reads a local
  never triggers one.
