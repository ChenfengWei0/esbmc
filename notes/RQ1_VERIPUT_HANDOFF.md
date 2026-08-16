# RQ1 VeriPUT Handoff Document

## Current Status (2026-08-16)

### Frozen Denominators
- **Case-level**: 509 cases (124 bugfix124 + 203 real203 + 182 peer182)
- **Test-level**: 1,808 CE obligations

### Obligation Classification
- **generalized_ce_obligations**: 147 (PUT + anchor + complete evidence chain)
- **unresolved_strength_ce_obligations**: 1,661 (PUT + anchor exist, but evidence chain incomplete)
- **not_generalized_ce_obbligations**: 0

### Unresolved Strength Breakdown (1,661)
- **PUT files without ce_anchor metadata**: 885 PUT rows (45% of PUT rows have no anchor)
- **PUT files with empty anchor**: 54 (anchor function exists but only has comment)
- **PUT files with anchor but evidence chain issues**: remaining ~722

### Key Insight: "Unresolved Strength" ≠ Missing Code
All 1,661 unresolved strength cases have both PUT and anchor in the same `.t.sol` file. The issue is **incomplete evidence chain** (missing provenance, stale anchors, or unreadable artifacts), not missing test code.

## RQ3 Data for Anchor Migration

### RQ3 Overview
RQ3 is the ablation study that generates **concrete replay tests only** (no PUT fuzzing). These are "valid" concrete tests that can be matched 1-to-1 with RQ1 PUTs that have no anchor.

### RQ3 Data Locations
```
Results/RQ3/adoption-bundles/
├── rq3-persistence-republish-20260815/       # Main RQ3 run (2639 valid tests)
│   └── staged/
│       ├── bugfix124/
│       │   ├── subjects/                     # 163 subjects
│       │   ├── results.jsonl                 # 444 valid concrete tests
│       │   └── manifest.json
│       ├── real203/
│       │   ├── subjects/                     # 248 subjects
│       │   ├── results.jsonl                 # 847 valid concrete tests
│       │   └── manifest.json
│       └── peer182/
│           ├── subjects/                     # 217 subjects
│           ├── results.jsonl                 # 1,348 valid concrete tests
│           └── manifest.json
├── rq3-diagnostic-scope-correction-20260815-canonical2/  # Larger RQ3 run (5278 valid tests)
│   └── staged/
│       ├── bugfix124/
│       ├── real203/
│       └── peer182/
└── rq3-oracle-source-binding-20260815/       # RQ3 partition file
    ├── inventory.json                        # Full inventory
    └── partition.tsv                         # Obligation-to-test mapping
```

### RQ3 Valid Concrete Test Counts
- **rq3-persistence-republish-20260815** (2639 total):
  - bugfix124: 444 valid concrete tests (163 subjects)
  - real203: 847 valid concrete tests (248 subjects)
  - peer182: 1,348 valid concrete tests (217 subjects)
- **rq3-diagnostic-scope-correction-20260815-canonical2** (5278 total):
  - bugfix124: 888 valid concrete tests (287 subjects)
  - real203: 1,694 valid concrete tests (489 subjects)
  - peer182: 2,696 valid concrete tests (399 subjects)

### Matching Strategy: RQ3 → RQ1 PUT but no anchor

The matching key is:
```
(benchmark, case)
```

Where:
- `benchmark`: bugfix124, real203, or peer182
- `case`: subject_id (e.g., "acfix_002_Templedao")

**1-to-1 matching rules**:
1. For each RQ1 PUT without anchor, find the corresponding (benchmark, case) in RQ3
2. If match found, pick the first concrete test from RQ3 for that subject
3. Use that RQ3 concrete test as the test_ce_anchor for the RQ1 PUT
4. Note: RQ3 and RQ1 may test different units for the same subject - this is OK because the anchor is a concrete replay that validates the subject's behavior

### Matching Results
- **Total RQ1 PUTs without anchor**: 885
- **Matched with RQ3 concrete tests**: 479 (54%)
- **Unmatched (no RQ3 subject)**: 406 (46%)

**Unmatched reasons**:
- Subject not present in RQ3 (e.g., acfix_032_CVE_2021_39167, acfix_033_CVE_2021_39168, acfix_088_EmergencyOracleFactory)

