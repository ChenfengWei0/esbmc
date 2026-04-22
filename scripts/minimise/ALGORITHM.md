# Minimiser Implementation Spec

Implementation-side companion to `docs/minimise/algorithm.md`. The
methodology doc describes *what* the algorithm is; this doc describes
*how* the code is laid out, the exact JSON schemas used for
coordination between ESBMC and the Python driver, and the concrete
function boundaries so the implementation stays faithful to the
methodology.

## 1. Component layout

```
scripts/minimise/
├── ALGORITHM.md                 — this file
├── minimise.py                  — CLI entry point; orchestrates phases
├── oracle.py                    — oracle tuple + comparison + JSON I/O
├── solc_driver.py               — solc subprocess + error parsing + AST I/O
├── source_surgery.py            — byte-range deletion / visibility rewrite
├── phases/
│   ├── phase0_sweep.py          — dead-code sweep
│   ├── phase1_closure.py        — compile-driven syntactic closure
│   └── phase2_reduce.py         — verifier-driven greedy reduction
├── esbmc_driver.py              — esbmc subprocess + oracle extraction
├── manifest.py                  — manifest schema + writer
└── examples/
    └── <target>/                — known-answer targets (smoke + realistic)
```

The code depends only on the standard library plus the already-present
`solc` and `esbmc` binaries invoked as subprocesses.

## 2. Coordination schemas

### 2.1 `--dump-violation-info` (ESBMC → driver)

ESBMC writes this file after it reports a violation. The path is
passed via the new `--dump-violation-info <path>` flag.

```jsonc
{
  "schema_version": 1,
  "violated": true,
  "oracle": {
    "contract": "GEMCHAIN",
    "function": "mintToken_onlyOwner",
    "bug_type": "arithmetic overflow on add",
    "in_function_offset_lines": 3
  },
  "original_function": "mintToken",         // null if oracle.function is the original
  "trace_methods": [                         // Solidity-level functions visible in the counter-example
    {"contract": "GEMCHAIN", "function": "GEMCHAIN"},     // constructor
    {"contract": "GEMCHAIN", "function": "mintToken"}
  ],
  "locked_symbols": [                        // mandatory-set seed (derived inside ESBMC)
    "GEMCHAIN.GEMCHAIN",
    "GEMCHAIN.mintToken",
    "GEMCHAIN.onlyOwner"                    // modifier referenced by retained fn
  ],
  "source_files": ["mintToken.sol"],         // file list for multi-file inputs
  "esbmc_flags": ["--contract", "GEMCHAIN", "--overflow-check",
                  "--unsigned-overflow-check", "--cvc5", "--incremental-bmc"]
}
```

Fields:

- `oracle` — the tuple `(c, f, t, ℓ)` from the methodology doc. `ℓ` is
  the function-relative line number of the violation (0-based, measured
  from the opening `{` of the function body).
- `original_function` — populated when `f` is a modifier aux
  (`<name>_<modifier>`). The minimiser treats both the aux and the
  original as mandatory.
- `trace_methods` — Solidity-level function set stepped through by the
  counter-example. Extracted in the frontend by mapping per-step
  source locations back to the enclosing `FunctionDefinition`.
- `locked_symbols` — the mandatory set precomputed by ESBMC, as
  fully-qualified names (contract.function). The driver may add more.

### 2.2 `manifest.json` (driver → disk)

```jsonc
{
  "schema_version": 1,
  "tool": "esbmc-minimise",
  "oracle": { /* as above */ },
  "input": {
    "sources": ["mintToken.sol"],
    "esbmc_flags": [ /* as above */ ],
    "solc_version": "0.8.30"
  },
  "phase_0": {
    "removed": {
      "imports":  [{"file": "a.sol", "name": "Ownable"}],
      "structs":  [],
      "enums":    [],
      "events":   [{"file": "a.sol", "contract": "GEMCHAIN", "name": "Approval"}],
      "errors":   [],
      "functions": []
    },
    "compilation_calls": 1
  },
  "phase_1": {
    "mandatory_seed": [ /* copy of locked_symbols */ ],
    "syntactic_closure": ["GEMCHAIN.GEMCHAIN", "GEMCHAIN.mintToken",
                          "GEMCHAIN.onlyOwner", "GEMCHAIN.Transfer"],
    "fallback_level_used": 0,
    "compilation_calls": 4,
    "verifier_calls": 1
  },
  "phase_2": {
    "ordering_version": "v1",
    "attempts": [
      {"fn": "GEMCHAIN.transferFund", "op": "delete",       "compile": true,  "oracle": true,  "verdict": "deleted",      "wall_sec": 2.9},
      {"fn": "GEMCHAIN.burn",         "op": "delete",       "compile": true,  "oracle": false, "verdict": "try_internal", "wall_sec": 3.1},
      {"fn": "GEMCHAIN.burn",         "op": "internal",     "compile": true,  "oracle": true,  "verdict": "internalized", "wall_sec": 3.0},
      {"fn": "GEMCHAIN.mintToken",    "op": "skip_mandatory"}
    ],
    "verifier_calls": 30,
    "compilation_calls": 45
  },
  "result": {
    "reduction": {
      "functions_original": 18,
      "functions_retained": 6,
      "lines_original": 283,
      "lines_retained": 71
    },
    "wall_sec": 412.6,
    "status": "ok"                     // or "gave_up_at_level_N"
  }
}
```

### 2.3 Reduced program layout

Preserves multi-file structure:

```
<out_dir>/
├── reduced/
│   ├── mintToken.sol                    — primary source with deletions / visibility edits
│   └── (other files if the input was multi-file)
├── reduced.solast                       — fresh AST of the reduced program
└── manifest.json
```

