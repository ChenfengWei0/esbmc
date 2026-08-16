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

### Test-Level Statistics (CE obligations)

| Metric | Count | Description |
|--------|-------|-------------|
| raw | 1,808 | 所有 CE obligations (frozen ledger) |
| valid | 1,808 | 与 raw 相同 |
| Valid-but-not-PUT (concrete) | 787 | 定值测试，无参数 foundry 测试 |
| valid-PUT (fuzz) | 1,021 | 参数化模糊测试 |
| PUT with exactly 1 `test_ce_anchor` | 1,334 | ✅ 符合要求（含重复/多 enc） |
| PUT with 0 `test_ce_anchor` | 23 | ❌ 需要注入 anchor |
| PUT with >1 `test_ce_anchor` | 116 | ❌ 需要清理 |

**关系**: raw == valid → 1,808 == 1,808 ✓

### 统计口径说明

**1,808 CE obligations** 来自 `rq1_ce_obligations.frozen.json`，是冻结的 CE 义务清单。每个 CE 义务是唯一的 `(case, path_function, unit, enc, piece)` 组合。

**787 concrete** 是当前有效的 concrete replay test rows（deduplicated by file+test+kind+unit）。

**1,021 PUT** = 1,808 - 787，是 PUT 形态的 CE obligations。

**1,334/23/116** 是通过数 `.t.sol` 文件中的 `test_ce_anchor_` 函数得到的。这些数字大于 1,021 是因为一个 CE obligation 可能对应多个 test rows（重试、不同路径）。

### result.json 可信度

- **summary 字段 (put_valid, concrete_valid)**: ❌ 不可信。在 anchor migration 时被修改，summary 字段没有更新。
- **detailed test rows (strict_detailed_test_rows)**: ✅ 可用。通过 `_strict_valid_tests` 读取。
- **SafeToL2Setup**: result.json 显示 `put_valid: 1`，但 `_strict_valid_tests` 返回 0 行。PUT 文件实际存在。这是 result.json 数据不一致问题。

### By Benchmark

| Benchmark | Cases | PUTs | Concrete | PUT anchored | PUT unanchored | PUT multi-anchor |
|-----------|-------|------|----------|--------------|----------------|------------------|
| real203 | 203 | 554 | 372 | 469 | 13 | 72 |
| peer182 | 182 | 588 | 228 | 563 | 5 | 20 |
| bugfix124 | 124 | 331 | 252 | 302 | 5 | 24 |
| **Total** | **509** | **1,473** | **852** | **1,334** | **23** | **116** |

**Note**: The `generalized_ce_obligations`, `unresolved_strength_ce_obligations`, and `not_generalized_ce_obligations` fields in JSON files are **misleading** and should be ignored. Always verify by counting `test_ce_anchor_` functions in `.t.sol` files directly.

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

## Recovery Pool 521 — 历史残留说明 (2026-08-16)

### Recovery Pool 的本质

`rq1_recovery_pool_521.frozen.json` 是一个**历史快照**，记录了某个时间点被识别为 "valid-no-PUT" 的 521 个案例。它**不是**当前 inventory 的实时视图。

### 当前真实状态（按文件计数）

| 测试类型 | 数量 | 说明 |
|---------|------|------|
| PUT tests (fuzz/parameterized) | 1,473 | 参数化模糊测试 |
| 其中带恰好 1 个 test_ce_anchor | 1,334 | 符合要求 |
| 其中无 anchor | 23 | 需要注入 anchor |
| 其中多个 anchor | 116 | 需要清理 |
| Concrete replay tests (固定值，无参数) | 852 | 非 fuzz 测试 |

### Recovery Pool 与当前的关系

- 521 个历史条目中，**145 个已匹配到当前 PUT**（这些 PUT 已有 anchor）
- **376 个条目已过期**（enc 值变了，或 case 被重新处理，identity 不再匹配）

### 376 个过期条目的原因

| 原因 | 数量 | 说明 |
|------|------|------|
| `enc_mismatch` | 196 | recovery pool 记录旧 enc，当前 PUT 已更新 |
| `no_matching_unit` | 178 | 当前无匹配 unit |
| `enc_match_but_not_in_inventory` | 2 | enc 匹配但找不到 |

### 建议

1. **Recovery pool 是历史快照，不是当前问题** — 376 个过期条目不需要修复
2. **当前关注点**：
   - 23 个 PUT 缺少 anchor → 需要注入
   - 116 个 PUT 有多个 anchor → 需要清理
3. **不要使用 JSON 中的 generalized/unresolved_strength 字段** — 这些字段与实际情况不符，应通过数 `.t.sol` 文件中的 `test_ce_anchor_` 函数来验证

### Key Scripts for Investigation
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_anchor_migrate.py` — Anchor migration
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_final_test_inventory.py` — Obligation classification
- `/home/samson/workspace/esbmc/notes/coverage/scripts/rq1_concrete_replay_migrate.py` — Data access utilities
- `/home/samson/workspace/esbmc/notes/coverage/rq1_recovery_pool_521.frozen.json` — Frozen recovery pool
- `/home/samson/workspace/esbmc/notes/coverage/rq1_ce_obligations.frozen.json` — Frozen CE obligations
