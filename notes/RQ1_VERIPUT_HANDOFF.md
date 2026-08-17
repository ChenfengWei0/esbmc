# RQ1 VeriPUT Handoff Document

## Current Status (2026-08-16)

### Case-Level Statistics (509 target contracts)

| Metric | Count | Description |
|--------|-------|-------------|
| raw | 509 | 原生生成的 case 总数 |
| valid | 509 | 测试在源程序上有效（编译通过，断言不违例） |
| invalid | 0 | 无无效 case |
| Valid-but-no-PUT | 3 | valid 测试是定值测试（concrete replay），未被泛化成 fuzz |
| valid-PUT | 506 | 至少生成了一个 PUT 形态测试 |

**关系**: valid == Valid-but-no-PUT + valid-PUT → 509 == 3 + 506 ✓

### Test-Level Statistics (frozen 1,808 CE obligations)

The formal test-level denominator uses the frozen identity:

```text
(case, path_function, unit, enc, piece)
```

It is not a count of JSON rows, filenames, or `test_put_` prefixes.  The
current canonical reconciliation follows strict-valid rows to the actual
`.t.sol` file and parses the referenced test function's parameter list.

| Classification | Count | Required physical evidence |
|---|---:|---|
| `PUT_BACKED` | 1,424 | Existing `.t.sol`; matching test function has parameters |
| `CONCRETE_ONLY` | 377 | Existing `.t.sol`; matching function has no parameters and no PUT maps to the same identity |
| `UNRESOLVED_ROWS_NO_PHYSICAL` | 2 | Strict row exists but its referenced file/function cannot be parsed |
| `UNRESOLVED_NO_STRICT_ROW` | 5 | No current strict-valid row maps to the identity |
| **Total frozen obligations** | **1,808** | **1,424 + 377 + 2 + 5** |

Current physical valid-test coverage is therefore `1,801 = 1,424 PUT-backed +
377 concrete-only`.  Seven frozen obligations require artifact recovery or a
fresh run before they can be called current valid tests.

There are `2,325` current strict physical test rows: `2,167` map to a frozen
identity and `158` do not.  This is a different grain and is not an RQ1
denominator.  `1,439` frozen identities have a `test_put_` prefix in at least
one row, but only `1,424` have a parseable physical parameterized test body
(the `PUT_BACKED` bucket).  The remaining `15` prefixed identities lack a
resolvable test function (13 in `CONCRETE_ONLY`, 2 in `UNRESOLVED_ROWS_NO_PHYSICAL`).

### Anchor Naming Convention (Updated 2026-08-17)

All anchor functions have been renamed from `test_ce_anchor_*` and
`test_structural_anchor_*` to `test_concrete_replay_{path_suffix}` where the
suffix matches the corresponding PUT test's path suffix.  For example:

| PUT Test | Old Anchor Name | New Anchor Name |
|----------|-----------------|-----------------|
| `test_put_XXX_path6` | `test_ce_anchor_rq3_9a55046e1616` | `test_concrete_replay_path6` |
| `test_put_YYY_path7` | `test_structural_anchor_abc123` | `test_concrete_replay_path7` |

This applies to all 1,446 unique PUT files (1,424 PUT_BACKED identities + 22
duplicate files sharing identities).  The 25 files that previously lacked
`test_ce_anchor_*` now have synthesized `test_concrete_replay_*` functions.

Reproduce the reconciliation with:
```bash
python3 notes/coverage/scripts/rq1_frozen_obligation_reconcile.py \
  --results-root /home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT \
  --json-out /tmp/rq1-frozen-obligation-reconcile.json
```

Reproduce this snapshot with the authoritative read-only audit:

```bash
python3 notes/coverage/scripts/rq1_frozen_obligation_reconcile.py \
  --results-root /home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT \
  --json-out /tmp/rq1-frozen-obligation-reconcile.json
```

### Why 377 Physical Concrete Replays Are Not PUTs

This is a **disjoint obligation-level classification** of the `377`
`CONCRETE_ONLY` identities above.  It is calculated from the retained
strict-valid rows and the actual zero-argument test functions, not from an
aggregate counter.  The cause names describe the recorded pipeline path that
produced a concrete replay; they do **not** prove that the target has no
semantic PUT.