No flattening. The reduced source is a byte-range-spliced derivative of
the original; it must always be re-compilable by the pinned `solc`.

## 3. Internals

### 3.1 `oracle.py`

- `Oracle` dataclass: `contract`, `function`, `bug_type`,
  `in_function_offset_lines`. `__eq__` is component-wise equality.
- `load_from_dump(path) -> Oracle` — reads `--dump-violation-info`
  JSON.
- `extract_from_run(esbmc_stdout, source_files) -> Oracle` — fallback
  path when a minimisation iteration runs ESBMC and must derive the
  oracle from a fresh run. Uses `--dump-violation-info` per iteration
  too; `esbmc_stdout` is only for diagnostic logging.

### 3.2 `solc_driver.py`

- `compile(sources) -> CompileResult` — wraps `solc --ast-compact-json
  <sources>`; returns either an AST (on success) or the raw stderr for
  logging (on failure). The driver no longer parses stderr to identify
  missing symbols: the Phase 1 closure loop is driven by an AST walker
  that traverses every retained function's body (see `phase1_closure
  ._add_dependencies`), which is strictly better than stderr scraping
  — it is deterministic, independent of solc's error-message format
  across versions, and handles overloads by fully-qualified id rather
  than by bare name.

- `source_surgery.parse_src(node)` — for every AST node with a `src`
  field, build a byte-range. Used by `source_surgery` for deletions
  and visibility rewrites.

### 3.3 `source_surgery.py`

The surgery layer performs byte-range edits on the original source
text:

- `delete(symbol: Symbol) -> SourceEdit` — emits a deletion edit for
  the symbol's byte range. Boundary handling: trailing comma (for
  parameter lists), trailing semicolon (for statements and
  declarations), trailing newline. Does *not* attempt to normalize
  whitespace.
- `set_visibility(symbol: Symbol, new_vis: str) -> SourceEdit` —
  locates the visibility keyword token within the function signature
  range (by tokenising the signature line only, not the full source,
  to avoid rewriting `public` inside a string literal) and replaces
  it.
- `apply(source: str, edits: list[SourceEdit]) -> str` — applies
  edits in reverse byte-order so earlier offsets stay valid.

### 3.4 `esbmc_driver.py`

- `ESBMCDriver(binary_path, base_flags)`.
- `run(sources, extra_flags, dump_violation_path) -> RunResult`:
  invokes the binary under a hard wrapper (`ulimit -v 4000000`,
  `ulimit -t <N>`, outer `timeout`) as required by
  `feedback_esbmc_unwind.md`.
- `RunResult` carries `returncode`, `stdout`, `stderr`, and a parsed
  `Oracle | None` (from the dump file).

### 3.5 Phase drivers

- `phase0_sweep.run(source_dir) -> (reduced_dir, phase0_entry)` —
  returns the mutated program and a manifest fragment.
- `phase1_closure.run(source_dir, mandatory, esbmc, solc) ->
  (reduced_dir, phase1_entry)` — runs the four-level loop, returning
  the lowest level that passed.
- `phase2_reduce.run(source_dir, mandatory, esbmc, solc) ->
  (reduced_dir, phase2_entry)` — fixpoint loop with weight-sorted
  candidates. Each outer pass rebuilds the AST, recomputes weights
  against the current retained set, and walks candidates once; the
  loop terminates when a pass produces no commit. Iteration is needed
  for output size (not just speed): within a pass the weighted list
  is frozen, so a callee tried before its caller is preserved and
  only becomes deletable on a later pass when the caller is gone.
  Bounded by `MAX_PASSES = 8` as a defensive cap; `phase_2.passes`
  and `phase_2.fixpoint_reached` are recorded in the manifest.

### 3.6 `minimise.py` (CLI)

```
usage: minimise.py --input <sol-or-dir> --oracle <violation-info.json>
                   --esbmc <path> --solc <path> --esbmc-flags "<flags>"
                   --out <out-dir>
                   [--max-verifier-calls N]   # safety cap; default None (unlimited)
                   [--skip-phase-0]           # diagnostic: start from Phase 1
                   [--dry-run]                # parse inputs, print plan, exit
```

The driver does not itself run the initial ESBMC that produced the
violation; the user does that with `--dump-violation-info <path>`,
then passes `<path>` as `--oracle`. This separation keeps the driver
pure (deterministic given the oracle + inputs) and lets the user
choose their own verification configuration.

## 4. Invariants the implementation must preserve

1. **No silent substitution.** If at any point the minimiser cannot
   satisfy the oracle, it must output the full original (or a strictly
   larger intermediate that last verified), never a "best-effort"
   program that does not reproduce the oracle.
2. **No ESBMC retry on crash.** Per
   `feedback_esbmc_unwind.md`/`feedback_no_lazy_fix.md`: a crashing
   reduced program indicates a bug to fix, not a case to skip.
   Implementation: abort the run with a non-zero exit code, dump the
   offending program to the output dir as `crash/<hash>/`, surface the
   stderr. Do not continue past the crash.
3. **Deterministic ordering.** Within each Phase 2 weight bucket, ties
   broken alphabetically by fully-qualified name (stable across runs).
4. **Manifest is append-only within a run.** Each phase flushes its
   fragment immediately; if the run is killed, the partial manifest
   tells the user how far it got.

## 5. Testing

- `tests/` folder co-located with the scripts, not under
  `regression/`. Reason: the minimiser is driver code, not Solidity
  semantics code; `regression/esbmc-solidity/` is for the verifier.
  A dedicated `scripts/minimise/tests/run_tests.sh` runs
  example-based end-to-end tests against the two known-answer targets.
- Each example has a sidecar `expected.json` capturing the known-good
  reduction ratio and fallback level used. The test asserts the actual
  run stays within tolerance of these expectations.
