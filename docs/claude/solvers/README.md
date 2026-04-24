# ESBMC Solvers — Developer Docs

Topic-indexed map of `src/solvers/` — the SMT reduction layer. Takes
the SSA-form equation built by `src/goto-symex/` and lowers it to an
SMT formula in some backend solver.

**Read before editing**: `src/solvers/README.txt` (upstream author's
overview) and [architecture.md](architecture.md).

**When you want to understand**:

| Question | Start here |
|---|---|
| Pipeline SSA → SMT formula → solver? | [architecture.md](architecture.md) |
| Where is the big expr2tc → smt_ast dispatch? | [smt-conv.md](smt-conv.md) |
| How does ESBMC flatten arrays when a solver lacks array theory? | [array-conv.md](array-conv.md) |
| Why does "array of array with unbounded subtype" assert? | [array-conv.md](array-conv.md) §KNOWNBUG |
| How are pointers encoded? `same_object` / `pointer_offset`? | [memory-model.md](memory-model.md) |
| How are casts / byte_extract / tuples / overflow encoded? | [type-encoding.md](type-encoding.md) |
| z3 vs cvc5 vs bitwuzla — who does what natively? | [backends.md](backends.md) |

**When debugging a solver-layer surprise**:

- `--program-only` / `--program-only --no-slice` — dump the SSA before
  it reaches the solver.
- `--smt-formula-only` — dump the SMT formula without solving.
- `--ssa-smt-trace` / `--ssa-trace` — emit per-step conversion log.
- `--fp2bv` / `--array-flattener` / `--tuple-{node,sym}-flattener` —
  force the universal flatteners even when the backend has native
  support. A verdict change under these is a solid signal that the
  native path has a bug the flattener doesn't.
- `--dump-smt` / `print_model` — capture what the solver saw.

**Known open issues tracked here**:

- **`array_convt` cannot encode unbounded array-of-array.** The
  assertion at `src/solvers/smt/array_conv.cpp:92-95` fires when a
  user-level `T[N][]` (or `mapping(K => T[N])`) reaches the flattener.
  Root cause and fix landscape in [array-conv.md](array-conv.md)
  §KNOWNBUG. Affects several Solidity multi-dim regressions, tracked
  in `docs/claude/solidity/language-support.md` §B.
- **Tuple array + native-array combo on some backends** — node
  flattener is the default for historical reasons; sym flattener
  works for some cases that node flattener mishandles. Neither path
  is well-documented; see [type-encoding.md](type-encoding.md)
  §Tuples.
