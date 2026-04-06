# AGENTS.md

This file provides guidance to coding agents working with this repository. The same workflow rules also live in `CLAUDE.md` (which Claude Code loads automatically); update both files together when changing build, test, style, or post-implementation rules.

## Project Overview

ESBMC (Efficient SMT-based Context-Bounded Model Checker) is a formal verification tool that detects bugs in C, C++, CUDA, CHERI-C, Python, Java/Kotlin, Solidity, and Rust programs. It works by parsing source code into an AST, converting to a GOTO intermediate representation, symbolically executing it to produce SSA constraints, encoding those as SMT formulas, and checking satisfiability to find property violations (or prove their absence).

## Build Commands

```bash
# Full build (install deps, configure, build, install)
./scripts/build.sh

# Individual steps
./scripts/build.sh deps      # Install dependencies + configure
./scripts/build.sh build     # Build only
./scripts/build.sh install   # Install to ./release/

# Common options
./scripts/build.sh -b Debug build          # Debug build
./scripts/build.sh -s address build        # With AddressSanitizer
./scripts/build.sh -C deps build install   # SV-COMP build (extra solvers)

# Enable CVC5 solver (required for Solidity 256-bit tests)
cmake .. -DENABLE_CVC5=ON -DDOWNLOAD_DEPENDENCIES=ON
```

The binary is installed to `./release/bin/esbmc`.

## Testing

Tests are run via CTest from the `build/` directory:

```bash
cd build/

# IMPORTANT: Default ESBMC_REGRESS_TIMEOUT is 1200s (20 min).
# Some tests (k-induction without bounds) will hang until that limit.
# Always configure cmake with a shorter timeout for interactive use:
cmake -DESBMC_REGRESS_TIMEOUT=60 ..

# Run Solidity regression tests (preferred for Solidity frontend work)
ctest -j$(nproc) -L "esbmc-solidity"

# Run a specific test suite (label matches "folder/" pattern)
ctest -j4 -L "esbmc-cpp/cpp"
ctest -j4 -L "python"

# Run a single test by name
ctest -R "regression/esbmc/00_bitshift_01"

# Run all regression tests
ctest -j$(nproc) --progress --output-on-failure

# Exclude slow Python tests
ctest -j4 -LE python-intensive

# Run unit tests only
ctest -j4 -L unit
```

### Regression Test Format

Each test is a directory under `regression/` containing:
- A source file (e.g., `main.c`, `main.py`)
- A `test.desc` file with this format:
  ```
  CORE                          # Mode: CORE, THOROUGH, KNOWNBUG, or FUTURE
  main.c                        # Input file
  --no-slice --some-flag        # ESBMC command-line arguments
  ^VERIFICATION FAILED$         # Expected output regex (one per line)
  ```

Every PR should include at least two test cases: one that passes and one that fails verification.

## Code Formatting

- C/C++: clang-format with Clang 11
- Python: YAPF
- CMake: cmakelint

### Static Analysis (cppcheck)

Before every commit, run cppcheck on **changed Solidity frontend files** to catch issues that Codacy will flag in PR review:

```bash
# Run on all changed .cpp/.h files in the Solidity frontend
git diff --name-only --diff-filter=d HEAD | grep 'src/solidity-frontend/.*\.\(cpp\|h\)$' | \
  xargs -r cppcheck --enable=style,warning \
    --suppress=missingIncludeSystem --suppress=missingInclude \
    --suppress=shadowVariable --suppress=useStlAlgorithm \
    --template='{file}:{line}: ({severity}) {id}: {message}' --quiet

# For unstaged changes, use: git diff --name-only --diff-filter=d
# For staged changes, use:   git diff --cached --name-only --diff-filter=d
```

**Must-fix categories** (Codacy will block PRs for these):
- `unreadVariable` — variable assigned but never read
- `unusedVariable` — variable declared but never used
- `variableScope` — variable scope can be reduced

**Can-ignore categories** (noisy, suppressed above):
- `shadowVariable` — common in ESBMC's codebase style
- `useStlAlgorithm` — raw loops are fine for readability

## Architecture

### Verification Pipeline

```
Source code → Frontend (AST) → GOTO program → Symbolic execution (SSA) → SMT formula → Solver → Result
```

### Key Source Directories (`src/`)

