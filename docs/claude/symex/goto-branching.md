# GOTO / Branching / Phi Merging

`src/goto-symex/symex_goto.cpp` (533 LOC). Handles forward conditional
jumps (if/else), backward jumps (loops), unwinding, and phi
construction at join points.

## Entry points

| Function | Purpose |
|---|---|
| `symex_goto(old_guard)` | Handle a GOTO instruction — either take the branch, queue the state for a phi, or handle a back-edge with unwinding. |
| `merge_gotos()` | Called at the top of every step: if any previously-queued states target the current pc, merge them in. |
| `phi_function(goto_state)` | Emit phi-function SSA assignments for each variable that diverges between `cur_state` and `goto_state`. |
| `merge_value_sets(src)` | Union the points-to map from the merging state into the current one. |
| `merge_locality(src)` | Union the per-frame local-variable set. |
| `loop_bound_exceeded(guard)` | Emit the unwinding assertion (or assumption under `--no-unwinding-assertions`). |
| `get_unwind(source, unwind)` | Look up the effective max_unwind for the loop (global `--unwind` or per-loop `--unwindset`). |

## Forward branch — `if / else`

Given a forward GOTO with `old_guard`:

1. Rename + simplify to `new_guard`.
2. Early-out: if provably false (via interval domain with
   `--interval-symex-guard`, or SMT with `--smt-symex-guard`), bump pc
   and return. No state branch.
3. Otherwise split: pick `new_state_pc` = GOTO target,
   `state_pc` = next instruction. The current `cur_state` continues at
   `state_pc`. A copy of `cur_state` (`goto_statet`) is pushed onto
   the frame's `goto_state_map[new_state_pc]` — this is the "taken"
   branch, to be phi-merged when execution reaches the target.
4. Guard bookkeeping: the "fall-through" cur_state gets
   `guard ∧ ¬new_guard`; the queued state gets `guard ∧ new_guard`.
5. If `new_guard` isn't already a single symbol (or negated symbol),
   a fresh `guard_identifier()` SSA variable is emitted and used
   instead of replicating the whole guard expression across both
   branches.

```
BEFORE:           AFTER symex_goto on `IF c GOTO L`
pc = <IF-insn>    pc = <IF-insn + 1>               (fall-through)
guard = g         guard = g ∧ ¬c
                  queued in frame.goto_state_map[L]:
                     goto_statet with guard = g ∧ c
```

When the main loop later reaches `L`, `merge_gotos()` fires.

## Merge at join point — `merge_gotos`

`symex_goto.cpp:300`. At the top of every `symex_step`, checks
whether the frame's `goto_state_map` has an entry for the current pc.
If yes:

1. `merge_state_guards(goto_state, *cur_state)` computes the joined
   guard. Any contribution from a branch with `guard=false` is
   absorbed only if it simplifies the resulting guard.
