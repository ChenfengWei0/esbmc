# Symex State

The vocabulary every other topic in this directory assumes. Read this first.

## The three objects that hold state

```
reachability_treet
  └── list<execution_statet>           (one per reached context-switch point)
       └── vector<goto_symex_statet>   (one per live thread)
            └── call_stackt = vector<framet>
```

- **`goto_symex_statet`** (`goto_symex_state.{cpp,h}`) — the state of ONE
  thread at a given instant. Program counter, call stack, guard,
  per-thread value-set, per-thread L2 rename map, loop-unwind counters.
  Despite being called "state", it is *per-thread*, not global.
- **`execution_statet`** (`execution_state.{cpp,h}`) — the "global"
  context. Owns the authoritative L2 rename map and the vector of
  per-thread `goto_symex_statet`s. Extends `goto_symext` so it can
  override `symex_step` and intercept the threading/atomic instructions
  before delegating to the base per-thread dispatch.
- **`reachability_treet`** (`reachability_tree.{cpp,h}`) — the
  interleaving/DFS driver. Holds a **list** of `execution_statet`, one
  per checkpoint where a context switch was taken. Cloning an element of
  this list is how ESBMC "rewinds" to try a different schedule.

Single-threaded programs still go through all three layers, but the
`execution_statet` has one thread in its vector and the
`reachability_treet` list has one element.

## framet — per call-frame state

`goto_symex_statet::framet` (declared in `goto_symex_state.h:142`) holds
everything tied to a specific function activation:

- `function_identifier` — which function this frame corresponds to.
- `level1` — the L1 rename map (see below). L1 numbers are per-frame:
  two recursive calls to `f` get two different `level1` maps, so their
  locals don't alias.
- `calling_location`, `end_of_function`, `return_value` — bookkeeping
  for `RETURN`: where to jump back to, which lvalue to assign the
  return value to.
- `goto_state_map` — states queued for phi merging at join points (see
  [goto-branching.md](goto-branching.md)).
- `declaration_history`, `local_variables` — the set of names that
  have been `DECL`'d in this frame; used to decide when a `DECL`
  actually needs a fresh L1 number (block re-entry) vs. already has
  one (straight fall-through).
- `entry_guard` — the guard under which the function was called; used
  to set up `RETURN`.
- `hidden`, `stack_frame_total`, `va_index`, function-pointer merge
  fields — secondary bookkeeping.

Key inline helpers on `goto_symex_statet`:

- `top()` — `call_stack.back()`, the currently-executing frame.
- `new_frame(tid)` — push a blank frame (used by `symex_function_call`).
- `pop_frame()` — pop on `END_FUNCTION`; asserts the frame's
  `goto_state_map` is empty (no unmerged phi states leaked).

## The three rename levels

Defined in `renaming.{cpp,h}`. Every SSA name has the form
`base_name?l1_num!thread_num&frame_num#l2_num` (see
`symbol2t::get_symbol_name`).

| Level | What it encodes | When it changes |
|---|---|---|
| **L0** | The original base name (e.g. `x`, `c:@main::arr`). | Never — this is the source-level identifier. |
| **L1** | A per-stack-frame uniquifier. Two recursive calls to `f` each get a fresh L1 number for every local. | On `DECL` (fresh block entry). Stored in `framet::level1`. |
| **L2** | SSA version counter. Every assignment bumps it. | On every `assignment()` call. Stored in `level2` map, owned by `execution_statet`. |

Why three levels rather than one global counter:

- **L0→L1 (function locality)** lets recursion work without giving every
  recursive invocation's `x` the same SSA name. L1 is strictly about
  telling copies of the same local apart.
- **L1→L2 (SSA version)** is classic SSA: each assignment gets a fresh
  number, phi-functions at join points pick one.

Globals skip L1 — `level1t::get_ident_name` detects an unknown name and
tags it `level1_global`. L2 still applies to globals (and on writes
flows through the global `ex_state_level2t` in the execution state).

`goto_symex_statet::rename()` and `rename_address()` are the entry
points that walk an expression and apply L1+L2 renaming in place.
`rename_address()` skips L2 inside `address_of` — an address-of
captures the *storage location*, not a specific SSA version.

## goto_statet — frozen branch state

