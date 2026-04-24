# GOTO Programs — Developer Docs

Topic-indexed map of `src/goto-programs/` — the mid-tier IR between
the language frontends and symex. Takes the AST-like irep2 tree from
the frontend, lowers it into a flat control-flow graph of GOTO
instructions, then runs transform passes (loop analysis, k-induction
havoc, contractors, race assertions, unwinding, slicing prep).

**When you want to understand**:

| Question | Start here |
|---|---|
| AST → GOTO → passes pipeline? | [architecture.md](architecture.md) |
| What GOTO instruction types exist? | [instructions.md](instructions.md) |
| How does k-induction inject havoc? Why is KNOWNBUG #1 here? | [loops-and-k-induction.md](loops-and-k-induction.md) |

**Known bugs tracked here**:

- **KNOWNBUG #1: k-induction inductive-step havoc misses
  pointer-through-function writes.** Root cause
  `goto_loops.cpp:104` `get_modified_variables` — syntactic walker
  doesn't follow `address_of` actuals into callee bodies.
  Affects ~75 Solidity regressions because every method call routes
  through `&_ESBMC_Object_<C>`.
  Full analysis + fix directions in
  [loops-and-k-induction.md](loops-and-k-induction.md) §KNOWNBUG.