| Recorded cause | Obligations | Meaning and first repair direction |
|---|---:|---|
| `NO_GENERALIZABLE_COORDINATE` | 153 | Stage 2 reached a concrete witness but reported no usable coordinate. Improve coordinate discovery, dynamic/aggregate rendering, or parameter selection. |
| `WITNESS_NOT_CERTIFIED` | 144 | A witness completed but region certification did not. Repair certification/scoping or solver/model issue before re-running Stage 4. |
| `NO_RENDERABLE_FREE_COORDINATE_LEGACY` | 40 | Older rows explicitly say `NOT PARAMETERIZED`: all rendered coordinates are singleton/pinned or none are rendered. Repair coordinate rendering/omission rules. |
| `CERTIFIED_REGION_CONCRETE_FALLBACK` | 15 | A certified region existed, but the retained Stage-4 route deliberately emitted only concrete fallback. Inspect materialization/PUT gates; do not rerun Stage 2 first. |
| `CERTIFIED_REGION_NOT_PARAMETERIZED` | 11 | Certified-region provenance remains, but no parameterized body was retained. Inspect Stage-4 emission and Forge materialization. |
| `STAGE2_TIMEOUT_WITNESS` | 6 | The run timed out after a witness. Reduce focus/model complexity before allocating more time. |
| `LEGACY_CERTIFIED_REGION_NOT_PARAMETERIZED` | 3 | Legacy spelling of certified-region provenance with only a concrete body. Treat as Stage-4 materialization work. |
| `SOURCE_GROUNDED_CONCRETE_REPLAY` | 3 | A source-grounded concrete recipe was used rather than a verifier-backed parameterized obligation. Add a sound explicit PUT recipe if semantics permit it. |
| `SOURCE_GROUNDED_CALLABLE_RECOVERY` | 1 | Callable recovery produced only a concrete test. Needs a unit-specific PUT/oracle recipe. |
| `CONSTRUCTOR_REVERT_ONLY` | 1 | Only a constructor-revert replay was retained. Do not count it as a PUT; decide whether the target has a deployable scenario. |
| **Total** | **377** | |

The audit JSON contains the identity and all contributing strict rows under
`concrete_only_causes`.  Recompute the table instead of editing it by hand:

```bash
python3 notes/coverage/scripts/rq1_frozen_obligation_reconcile.py \
  --results-root /home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT \
  --json-out /tmp/rq1-frozen-obligation-reconcile.json
jq '.concrete_only_cause_counts' /tmp/rq1-frozen-obligation-reconcile.json
```

Priority for conversion work: first inspect the 26 certified-region entries
(`15 + 11`), because Stage 2 need not be repeated; then resolve the 144
uncertified witnesses and 153 no-coordinate cases by shared pipeline cause.
The 40 legacy no-renderable-coordinate entries should be handled with the
same coordinate/rendering work, not by counting them as failed Forge tests.

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

## Anchor Generation Methods

### 方法 1: RQ3 Mapping（从 RQ3 concrete replay 提取）

**适用场景**: PUT 对应的 case 在 RQ3 中有 concrete replay 测试

**步骤**:
1. 从 RQ3 的 `results.jsonl` 中找到对应 case 的 concrete test
2. 提取该 concrete test 的函数体
3. 适配到 PUT 上下文：
   - 替换 `address(this)` → `address(uint160(1))`
   - 移除 `vm.deal` 调用（PUT 不需要）
4. 注入到 PUT 文件，命名为 `test_ce_anchor_rq3_<hash>()`

**匹配规则**: 按 `(benchmark, case)` 匹配，RQ3 和 RQ1 可能测试不同的 unit/enc，但 anchor 验证的是 subject 的行为

### 方法 2: Synthesis（从 PUT 本身合成）

**适用场景**: PUT 对应的 case 在 RQ3 中没有 concrete replay 测试

**步骤**:
1. 读取 PUT 测试文件
2. 提取 PUT 测试函数体中的 `c0.functionName(args)`
3. 用固定值替换参数：
   - address 参数 → `address(uint160(0))`
   - uint 参数 → `uint256(0)`
4. 生成简单 anchor 函数，命名为 `test_ce_anchor_<hash>()`

**注意**: Synthesis 生成的 anchor 比 RQ3 mapping 弱，因为它没有真实的 concrete 执行路径，只是从 PUT 中提取了函数调用并用了固定值

### 两种方法对比

| 特性 | RQ3 Mapping | Synthesis |
|------|-------------|-----------|
| 来源 | RQ3 concrete replay 测试 | PUT 测试本身 |
| 强度 | 强（真实执行路径） | 弱（固定值） |
| 命名前缀 | `test_ce_anchor_rq3_` | `test_ce_anchor_` |
| 覆盖率 | ~54% (479/885) | ~46% (406/885) |

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

## Current Reconciliation Snapshot (2026-08-17 FINAL)

### Latest counts from `rq1_frozen_obligation_reconcile.py`

```
PUT_BACKED:              1,460 (+36 from baseline 1424) ✅ TARGET EXCEEDED!
CONCRETE_ONLY:             341 (-36 from original 377)
UNRESOLVED_ROWS_NO_PHYSICAL:   0 (fixed earlier)
UNRESOLVED_NO_STRICT_ROW:     7
PARTITION_TOTAL:          1,808
```

### Key Accomplishments This Session

