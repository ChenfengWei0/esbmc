# Symex Architecture

## Pipeline at a glance

```
GOTO program ─► symex_step dispatch ─► SSA equation ─► slice ─► SMT formula ─► solver
                └── goto_symext                           └── symex_target_equationt
                    └── goto_symex_state (per-thread)
                        └── renaming (l0/l1/l2)
                        └── value_set (points-to)
                        └── call_stack / pc / guard
                └── reachability_treet (explores thread interleavings)
                └── execution_statet (global context wrapping one or more goto_symex_states)
```

The symex driver consumes a GOTO program (already produced by the
frontend + goto-transformer passes), symbolically executes it along
every reachable path, and **writes** each executed instruction as an
SSA-form "step" into `symex_target_equationt`. Downstream (outside this
subsystem) the equation is passed to `smt_convt` which flattens and
hands it to the solver.

`goto_symext` holds very little state itself (see its header comment);
state lives in `goto_symex_state` (per-thread program counter + call
stack + renaming + value-set) and `execution_state` (the global
"level 2" renaming + set of threads). The reachability tree drives
interleaving exploration by repeatedly replaying the program under
different context-switch choices.

## File responsibilities

### Core driver (`src/goto-symex/`)

| File | Responsibility |
|---|---|
| `goto_symex.h` | `class goto_symext` declaration; the interpreter interface. Almost no state, lots of methods — state lives in `goto_symex_state`. |
| `symex_main.cpp` | `symex_step` dispatch loop; top-level per-instruction handler; unwinding counter; loop detection; how the interpreter advances PC through the goto program. 1514 LOC — by far the biggest single file. |
| `symex_assign.cpp` | `ASSIGN` lowering: lhs/rhs processing, struct field writes (`WITH`), array element writes, tracking what the active frame considers "written". Relevant to k-induction havoc write-set analysis. |
| `symex_goto.cpp` | `GOTO`/branch/merge; phi construction at join points; loop entry; guard accumulation. |
| `symex_function.cpp` | `FUNCTION_CALL`: argument binding, return-value assignment, recursion-bound enforcement, nondet substitution for body-less functions. Pointer-passed args cross the boundary here. |
| `symex_dereference.cpp` | Entry point for `*p` handling; thin layer over `pointer-analysis/dereference.cpp` which does the real work. |
| `symex_catch.cpp` | Exception handling (`THROW`/`CATCH`). Mostly C++ / Java. |
| `symex_valid_object.cpp` | Pointer validity / allocation tracking assertions. |
| `symex_other.cpp` | Misc small handlers: `ATOMIC_*`, `OUTPUT`, `RENUMBER`, etc. |
| `symex_stack.cpp` | Call-stack frame manipulation helpers. |
| `symex_target.cpp` / `symex_target.h` | Abstract `symex_targett` interface — lets symex emit steps into a concrete sink. |
| `symex_target_equation.{cpp,h}` | Concrete sink: `symex_target_equationt` stores a list of `SSA_stept`. Also drives `convert_internal_step` which feeds steps to `smt_convt`. |
| `builtin_functions.cpp` | Intrinsics (e.g. `__ESBMC_assume`, allocator hooks, pthread internals) — bypass normal symex for library-defined semantics. |
| `dynamic_allocation.cpp` | `malloc`/`calloc`/`alloca` modeling: tracks `alloc`, `alloc_size`, `is_dynamic` infinite arrays. |

### State (`src/goto-symex/`)

| File | Responsibility |
|---|---|
| `goto_symex_state.{cpp,h}` | One thread's state snapshot: PC, call-stack (`framet` list), guard, per-frame local-rename maps, value-set. |
| `execution_state.{cpp,h}` | Global wrapper around ≥1 per-thread `goto_symex_state`s; owns the "level-2" (global) rename map and global value-set. |
| `renaming.{cpp,h}` | Three SSA rename levels: `level0` (original name), `level1` (per-stack-frame unique: `name&0`), `level2` (per-assignment version: `name&0#N`). |
| `reachability_tree.{cpp,h}` | Drives multi-path and multi-thread exploration. Each iteration yields one `symex_target_equationt` representing one scheduled execution. |
| `reachability_tree_cin.cpp` | CIN-related helpers — small. |

