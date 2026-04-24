# GOTO-Programs Architecture

## Pipeline

```
Language frontend (e.g. clang-c / clang-cpp / python / solidity)
  └── irep2 AST tree (in symbol table)
  └── goto_convert.cpp (1877 LOC) — AST → flat goto_programt
        └── One goto_programt per function; list of instructiont
  └── goto_functionst (map: function name → (goto_programt, type))

A cascade of transform passes, in bmct::run_decision_procedure
(see src/esbmc/bmc.cpp) call chain:

  - goto_check.cpp           — insert NULL deref / OOB / overflow checks
  - remove_no_op.cpp         — drop SKIP/LOCATION
  - remove_unreachable.cpp   — drop code past assume(false)
  - goto_inline.cpp          — inline function calls (optional)
  - goto_loop_invariant.cpp  — insert loop-invariant assumptions
  - loop_numbers.cpp         — number each loop uniquely
  - goto_k_induction.cpp     — k-induction havoc insertion (only
                               under --k-induction)
  - mark_decl_as_non_det.cpp — optional: havoc local decls
  - goto_atomicity_check.cpp — ATOMIC_BEGIN/END balancing

Output: goto_functionst consumed by symex (src/goto-symex/)
```

Each pass reads and writes the `goto_functionst`; passes are
idempotent after their first run.

## Core data structures

### `goto_programt` (`goto_program.h:56`)

Linked list of `instructiont`. Every instruction has:

- `type : goto_program_instruction_typet` (see
  [instructions.md](instructions.md))
- `code : expr2tc` — the lowered irep2 expression for this step
- `guard : expr2tc` — condition (for GOTO / ASSERT / ASSUME)
- `location : locationt` — source file / line / column
- `function : irep_idt` — which function this belongs to
- `targets` — list of `iterator` into this list (for GOTO targets)
- `labels` — source-level labels at this point
- `loop_invariants` / `loop_assigns_targets` — for
  `LOOP_INVARIANT` instructions
- `inductive_step_instruction : bool` — set by `goto_k_induction`
  on the synthesised havoc ASSIGNs
- `inductive_assertion : bool` — set by bmc on the
  k-induction-driver-inserted assertion (the "I(k) final check")
- `flipped_guard : bool` — set by `optimize_guarded_gotos` when
  `IF !cond GOTO skip; GOTO target` was folded into
  `IF cond GOTO target`
- `location_number` — per-instruction unique monotonic ID (used
  to compare iterators across mutations)
- `loop_number` — set by `loop_numbers.cpp`; 0 = not in a loop
- `target_number` — if nonzero, this instruction is a branch target
- `pragma_unroll_count` — source-hint for `#pragma unroll`

Convenience: `make_goto` / `make_return` / `make_function_call` /
`make_assertion` / `make_assumption` / `make_assignment` /
`make_decl` / etc. each `clear()` the instruction to a default
state then set `type`.

Predicates: `is_goto` / `is_return` / `is_assign` etc. are
one-liners testing the `type` field.

### `goto_functionst`

Map `irep_idt → (goto_programt body, code_type2t type)`, one entry
per function. Has `update()` which recomputes per-instruction
`location_number` and numbers targets.

## `goto_convert.cpp` — AST → flat CFG

1877 LOC. Walks irep2 code expressions (statements: `code_block`,
`code_assign`, `code_if`, `code_for`, `code_while`, `code_switch`,
`code_break`, `code_continue`, `code_return`, etc.) and emits an
instruction list.

Notable translations:

- **`if`** — GOTO false-branch, fall through true-branch, GOTO end.
- **`while (c) { body }`** — label L0; `IF !c GOTO L1`; body;
  `GOTO L0`; label L1. Backward GOTO marks the loop.
- **`for (init; c; inc) { body }`** — init; while with inc at
  body end.
- **`switch`** — chain of `IF eq(expr, case_k) GOTO label_k`; default
  fall-through.
- **`break` / `continue`** — tracked per enclosing construct on a
  stack; emit GOTO to the right escape label.
- **`return`** — assign to return lvalue; GOTO end-of-function.
- **Function calls in expressions** — hoisted to pre-FUNCTION_CALL
  ASSIGNs; the call instruction binds actuals and return; the
  result is the call's ret lvalue.
- **Side effects** (`x++`, `x += y`, post/pre inc/dec) — lowered
  to temporaries + ASSIGNs.
- **Destructor calls** (C++) — inserted via `destructor.cpp` at
  scope exit.

The resulting CFG has no implicit control flow — every branch is
a GOTO, every loop is backward-GOTO, every function call is
FUNCTION_CALL. This is the IR symex operates on.

## Transform passes

### Analysis passes (read-only)

- `goto_loops.cpp` (198 LOC) — detect loops, compute modified
  variable sets (see [loops-and-k-induction.md](loops-and-k-induction.md)).
- `goto_cfg.cpp` — control-flow graph construction.
- `abstract-interpretation/` subdir — interval / constant
  propagation domains.
- `static_analysis.cpp` — fixedpoint driver for abstract domains.
- `rw_set.cpp` — read/write set per instruction for concurrency.
- `show_claims.cpp` — enumerate assertions (`--show-claims`).
- `goto_coverage.cpp` — coverage profile emitter.