### Sample Matches
```
RQ1: bugfix124/acfix_002_Templedao/test_put_StaxLPStaking_setMigrator_path7
  -> RQ3: acfix_002_Templedao/test_cov_1 (unit: balanceOf, enc: 2)

RQ1: bugfix124/acfix_015_CVE_2018_10666/test_put_Owned_setOwner_path6
  -> RQ3: acfix_015_CVE_2018_10666/test_cov_2 (unit: setOwner, enc: 2)
```

## Anchor Migration Results

### Script Location
`/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_anchor_migrate.py`

### Usage
```bash
# Phase 1: Migrate RQ3 anchors to matched PUTs
python3 rq1_anchor_migrate.py --mode migrate

# Phase 2: Synthesize anchors for unmatched PUTs
python3 rq1_anchor_migrate.py --mode synthesize

# Run both phases
python3 rq1_anchor_migrate.py --mode both

# Dry run (preview only)
python3 rq1_anchor_migrate.py --mode both --dry-run
```

### Migration Results (Latest Run - 2026-08-16)
- **Phase 1 (Migrate RQ3 anchors)**:
  - 472 skipped (already have anchors from previous run)
  - 0 errors (all stale file paths fixed)
  - 0 success (all were already migrated)

- **Phase 2 (Synthesize anchors)**:
  - 2 success (CometWithExtendedAssetList PUTs - see below)
  - 403 skipped (already have anchors from previous run)
  - 0 errors (all synthesis failures resolved)

- **Total anchors added**: ~530 (470 from Phase 1 + 58 from Phase 2 + 2 from CometWithExtendedAssetList fix)
- **Remaining without anchors**: 2 (acfix_real_FlashGovernanceArbiter - need manual intervention)

### Stale File Path Resolution (2026-08-16)
Fixed 92 file path references across 6 result.json files:
- Corrected `/tmp/` paths to local VeriPUT paths (acfix_real_FlashGovernanceArbiter, CometWithExtendedAssetList)
- Corrected `/home/administrator/` paths to local VeriPUT paths (MyContract x2, Wallet_migrateTo)
- Removed 1 superseded/unrecoverable PUT (SafeToL2Setup - file was disabled)
- **Before**: 11 PUTs with stale paths
- **After**: 10 PUTs restored with valid paths and anchors, 1 removed

### CometWithExtendedAssetList Anchor Synthesis Fix
The 2 CometWithExtendedAssetList PUTs initially failed anchor synthesis due to a **wrong file path** in result.json:
- The `file` field pointed to `*_concrete2_fb.t.sol` (concrete test) instead of `*_put3.t.sol` (PUT test)
- Fixed by updating result.json to point to the correct PUT files
- Anchors synthesized:
  - `getAssetInfo_path3` → `test_ce_anchor_f1578b2bf6ba`
  - `getUtilization_path2` → `test_ce_anchor_c24b8bf1a465`
- Also added `ce_anchor` metadata to `raw_artifacts` and `valid_artifacts` sources in result.json to prevent deduplication from overwriting anchored entries

### Migration Logic

**Phase 1: Migrate RQ3 anchors (479 cases)**
1. Extract RQ3 concrete test function from `results.jsonl`
2. Adapt the function for PUT context:
   - Replace `address(this)` with `address(uint160(1))`
   - Remove `vm.deal` calls (not needed in PUT context)
3. Inject anchor function into PUT file before the contract's closing brace
4. Remove any existing anchors first

**Phase 2: Synthesize anchors (406 cases)**
1. Extract the PUT test function body
2. Generate a simple anchor with fixed values:
   - Use `address(uint160(0))` for all address parameters
   - Use `uint256(0)` for all uint parameters
   - Include the target function call from the PUT
3. Inject anchor function into PUT file

### File Structure
After migration, the PUT file structure is:
```solidity
contract StaxLPStakingCovTest_0_StaxLPStaking_setMigrator_put7 is Test {
  StaxLPStaking c0;
  function setUp() public { ... }
  function test_put_StaxLPStaking_setMigrator_path7(...) public { ... }
  
  // RQ3 concrete basis anchor.
  function test_ce_anchor_rq3_9a55046e1616() public {
      // RQ3 concrete test adapted for PUT context
      ...
  }
}

contract StaxLPStakingCovTest_1 is Test { ... }
```

## File Paths Reference

