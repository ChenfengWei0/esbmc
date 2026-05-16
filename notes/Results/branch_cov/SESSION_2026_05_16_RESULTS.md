# Coverage 会话结果与结论 — 2026-05-16

本会话的事实记录(Claim = 本会话实测;Residual = 未测,不编造)。

## 1. 决策锁定:文件/项目级 coverage

`--contract C_i` 仅作成本切分的 driver(整单元一次性太贵)。分母 =
整个扁平编译单元里 distinct 源决策(按源身份去重);分子 = 各
`--contract` 运行 reached 集的并。**不按合约缩分母。**

## 2. S-D 已交付并提交(commit `aa7c7bf9af`)

分支 `fix/coverage-filelevel-dedup-denominator`。

修复 = 一行,`src/goto-programs/goto_coverage.cpp:291`:
`total_branch = static_cast<size_t>(all_claims.size());`
(原 `= get_total_instrument();`)。

机制(本会话亲读):`get_total_cond_assert()` 早已用
`(condition, location.as_string())` 做 `std::set` 去重;
`locationt::as_string()`(`util/location.cpp:3-38`)用**源**函数名+
file:line,非改名后的 `@C@A@F@…#NN` → 继承/modifier 物理副本逐字节
折叠。分子本就用这个去重集,只有分母是原始未去重计数 = 全部 bug。
AST-node-id 键 / 删 location_pool / 可达性 BFS **均不需要**。

验证(旧 vs 新二进制,最终判决):
- modifier_crosscontract: Branches **6→4**(折叠 @C@A@/@C@B@ 副本)
- sibling / uncalled_library: 无操作(无副本)
- single_contract_pass(CORE 对照): 2/2/100% 不变
- `ctest -R cov_scope_` 4/4 PASS;3 KNOWNBUG→**CORE**(triple
  `^Branches : 4$`/`^Reached : 2$`/`^Branch Coverage: 50%$`)

回归(本会话实测):
- `regression/goto-coverage` **109/109 PASS**(branch/cond/func/
  kpath/stl/github 全绿)——未受影响
- `cover_*` / `branch_cov_*` 26/27(唯一失败 = 既存无界 k-induction
  超时 `cover_iterable_mapping_1`,与本修无关:本修严格减少工作量)

**误诊更正:** 此前记为"独立 Line-N 分子 bug"的 `Reached:0` 是
k-induction 非最终迭代行;最终判决一直 `Reached:2`,旧二进制亦然。
cov_scope 这些 case 没有 Line-N bug。

## 3. EscrowDst 实测(R1 关闭)

旧 `release-bundle` 二进制在 `$balance`/`Create2` GOTO-gen SIGABRT
(stale,缺工作树 crash-fix);**新二进制实测**:
**Branches 80 / Reached 37 / 46.25%**(修复前 bug 是 90;去重折叠
10 个重复 `(cond,location)` → 80)。

**与 hardhat/solidity-coverage 比较 = 苹果对橘子(关键发现):**
KNOWNBUGS.md 记录的 native `2/2/100%` 是 solidity-coverage 对
**`EscrowDst.sol` 单源文件自身**的分支(库/基类在各自文件单独算)。
ESBMC 吃**扁平化单文件**(EscrowDst + BaseEscrow + TimelocksLib/
ImmutablesLib/ProxyHashLib + OZ 全内联),故 80 = **整扁平单元**去重
distinct 决策。直接比无意义;要可比需 hardhat **项目级聚合** vs
ESBMC 整单元,或 ESBMC 按原始源文件归属(前端已扁平,没有)。
⇒ 扁平化输入下,ESBMC 的"文件级" = 扁平单元级,天然 > solidity-
coverage 的"单源文件级"。

## 4. 四问结论

- **`--contract C --coverage` 的分母** = 整扁平单元 distinct
  `(cond, location.as_string())` 源决策数,**与传哪个 `--contract`
  无关**(同 .solast → 同插桩 → 同 all_claims 集)。EscrowDst 的 80
  是整单元,不是"EscrowDst 合约自身分支"。
- **平衡 whole-file vs 逐合约**:分母跨 run 不变(整单元、同
  .solast);单个 `--contract C` run = 对的分母 + 仅 C 可达的部分
  分子(健全但悲观,只少报不多报)。真项目覆盖率 = 一组覆盖性合约
  各跑一次,reached 集按同一去重键取并 ÷ 共享整单元分母。跨 run
  取并聚合器 = **S-N2,未实现**。
- **coverage 模式不用 `--bound`**:确认。单调加约束只移除可行路径
  不增加 → `cov%(--bound) ≤ 真实` → 对覆盖率不健全(把真能到的
  分支误判死、压低)。coverage 已中和 dispatcher 且强制
  `--k-induction`;`--bound` 正交且有害。(机制交互代码本会话未读,
  但单调性健全性论证严谨,足以支撑结论。)

## 5. aqua/Aqua 状态更正

**`cov_pilot_aqua_Aqua`(16 行最小复现)已修复,非待修。** test.desc
= CORE,钉 `^Branch Coverage: 75%$`;新二进制实跑 `Branches:4
Reached:3 75%` 通过。2026-05-15 已由 `solidity_convert_expr.cpp:4203`
深层嵌套 mapping WRITE 修复翻 CORE(本会话前已提交)。
完整 87KB aqua 扁平合约是否仍有其他墙 = 待探(下一步)。

## 6. 残留 / 下一步

- **S-N2**:跨 `--contract` run 的 reached 取并聚合器(真项目覆盖率
  机制)——未实现。
- **EscrowDst pin 过时**:KNOWNBUG 钉 `^Branches : 90$`,实测已 80;
  需重钉(本提交未动,避免范围蔓延 + 需 ctest 复核)。
- farming/st1inch/lop 修复后数:未测(farming 卡 BytesStatic;
  st1inch k-induction budget-burn;lop 14 库函数 `--function`)。
- 完整 aqua 残留症状:下一步探。
- 提交前 code-review 步(CLAUDE.md §3)对 `aa7c7bf9af` 未跑。
- `goto_coverage.cpp:182 flg always true` = 既存(别的函数),
  非本 diff,留待独立清理。
