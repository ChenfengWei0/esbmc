# Running ESBMC on Solidity

ESBMC supports two ways to verify Solidity contracts:

```sh
# Auto-invoke solc (recommended): ESBMC finds and runs solc automatically
esbmc contract.sol --contract MyContract

# Manual AST generation (legacy): user runs solc separately
solc --ast-compact-json contract.sol > contract.solast
esbmc --sol contract.sol contract.solast --contract MyContract
```

**solc discovery order**: `--solc-bin <path>` > `$SOLC` env var > `solc` in `$PATH`. When auto-invoked, ESBMC prints the solc path and version (e.g. `Compiling Solidity AST using: /usr/local/bin/solc (v0.8.30)`).

**Version compatibility** (checked at AST level via `PragmaDirective`):
- pragma *pinned* to `< 0.5.0` (no upper bound, or upper bound also < 0.5.0): rejected
- pragma with pre-0.5 lower bound but upper bound ≥ 0.5.0 (e.g. `pragma solidity >=0.4.0 <0.9.0;`): warning, accepted (solc has already compiled with a modern compiler — see `solidity_convert.cpp::check_min_version`)
- `0.5.0 – 0.7.0`: warning (may cause unexpected behaviour)
- `>= 0.7.0`: fully supported (tested against 0.8.x)

## Implementation (auto-solc)

Auto-solc is implemented in `solidity_language.cpp`:
- `find_solc()`: searches for solc binary in priority order
- `get_solc_version()`: extracts version string from `solc --version`
- `invoke_solc()`: runs `solc --ast-compact-json` to temp file, displays errors on failure
- `parse()`: detects `.sol` vs `.solast` input, auto-invokes solc for `.sol` files
- `.sol` extension registered in `langapi/mode.cpp` alongside `.solast`
- `esbmc_parseoptions.cpp`: auto-sets Solidity options (no-align-check, force-malloc-success) when `.sol` file detected in positional args

## Safety checks (SMTChecker-style opt-in)

Solidity runs implicitly set `--no-standard-checks`, so every property
check is opt-in.  Mention the flag for each property you want verified —
nothing else fires.

| Property | Flag | Notes |
|---|---|---|
| User `assert(...)` / `require(...)` / `revert` | (always on) | Disable with `--no-assertions`. |
| Array / `bytes` / `string` out-of-bounds | `--bounds-check` | Solidity-level reads (`a[i]`, `bytes[i]`, `string[i]`) — model-level bounds traps fire regardless. |
| Division by zero | `--div-by-zero-check` | Catches `a / b` and `a % b` once Yul-lowered (e.g. inside an `unchecked` block, where 0.8's built-in trap is suppressed). |
| Signed `int*` over/underflow | `--overflow-check` | Required for the int over/underflow regressions. |
| Unsigned `uint*` over/underflow | `--unsigned-overflow-check` | Optional companion to `--overflow-check`. |
| Reentrancy (cross-call invariant violation on the dispatcher loop) | `--reentry-check` | See [tod.md](tod.md) for the harness shape. |
| TOD on `address(this).balance` | `--tod-balance-check[=auto\|f1,f2]` | Requires `--bound` + EOA balance model — see [tod.md](tod.md). |
| TOD on storage state vars | `--tod-race-check[=auto\|f1,f2]` | Requires `--contract` — see [tod.md](tod.md). |

Notes:
- **`--bounds-check` for memory arrays:** ESBMC's Solidity `bytes` /
  `string` / dynamic-array model performs its own bounds trap inside
  the model's accessor (e.g. `bytes_dynamic_bounds_check`).  Those
  fire whenever the access is reachable, regardless of
  `--bounds-check`.  The `--bounds-check` flag enables the *generic*
  goto-level bounds check, which catches additional patterns (Yul-level
  or pointer-arithmetic-style) that bypass the model accessor.
- **`--no-X-check` precedence:** the negative still works for
  C/C++/Python; Solidity already has every standard check off, so the
  negatives are no-ops.  Passing both `--bounds-check` and
  `--no-bounds-check` directly (without `--no-standard-checks` involved)
  is undefined — the negative wins.  The natural Solidity workflow is
  Solidity-default + the positive flag(s) you want.

Examples:

```sh
# Verify only div-by-zero (default checks all suppressed by Solidity default).
esbmc contract.sol --contract C --div-by-zero-check

# Reentrancy + signed-int overflow on the same run.
esbmc contract.sol --contract C --reentry-check --overflow-check --bound

# Force every standard check on (matches C/C++ default behaviour).
esbmc contract.sol --contract C --bounds-check --div-by-zero-check \
  --overflow-check --unsigned-overflow-check
```