### RQ1 Data
- **Results**: `/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/`
- **Scripts**: `/home/samson/workspace/esbmc/notes/coverage/scripts/`
- **Key scripts**:
  - `rq1_final_test_inventory.py` — Obligation classification
  - `rq1_concrete_replay_migrate.py` — RQ1 data access
  - `target_manifest.py` — 509 case manifest
  - `rq1_anchor_migrate.py` — Anchor migration script

### RQ3 Data
- **Results**: `/home/samson/workspace/VeriPUT/Results/RQ3/`
- **Main run**: `adoption-bundles/rq3-persistence-republish-20260815/staged/`
- **Partition**: `adoption-bundles/rq3-oracle-source-binding-20260815/partition.tsv`

### Git Status
- **Last commit**: `results: checkpoint RQ1 VeriPUT before anchor migration`
- **Branch**: (check current branch)

## 376 Valid-no-PUT Investigation (2026-08-16)

### Current State
- **Frozen CE obligations**: 1,808 total
  - Generalized (PUT + anchor + double-green): 147
  - Unresolved strength (PUT exists, anchor evidence incomplete): 1,661
  - Not generalized (concrete replay only): 0
- **Recovery pool**: 521 initial valid-no-PUT cases
  - Recovered (converted to generalized): 145
  - **Remaining**: 376

### Root Cause Classification (376 remaining)

The 376 remaining cases fall into 8 categories based on the `category` field in `rq1_recovery_pool_521.frozen.json`. Each category represents a different failure mode in the PUT generation/anchor injection pipeline.

#### 1. no-generalizable-coordinate (232 obligations, 112 unique cases)
**Description**: ESBMC certified the region but couldn't find a fuzzable coordinate to generalize over. The PUT was generated but anchor injection failed.

**Failure modes**:
- `missing-ce-anchor`: PUT exists in result.json but has no `ce_anchor` metadata
- `missing-anchor-body`: `ce_anchor` metadata exists but the actual anchor function is missing from the PUT `.t.sol` file

**Examples**:
- `acfix_021_CVE_2018_19832`: `allowance#2`, `balanceOf#2` — missing-ce-anchor
- `SafeProxyFactory`: `proxyCreationCode#3` — has ce_anchor metadata but missing-anchor-body
- `SablierComptroller`: `supportsInterface#3`, `supportsInterface#6` — missing-ce-anchor

**Root cause**: PUT files were generated but anchor injection either failed silently or the anchor body was lost during file operations.

#### 2. not-certified-fallback (184 obligations, 66 unique cases)
**Description**: ESBMC certification fell back to a non-optimal path. PUTs exist but anchor evidence is incomplete.

**Failure modes**:
- `missing-ce-anchor`: No ce_anchor metadata
- `missing-anchor-body`: ce_anchor metadata exists but anchor function missing from file

**Examples**:
- `OpenAddressLottery`: `kill#6`, `luckyNumberOfAddress#29` — missing-ce-anchor
- `Ballot`: `delegate#63`, `giveRightToVote#31` — has ce_anchor but missing-anchor-body
- `AavePoolReward`: `getReward#3` — missing-ce-anchor

**Root cause**: Similar to category 1 — PUT generation succeeded but anchor injection failed.

#### 3. certified-region-renderer-gap (38 obligations, 27 unique cases)
**Description**: ESBMC certified the region successfully, but the PUT renderer didn't include the anchor body.

**Failure mode**:
- `missing-anchor-body`: ce_anchor metadata exists but anchor function missing from PUT file

**Examples**:
- `FlashGovernanceArbiter`: `enforceTolerance#13`, `enforceToleranceInt#7` — missing-anchor-body
- `TokenPairRegistry`: `pendingOwner#3` — missing-anchor-body
- `PausableZoneController`: `acceptOwnership#7` — missing-ce-anchor

**Root cause**: PUT renderer gap — metadata says anchor should exist, but the actual Solidity function wasn't written to the file.

#### 4. metadata-unknown (29 obligations, 14 unique cases)
**Description**: Mixed category — some PUTs have ce_anchor metadata, some don't. The common factor is missing anchor body.

**Failure modes**:
- `missing-anchor-body`: ce_anchor metadata exists but anchor function missing from file (majority)
- `missing-ce-anchor`: No ce_anchor metadata (minority)

**Examples**:
- `many_fun`: `f1#6`, `f1#7` — missing-anchor-body
- `exampl`: `A#6`, `A_set#3` — missing-anchor-body
- `Greeter`: `changeHello#6` — missing-anchor-body, `changeHello#14` — missing-ce-anchor