Nested class on `goto_symex_statet` (declared at `goto_symex_state.h:90`).
It is a snapshot of a thread at a branch, to be merged later:

```cpp
class goto_statet {
  unsigned num_instructions;
  std::shared_ptr<renaming::level2t> level2_ptr;   // cloned
  value_sett value_set;                            // cloned
  guardt guard;                                    // path guard
  unsigned int thread_id;
  variable_name_sett local_variables;
};
```

Used by:

- `symex_goto` stores one on the frame's `goto_state_map` for the join
  instruction.
- `phi_function()` merges all queued `goto_statet`s into the current
  state by emitting phi assignments (see [goto-branching.md](goto-branching.md)).

## Guards

`guardt` (from `util/guard.h`) accumulates the path condition:
`guard.guard_expr(e)` produces `g ⇒ e`. Entry to a branch pushes the
branch condition; joining pops it. The assertion machinery
(`goto_symext::assertion`) always guards the claim with
`cur_state->guard.as_expr()` so the SMT formula records *under what
conditions* the assertion was reached.

`global_guard` is a second guard shared across the execution state, used
mostly by threading constructs.

## value_set — points-to per state

See [value-set.md](value-set.md) for the full treatment. Summary:
`goto_symex_statet::value_set` is the per-thread flow-sensitive
points-to map, mutated on pointer writes and copied when states branch.
`execution_statet` additionally owns a "global" value-set used across
threads.

## Constant propagation

`goto_symex_statet::constant_propagation(expr)` decides whether a
renamed expression should be folded in place (instead of emitted as an
SSA variable). It refuses:

- Infinite-size arrays (can't fully fold).
- Multi-dim array types (the *outer* subtype is itself an array type).
  This is a recent deliberate restriction — see the native-nested-array
  fix `1dbfc7dd10`.
- `nondet$symex::nondet` symbols (losing them would hide all nondet
  inputs from counter-examples).

If you are chasing a "why did my expression get simplified to a
constant" bug, this is where to look.

## realloc_map

`std::map<expr2tc, unsigned>` on the state. Tracks which abstract
pointer values have been re-numbered by a `realloc` — every time
`realloc(p, ...)` is executed on a given `p`, that map entry is bumped,
and subsequent pointer operations on `p` pick up the new number so they
can't alias the pre-realloc version.

Merging at phi points is safe without special handling: the renumber
decision itself is SMT-guarded.

## Memory lifetime

- `execution_statet` cloning happens inside `reachability_treet` on a
  context-switch decision. The clone deep-copies each
  `goto_symex_statet` and the global L2/value-set.
- `goto_statet` cloning (for phi queuing) also clones L2 and value-set
  because each branch independently bumps SSA numbers.
- `framet` is trivially copyable — cloning a state clones its
  `call_stackt` by value.

The `shared_ptr<level2t>` inside `goto_statet` exists because
`level2t` is abstract (subclassed for `--schedule` and for basic
mode); `clone()` returns a fresh derived copy.

## Debugging hooks

- `print_stack_trace(indent, os)` — dumps the current call stack,
  function-identifier by function-identifier, with the next-instruction
  line.
- `gen_stack_trace()` — structured version returned as
  `vector<stack_framet>`; used in counter-examples.
- `--show-symex-value-sets` (checked inside `execution_statet::symex_step`)
  dumps the active thread's value-set before every step — very verbose,
  but it is the only way to observe the points-to map's evolution.
- `--ssa-symbol-table` — dumps L0 → (L1, L2) mappings alongside the SSA
  equation; useful when a name in the equation looks mangled and you
  need to find the source symbol.

## Common pitfalls

- **"Why does this symbol have SSA number 0?"** — Unwritten since the
  current L1 scope started. L2 `current_number(rec)` returns 0 when the
  name is absent from `current_names`.
- **"Why did my assignment not produce a new SSA version?"** — the
  expression was constant-propagated; check `constant_propagation`'s
  rules.
- **"Why are two recursive calls aliasing?"** — the `DECL` wasn't hit
  (e.g. the local was elided by an earlier pass), so L1 didn't bump.
  Check `declaration_history` in the frame.
- **"Why does a global look like an L1 name?"** — `level1_global` is
  still reported as "level1" by the renaming printer, but its `l1_num`
  is zero; real L1 locals have a nonzero number.
