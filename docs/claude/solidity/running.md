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
