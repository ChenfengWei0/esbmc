# CLAUDE_Solidity.md — Index

Solidity frontend specific guidance for Claude Code. This file is an
**index only**. Each topic lives in a short, focused doc under
`docs/claude/solidity/`. Read the relevant sub-doc when it's in scope
for your task; don't try to read everything up front.

## Contents

### Getting started
- [Running ESBMC on Solidity](docs/claude/solidity/running.md) — invocation, solc discovery, version compatibility, auto-solc implementation.
- [File structure](docs/claude/solidity/structure.md) — source layout of `src/solidity-frontend/` and c2goto library.

### How verification is set up
- [Verification modes](docs/claude/solidity/modes.md) — `--contract` / `--function` / `--focus-function` / `--bound` / `--unbound`, soundness cheat sheet, performance notes, auto-solver selection.

### How the frontend works
- [Code architecture](docs/claude/solidity/architecture.md) — `get_expr` dispatch, `find_decl_ref`, `SolType` enum mapping, RAII guards, name helpers, C PoC debugging technique.
- [Operational models](docs/claude/solidity/operational-models.md) — sol64 c2goto pipeline, VSA ASSUME integration, `__ESOL_deep_copy` per-type semantics, multi-dim walker emission, EOA balance model, keccak / sha256 bytes packing.

### What the frontend can (and can't) express
- [Language support audit](docs/claude/solidity/language-support.md) — supported feature matrix, gaps A–K (crypto, modular, dynamic arrays, multi-dim, data location, low-level calls, mappings, address conversion, hash abstraction, uint256, `super`, try/catch, using-for, etc.), roadmap.
- [Approximation ledger](docs/claude/solidity/approximation-ledger.md) — 21 deliberate over/under-approximations, false-positive and false-negative consequences, how to use the ledger during review.

### Tooling & analyses
- [TOD detection](docs/claude/solidity/tod.md) — TransRacer-style harness generator, `--tod-balance-check`, `--tod-race-check`, auto-discovery algorithm, `__ESOL_` intrinsics.
- [Coverage](docs/claude/solidity/coverage.md) — Solidity-specific handling on top of `CLAUDE_COVERAGE.md`.
- [Counter-example minimiser](docs/claude/solidity/minimise.md) — `esbmc-minimise` three-phase reducer, `--dump-violation-info` flag.

### History & baselines
- [Testing](docs/claude/solidity/testing.md) — build flags, test baseline, adversarial tests, stress-test layout, slow THOROUGH tests.
- [Bug & incident history](docs/claude/solidity/bugs-history.md) — 19 resolved frontend bugs, 1inch liquidity-protocol scan, open crashes.

## Quick references (project-wide rules)

- Commit prefix: `[Solidity]`
- Test directory: `regression/esbmc-solidity/`
- C2goto library: `src/c2goto/library/solidity/`
- Must-use test flags: `--unwind N --no-unwinding-assertions`
- **Never** use `--function` in regression tests (see [modes.md](docs/claude/solidity/modes.md#hard-rule--never-use---function-in-regression-tests))
- Every commit must include a test results line (see main `CLAUDE.md`)
- Never use `Co-Authored-By: Claude`; use `Assisted-by: Claude-Opus4.6`

## Search tips

- Approximation markers in code: `rg '\[APPROX:'`
- TOD harness generator entry points: `src/solidity-frontend/solidity_tod_harness.cpp`
- `__ESOL_*` intrinsic lowering: `src/solidity-frontend/solidity_convert_expr.cpp` (`get_call_expr`) + `src/solidity-frontend/solidity_convert_constructor.cpp` (`build_tod_clone_helper`, `build_esol_state_forward_helper`)
- Solver auto-selection: `src/esbmc/esbmc_parseoptions.cpp` (Solidity detection block)
- Minimiser driver: `scripts/minimise/minimise.py`
