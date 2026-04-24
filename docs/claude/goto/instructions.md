# GOTO Instruction Types

21 instruction types defined in `goto_program.h:26-48`. This is the
complete vocabulary of the GOTO IR.

| Type | Code | Payload | Guard | Meaning |
|---|---|---|---|---|
| `NO_INSTRUCTION_TYPE` | 0 | — | — | Uninitialised (sentinel, should never appear after `update()`) |
| `GOTO` | 1 | — | `guard` | Branch to `targets.front()` if guard true; guard=true is unconditional |
| `ASSUME` | 2 | — | `guard` | Tell the solver `guard holds`; paths where guard is false are pruned |
| `ASSERT` | 3 | — | `guard` | Check that guard holds; if not, verification fails with this instruction's location |
| `OTHER` | 4 | `code` | — | Misc. action lowered to an expression (e.g. a `printf`, a data-race marker, a nondet init) |
| `SKIP` | 5 | — | — | No-op; kept for structural reasons (removed by `remove_no_op` pass) |
| `LOCATION` | 8 | — | — | No-op but with a `location` field; carries source-line info for lines containing no other code |
| `END_FUNCTION` | 9 | — | — | End-of-function marker; triggers symex frame pop |
| `ATOMIC_BEGIN` | 10 | — | — | Begin atomic section (no context switches) |
| `ATOMIC_END` | 11 | — | — | End atomic section |
| `RETURN` | 12 | `code` (code_return2t) | — | Return from the current function; `code.operand` is the return value |
| `ASSIGN` | 13 | `code` (code_assign2t) | — | `lhs = rhs` |
| `DECL` | 14 | `code` (code_decl2t) | — | Declare a local variable (lifetime start) |
| `DEAD` | 15 | `code` (code_dead2t) | — | End-of-life for a local (lifetime end) |
| `FUNCTION_CALL` | 16 | `code` (code_function_call2t) | — | `ret = fn(args)` |
| `THROW` | 17 | — | — | C++ throw |
| `CATCH` | 18 | — | — | C++ catch entry |
| `THROW_DECL` | 19 | — | — | Function-level "can throw these types" declaration |
| `THROW_DECL_END` | 20 | — | — | End of THROW_DECL scope |
| `LOOP_INVARIANT` | 21 | `loop_invariants[]` | — | User-provided loop invariant; lowered by `goto_loop_invariant.cpp` |

## The four you'll see most

- **`GOTO`** — all branches. Back-edges (`target->location_number <
  this->location_number`) mark loops. Unconditional GOTOs have
  `guard = true_expr`.
- **`ASSIGN`** — `lhs = rhs` with `code : code_assign2t`. The most
  common; every dataflow step in the program.
- **`ASSERT`** — verification checks. `guard` holds the condition
  under which the property must be true.
- **`FUNCTION_CALL`** — `code : code_function_call2t` with `function`
  (the callee), `ret` (lvalue for return), `operands` (actual
  arguments).

## Convenience helpers

`instructiont` exposes constructor helpers for every type:
`make_goto` / `make_assertion` / `make_assumption` / `make_assign` /
`make_decl` / `make_dead` / `make_skip` / `make_other` / ...
(`goto_program.h:176-264`).

Each of these calls `clear(type)` which resets every field and sets
`type`. So when building GOTO programs manually, do NOT skip the
helper — fields left uninitialised can leak between instructions.

## Predicates

`is_goto`, `is_return`, `is_assign`, `is_function_call`,
`is_throw`, `is_catch`, `is_skip`, `is_location`, `is_other`,
`is_decl`, `is_dead`, `is_assume`, `is_assert`, `is_loop_invariant`,
`is_atomic_begin`, `is_atomic_end`, `is_end_function`.

Plus `is_backwards_goto()` (GOTO with any target whose
`location_number` is less than this instruction's).

## The k-induction-specific flags

Two bool fields on `instructiont` that change symex behaviour:

- **`inductive_step_instruction`** — set by `goto_k_induction.cpp`
  on the havoc ASSIGNs it inserts before a loop. Symex skips these
  during base-case and forward-condition runs (see
  `src/goto-symex/execution_state.cpp:214`).
- **`inductive_assertion`** — set by
  `src/esbmc/bmc.cpp:1330` on the assertion the k-induction driver
  inserts after the loop to check the invariant. Excluded from the
  assert-to-assume conversion at `src/goto-symex/symex_main.cpp:134`.

## Loop metadata

Every instruction has:

- `loop_number : unsigned` — 0 if not in a loop; else a unique
  integer per loop (assigned by `loop_numbers.cpp` via DFS of the
  control-flow graph).
- `pragma_unroll_count : unsigned` — `#pragma unroll N` hint from
  source.

Loop detection happens after `goto_convert` and before `loop_numbers`.
The `loopst` container in `loopst.h` records per-loop `head` and
`exit` iterators plus the sets of modified/unmodified variables
(see [loops-and-k-induction.md](loops-and-k-induction.md)).

## Targets

GOTO-type instructions hold `targets : list<iterator>`. Almost always
exactly one — the `get_target()` helper asserts that.

Exception: THROW / CATCH / function-pointer dispatch can have
multiple. See `goto_convert.cpp` for how these are built.

`target_number` is the reverse index: if nonzero, this instruction is
a branch target and `target_number` is its ordinal (1-based).

## Labels

Source-level labels (`label: stmt`) attach to instructions via the
`labels` list. Multiple labels can be on one instruction (e.g. `a: b:
stmt;`). Used for GOTO source-reconstruction and error messages.

## `flipped_guard`

Set by `optimize_guarded_gotos`. Original emission:
```
IF !cond GOTO skip      ; guard = !cond
GOTO body               ; guard = true
skip:
```
Optimised to:
```
IF cond GOTO body       ; guard = cond, flipped_guard = true
```

The flag is honoured by `src/goto-symex/symex_goto.cpp:91` to emit
the correct branching-witness sense under `--witness-output-yaml`.

## Iteration macros

`goto_program.h:14-24` defines:

- `forall_goto_program_instructions(it, program)` — const iteration
- `Forall_goto_program_instructions(it, program)` — mutable iteration

Use these when walking an entire program; the naked iterator form
is fine for targeted updates.

## Debugging

- `--show-goto-functions` — dump the full GOTO program after
  transforms, before symex.
- `--show-program` — same but broken down by function.
- `--show-claims` — list every ASSERT with its index and location.
- `--unwind N` — bound the number of back-edges taken per loop.
- `ostream << instructiont` — prints a human-readable form
  (type, guard, code, targets).

## Common pitfalls

- **"Instruction types are 0"** — you copied an `instructiont`
  without running `clear()` first. Always use `make_*` helpers.
- **"GOTO targets are wrong"** — targets are iterators, not
  pointers. Iterator invalidation from `insert` / `erase` rebuilds
  targets. Use `goto_functions.update()` after mutations.
- **"loop_number is 0 on an instruction inside a loop"** —
  `loop_numbers.cpp` hasn't run yet. Most transform passes rely on
  this being set, so the order matters.
- **"ATOMIC imbalance abort"** — `goto_atomicity_check` verifies
  BEGIN/END pairs. A transform that splits a block containing an
  atomic marker has to preserve the balance.