| Directory | Purpose |
|-----------|---------|
| `esbmc/` | Entry point (`main.cpp`), BMC orchestration (`bmc.cpp`), CLI parsing (`esbmc_parseoptions.cpp`) |
| `irep2/` | Core intermediate representation: typed expressions (`irep2_expr.h`) and types (`irep2_type.h`). Uses `expr2tc`/`type2tc` smart pointers. |
| `goto-programs/` | GOTO IR: control flow graph with instruction types (GOTO, ASSERT, ASSUME, ASSIGN, etc.). `goto_convert.cpp` transforms AST to GOTO. |
| `goto-symex/` | Symbolic execution engine. `symex_main.cpp` drives path exploration, generates SSA form via `symex_target_equation`. |
| `solvers/` | SMT/SAT solver backends. Abstract interface in `smt/smt_conv.h`; implementations in `z3/`, `bitwuzla/`, `boolector/`, `cvc5/`, `yices/`, `mathsat/`, `smtlib/`. |
| `clang-c-frontend/` | C frontend using Clang. `clang_c_convert.cpp` is the main AST-to-irep2 converter. |
| `clang-cpp-frontend/` | C++ frontend extending the C frontend. Handles classes, templates, virtual functions. |
| `python-frontend/` | Python frontend. Converts Python AST (via ast2json) to irep2. |
| `solidity-frontend/` | Solidity smart contract frontend. |
| `jimple-frontend/` | Java/Kotlin frontend via Soot's Jimple IR. |
| `c2goto/` | C library models and standard definitions for GOTO conversion. |
| `pointer-analysis/` | Static pointer analysis framework. |
| `util/` | Shared utilities: symbol table (`context.h`), config, expression simplifier, type casting. |

### Solver Architecture

The solver layer uses an abstract interface (`smt_convt`) with per-solver implementations. The conversion pipeline flattens irep2 expressions → lowers memory model/pointers/casts → encodes to solver-native AST → queries satisfiability. See `src/solvers/README.txt` for details.

### Expression System

The `irep2` layer defines 170+ expression types and 20+ type constructors. Expressions use `expr2tc` (shared pointer wrapper) and are enumerated in `ESBMC_LIST_OF_EXPRS`. Types use `type2tc`. This is the universal IR that all frontends target and all backends consume.

**Before committing:**

- Always run the project's test suite. If tests fail, fix the failures before committing — never commit broken or untested code.
- **Regression suite cap.** When running the full regression suite, cap the run at **5 minutes** (300000 ms) — pass the timeout to the `Bash` tool's `timeout` parameter, or wrap the invocation with `timeout 5m …`. If the suite cannot complete in 5 minutes, narrow the scope (e.g. run only the affected subset) or ask the user before extending the limit.
- **Lint and typecheck.** Run lint and typecheckers and fix any errors. For Python code, use `pylint`. For C++ code, ensure clang-format compliance (CI enforces this).

## Branching

Before implementing any feature or bug fix, always work on a dedicated branch:

1. Check the current branch — never work directly on `master`.
2. Create a branch with a descriptive name (e.g. `feat/short-description` or `fix/short-description`).
3. Confirm the branch is active before making any changes.

## Solver Selection for Solidity

