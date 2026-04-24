# ASSIGN Lowering & What Counts as "Written"

`src/goto-symex/symex_assign.cpp` (1018 LOC). Single entry point
`goto_symext::symex_assign(code_assign, hidden, guard)` at line 316 with
a recursive case dispatcher `symex_assign_rec` at line 390.

This file matters for three reasons:

1. Every value the SMT formula ever talks about flows through it —
   phi merges, function argument binding, and return-value assignment
   are all just `symex_assign` calls.
2. It decides *when a fresh SSA version is minted*, via its call to
   `cur_state->assignment(lhs, rhs)` inside `symex_assign_symbol`.
3. It tracks what is written inside a loop body — the "write set" —
   which feeds the k-induction havoc decision (see
   [k-induction.md](k-induction.md)).

## Pipeline

```
symex_assign(code_assign, hidden, guard=true)
  ├── early return if struct target has zero members
  ├── replace_nondet(lhs) / replace_nondet(rhs)
  ├── volatile_check(rhs)
  ├── dereference(lhs, WRITE)       ← may turn *p=v into a chain of conditional writes
  ├── dereference(rhs, READ)
  ├── replace_dynamic_allocation(lhs/rhs)
  ├── replace_races_check(lhs)
  ├── simplify_python_builtins(rhs)
  ├── if (is_code_printf2t(rhs))   → symex_printf and return
  ├── if (is_sideeffect2t(rhs))    → handle_sideeffect and return
  ├── if (is_if2t(rhs))            → handle_conditional (lets rhs.cond branch symex_assign)
  └── symex_assign_rec(lhs, original_lhs, rhs, nil, guard, hidden_ssa)
```

`symex_assign_rec` is the shape-dispatcher on `lhs`:

| `lhs` shape | Handler | Rewrite |
|---|---|---|
| `symbol` | `symex_assign_symbol` | emits the SSA step |
| `index(a, i)` | `symex_assign_array` | rewrites `a[i]=e` → `a = a WITH [i:=e]`, recurses on `a` |
| `member(s, f)` | `symex_assign_member` | rewrites `s.f=e` → `s = s WITH [f:=e]`, recurses on `s` |
| `if(c, t, f)` | `symex_assign_if` | splits into two guarded assigns on `t` and `f` |
| `typecast` / `bitcast` | `symex_assign_typecast` | peels the cast, adjusts rhs |
| `byte_extract` | `symex_assign_byte_extract` | wraps rhs with `byte_update`, recurses |
| `concat` | `symex_assign_concat` | splits lhs into pieces; recurse per piece |
| `extract` | `symex_assign_extract` | bitfield write via extract |
| `bitand` | `symex_assign_bitfield` | bitfield write via bitand mask |
| `constant_struct` | `symex_assign_structure` | member-wise recursion |
| `constant_union` | `symex_assign_union` | union tag handling |
| `constant_string` / `null_object` | — | ignored |

The key invariant: by the time the recursion reaches a `symbol` leaf,
all the compound `WITH`/`byte_update`/`extract` wrappers have been
pushed into the rhs, so a single new SSA version of the whole base
symbol is emitted.

## `symex_assign_symbol` (the SSA step emitter)

`symex_assign.cpp:453`. Shape:

```cpp
// 1. Guard-conditional rhs: if (!guard.is_true()) rhs = (guard ? rhs : lhs)
if (!guard.is_true())
  rhs = if2tc(rhs->type, guard.as_expr(), rhs, lhs);

// 2. Rename both sides
cur_state->rename(rhs);                       // L1+L2 on all symbols in rhs
do_simplify(rhs);                             // constant-fold
expr2tc renamed_lhs = lhs;
cur_state->rename_type(renamed_lhs);          // renaming may have changed types
cur_state->assignment(renamed_lhs, rhs);      // ← mints fresh L2 on lhs

// 3. Emit onto the target equation
target->assignment(
  tmp_guard.as_expr(),                        // current state guard ∧ assign guard
  renamed_lhs, new_lhs, rhs, full_rhs,
  cur_state->source,
  cur_state->gen_stack_trace(),
  hidden, first_loop);
```

Two things worth naming explicitly:

- **The guard fold on rhs** (`rhs = guard ? rhs : lhs`) is how the
  write-set tracker sees the assignment as unconditional for SSA
  numbering purposes while the formula still respects the guard. An
  extra conditional version bump is what makes the "versions always
  refer to concrete writes" invariant hold at phi points.
- **`cur_state->assignment` vs `target->assignment`** are two
  different operations. The former updates the per-state L2 map (mints
  the new version number). The latter records the step in the output
  equation. Both have to be called to "perform" an assignment.