2. `phi_function(goto_state)` — see below.
3. `merge_locality` + `merge_value_sets` — union the aux data.
4. `num_instructions` becomes the min of the two (so depth limits
   don't double-count).
5. Drop the map entry.

## Phi construction — `phi_function`

`symex_goto.cpp:367`. Walks both L2 name maps (`cur_state->level2`
and `goto_state.level2`). For every name whose current-number
differs between the two, it emits:

```
if (goto_state.guard − cur_state.guard) then goto_rhs else cur_rhs
```

where `goto_rhs` / `cur_rhs` are the symbol renamed to the L2 number
each state had. The lhs is a freshly-bumped L2 version, and the step
is emitted via `target->assignment(..., hidden=true)` so it doesn't
show up in counter-examples.

Key things the phi logic filters out:

- `guard_identifier_s` — the auxiliary variables introduced for guard
  atomisation.
- `symex::invalid_object*`, `symex_throw::thrown_obj*` — internal
  bookkeeping symbols.
- Names present only in one side (the one that merged-in deleted it).

Edge case: if `ns.lookup(variable.base_name)` returns null — a
frontend bug where a renamed variable refers to a non-existent
symbol — the function logs a warning and skips instead of
null-deref-crashing.

## Backward branch — loop unwinding

When `instruction.is_backwards_goto()` is true:

1. Bump `cur_state->loop_iterations[loop_id]`.
2. Check `get_unwind(source, unwind)` — returns true if the counter
   exceeds `max_unwind` (either global `--unwind` or a per-loop
   override from `--unwindset`).
3. If exceeded: `loop_bound_exceeded(new_guard)`:
   - With default (`--no-unwinding-assertions` NOT set): emit
     `claim(¬guard, "unwinding assertion loop N")`. If the SMT formula
     shows the loop could have taken one more iteration, the
     verifier reports the unwinding assertion failure — a clear
     unsoundness signal.
   - With `--no-unwinding-assertions`: emit an **assumption**
     `¬guard` onto the target. This silently truncates the loop; any
     iterations beyond `max_unwind` are vacuously assumed not to
     happen. See `feedback_silent_truncation_flags.md` in memory for
     why this is an unsoundness footgun — and
     `feedback_unwinding_assertion_not_a_bug.md` for why its flip side
     (a spurious "unwinding assertion" failure) is not itself a bug.
   - Either way, adds `¬guard` to the state guard so no further
     body-of-loop steps can fire.
4. Reset the iteration counter to zero.
5. Reset `first_loop` if this was the outer k-induction loop.

If the unwind limit is **not** exceeded, same branching path as the
forward case — one `cur_state` goes round the loop once more, the
other is queued for the post-loop join.

### k-induction interaction

- `first_loop` (set at `symex_main.cpp:229`) records the first loop
  that enters the inductive step. This is the loop whose assertions
  get the assert-to-assume conversion on non-last iterations.
- When `cur_state->source.pc == goto_target` on a backward GOTO (a
  tight self-loop), the unwind path is short-circuited: emit an
  assume of the negated guard and fall through. This prevents a
  `while(1)` from consuming the whole unwind budget in one step.

## Guard identifier

`symex_goto.cpp:225` — `guard_identifier()` produces a fresh symbol
(`goto_symex$$guard#N` or similar) used to avoid replicating a large
guard expression across both branches of an if. The freshly-assigned
guard symbol is emitted to the target so that the downstream SMT
conversion can reuse it by name.

## `partial_loops` option

Set by `--partial-loops`: `loop_bound_exceeded` silently returns
without emitting either an assertion or an assumption. The loop is
cut off, but no bound constraint is added — exploration continues
outside the loop with whatever state the last unwound iteration
produced. This is the weakest of the three options and should only be
used for quick probes, never for soundness claims.

## Branching witness emission

The `--witness-output-yaml` hook (`symex_goto.cpp:82`) records forward
branches with non-constant conditions. The `flipped_guard` bit (set
by `optimize_guarded_gotos` during GOTO conversion) is used to restore
the canonical "true means body was reached" direction — without this,
the witness would invert the sense for any goto the optimiser flipped.

## Common pitfalls

- **"Phi isn't firing for my variable"** — both sides need to have
  the variable in their L2 name map with *different* current-numbers.
  A variable written only under the taken branch won't phi if the
  fall-through never saw a DECL for it.
- **"The unwind counter reset to zero mid-loop"** — `new_guard_false`
  (the branch is provably not taken) also resets
  `loop_iterations[loop_id] = 0`. This is correct for escaping the
  loop but surprising if you expected the counter to persist across
  re-entry.
- **"Two phi assignments fight over the same SSA version"** — two
  successive merges at the same pc are processed in reverse iteration
  order (`rbegin`→`rend`), so the newest queued state is merged last.
  Debug with `--symex-trace`.
- **"`unwinding assertion loop N` fires in normal code"** — memory
  `feedback_unwinding_assertion_not_a_bug.md`: this is the oracle
  signal that the user's `--unwind N` was too tight, not a bug. Use
  `--incremental-bmc` or `--k-induction` for a correct auto-bound.