Z3 struggles with 256-bit bitvector arithmetic (common in Solidity's `uint256`). CVC5 and Bitwuzla vastly outperform Z3 on QF_BV benchmarks. For Solidity tests involving 256-bit overflow checks, use `--cvc5` instead of the default Z3 solver.

## ERC20 Model

A simplified ERC20 model is available at `regression/esbmc-solidity/ERC20.sol` for verifying contracts that inherit from OpenZeppelin's ERC20. It implements `balanceOf`, `transfer`, `transferFrom`, `approve`, `allowance`, `_transfer`, `_mint`, `_burn`, `_approve` using only ESBMC-supported Solidity features. To use it, copy the ERC20 contract definition into the same `.sol` file as the contract under verification and regenerate the `.solast` with `solc --ast-compact-json`.

Note: `--function` mode skips the constructor. For ERC20 tests that depend on constructor initialization (e.g., `_mint` in constructor), omit `--function` and use `--contract` instead.

## Code Style

- **C++**: Clang-format (Clang 11), Allman braces, 80-col limit, 2-space indent, no tabs. Config in `.clang-format`.
- **Python**: YAPF, PEP 8 based, 100-col limit. Config in `.style.yapf`.
- Prefer modern C++ idioms (C++11+). Use const-correctness throughout. Prefer stack allocation over heap when possible. Follow existing patterns in the file being modified.
- CI enforces formatting on PRs via GitHub Actions.

## Coding Guidelines

- Write simple, clean, and readable code with minimal indirection.
- Each function should do one thing well. No redundant abstractions or duplicate code.
- Check the entire codebase to reuse existing methods before writing new ones.
- Tests MUST NOT use mocks, patches, or any form of test doubles. Integration tests are preferred.
- After implementation, simplify and clean up the code aggressively — remove unnecessary conditional checks while ensuring correctness.
- Run ESBMC over your solution to formally check that it works and does not introduce new errors.

## Post-implementation Pass

After implementing any non-trivial coding task, before committing:

1. **Simplify aggressively.** Remove unnecessary conditional checks, dead code, redundant abstractions, duplicate logic. Re-verify the code still works correctly. Apply the same pass to test code.
2. **Verify with ESBMC** when the task touches C/C++ code or ESBMC's own headers/frontends. Use the `esbmc-verifier` agent to confirm the patch works and introduces no new errors. For non-ESBMC tasks (e.g. Python frontend, build scripts), run the project's normal lint/typecheck/test commands.
3. **Code review.** Use the `code-reviewer` agent on the diff. Apply high-confidence findings; explain anything you skip.

## Available Subagents

These specialised agents are configured in `~/.claude/agents/` and should be preferred over ad-hoc Bash invocations when their description fits the task.

- **`esbmc-verifier`** — Recommended formal-verification tool for this repo. Two modes: (A) bug-fixing inside ESBMC's own codebase — inspects GOTO IR (`--goto-functions-only`), VCCs (`--show-vcc`), and the symbol table; applies minimal patches; re-runs ESBMC to confirm `VERIFICATION SUCCESSFUL`; produces a two-tier harness package under `regression/<suite>/github_<N>/` (literal repro), `regression/<suite>/github_<N>-nondet/` (nondet generalisation), and an optional `_fail/` negative variant when the patch shifts a checker boundary. (B) Any external C/C++ codebase (application, library, firmware) — three-phase strategy (language-level safety → functional contracts via k-induction → bug-specific negative proofs) with stub-shadowing for whatever the module depends on (DBs, network, filesystem, hardware/RTOS, vendor SDKs). Invoke for the post-implementation ESBMC step (§Post-implementation Pass #2), for deterministic witnesses when sanitizers cannot reproduce a memory/UB bug (§Regression Tests for Memory/UB Bugs), and when diagnosing unexpected ESBMC results (§Debugging Verification Issues). Defaults to bitwuzla; honours `test.desc` flags when present. For one-shot sanity checks (`esbmc file.c --incremental-bmc`), call `esbmc` directly via Bash instead.
- **`code-reviewer`** — Diff review against the priorities in §Code Review Priorities. Invoke for the post-implementation review step (§Post-implementation Pass #3).
- **`creduce-reducer`** — Reduces C/C++ programs that trigger an ESBMC bug to a minimal reproducer using C-Reduce with property-preserving interestingness scripts. Use when filing or investigating ESBMC bug reports against large inputs.

## Regression Tests for Memory/UB Bugs

When fixing a memory-safety or undefined-behaviour bug in C/C++ code:

1. Before applying the fix, write a regression test that reproduces the bug under sanitizers (ASan, UBSan, or MSan as appropriate; TSan for data races).
2. Compile and run the regression test, and confirm it fails on the unfixed code — either via a clear sanitizer diagnostic or by tripping an embedded `assert` — so the failure mode is reproducible end-to-end, not just inferred.
3. Apply the fix and re-run the compiled test; confirm it now passes cleanly (assertion holds and no sanitizer diagnostic).
4. Skip this step for pure logic bugs, build/config issues, or non-C/C++ work — sanitizers do not apply.

If sanitizers do not reproduce the bug (e.g. timing-dependent races, allocator-dependent use-after-free, MSan without instrumented dependencies, optimisation-dependent UB, or input coverage gaps):

1. Try a different sanitizer (ASan ↔ TSan ↔ MSan ↔ UBSan) and vary build flags (`-O0` vs `-O2`, `_GLIBCXX_DEBUG`, `MALLOC_PERTURB_`, `ASAN_OPTIONS=detect_stack_use_after_return=1`).
2. If still not reproducible under sanitizers, fall back to ESBMC (`esbmc-verifier` agent) to obtain a deterministic witness.
3. As a last resort, write a regression test that reproduces the observable symptom (wrong output, assertion, crash) without relying on a sanitizer diagnostic, and note in the commit message why sanitizer-based reproduction was not feasible.

## Consulting the C/C++ Standard

When a C/C++ change concerns standard-defined semantics — undefined behaviour, implicit conversions, object lifetime, name lookup, overload resolution, constant evaluation, or similar — consult the relevant standard draft (e.g. the latest C or C++ working draft on open-std.org, or cppreference for a digestible summary) before implementing. Cite the section in the commit message or code comment when it clarifies a non-obvious choice. Skip for routine edits that do not depend on standard semantics.

## Incremental Patch Testing

When a fix involves multiple patches (e.g. N1, N2), apply and test them one at a time:

1. Apply patch N1, then run the relevant tests to check whether the bug is fixed.
2. If fixed, stop — do not apply further patches.
3. If not fixed, apply patch N2 and test again. Repeat until the bug is resolved or all patches are exhausted.
4. Do not apply all patches at once before testing.

## Code Review Priorities

1. **Critical**: Verification soundness, memory safety, undefined behavior
2. **High**: Logic errors in SMT encoding/symbolic execution, performance regressions, missing tests
3. **Medium**: Code quality, API consistency, documentation gaps
4. **Low**: Minor style if matching surrounding code

## Source Architecture

Key directories under `src/`:

- `esbmc/` — Main entry point and CLI driver
- `irep2/` — Internal representation (IRep2), the core data structure for expressions/types
- `goto-programs/` — GOTO intermediate representation and transformations
- `goto-symex/` — Symbolic execution engine (core verification logic)
- `solvers/` — SMT solver backends (z3, bitwuzla, boolector, cvc4, cvc5, yices, mathsat, smtlib)
- `langapi/` — Language API abstractions shared across frontends
- `pointer-analysis/` — Memory model and pointer safety analysis
- `util/` — Shared utilities and data structures

Frontends (each parses a language into the shared GOTO representation):
- `clang-c-frontend/` — C, CHERI-C, CUDA (via Clang)
- `clang-cpp-frontend/` — C++ (via Clang)
- `python-frontend/` — Python 3.10+ (AST→JSON→IRep2)
- `jimple-frontend/` — Java/Kotlin (via Soot/Jimple)
- `solidity-frontend/` — Solidity smart contracts

Tools:
- `c2goto/` — Converts C operational models to GOTO binaries
- `goto2c/` — Converts GOTO programs back to C

Other top-level directories:
- `unit/` — Catch2 unit tests
- `regression/` — regression test suites (60+ categories)
- `scripts/` — build scripts and CMake modules (`scripts/cmake/`)
- `docs/` — generated documentation
- `website/` — Hugo-based project website

## Debugging Verification Issues

When ESBMC produces an unexpected VERIFICATION FAILED or SUCCESSFUL result, use these techniques:

**1. Inspect the GOTO program** — Use `--goto-functions-only` to dump the intermediate GOTO representation. This reveals exactly what code ESBMC is verifying, including how frontend constructs are lowered:
```sh
esbmc test.py --unwind 9 --goto-functions-only 2>&1 | grep -A50 "python_user_main"
```
Look for the `python_user_main` function to see how Python source maps to GOTO instructions (ASSIGN, FUNCTION_CALL, ASSERT). This is especially useful for catching compile-time optimizations that incorrectly pre-resolve values.

**2. Bisect with simpler test cases** — When a test fails, create variants that isolate the problem.

**3. Read the counterexample trace** — ESBMC's `[Counterexample]` section shows the state at each step. Track field assignments in structs (e.g., `PyObject`'s `.value`, `.type_id`, `.size`) through the trace.

**4. Key files for Python frontend debugging:**
- `src/python-frontend/python_converter.cpp` — Main expression/statement conversion
- `src/python-frontend/python_list.cpp` — List operations
- `src/python-frontend/function_call_expr.cpp` — Method call handling
- `src/c2goto/library/python/list.c` — C operational model for list operations

**5. Hypothesis tests** — Property-based tests in `unit/python-frontend/` test ESBMC's models against CPython. Run with: `uv run python -m pytest unit/python-frontend/ -v`

## Commit Conventions

Prefix commits with a category tag in brackets, e.g., `[python]`, `[build]`, `[solver]`, `[om]` (operational model). Title: one line, imperative mood, <72 chars. Description: 2–4 lines explaining what changed and why. Reference the relevant issue/PR with `Fixes #N` when applicable.

**Never squash commits.** Always preserve the full commit history — every individual commit must remain intact. Do not use `git merge --squash`, `git rebase` to squash, or any PR merge strategy that collapses commits.

## PR Conventions

- Branch from `master` (the default branch)
- Target PRs to `master`
- Check formatting with clang-format before submitting

## Issue and PR Labels

Always apply at least one label when creating an issue or PR. Pick the label that matches the affected area — e.g. `python`, `clang-c-frontend`, `solver`, `build`, `docs`. Use `gh label list --repo esbmc/esbmc` to see the available labels, then `gh issue edit <N> --add-label <label>` or `gh pr edit <N> --add-label <label>`. If no existing label fits, ask the user rather than creating a new one.

For module-specific instructions, subdirectory CLAUDE.md files can be added (they load automatically when working in those directories).