## WITH — struct/array field writes

`symex_assign_array` (`symex_assign.cpp:650`):

```
    a[i] = e
→   a = a WITH [i := e]
→   (recurse into symex_assign_rec on `a`)
```

Similarly for member:

```
    s.f = e
→   s = s WITH [f := e]
```

This matters because the **write** is attributed to the *base symbol*
(`a` or `s`), not to the element/field. Under k-induction havoc (see
below), if the base symbol of a WITH-write is known, havoc can zap the
whole container at the top of the loop; if it isn't (e.g. the lhs is
`*p[i]` where `p` is a function-argument pointer), the write may not
be attributed to any frame-local variable, and havoc misses it.

## Bitfield / byte-level writes

`symex_assign_byte_extract` / `symex_assign_bitfield` /
`symex_assign_extract` / `symex_assign_concat` handle the cases where
the lhs is a sub-bit-range of a wider value. They all share the same
shape: rewrite the rhs to be "base with this slice updated", then
recurse with the wider base as the new lhs. This way the SSA step is
still "base = new_base", a single symbol assignment at the leaf.

## `handle_sideeffect` and `handle_conditional`

`symex_assign.cpp:228` and `:285`:

- `handle_sideeffect(lhs, sideeff, guard)` — dispatches
  `SIDE_EFFECT_*` expressions (`malloc`, `cpp_new`, `va_arg`, `printf`,
  function calls with return, etc.). Each sideeffect kind lowers to
  its own sequence of symex calls, often spawning an auxiliary
  assignment back to `lhs` with a freshly-computed result.
- `handle_conditional(lhs, if_expr, guard)` — if the rhs is an
  `if2t`, it recognizes patterns where recursing through the condition
  produces simpler SSA than emitting one monolithic assignment. When
  it handles the case, it returns true and the outer `symex_assign`
  returns early.

## `analyze_assign` — write detection

`execution_state.cpp:781` (not in `symex_assign.cpp`, but closely
related). After every ASSIGN, the execution state calls
`analyze_assign(code)` which (a) pokes the global-read/write maps used
by the cswitch-point detection, and (b) populates
`is_global` — the set of symbol expressions that were ever written
through. This is *separate* from the k-induction havoc machinery, but
uses overlapping concepts.

## `replace_nondet`

`symex_assign.cpp:1002`. Walks an expression tree. Every `sideeffect`
whose kind is `nondet` gets rewritten to a fresh
`symbol: nondet$symex::<counter>`, with a monotonically-incremented
counter. That way two textually identical `nondet_int()` calls get
different symbols and can pick different values.

## "What counts as written in the loop body" — for k-induction

When k-induction is in inductive-step mode, a havoc preamble is
emitted *before* the loop. That preamble has to nondet every variable
that the loop body writes. The decision is driven by a separate
analysis in the GOTO conversion pipeline (not symex itself):
`goto_programs/loop_analysis.cpp` and `goto_programs/havoc_loops.cpp`
walk the loop body and collect:

- `lhs` of every ASSIGN inside the body, reducing compound WITH/member
  rewrites back to the base symbol;
- `call.ret` of every FUNCTION_CALL with a non-nil return;
- `call.operands[i]` that is an `address_of(local)` (pointer-by-arg),
  with reducibility to the pointed-to object — **this is the step
  that misses mutations through pointer-to-global**.

The havoc set is then attached to `inductive_step_instruction`s
prepended before the loop. Symex sees those instructions first and
emits `local = nondet_symbol(...)` assigns. Anything not in that set
keeps its pre-loop value going into the induction step — which, if
silently wrong, is unsound.

See [k-induction.md](k-induction.md) §Bugs for the specific
`dispatch(&global_struct)` failure mode.

## Debugging tips

- **Non-emitted assignment** — did `replace_nondet` rewrite the rhs
  into a nondet symbol that is then constant-propagated away? Check
  `--no-simplify` to get the raw SSA.
- **Pointer-lhs assignments** — `dereference(lhs, WRITE)` may fan out
  one `symex_assign` call into many, one per value-set candidate. Look
  at the value-set with `--show-symex-value-sets` right before the
  ASSIGN.
- **"The SSA version of `x` didn't bump"** — check
  `cur_state->assignment` → `level2.make_assignment`. It may have
  suppressed bumping because the rhs simplified to the same expression
  as the current constant value.
- **Hidden SSA steps** — `hidden=true` steps are suppressed from
  counter-examples but still participate in the SMT formula. If a
  witness is missing an assignment you expect, check the `hidden`
  argument path: it gets force-set when the base symbol's name
  contains `$` or `__ESBMC_`.
