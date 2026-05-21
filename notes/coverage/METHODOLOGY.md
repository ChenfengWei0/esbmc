# Branch-coverage comparison methodology — LOCKED 2026-05-20

This document is the **single source of truth** for how branch coverage is
measured and compared between ESBMC and the native test suite. Once this
spec is locked, no number in `notes/coverage/data/*.json` may change
except by re-running the pipeline against new ESBMC binaries.

## 1. Why the denominator must be FIXED before either tool measures it

A coverage comparison is meaningless unless both tools answer the same
question over the same set of decisions. Without a fixed denominator,
each tool's instrumentation idiosyncrasies (compound-condition lowering,
require-vs-if treatment, modifier inlining) shift the divisor and the
percentages become incomparable.

**Therefore the denominator is derived from the SOLIDITY SOURCE AST,
not from either tool's runtime output.** Tools then report REACH against
this fixed denominator.

## 2. The canonical decision set

The denominator is the set of **canonical decision points** enumerated by
walking the AST of each original `.sol` file in the project's
production-source tree, recognising these node kinds:

| AST node kind            | Branches per node | Rationale |
|--------------------------|-------------------|-----------|
| `IfStatement`            | 2 (true / false)  | Standard if/else |
| `Conditional` (ternary)  | 2                 | `a ? b : c` is a 2-outcome decision |
| `WhileStatement`         | 2 (enter / exit)  | Loop entry decision |
| `ForStatement`           | 2                 | Loop entry decision |
| `DoWhileStatement`       | 2                 | Loop back-edge decision |
| `FunctionCall(require)`  | 2 (pass / revert) | require is a guarded branch |
| `FunctionCall(assert)`   | 2 (pass / revert) | assert is a guarded branch |
| `BinaryOperation('&&')`  | 2 (short-circuit) | Solidity `&&` ALWAYS short-circuits regardless of expression position; this is unconditional branching by language semantics. Counted everywhere (in conditions, returns, assignments, etc.) to match what solc-coverage instruments. |
| `BinaryOperation('\|\|')` | 2 (short-circuit) | Same as `&&`. |

Each decision point contributes **one (file, line) entry** to the
canonical decision set. Two decision points on the same line (rare; only
multi-statement-per-line styling) collapse to one entry.

**Excluded from the denominator** (these are intentional, not oversights):

- `try/catch` — different concept (exception, not branch).
- Function-call dispatch (virtual/override) — runtime dispatch is not a
  branch in the coverage sense; both lcov and ESBMC agree to skip.
- Implicit reverts (overflow, division-by-zero) — runtime checks emitted
  by the compiler, not source-level decisions.
- Inline-assembly Yul `if/switch` — ESBMC over-approximates Yul, lcov
  cannot instrument it; consistently excluded.

The list of recognised AST kinds and the contextual-`&&`/`||` rule is
the **canonical decision-set definition**. No tool's behaviour can
change what counts as a decision.

## 3. Scope (which files contribute to the denominator)

Per ESBMC entry-bench:

- **Project-own production source**: files under
  `notes/coverage-comparison/<project>/<src_root>/` excluding paths
  matching any of `test/`, `tests/`, `mock/`, `mocks/`, `script/`,
  `scripts/`, `interfaces/`, `interface/`, `.t.sol`.
- **Restricted to the entry's flat content**: a project-own file is
  *only* in scope if the ESBMC entry's flat includes the contract /
  library it defines (some files contain entry-sibling contracts that
  the chosen `--contract <Primary>` cannot reach via dispatcher — those
  files are out of scope for this entry).

The flat's contents are determined by parsing `// File <path>` markers
(hardhat) or `// <path>` markers (forge) written at every original-file
boundary; any file with a marker in the flat is in scope.

### 3.1 Library primaries (no dispatcher harness)

A `library` Solidity declaration has no constructor, no state, no `this`,
and cannot be instantiated.  ESBMC's `--contract <Library>` correctly
errors "no verification targets" because the dispatcher harness has
nothing to construct.  For a project whose primary entry is a library
(e.g. `limit_order_protocol` / `MakerTraitsLib`), Pair 1's *natural*
implementation is the union of `--function fn` runs over every
public/internal library function — the same lens Pair 2 uses.  The
collector detects this case (`primary_contract_kind == "library"`) and
sets Pair 1's reach = Pair 2's reach for the same canonical denominator.
The methodology is consistent: Pair 1 always measures "reach achievable
via the project's primary entry"; for libraries that entry is the set
of callable lib functions.

## 4. Reach measurement, per tool

For each canonical decision (identified by original-file path and line):

- **ESBMC reach** ⇔ at least one source line in the file's flat block
  is recorded as covered in the ESBMC `--coverage-covered-set` union
  JSON. Mapping flat-line to original file is via the `// File <path>`
  marker preceding the flat-line; mapping flat-line to a specific
  canonical decision LINE is approximate (flat may shift line numbers
  inside a block by stripped imports), so reach is aggregated **per
  file**, not per line: file F has |reach| equal to the number of
  unique flat-lines reached inside F's block, capped by the file's
  canonical decision count.
- **Native reach** ⇔ lcov BRDA arm count > 0 on at least one arm at
  that line, restricted to the same per-file aggregation.

The flat-line ↔ original-line approximation is acknowledged. To bound
its effect, we use **per-file aggregation only**: we trust that the
*set of files* a tool reaches is exact, and within a file we trust the
*count of reached lines* (since flat-line shifts are within-file).

## 5. Aggregation

Per file F:

- `denominator(F)` = AST decision count (fixed by §2)
- `esbmc_reach(F)` = unique reached source lines per §4, capped at `denominator(F)`
- `native_reach(F)` = same, per §4

Per project / per entry (sum across files in scope):

- `total_denom`   = Σ denominator(F)
- `esbmc_total`   = Σ esbmc_reach(F)
- `native_total`  = Σ native_reach(F)
- `esbmc_pct`     = 100 × esbmc_total / total_denom
- `native_pct`    = 100 × native_total / total_denom

**Both percentages share the same denominator by construction.**
That is the absolute, non-negotiable, defensible-against-reviewers
property of this methodology.

## 6. Instrumentation gap (informational, not in the comparison)

For each file F in scope, the JSON also records:

- `astDecisions(F)` — canonical count from §2
- `esbmcInstrumented(F)` — count of unique lines that appear in
  ESBMC's `--show-claims` for F's flat block
- `lcovInstrumented(F)` — count of unique BRDA lines in lcov for F

If `esbmcInstrumented(F) < astDecisions(F)`, ESBMC missed decisions —
this is an ESBMC instrumentation bug to investigate. Same for
`lcovInstrumented(F)`. These gaps are reported per file and **do not
affect the comparison percentages** (which use the AST denominator).

## 7. Reproducibility contract

Every number in `notes/coverage/data/*.json` is reproducible from the
artifacts kept in the repository (flat.sol files, lcov.info files,
original source trees) plus the ESBMC binary used. The mapping from
artifact → number is:

```
collect.py:cmd_esbmc(<bench>)
  step 1: parse_ast_decisions(notes/coverage-comparison/<proj>/src)
          -> canonical_decisions[(file, line)]   (the denominator)
  step 2: parse_flat_file_blocks(inputs/<bench>.flat.sol)
          -> {original_file: [flat_line_start, flat_line_end]}
  step 3: run ESBMC with --coverage-whole-unit --coverage-covered-set <union.json>
          -> union.json  (atomic-persisted reaches, partial-safe)
  step 4: bucket union.json reaches by original file via step-2 mapping
          -> esbmc_reach per file
  step 5: parse_lcov(<lcov.info>)
          -> native_reach per file (BRDA arm > 0)
  step 6: aggregate -> JSON
```

Re-running `collect.py` produces byte-identical JSON output given the
same inputs (timestamps + paths excluded). Re-running with a different
ESBMC binary changes ESBMC's reach but never the denominator.

## 8. Locked numeric contract

After this spec is locked, the following invariants hold in every
`esbmc_<bench>.json` and `native_<project>.json`:

1. **`total.branchesTotal` is identical for both ESBMC and native of the
   same bench**, because both derive from the same canonical-decision
   AST walk.
2. **Re-running the pipeline does not change `branchesTotal`** for a
   fixed source tree.
3. **`branchesReached` only changes if (a) ESBMC binary changes
   measurably, or (b) the native test suite is re-run with different
   results.** Mere re-parsing does not change reach.

If any invariant breaks, it's a pipeline bug to fix immediately, not a
data update.

## 9. What this spec deliberately does not promise

- It does not claim ESBMC and native test suite *measure the same
  underlying reachability* — they don't (ESBMC: single-entry static
  analysis; native: multi-fixture dynamic execution). It only claims
  both are reporting reach over the same canonical decision set.
- It does not claim the comparison is fair as a measure of "test
  quality" — fairness lives in §3 (scope alignment) and is bounded by
  what `--contract <Primary>`'s dispatcher harness can reach.
- It does not claim per-line precision within a file — only per-file
  aggregation (§4 approximation note).

## 10. ESBMC modeling limits surfaced by this comparison

Reachability gaps that the data exposes — each is a real ESBMC
modeling limitation, not a methodology hole:

(a) **Cryptographic-inversion-guarded paths** (e.g.
`cross_chain_swap/EscrowSrc`, line 1518 inside `_uniTransfer`).
The `rescueFunds` body is guarded by
`Create2.computeAddress(immutablesHash, BYTECODE_HASH, FACTORY) ==
address(this)`.  In production a Create2-deployed contract trivially
satisfies this; in ESBMC's symbolic execution it requires inverting
keccak256, which the wide-BV-table modelling (see
`reference_wide_bv_keccak_table`) cannot do in any reasonable budget.
Native lcov hits these branches because the test fixture deploys via
Create2 with matching params.  Both tools count the decision; ESBMC
proves it unreachable under its current crypto model.

(b) **Constructor invariants that constrain post-construction state**
(e.g. `st1inch/St1inch`, `_votingPowerAt` invariant check at lines
4548-4549).  The constructor's `require(_votingPowerAt(...) ...)`
chain narrows the symbolic state so much that body-level decisions
in user methods (`deposit`, `withdraw`, `earlyWithdrawTo`, …) get
proven unreachable from the harness initial state.  Native test
fixtures deploy with values that trivially satisfy the invariant and
then call methods with arbitrary balances; ESBMC's nondet-everything
harness cannot generate the same "arbitrary balance after
constructor" state without backtracking through the cryptographic
hash that drives the invariant.

Both are addressable with targeted ESBMC modelling changes
(over-approximate `Create2.computeAddress` to nondet-address;
nondet-havoc state-vars across dispatcher iterations after
constructor invariants are checked once).  Those changes are
soundness-preserving for coverage measurement (they add reach, never
remove paths) but they are out of scope for this dataset capture;
they will be done in a follow-up patch.  The data below documents
the gap explicitly per file so a future re-run with those fixes
shows the closure as a JSON-diff.
