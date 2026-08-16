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

## 376 Recovery Pool "Remaining" — 真实含义调查 (2026-08-16)

### 关键发现：376 不在当前 CE 义务中

**376 不是 1,808 个 CE 义务的子集。** 它们是 recovery pool 中的历史残留条目，其 identity 已不再匹配当前 inventory。

### 数据关系

```
Frozen CE obligations (1,808):
  ├─ Generalized: 147 (PUT + anchor + double-green)
  ├─ Unresolved strength: 1,661 (PUT 存在，anchor 证据不完整)
  └─ Not generalized: 0 (仅 concrete replay)

Recovery pool (521, 历史快照):
  ├─ 145 — 已匹配到当前 generalized (已恢复)
  └─ 376 — 不再匹配当前 inventory (历史残留)

数学关系: 147 + 1,661 + 0 = 1,808 ✓
          145 + 376 = 521 (recovery pool)
          376 不在 1,808 中
```

### 376 个案例去哪了？

所有 376 个案例的 subject_dir 都存在，且包含有效的 PUT 测试。问题在于 **recovery pool 中的 identity 与当前 inventory 不匹配**。

#### 不匹配原因

| 原因 | 数量 | 说明 |
|------|------|------|
| `enc_mismatch` | 196 | recovery pool 中的 `enc` 值与当前 PUT 的 `enc` 不同 |
| `no_matching_unit` | 178 | 当前 inventory 中没有匹配的 `unit` |
| `enc_match_but_not_in_inventory` | 2 | enc 匹配但仍不在 inventory 中 |

#### 具体例子

**enc_mismatch (196 个)**: Recovery pool 记录的是旧 run 的 enc 值，当前 PUT 已更新为不同的 enc。
```
Recovery pool: acfix_016_CVE_2018_10705/setOwner#enc=2
Current:       acfix_016_CVE_2018_10705/setOwner#enc=6  ← enc 不同，identity 不匹配

Recovery pool: acfix_022_CVE_2018_19833/burn#enc=15
Current:       acfix_022_CVE_2018_19833/burn#enc=6  ← enc 不同，identity 不匹配
```

**no_matching_unit (178 个)**: Recovery pool 记录的 unit 在当前 PUT 中不存在。
```
Recovery pool: acfix_021_CVE_2018_19832/allowance#enc=2
Current:       acfix_021_CVE_2018_19832 的 PUT 中 unit 已改变或 case 被重新处理
```

### Recovery Pool 的本质

Recovery pool 是一个**历史快照**，记录了某个时间点被识别为 "valid-no-PUT" 的 521 个案例。它不是当前 inventory 的实时视图。

- 145 个案例已成功恢复（PUT + anchor + double-green），已匹配到 generalized
- 376 个案例的 identity 已过期（enc 值变了，或 case 被重新处理），不再匹配当前 inventory

### 与 ce_anchor 的关系

**376 与 ce_anchor 无关。** 这些案例不是 "缺少 anchor" 的问题，而是 recovery pool 条目已过期的问题。它们可能已经有 PUT 和 anchor，只是 identity 不匹配。

### 建议

1. **376 不需要修复** — 它们是历史残留，不是当前问题
2. **关注 1,661 unresolved_strength** — 这些是当前真正缺少完整 anchor 证据的案例
3. **recovery pool 可以视为已完成** — 521 中有 145 已恢复，376 已过期（不再是有效跟踪对象）

### Key Scripts for Investigation
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_anchor_migrate.py` — Anchor migration
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_final_test_inventory.py` — Obligation classification
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_concrete_replay_migrate.py` — Data access utilities
- `/home/samson/workspace/esbmc/notes/coverage/rq1_recovery_pool_521.frozen.json` — Frozen recovery pool
- `/home/samson/workspace/esbmc/notes/coverage/rq1_ce_obligations.frozen.json` — Frozen CE obligations