### Mutation passes (rewriters)

- `goto_check.cpp` — insert runtime-check assertions (NULL deref,
  OOB array, overflow, divide-by-zero). Gated by
  `--no-pointer-check` / `--no-bounds-check` / etc.
- `remove_no_op.cpp` — delete SKIP/LOCATION instructions.
- `remove_unreachable.cpp` — delete instructions past an
  assume(false).
- `goto_inline.cpp` — inline function calls (optional; expensive
  but exposes more optimisation).
- `goto_loop_invariant.cpp` — lower `LOOP_INVARIANT` to explicit
  assume+havoc at loop head.
- `goto_k_induction.cpp` (359 LOC) — see
  [loops-and-k-induction.md](loops-and-k-induction.md).
- `goto_contractor.cpp` — use interval contractors for loop bounds.
- `loop_unroll.cpp` — unroll small bounded loops (for some
  optimisations).
- `loop_numbers.cpp` — assign a unique integer to each loop.
- `mark_decl_as_non_det.cpp` — mark locals as having nondet initial
  values.
- `goto_coverage_rm.cpp` — inverse of `goto_coverage`, remove
  coverage instrumentation.
- `destructor.cpp` — insert C++ destructor calls at scope exit.
- `frame_enforcer.cpp` — emit assignments for "modifies" contracts.
- `set_claims.cpp` — activate / deactivate assertions by name.
- `goto_atomicity_check.cpp` — verify ATOMIC_BEGIN/END balance.
- `goto_sideeffects.cpp` — remove any leftover side-effects.
- `add_race_assertions.cpp` — data-race assertion insertion.

### Serialisation

- `goto_binary_reader.{cpp,h}` + `read_bin_goto_object.{cpp,h}` —
  load pre-compiled GOTO binaries.
- `write_goto_binary.{cpp,h}` — write them.
- `goto_program_serialization.cpp` + `goto_function_serialization.cpp` —
  irep2-level serialisation.
- `goto_program_irep.cpp` — convert goto_programt ↔ irept tree
  (for debugging / dumps).

## Key files at a glance

| File | LOC | Purpose |
|---|---|---|
| `goto_program.h` | 730 | `goto_programt` + `instructiont` + instruction types |
| `goto_program.cpp` | — | instruction output, copy, update |
| `goto_convert.cpp` | 1877 | AST → GOTO — the biggest pass |
| `goto_convert_functions.cpp` | 453 | per-function driver around goto_convert |
| `goto_check.cpp` | — | runtime-check assertion injection |
| `goto_inline.cpp` | — | function-call inlining |
| `goto_k_induction.cpp` | 359 | k-induction loop transform (havoc + assume-hypothesis) |
| `goto_loops.cpp` | 198 | loop discovery + modified-var analysis |
| `loopst.{cpp,h}` | 90 + 49 | `loopst` data (modified / unmodified vars, head / exit) |
| `goto_functions.h` | — | `goto_functionst` container |
| `goto_function.cpp` | — | per-function helpers |
| `loop_numbers.{cpp,h}` | — | assign unique loop_number to each back-edge |
| `goto_contractor.cpp` | — | interval contractor for loop bounds |
| `goto_loop_invariant.cpp` | — | user-provided loop invariant lowering |
| `loop_unroll.{cpp,h}` | — | bounded loop unrolling |
| `mark_decl_as_non_det.{cpp,h}` | — | `DECL`-havocer |
| `remove_no_op.cpp` | — | SKIP/LOCATION removal |
| `remove_unreachable.cpp` | — | post-assume(false) removal |
| `goto_atomicity_check.cpp` | — | ATOMIC_*  balance |
| `static_analysis.cpp` | — | fixedpoint loop |
| `abstract-interpretation/` | — | interval / constant-prop domains |
| `add_race_assertions.cpp` | — | data-race instrumentation |
| `builtin_functions.cpp` | — | frontend-side intrinsics (__ESBMC_*) |
| `set_claims.cpp` | — | claim activation by name / number |
| `show_claims.{cpp,h}` | — | `--show-claims` enumeration |
| `destructor.{cpp,h}` | — | C++ destructor insertion |
| `goto_cfg.{cpp,h}` | — | CFG building + dominance |
| `rw_set.{cpp,h}` | — | per-instruction R/W set |
| `contracts/` | — | function-contract machinery |

## Reading order for a fresh contributor

1. [architecture.md](architecture.md) — this file.
2. [instructions.md](instructions.md) — the 21 instruction types.
3. [loops-and-k-induction.md](loops-and-k-induction.md) — the
   bug-carrying transform.
4. For any pass you need to modify: read its `.cpp` directly —
   most passes are small and self-contained.

## Relation to symex and solvers

- `src/goto-programs/` produces the GOTO CFG.
- `src/goto-symex/` walks the CFG executing each instruction,
  building an SSA target equation
  ([symex docs](../symex/README.md)).
- `src/solvers/` lowers the SSA equation to SMT
  ([solvers docs](../solvers/README.md)).

The critical interface is `goto_functionst` (produced by this
layer) and the `symex_target_equationt` (consumed by solvers).
Transform passes here may insert GOTO instructions that encode
verification-tactic decisions (k-induction havoc, loop unwind
markers) which symex then sees as ordinary instructions.