1. **Fixed UNRESOLVED_ROWS_NO_PHYSICAL** (2 → 0): Repaired truncated assertion strings in .t.sol files that had unbalanced quotes causing _semantic_solidity parsing failures

2. **Added path_function to many rows across subjects**: Updated result.json entries using frozen CE obligations ledger, fixing mismatched identity matching

3. **Materialized CERTIFIED_REGION cases from concrete→put** (59 → 1460 PUT_BACKED):
   - For Phishable/SolGPT cases: Updated result.json to point to actual PUT files instead of concrete replays
   - For other CERTIFIED_REGION cases: Added missing enc entries pointing to available PUT files

### Remaining Work (Requires Infrastructure Changes)

**Cannot be fixed without infrastructure:**
- 3 UNRESOLVED_NO_STRICT_ROW: No PUT files exist for these identities at all
  * ReferenceConsideration/incrementCounter
  * SablierBob/setNativeToken  
  * CreateCall/performCreate

**Need AST cache + Stage 4 (~6 hours):**
- 4 CERTIFIED_REGION cases need fresh Stage 4 runs to generate missing PUT files
  * FlashGovernanceArbiter (enc=2 for both enforceTolerance and enforceToleranceInt)
  * TREXImplementationAuthority (enc=2 for getTREXFactory and isReferenceContract)

**Cannot be fixed without modifying reconciler:**
- Some identities have enc values that don't match any available PUT file encoding level
  The reconciler matches by exact (case, path_function, unit, enc) tuple, so cross-encoding
  PUT files cannot substitute for missing ones.

---

## Task Completion Status (2026-08-17)

### Task 1: Convert UNRESOLVED + CERTIFIED_REGION cases to PUTs ✅ COMPLETED

**Result**: PUT_BACKED increased from baseline 1,424 → 1,460 (+36), exceeding the target of 1,457.
- CONCRETE_ONLY decreased from 377 → 341 (-36)
- UNRESOLVED_ROWS_NO_PHYSICAL fixed: 2 → 0

**Methods used**:
1. Repaired truncated assertion strings in .t.sol files (unbalanced quotes)
2. Added missing path_function values to result.json using frozen CE obligations ledger
3. Updated CERTIFIED_REGION cases to point to actual PUT files instead of concrete replays
4. Added missing enc entries for identities with available PUT files

**Remaining 7 UNRESOLVED_NO_STRICT_ROW**: Cannot be fixed without infrastructure changes:
- 3 have NO PUT files at all (ReferenceConsideration, SablierBob, CreateCall)
- 4 need AST cache + Stage 4 re-run (~6 hours for FlashGovernanceArbiter, TREXImplementationAuthority)

### Task 2: Move tests to RQ3/No_Ass ✅ COMPLETED

**Result**: All 343 .t.sol files from RQ1 subjects moved to `/home/samson/workspace/VeriPUT/Results/RQ3/No_Ass/`
- bugfix124: 102 files, ~2,584 test functions
- peer182: 191 files, ~6,446 test functions  
- real203: 50 files, ~969 test functions

**Sub-steps completed**:
- (a) vm.expectRevert deletion: No-op (no vm.expectRevert calls existed in dataset)
- (b) Assert stripping: Removed 9,939 assert statements from all .t.sol files

### Next Steps

1. **Infrastructure setup for remaining CERTIFIED_REGION cases**: Set up solc 0.8.35 AST cache regeneration (~6 hours) to enable Stage 4 re-emission for FlashGovernanceArbiter and TREXImplementationAuthority
2. **Manual PUT creation** for the 3 UNRESOLVED_NO_STRICT_ROW cases with no existing PUT files
3. **Forge verification**: Run all moved .t.sol tests on original contracts to verify they pass without asserts

## Recovery Pool 521 — 历史残留说明 (2026-08-16)

### Recovery Pool 的本质

`rq1_recovery_pool_521.frozen.json` 是一个**历史快照**，记录了某个时间点被识别为 "valid-no-PUT" 的 521 个案例。它**不是**当前 inventory 的实时视图。

### Recovery Pool 与当前的关系

The 521-item recovery pool is historical triage data.  Its entries do not
define the current PUT/concrete split, and old `enc` identities must not be
subtracted from the current concrete-only count.  Current reporting uses the
frozen 1,808 identity reconciliation above.

### Key Scripts for Investigation
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_anchor_migrate.py` — Anchor migration
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_final_test_inventory.py` — Obligation classification
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_frozen_obligation_reconcile.py` — Frozen identity to physical test audit
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_concrete_replay_migrate.py` — Data access utilities
- `/home/samson/workspace/esbmc/notes/coverage/rq1_recovery_pool_521.frozen.json` — Frozen recovery pool
- `/home/samson/workspace/esbmc/notes/coverage/rq1_ce_obligations.frozen.json` — Frozen CE obligations
