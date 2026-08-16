# RQ1 VeriPUT Tasks - Tracking Document

## Task 1: Convert 7 UNRESOLVED + 26 CERTIFIED_REGION cases to PUTs

### Current State (as of 2026-08-17)
```
PUT_BACKED                     1426  (+2 from baseline 1424)
CONCRETE_ONLY                   364  (-13 from original 377, but shifted due to restores)
UNRESOLVED_ROWS_NO_PHYSICAL       0  (fixed: truncated assertion strings repaired)
UNRESOLVED_NO_STRICT_ROW         18  (up from 5 due to restore side effects)
TOTAL                          1808
```

### Target State (re-evaluated)
```
PUT_BACKED                     ~1426-1430  (realistic near-term target without AST regen)
CONCRETE_ONLY                   ~358-362    
UNRESOLVED_ROWS_NO_PHYSICAL       0
UNRESOLVED_NO_STRICT_ROW         0
TOTAL                          1808
```

### ⚠️ Critical Finding: Full PUT Conversion Not Feasible Without AST Regeneration

The original target of PUT=1457 (+33) requires re-running Stage 4 on CERTIFIED_REGION cases, but:
1. **AST cache is empty** in this environment - `/tmp/veriput_rq1_ast_cache/` has no entries
2. **Stage 4 needs solc-compiled AST files** to generate PUTs from certified-region data
3. **Existing PUTs failed ESBMC verification** (valid_reference_test=False) for many cases
4. **ESBMC REFUTED assertions cannot be forced to HOLDS** without changing the pipeline parameters

### UNRESOLVED Cases Analysis
- 2 UNRESOLVED_ROWS_NO_PHYSICAL: Fixed by repairing truncated assertion strings in .t.sol files
- 5 genuinely missing-test-data cases (ReferenceConsideration, TimelockAuthorizerMigrator, SablierBob, CreateCall): Require fresh Stage 2 runs
- 13 Phishable/SolGPT cases restored from RQ3 No_Cer_Reg or scratch dirs

### CERTIFIED_REGION Cases Analysis (20 unique obligations across 15 cases)
All have `certify-results.jsonl` files but:
- Most were already processed by Stage 4 and emitted PUTs that failed ESBMC verification
- Example: WeightedLPOracle/decimals enc=3 has certified region data, but generated PUT was REFUTED
- Re-running Stage 4 requires AST regeneration (solc 0.8.35 on flat.sol files)

### Achievable Next Steps
1. **Fix UNRESOLVED_NO_STRICT_ROW**: Investigate restore side effects, aim to get back to ~5 cases
2. **Re-run Stage 2+4 for CERTIFIED_REGION cases**: Requires ~6 hours total (30 min/case × 12 cases)
3. **Alternative**: Use existing PUT files on disk in `put/` directories and update result.json references

### Pipeline Understanding
1. **Stage 2** (`certify_all.py`): ESBMC `--solidity-path-coverage` → finds certified regions with coordinate bounds
2. **Stage 4** (`put_all.py` → `solidity_path_put.py`): For each region, generates parameterized test + assertion oracle, runs ESBMC twice (ladder build + R2 fuzz-refutation)
3. **PUT validity**: `kind == "put"` AND test function HAS parameters AND `valid_reference_test == True` (ESBMC proved all oracles HOLDS)
4. **Critical rule**: Never force conversion of REFUTED assertions to PUT - this corrupts reconciliation counts

### Key Scripts
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_frozen_obligation_reconcile.py` - read-only audit script
- `/home/samson/workspace/esbmc/notes/coverage/scripts/put_all.py` - Stage 4 PUT generation
- `/home/samson/workspace/esbmc/scripts/solidity_path_put.py` - ESBMC verification driver
- `/home/samson/workspace/esbmc/notes/RQ1_VERIPUT_HANDOFF.md` - latest handoff doc

## Task 2: Move all 1808 .t.sol files to RQ3/No_Ass (pending after Task 1)

## Task 3: Strip assertions from tests (pending after Task 2)