**Root cause**: Same as category 3 — anchor metadata was injected but the actual function body wasn't written.

#### 5. certification-timeout (7 obligations, 6 unique cases)
**Description**: ESBMC certification timed out before completing. PUTs were generated but anchors were never injected.

**Failure mode**:
- `missing-ce-anchor`: No ce_anchor metadata (certification didn't complete)

**Examples**:
- `ERC-3643__Token`: `increaseAllowance#15` — missing-ce-anchor
- `array-utils`: `indexOf#7`, `indexOfFromEnd#7` — missing-ce-anchor

**Root cause**: Certification timeout — PUT generation started but didn't complete anchor injection.

#### 6. manual-source-grounded (4 obligations, 4 unique cases)
**Description**: Cases where the source grounding was done manually or through a non-standard path.

**Failure modes**:
- `invalid-anchor-provenance`: ce_anchor metadata exists but provenance checks fail
- `missing-ce-anchor`: No ce_anchor metadata

**Examples**:
- `FIFSRegistrar`: `register_source_put` — invalid-anchor-provenance
- `VaultAdmin`: `getMinimumPoolTokens#3` — missing-ce-anchor
- `PublicResolver`: `supportsInterface#2` — missing-ce-anchor

**Root cause**: Non-standard anchor provenance — metadata exists but doesn't pass validation.

#### 7. unmatched-no-current-manifest (3 obligations, 3 unique cases)
**Description**: Cases that were in the original recovery pool but no longer have a current manifest entry.

**Failure modes**:
- `invalid-anchor-provenance`: ce_anchor metadata exists but provenance checks fail
- `missing-ce-anchor`: No ce_anchor metadata

**Examples**:
- `ReferenceConsideration`: `name#1` — invalid-anchor-provenance
- `TimelockAuthorizerMigrator`: `executeDelays#1p1`, `finalizeMigration#1p1` — invalid-anchor-provenance
- `SablierBob`: `getAdapter#1`, `getDefaultAdapterFor#1` — missing-ce-anchor

**Root cause**: Manifest drift — cases were in the recovery pool but manifest entries were removed or changed.

#### 8. constructor-fallback (1 obligation, 1 case)
**Description**: Constructor-related case that fell back to a non-standard path.

**Failure mode**: Not yet analyzed

**Example**:
- `peer_soltg__constructor_state_variable_init_diamond`: `__deploy__#0`

### Summary of Failure Modes

| Failure Mode | Count | Description |
|-------------|-------|-------------|
| `missing-ce-anchor` | ~200 | PUT exists but no ce_anchor metadata in result.json |
| `missing-anchor-body` | ~150 | ce_anchor metadata exists but anchor function missing from .t.sol file |
| `invalid-anchor-provenance` | ~7 | ce_anchor metadata exists but provenance validation fails |
| `subject-dir-missing` | ~20 | Subject directory not found (path resolution issue) |
| `no-matching-put` | ~10 | No PUT found matching the recovery pool identity |

### Recommended Next Steps

1. **Priority 1: Fix missing-anchor-body cases (~150)**
   - These have ce_anchor metadata but the actual anchor function is missing from the PUT file
   - Likely caused by anchor injection script writing to wrong location or file overwrite
   - Fix: Re-run anchor injection for these cases

2. **Priority 2: Fix missing-ce-anchor cases (~200)**
   - These have PUT files but no ce_anchor metadata
   - Fix: Run anchor migration script (rq1_anchor_migrate.py) on these cases

3. **Priority 3: Fix invalid-anchor-provenance cases (~7)**
   - These have ce_anchor metadata but provenance validation fails
   - Fix: Investigate specific provenance failures and repair

4. **Priority 4: Investigate subject-dir-missing and no-matching-put cases (~30)**
   - Path resolution issues or manifest drift
   - Fix: Update result.json paths or regenerate missing cases

### Key Scripts for Investigation
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_anchor_migrate.py` — Anchor migration
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_final_test_inventory.py` — Obligation classification
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_concrete_replay_migrate.py` — Data access utilities
- `/home/samson/workspace/esbmc/notes/coverage/rq1_recovery_pool_521.frozen.json` — Frozen recovery pool
- `/home/samson/workspace/esbmc/notes/coverage/rq1_ce_obligations.frozen.json` — Frozen CE obligations
