# ESBMC Symbolic Execution — Developer Docs

Topic-indexed map of `src/goto-symex/` (22k LOC, 29 .cpp files) and the
sister `src/pointer-analysis/` (5k LOC) that it drives. Written for
Claude / human devs who need to land a change without re-reading the
whole subsystem each time.

**When you want to understand**:

| Question | Start here |
|---|---|
| What is the overall pipeline GOTO → SSA → SMT? | [architecture.md](architecture.md) |
| How does per-thread state evolve? Renaming? SSA names? | [state.md](state.md) |
| Who drives `symex_step`? How are instructions dispatched? | [main-loop.md](main-loop.md) |
| Where does an `ASSIGN` lhs=rhs turn into an SSA step? | [assign-and-write-set.md](assign-and-write-set.md) |
| How is k-induction's base/forward/inductive split implemented? | [k-induction.md](k-induction.md) |
| Where does `*p` resolve to the object(s) it may point at? | [dereference.md](dereference.md) |
| What is a value-set? When is it refined / merged? | [value-set.md](value-set.md) |
| How does a `GOTO`/branch produce phi merges? | [goto-branching.md](goto-branching.md) |
| Where does a `FUNCTION_CALL` bind arguments, emit body, return? | [function-calls.md](function-calls.md) |
| What step types live in `symex_target_equation`? | [target-equation.md](target-equation.md) |
| Why does `--no-slice` change a verdict? | [slice.md](slice.md) |

**When debugging a soundness surprise**:

- `--show-claims` — enumerate the claims being generated from the goto
  program (most Solidity user-asserts become `Claim N: assertion ...`).
- `--program-only --no-slice` — print the full SSA before slicing, to
  confirm symex reached the site you care about.
- `--ssa-symbol-table` — show symbol table alongside SSA; useful for
  tracing a renamed name back to its source.
- `--k-induction … --max-k-step N --k-step M --show-claims` — reveal
  both the property set and the k-induction phases; the SSA for each
  of B(k), F(k), I(k) is re-emitted from scratch per iteration.

**Known open issues tracked here** (see MEMORY for broader trip-wires):

- k-induction inductive-step havoc misses variables written through
  pointer-passed-to-function — [k-induction.md](k-induction.md) §Bugs.
  Minimal repro: two C programs differing only in
  `dispatch(&obj)` vs `obj.x++` inline diverge in verdict. Affects
  nearly every non-trivial Solidity regression because the harness
  routes every method call through `&_ESBMC_Object_<C>`.
- Value-set offset-granular points-to loss on byte-array-backed struct
  fields holding pointers — [value-set.md](value-set.md) §Bugs. This
  is the language-agnostic reason the old `T**` multi-dim lowering
  aliased cross-row writes (fixed downstream in the Solidity frontend
  via native `array_typet` nesting, but the underlying value-set
  limitation remains).