### Pointer / value-set (`src/pointer-analysis/`)

| File | Responsibility |
|---|---|
| `value_set.{cpp,h}` | Flow-sensitive per-state points-to map `pointer → set of referent objects`. Updated on every pointer assignment; merged at phi points. |
| `value_set_analysis.{cpp,h}` | Flow-insensitive static analysis variant; runs pre-symex to produce an initial points-to map. |
| `value_set_domain.{cpp,h}` | Abstract domain wrapper for the static analysis. |
| `dereference.{cpp,h}` | The translator `*p → chain of if-expressions over value-set`. 2682 LOC — bulk of the pointer-analysis logic. |
| `goto_program_dereference.cpp` | Rewrites dereferences in the GOTO program itself (used by some passes outside symex proper). |
| `show_value_sets.cpp` | Debug dump helper. |
| `README.md` | Upstream-authored overview of the memory model — **read this before editing dereference**. |

### Trace / output (`src/goto-symex/`)

| File | Responsibility |
|---|---|
| `goto_trace.{cpp,h}` / `build_goto_trace.{cpp,h}` | Witness / counter-example construction from a satisfying SAT/SMT assignment. |
| `slice.{cpp,h}` | Equation slicer: drops unused assignments and assertions before SMT. Can silently hide reachable bugs if the slicer is over-aggressive — relevant to some of this repo's silent-drop reports. |
| `html.cpp` / `json.cpp` / `xml_goto_trace.cpp` / `witnesses.cpp` | Format-specific trace emitters (SV-COMP, browser, etc). |
| `ctest.cpp` / `pytest.cpp` | Test-case generation from counter-examples. |
| `printf_formatter.cpp` | Pretty-print `printf`-style values in traces. |

## Directory-to-topic mapping

| If you're touching ... | Likely topic docs |
|---|---|
| `symex_main.cpp` | [main-loop.md](main-loop.md) |
| `symex_assign.cpp` | [assign-and-write-set.md](assign-and-write-set.md) |
| `symex_function.cpp` / `symex_goto.cpp` | [function-calls.md](function-calls.md) / [goto-branching.md](goto-branching.md) |
| `symex_dereference.cpp` + `pointer-analysis/dereference.cpp` | [dereference.md](dereference.md) |
| `pointer-analysis/value_set*` | [value-set.md](value-set.md) |
| `reachability_tree.cpp` + `execution_state.cpp` | [main-loop.md](main-loop.md) |
| `goto_symex_state.cpp` + `renaming.cpp` | [state.md](state.md) |
| `symex_target_equation.cpp` | [target-equation.md](target-equation.md) |
| `slice.cpp` | [slice.md](slice.md) |
| k-induction driver (lives in `src/esbmc/` + `symex_main.cpp` hooks) | [k-induction.md](k-induction.md) |

## Reading order for a fresh contributor

1. [state.md](state.md) — the vocabulary (frame, rename level, guard).
2. [main-loop.md](main-loop.md) — how `symex_step` is called in a loop.
3. [assign-and-write-set.md](assign-and-write-set.md) — the most common
   instruction and the data flow it produces.
4. [goto-branching.md](goto-branching.md) + [function-calls.md](function-calls.md) —
   the two control-flow primitives.
5. [dereference.md](dereference.md) + [value-set.md](value-set.md) —
   the pointer machinery that makes non-trivial C/C++ / Solidity
   programs work.
6. [target-equation.md](target-equation.md) + [slice.md](slice.md) —
   the output side.
7. [k-induction.md](k-induction.md) — the proof mode on top of all of
   the above.
