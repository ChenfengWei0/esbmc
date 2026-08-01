# AUDIT — 一份文件，供外部审核

本文件合并并取代今天分散写出的三份：`POSTMORTEM_FIVE_DAYS.md`、
`coverage/EXPLOSION_CONTROL_AUDIT.md`、`EXPLOSION_CONTROL_OPTIONS.md`。
分成多份本身就是被批评的问题之一（"你现在有过多文档了"），所以合并。

**读者假设**：不熟悉本项目的审核者（人或 AI）。因此每个术语首次出现时定义，
每条结论标出处。

---

## 第 0 部分 · 出处标记规则（先读这个，否则会把猜测当结论）

- `[实测]` = 我在真实运行的 stdout / 产物文件 / 源码里**直接读到**，本次会话内。
- `[沿用]` = 项目此前从源码读出并落盘的结论，**我本轮没有重新核对**。
- `[推断]` = 我对行为的推理，**不是引文**。审核者应当把它当作待验证的断言。

这条规则本身是有来历的：本项目已发生过多次"看文件名/看数字形状编一个机制，
再据此推理"的失败。**标记不是礼貌，是防线。**

**⚠ 关于 `--help`**：本项目已记录 `--help` 在事务深度语义上是**错的**。
权威是 `notes/path-coverage-invocation-contract.md`（1053 行，逐条 file:line
从源码读出），我本轮**没有重读它**——所有 `[沿用]` 行请对照该文件，不要对照我。

---

## 第 1 部分 · 项目是什么，承诺了什么

### 1.1 交付物的定义

**VeriPUT**：从验证器推导出**参数化单元测试**（Parameterized Unit Test），
用于智能合约的**路径覆盖**。

四个阶段：

| 阶段 | 内容 |
|---|---|
| ① | 对每个 public/external 单元，枚举其**完整路径**（一次调用从入口到返回或回滚的完整决策序列） |
| ② | 把一条路径的**反例**（一个具体输入）长成一个**被证明的输入区域** |
| ③ | 证明该区域上的**断言**（R0/R1/R2） |
| ④ | 渲染成 Foundry 测试 |

**最终交付物 = 一个带 `vm.assume(区域)` 和 post-state 断言的 `.t.sol`，
`forge test` 跑绿。**

### 1.2 关键定义（用户裁定过，是承重的）

- **单元 u** = 外界可以调用的一个**入口点**。
- **路径 π** = **一次调用**从入口到返回或回滚所走的完整决策序列。
- **路径域 D_π** = **测试能给出的输入**里，走那条路的那些。

⇒ 由此推出（用户明确裁定）：**构造器里的那次执行不是一次外部调用**——它没有
calldata、没有 ABI 值门、没有发起交易的账户，**压根不在 X_u 里**。把它算作覆盖会
破坏健全性命题（发出的测试会是红的）。

### 1.3 命题

> 发出的测试所接纳的输入都落在 D_π，且测试在**未改动的合约**上通过。

**"在未改动的合约上跑绿"是不可谈判的。** 它是本项目所有"宁可弱化、不可撒谎"
设计取向的来源。

---

## 第 2 部分 · 五天之后的实际状态：交付物是 0

### 2.1 转化率（唯一有意义的口径）

四个 benchmark，per-unit 口径，工具 `notes/coverage/scripts/funnel.py` `[实测]`：

| 阶段 | 数量 | 转化 |
|---|---|---|
| X 插桩出的完整路径 | 922 | — |
| Y 拿到反例（具体输入） | 171 | **19 %** |
| Z 写成 `.t.sol` 用例 | 209 | **口径不同，算不出比率** |
| A 输入区域被认证 | **7** | **4 %** |
| **B 带断言的参数化测试** | **0** | **0 %** |

### 2.2 逐格解释这四段损失

**X→Y = 19 %。** 未见证的 751 条**全部**是 `bounded-holds`——"在这个探索界内
没找到走这条路的输入"。注意它**不是**"证明了不可达"：
`path_cov_can_prove_unreachable()` 无条件返回 false，所以三态里的 `I`（已证不可达）
**结构性恒为 0**。`[实测 — funnel.py 输出]`

**⚠ 不能直接翻这个布尔**：入口状态从不 havoc，运行是从后构造状态起的一笔交易，
所以"不可达"目前只意味着"在这个 harness 里不可达"。翻了它等于对每一条
"换个入口状态就能走到"的路径**谎称已证明不可行**——那是三态方案里最强的断言。
真正的阻塞是入口状态 havoc（`__ESOL_nondet_state_forward`），不是那个布尔。`[实测]`

**Y→Z 算不出比率。** Z 数的是 `cases` 不是 paths，而且**两个方向都有**：
`EscrowDst__cancel` 12 条见证 → 45 个 case；`FarmingPool__exit` 37 条见证 → **5** 个
case。⇒ `cases` 既不是路径数的上界也不是下界，**在 emitter 声明它自己的计数单位
之前，X→Y→Z 的链式比率是无定义的**。`[实测]`

**Y→A = 4 %。** 而且这 4 % 里三分之一是假的，见 2.3。

**A→B = 0 %。** 见 2.4。

### 2.3 ⛔ 那 7 个"认证成功"里，6 个是带额外语法的定值测试

工具自己的 applicability metric 原话 `[实测 — certify_summary.py]`：

> "A coordinate certified as `[v, v]` is a concrete test with extra syntax.
> Counting it as 'bounded' is what would make a unit look generalised when half
> its region is a constant."

| benchmark | 有界坐标中是单点 `[v,v]` 的 |
|---|---|
| aqua | 0 / 12 = **0 %** |
| **farming** | **6 / 6 = 100 %** |
| 合计 | **6 / 18 = 33 %** |

⇒ **整个语料里真正有宽度的坐标只有 12 个，全部在 aqua 一个合约上。**

⇒ subgoal 3（完美实现区间输入）的真实状态不是「4 % 已认证」，是
**171 条见证 → 12 个真正的区间**。

### 2.4 ⛔ A→B = 0 的原因不是"难"，是两个结构性问题

**(a) emitter 的类型表达不出 PUT。** `[实测 — 通读 src/goto-symex/foundry.h]`

```
test_case = std::vector<sol_call>
sol_arg { param, sol_type, literal /* 一个定值字面量 */, value, ... }
```

整个数据模型里**没有"区间"，没有"断言阶梯"**。下游全部围绕那一个 `literal` 渲染。
所以 B 不是"一个会返回 0 的功能"，是**一个表达不出该概念的类型**。

**(b) 流水线顺序是反的。** `[实测]`
阶段 ②（认证）是**进程外**的 python sweep，在 esbmc **退出之后**跑，产物是
`notes/coverage/certify/results.jsonl`。而 emitter 跑在 esbmc **内部**（每命中一个
目标调一次 `collect()`，最后 `generate()`）。

⇒ **emitter 运行时那个文件还不存在。阶段 ② 发生在阶段 ④ 之后。**

好消息：接头已存在。`claims_by_fingerprint` 把每个发出的 case 的去重指纹映射到它
来源的 claim 身份（如 `pull:path:63`），其注释写明它存在就是为了让发出的测试能
**回溯审计到产生它的报告**。`[实测]`

---

## 第 3 部分 · 本质分析：那个 3 行函数到底发生了什么

**这是本审计的核心。用户的原话是"那么简单的 3 行函数，都得不到测试"。**

### 3.1 函数

```solidity
function setFeeReceiver(address feeReceiver_) public onlyOwner {
    if (feeReceiver_ == address(0)) revert ZeroAddress();
    feeReceiver = feeReceiver_;
    emit FeeReceiverSet(feeReceiver_);
}
```

### 3.2 我们的实际表现 `[实测]`

| 度量 | 值 |
|---|---|
| 枚举出的完整路径 | 5 |
| **见证的** | **0** |
| VCC | 10（构造器也调用它，见 3.6） |
| symex | 0.098 s，1526 条赋值 |
| VCC 化简后 | 875 条赋值 |
| **编码成 SMT** | **0.028 s** |
| 求解 | z3 `out of memory` ／ bitwuzla 120 s 不返回 ／ cvc5 `std::bad_alloc` |

**编码 28 毫秒，求解耗尽 8 GiB。公式很小，但极难。**

### 3.3 这条路径条件本身有多难？

从报告的 `decisions` 数组读出 `[实测]`：

- `path:2`（msg.value 非零 → 回滚）：**一条**约束 `msg.value == 0`
- `path:15`（正常退出）：**三条** —— `msg.value == 0`、`msg.sender == owner`
  （来自 `onlyOwner`）、`feeReceiver_ != address(0)`

**三条 256 位线性约束。任何 SMT 求解器微秒级解掉。一个 fuzzer 随机撒点，
前两条第一发就能满足。**

### 3.4 那 8 GiB 花在哪

花在**路径条件之外**的东西上。我们交给求解器的公式还包含：

**(a) 整个构造器的符号执行** `[实测 — contracts/St1inch.sol:99-120]`：

```solidity
constructor(IERC20 oneInch_, uint256 expBase_, address feeReceiver_)
    ... VotingPowerCalculator(expBase_, block.timestamp) ... {
    if (_votingPowerAt(1e18, block.timestamp + MAX_LOCK_PERIOD) * 20 < 1e18)
        revert ExpBaseTooBig();
    if (_votingPowerAt(1e18, block.timestamp + MAX_LOCK_PERIOD + 1) * 20 > 1e18)
        revert ExpBaseTooSmall();
    setFeeReceiver(feeReceiver_);
    ONE_INCH = oneInch_;
}
```

而 `_votingPowerAt` 是 **30 个独立分支，每个一次 256 位乘法 + 一次 256 位除法**：

```solidity
if (t & 0x1 != 0) { v = (v * T0) / 1e18; }   //  ... 共 30 层
```

**被调用两次，结果被比较，不满足就 revert。**

**(b) ERC20 的 name/symbol 字符串赋值**（`nondet_string` / `_str_assign` 三个循环，
每次运行的截断警告都点名它们）`[实测]`

**(c) 外部调用重入模型**（外部调用被建模成对本合约 dispatcher 的 nondet 重入）

**(d) dispatcher 本身**

### 3.5 ⛔⛔ 关键：构造器的 revert 守卫是每一条单元路径的合取项

**这一条是整份审计里最重要的一句，而我五天没说出口。**

我们的 harness 把「部署 + N 笔交易」建模成**一个符号程序**。因此一条单元路径的
**可行性**隐含地断言了「**部署没有回滚**」。对 st1inch 展开就是：

```
_votingPowerAt(1e18, T)   * 20 >= 1e18
∧  _votingPowerAt(1e18, T+1) * 20 <= 1e18
```

即**两次 30 层条件化 256 位乘除的联立**。

⇒ **`setFeeReceiver` 那三条平凡约束，是和这个东西合取在一起交给求解器的。**

⇒ **合约里每一个单元都解不动，不管它自己多简单。**

⇒ **86 条决策拿不到，不是 86 个难题，是同一个难题被复制了 86 次。** `[推断，但见 3.6 的实测支持]`

### 3.6 我自己的数据早就证明了，而我给了另一个解释

D36 的单因子阶梯（每级只加一个成分）`[实测]`：

| 级 | F | 判定 |
|---|---|---|
| 函数本身（owner、modifier、error、event） | **4/4** | ✅ 0.002–0.004 s |
| + 构造器调用该单元 | **4/4** | ✅ |
| + 30 个 immutable `T_k = (T_{k-1}²)/1e18`（**直线**） | **4/4** | ✅ |
| + name/symbol 字符串 | **4/4** | ✅ |
| **+ 构造器调用 30 分支的 `_votingPowerAt`** | **0/4** | ⛔ |

**只有最后一级翻转。** 而且分支数扫描显示：1 个分支 → 4/4；**5 个分支 → 120 秒
超时**；10–30 个分支 → 0/4，公式大小从 123 涨到 355 条赋值、**编码始终 2 毫秒**。
位宽扫描：**同样 355 条赋值**，uint64 **36 毫秒解出**，uint128 超 120 秒，
uint256 耗尽 4 GiB。

**我当时写下的结论**：

> 「条件化的宽位宽算术。30 层在 64 位是平凡的，128 位超过 120 秒预算，
> 256 位 z3 几秒内内存耗尽。」

**这个结论对现象的描述是准确的，但它把因果放错了层次。** 正确的说法是：

> **构造器里有一个求解器算不动的 revert 守卫，而我们的建模让这个守卫成为
> 合约内每一条单元路径的合取项。**

**代价**：前一种说法指向「换后端 / 加预算 / 优化编码」——我为此花了 D31、D37、D38
三份文档、一次五后端矩阵、一次 900 秒的 cvc5 重跑，**全部失败**。
后一种说法指向「**别去解构造器，去执行它**」——**我一次都没试过。**

### 3.7 本质错误：把"该执行的"当成"该求解的"

**构造器一旦参数确定就没有任何不确定性——它是确定性代码，跑一遍就知道结果。
我们却把它编码成约束，请 SMT 求解器证明它不回滚。**

`_votingPowerAt(1e18, T) * 20 >= 1e18` 在 `expBase_` 和 `block.timestamp` 都是
具体值时是一个**闭公式**——要么真要么假，30 次整数乘除算出来，**微秒级**。
只有当 `expBase_` 是符号量时，它才是那个耗尽 8 GiB 的东西。

**而测试根本不需要 `expBase_` 是符号量。** 发出的测试里必须写一个具体部署：

```solidity
st = new St1inch(token, EXP_BASE, feeReceiver);   // 全部具体值
```

⇒ **我们为了发出一个具体部署，先去符号地求解了所有可能的部署。**

### 3.8 这是单元测试与整程序验证的分界，而我站错了边

| | 整程序验证 | 单元测试生成 |
|---|---|---|
| 部署 | 符号的：对所有合法构造参数成立 | **具体的：一个部署** |
| 入口状态 | 所有可达状态 | **一个确定的后置状态** |
| 交付物 | 性质的证明 | **一条能跑绿的用例** |

我们的**问题定义**（1.2）站在右边。我们的**求解任务**站在左边。
**这两边不一致，是这五天的根本病灶。**

它同时解释三件我一直分开处理的事：

1. **st1inch 0/86** —— 构造器守卫污染每条路径条件（3.5）。
2. **751 条未见证路径 100 % 是 `bounded-holds`** —— 我一直把它当"要不要 havoc 入口
   状态"的**开关**问题；它其实是"入口状态该由谁决定"的**建模**问题。
3. **`I` 结构性为 0** —— 在**具体部署 + 具体入口状态**下，"这条路径不可达"是一个
   **可判定**的问题；`I` 本来可以有意义。

---

## 第 4 部分 · 诚实回答：为什么 fuzz 更容易

用户说「我要是插个桩自己去 fuzz 都不会有这么吃力」。**这句话是对的。**

### 4.1 fuzz 强在哪

| | fuzz | 我们 |
|---|---|---|
| 部署 | **具体执行**，微秒 | 符号求解，8 GiB 耗尽 |
| 满足 `msg.sender == owner` | `vm.prank(owner)`，零成本 | 一条 SMT 约束，混在构造器守卫里 |
| 满足 `feeReceiver_ != 0` | 随机撒点，命中概率 ≈ 1 | 同上 |
| 覆盖这 3 行的两条分支 | **秒级** | **900 秒不返回** |

**在"让简单函数的简单分支被执行到"这件事上，fuzz 完胜。这不是我该辩解的地方。**

### 4.2 我们凭什么还值得存在——只有三条，且只有第三条是不可替代的

1. **穷尽性**：fuzz 不知道自己漏了什么。我们枚举完整路径，能说"这个单元有 5 条路径"
   并对每条给出状态与**理由**。**这条我们做到了**（31/31 缺失决策逐条归因，见第 6 部分）。
2. **不可达的判定**：fuzz 分不开"撒不到"和"不存在"。我们**本应**能分，
   但 `I` 结构性为 0，**所以目前也分不开**。**承诺了、没做到。**
3. **输入区域（PUT）**：fuzz 给一个值；我们承诺给一个**被证明的区间** + 断言。
   **这是唯一不可替代的东西，而它现在是 0。**

⇒ **我们放弃了唯一的护城河（第 3 条），去和 fuzz 拼它最强的地方。**

### 4.3 一条我从未考虑过的设计

**用 forge fuzz 当 stage-1。** 让 fuzz 去找具体输入（它擅长），我们只做 ②③
（把那个具体输入长成被证明的区间 + 断言）。那样 X→Y 根本不是问题，全部力气会花在
**只有我们能做的那一格**上。

**我五天里一次都没考虑过。** 我默认了"路径必须由我们的符号执行来见证"，
**而这个默认从来没有被论证过。**

---

## 第 5 部分 · 爆炸控制的完整审计：十道界

### 5.1 总表——关键区分是"丢路径"还是"只弱化"

| # | 机制 | 值 | 丢路径？ | 报告里可见？ | 出处 |
|---|---|---|---|---|---|
| 1 | `--solidity-max-tx` | **1** | 是（更深状态不可达） | ✅ `bound.max_tx` | `[实测]` |
| 2 | loop unwind bound | **4** | **是，静默** | ⚠ 仅警告 + `loops_truncated` | `[实测]` |
| 3 | call-depth bound | **4** | **否——合并** | ⚠ 警告点名站点 | `[实测]` |
| 4 | per-unit goal cap | **10000** | **是** | ❌ 我读过的报告里没见到 | `[推断]` |
| 5 | **降级 degradation** | 在 #4 **之前** | **否——弱化** | ⚠ 每单元警告 | `[实测]` |
| 6 | `--focus-function` | 1 单元 | 故意窄化分母 | ✅ 打印被排除数 | `[实测]` |
| 7 | 短路操作数上限 | `SC_DECISION_MAX = 12` | **是——整站点丢** | ⚠ 仅计数 | `[沿用]` |
| 8 | per-claim 求解预算 | **120 s** | 否——报 `claim-budget-exceeded` | ✅ 带执行机制字符串 | `[实测]` |
| 9 | **外层进程超时** | 300/900 s | **是——整个单元消失** | ❌ **零披露** | `[实测]` |
| 10 | `--memlimit` | **8 g** | 否——写部分报告 | ✅ `partial: true` + 原因 | `[实测]` |

### 5.2 设计得最好的一处：#5 刻意排在 #4 之前

运行日志原文 `[实测]`：

> "DEGRADED unit '…' — fully expanded it enumerates more paths than the per-unit
> budget (10000), so 1 call point(s) were **WITHDRAWN from its path identity**
> and are now treated as black boxes … The callees still **EXECUTE** (the call is
> still there), they just stop contributing decisions, so the path classes get
> coarser **while still partitioning the input space** — sound, with weaker
> assertions … **This is tried BEFORE the goal cap on purpose: the cap would
> instead DROP paths that exist in the model.**"

**宁可弱化断言，不可丢失路径**，而且这句话写在**产物里**而不是注释里。
实测 st1inch 降级 12 个单元、12 个调用点，**0 个**单元无法塞进预算。

### 5.3 #3 的代价被"合并"这个词低估了

日志原文 `[实测]`：

> "8 call site(s) are deeper than the call depth bound (4) and were NOT expanded
> (`_ethTransfer`, `_withdraw_onlyValidImmutables`, `ImmutablesLib.hash`,
> `SafeERC20.safeTransfer`); paths through them are **MERGED rather than
> enumerated**."

被调者仍然**执行**，只是不再贡献决策——听起来无害。但**被合并的被调者的分支
永远不可能出现在任何见证路径的 `decisions` 数组里**。实测后果：
`EscrowDst._withdraw`、`BaseEscrow._ethTransfer`、`onlyValidSecret` 三处缺失
决策全部由此而来。

**而且提高它已被测量为无用**（D28）`[实测]`：4→6 买到 **8 条路径、4 个见证、
零条决策**；bound 8 在 400 秒内不返回；**残余越界点反而从 8 涨到 34**——
往深展开暴露的前沿比它消耗的多。

### 5.4 ⛔ #9 是唯一零披露的

被外层超时杀掉的运行**不留 `cov-report.json`**——信号处理臂**写不了 JSON**
（malloc、iostream、日志互斥锁在 handler 里都不安全）。所以那个单元不是显示为 0，
**它根本不出现**。语料里三个单元处于这个状态（36 条枚举出的路径）。

`branch_gate.py` 确实抓到了（读 journal、计 `killed`、判词加 `(partial)`），
**但任何遍历 `reports/` 的消费者都看不到**。

### 5.5 没有被界住的那一半——这才是重点

| 没界住 | 后果 `[实测]` |
|---|---|
| **外部调用重入的实例化** | `EscrowDst.publicWithdraw`：**5 条路径 780 个 VCC，extcall 780**——每个 VCC 都是同一断言的一次重入实例化，**156×/路径**。语料里最大的乘数，**无任何上限** |
| **claim key 的多重性** | 那 780 个塌缩到 **5 个 key**，分别求解，**结果互相打架**。`withdraw:path:2` 一个 key 解 **85 次**：84 PASS + 1 FAIL |
| symex 赋值数 | 无界。`withdraw` 光 symex 就 166 秒 / 44639 条赋值 |
| 字符串库循环展开 | 每个调用点展开；farming `exit` 1810 次。只被 #2 间接界住 |
| **入口状态** | **从不 havoc**，是后构造状态。⇒ `I` 结构性为 0（见 2.2、3.8） |

---

## 第 6 部分 · 选项定义与原理（供审核者查证）

### 6.1 `--solidity-max-tx N` —— 事务序列深度

**定义**：harness 从后构造状态起，对合约驱动多少笔交易。

**⛔ 反直觉处**：`--solidity-max-tx 0` **不是**"无界"，**是最浅**。`[沿用]`
机制：界 0 发出 `while (nondet) { dispatch(); }`，Solidity **覆盖**插桩随后把该
循环的**回边改写成 SKIP**，剩下**恰好一笔有守卫的交易**——严格少于 2。
报告的 `note` 字段原文就这么写 `[实测]`。**路径覆盖自己的默认值是 2**（它不在
`unbounded_modes` 里）`[沿用]`。

**为什么锁在 1**：D25 在 `poc/Tiny.sol`（第 41 行需要前一次调用才可达）上实测
**锁定的 branch-coverage 基线**：基线原样 5/8、第 41 行未达；plain BMC + tx=2
则 8/8。⇒ **基线在一笔交易上，而且在两笔上会涨**，且它已锁定不能重跑。
产物侧跑 2 就是比被比较对象**更深**。`branch_gate.py` 用
`GATE_SOLIDITY_MAX_TX = 1` 在代码里强制并**拒绝**其他格采集的数据。`[实测]`

**代价**：任何守卫需要"前一笔交易建立的状态"的路径都不可达，报 `U/bounded-holds`。
报告自带 `known_limitation_entry_state` 说明这一点。实测个例：aqua 的
`require(tokensCount1 > 0 …)`，该单元九条 U 路径全部比被见证的深一层决策。

**未答**：逐步增长（阶梯）驱动是否存在？"真正关掉"是否存在？——**我没查**。
`Implementation_plan.md` §3.4 有 tx 阶梯 1→2→3，progress.md 自认**从未跑通**。

### 6.2 `--unwind N` —— 循环展开界

**定义**：符号执行对任何循环最多展开多少次迭代。

**机制**：**不给** `--unwind` 时，路径覆盖**自己设成 4**，以匹配它自己的路径枚举
所用的界。运行时公告 `[实测]`：

> "no `--unwind` given; bounding symbolic execution at 4 to match the path
> enumeration's own loop bound. Without it an external call … or any loop runs
> unbounded until the memory limit."

**代价，且是静默的**：在 `--no-unwinding-assertions` 生效时，需要更多迭代的路径被
**假设掉**而不是被报告 `[实测]`：

> "Coverage may be UNDER-REPORTED: 3 loop(s) hit the unwind bound while
> `--no-unwinding-assertions` was active, so the paths that needed more
> iterations were **silently assumed away**."

每次 Solidity 运行被点名的三个循环都在 **ESBMC 自己的字符串库**里
（`solidity_string.c`：`nondet_string` 第 245 行、`_str_assign` 第 206/209 行）——
即截断通常发生在 **harness**，不是合约。

**⛔ 审计空洞（用户指出，我认）**：**4 这个值不是论证出来的**，是路径枚举自己的
loop bound，为了对齐被采纳。**我们没有任何机制去知道该展开多少次。**
而且同一条警告的**后半句我在之前的审计里截断了**，完整是：

> "Raise `--unwind`, use `--unwindset`/`--unwindsetname` for the specific loop,
> **or switch to `--k-induction` / `--incremental-bmc`**"

**ESBMC 自己在建议 `--incremental-bmc`，我一次都没试过。**
`notes/coverage/unwind-vs-strategy.md`（33 KB）**我至今没读**，它很可能已经答过。

### 6.3 call-depth bound（= 4）—— 内部调用展开

**定义**：内部（非单元）调用被**内联进调用者路径身份**的深度。
单元的路径是一次调用的决策序列；在此界内展开的被调者，其决策进入该序列。

**超界时的机制**：**不丢路径，改为合并**（见 5.3 引文）。

**代价与不可修**：见 5.3。

### 6.4 `--path-cov-max-goals N`（10000）—— 每单元路径预算

**定义**：单个单元最多插桩多少条完整路径。`[沿用]`
**机制**：**丢弃模型里存在的路径**——所以它是最后手段，降级排在它前面。

**⛔ 审计空洞**：我**从没见它开火**，也没在任何读过的 `cov-report.json` 里找到它被
披露。若它静默生效，那是与 #9 同级的披露漏洞。`[推断 — 待审核者查证]`

### 6.5 降级 degradation

见 5.2 引文。**这是全表里设计最好的机制。**

### 6.6 `--focus-function <name>` 与 `--contract <C>`

**定义**：`--contract C` 选择建哪个合约的 dispatcher harness；
`--focus-function f` 窄化**哪些单元被插桩**。

**⛔ 它不窄化什么**：**内部调用展开对每个单元照跑**，所以聚焦单元的路径身份不变。
日志原文 `[实测]`：

> "`--focus-function 'setFeeReceiver'` narrowed INSTRUMENTATION to 1 unit(s);
> 38 other in-scope unit(s) were not enumerated at all. … **Internal-call
> EXPANSION still ran for every unit**, so this unit's path identity is unchanged"

**⚠ 后果**：整个合约仍然被转换。`--contract St1inch --focus-function setFeeReceiver`
仍然花了 **15.8 秒**建 GOTO、展开 **136** 个内部调用、降级 **12** 个单元——
为了一个 4 行函数。⇒ **`--focus-function` 界住的是分母，不是开销。**

**多输入形式存在**（逗号分隔集合，我们自己实现的，`scripts/multifocus_check.py`），
**而我之前的审计只写了单名形式**——用户指出的空洞之一。

**`--function` 在本项目被禁**（不是同一个选项）：它从**任意**合约状态孤立地验证一个
函数，反例可能建立在没有 `constructor → tx 序列` 能到达的状态上，发出的测试在
未改动合约上是**红的**。库单元因此被**拒绝**而不是被测量。`[实测]`

### 6.7 `--path-cov-claim-timeout N`（默认 120 s）

**定义**：**单个 claim** 的可满足性查询的墙钟预算。路径覆盖每个作业判定一个
**独立** claim，所以 per-check-sat 限制**就是** per-claim 限制。

**机制——由求解器执行，不是杀进程** `[实测]`：

| 后端 | 原生选项 |
|---|---|
| bitwuzla | `BITWUZLA_OPT_TIME_LIMIT_PER`（ms，每次 check） |
| cvc5 | `tlimit-per`（ms，每次 check） |
| z3 | solver 参数 `timeout`（ms，每次 check） |
| 其他 | **`NOT ENFORCED: backend '<name>' has no per-query time limit`** |

最后一行是为什么发布的是**机制字符串**而不是布尔：一份带 `claim_timeout_s: 120`
却什么都没界住的报告，正是"守卫从不开火却一切看起来正常"的形状。

**cvc5 用 `tlimit-per` 而非 `tlimit`**：后者是求解器生命周期累计的，
在 `--smt-during-symex` 下一个求解器服务所有 claim，累计限制会把预算之后的
**每一个** claim 都放弃掉。

**如何识别被放弃的 claim**：所有后端把 "unknown" 折进 `P_ERROR`
（`smt_convt::resultt` 没有 `P_UNKNOWN`），所以只看结果分不开"被放弃"和
"真正的求解器失败"。**用墙钟分**：SAT/UNSAT → 保留判决（再晚也算）；
非答案且耗时 ≥ 预算（100 ms 余量）→ `claim-budget-exceeded`。

**为什么 `claim-budget-exceeded` 自成一个 U token**：
`solver-unknown` = 求解器**答了**"我不知道"（是信息）；
`bounded-holds` = 它答了"这个探索下没有见证"；
`not-solved-this-run` = 从没问，简化器把 claim 折掉了；
**`claim-budget-exceeded` = 我们问了、它还在算、我们把它停了**——什么都不知道，
而且**唯独它的修法是加预算**。

### 6.8 `--memlimit` 与外层超时

`--memlimit` 上限 `RLIMIT_DATA`。耗尽时抛 `std::bad_alloc`，**救援**写出
标记为 `partial: true` 的 `cov-report.json` 而不是死得什么都不剩。求解前预留
128 MiB 并在救援的第一个动作释放，让报告写出器有内存可用——预留花的是地址空间
不是 RSS。`[实测 — cvc5 那次运行正是如此]`

**外层超时**由采集脚本施加。⛔ SIGTERM 时信号臂**写不了 JSON**，只打印信号安全的
文本块和 CE journal——**完全没有 `cov-report.json`**。见 5.4。

### 6.9 `--cov-report-json` —— 不是可选项，而且它改变切片

**定义**：产出 `cov-report.json`；**同时**是打开决策序列记录的开关。

**机制**：路径 claim 的守卫只提到 ghost 累加器，所以 per-claim 切片器会把每一条
状态写和环境读都切掉，反例载荷回来是**空的**。加了这个标志，符号被**豁免切片**
`[实测]`：

> "exempting 511 symbol(s) from slicing so each path's counterexample values
> survive into the report (20 contract object(s), 476 contract-scope store(s),
> 15 environment); slicing stays enabled for everything else."

**值得标注的副作用**：在 D36 的 fixture 上，`--no-slice` **保留得更多**
（472 vs 355 条赋值）却**快 3 倍**——所以求解开销**不与公式大小成正比**。

### 6.10 后端自动选择——有一个路由器，且在一个形状上被证伪

ESBMC 自动选后端并打印理由。D36 的 60 行 fixture 上实测 `[实测]`：

> `auto-selecting 'bitwuzla' as SMT backend (Z3 is much slower on 256-bit
> bit-vector arithmetic)`

| 位宽 | z3 | cvc5 | bitwuzla |
|---|---|---|---|
| `uint64` | **0.051 s** | 0.171 s | 10.271 s |
| `uint256` | `out of memory` | **3.667 s** | 120 s 无报告 |

⇒ 在这个形状上，路由器在**容易的那半**选了**最慢**的（z3 快 200 倍），
在**难的那半**选了**不返回**的，而**理由印在屏幕上、被这个形状证伪**。

**⚠ 不是普遍结论**：在**真实** st1inch 上 cvc5 也失败（`std::bad_alloc`，
0/10 claim 判定，死在第一次求解内）。**后端在隔离形状上有意义，救不了真实合约。**
这条也是 3.6 那个"修现象不修因果"的直接证据。

### 6.11 ⛔ 分层验证——我之前的审计漏掉的机制

**covered-set**（`--coverage-covered-set`，磁盘格式 version 3，
报告字段 `witnessed_in_earlier_round`）：**已见证的路径下一轮不再插桩**。
`[实测 — dying-run-keeps-its-work.md]`

这是**唯一跨运行**的爆炸控制，而我在爆炸控制审计里**完全没列它**——用户指出的
空洞之一。注意它有一个顺序约束：**必须先持久化载荷再启用它**，否则一次 OOM 丢掉的
见证会变成永久无载荷的 `F`（下一轮看到路径已见证、不再插桩，那个本可以产出输入的
轮次正是跳过它的轮次）。

---

## 第 7 部分 · 度量诚实性：本轮做的对账（这部分是做到了的）

**这一部分是我五天里做得最扎实的，也正是第 8 部分要批评的"把审计当进展"。**

### 7.1 branch-coverage 闸的当前数字 `[实测]`

| bench | 分母 | 基线 P1 | 基线 P2（标杆） | native | 我们 | 判定 |
|---|---|---|---|---|---|---|
| aqua | 8 | 7 | 7 | 6 | **7** | **PASS** |
| EscrowDst | 18 | 14 | 18 | 10 | 6 | FAIL (partial) |
| EscrowSrc | 16 | 8 | 16 | 8 | 6 | FAIL |
| farming | 26 | 26 | 26 | 26 | 18 | FAIL (partial) |
| limit_order | 3 | 3 | 3 | 2 | — | **REFUSED**（主目标是库） |
| st1inch | 86 | 72 | 72 | 83 | **0** | FAIL (partial) |

### 7.2 31 条缺失决策，31 条有名字，0 条未解释 `[实测]`

| bench | 缺 | 分解 |
|---|---|---|
| aqua | 1 | 1 tx=1 下无见证（该单元跑了，9 条 U 全 `bounded-holds`） |
| EscrowDst | 12 | 8 库/`--function` 禁令 · 2 深度界 · 1 经 modifier 施用者的深度界 · 1 经 modifier 的被杀单元 |
| EscrowSrc | 10 | 8 库禁令 · 1 深度界 · 1 经 modifier 施用者 |
| farming | 8 | **5 被杀单元 · 3 构造器作用域** |
| st1inch | 72 | 第 3 部分 |

**五类原因里四类是结构性的**（问题定义、作用域拒绝、已测不可修的深度界、被杀的
运行）；**只有 farming 那 5 条是工程上可回收的 reach**。

**其中 farming 的 3 条构造器作用域决策，是用户裁定所预言的那个量的第一次实测**：
基线拿到 26/26 含这三条，我们拿不到也不该拿到（它们只在部署时执行一次，
没有 calldata、没有 ABI 值门，发不出对应的测试）。⇒ **两边分母的差是 3。**

### 7.3 语料的账目缺陷（本轮发现）`[实测]`

- **48/95 个 `cov-report.json` 是陈旧的**——全部属于 journal 记为
  `skipped: library-has-no-dispatcher` 的单元。**已修**（采集器的跳过分支现在清
  workdir，双向验证过）。**gate 未受影响**：`reports/` 被对账两次。
- **3 个单元被杀且零产物**（36 条路径）。
- **语料横跨 4 个构建，且按 benchmark 干净切开**——"benchmark A 与 B 不同"和
  "构建 A 与 B 不同"是同一列。23 次运行还带 `srcDirty=true`。
- **`limit_order_protocol` 按构造 0/0**——`index.json` 写着
  `primary = {MakerTraitsLib, library}`，主目标是库，14 个单元全落在 `--function`
  禁令下。**它作为一行与另外五个并排，会高估语料。**

### 7.4 阶段 2 最大的损失，其"原因"指向一个没被保存的东西 `[实测]`

未认证原因分布：

| 原因（工具原话，从不合并） | 数 |
|---|---|
| **认证查询无判决——ESBMC exit -6（SIGABRT），"a TOOL outcome, not a property of the path"** | **59** |
| **没有一轮 outer-box 跑完——"a BUDGET outcome, not a property of the path"** | **35** |
| refuted 且无可用单坐标切 | 14 |
| shrink 轮次预算耗尽 | 9 |
| 区域为空（lo > hi） | 8 |

**59 + 35 = 94 次是工具/预算结果，产生它们的代码自己这么标注的。** 另有
**37 个单元根本没有可认证的见证路径**。

而那 59 次的 reason 末句是"**The last ERROR line in its output names the cause**"
——一条**去读某个东西的指令**，而那个输出**从来没有被保存**：`certify/` 只有三个
jsonl、没有日志；`certify_reason_fields.py` 遍历全部 66 条记录的每一个字符串，
**命中的全部是那句 reason 本身**。⇒ **不可诊断，除非改脚本后重跑。**

---

## 第 8 部分 · 我这五天实际在干什么，以及为什么浪费

### 8.1 产出分类（诚实统计）

| 类别 | 产出 | 是否推进交付物 |
|---|---|---|
| **度量与审计** | 漏斗、31/31 归因、语料对账、爆炸控制审计、选项定义、INDEX | ❌ 不推进 |
| **诊断** | D25–D40 共 16 份 | 部分推进（D36 定位了卡点） |
| **修复** | 采集器清陈旧目录、focus 窄化插桩、多输入 focus、partial 报告、CE journal、per-claim 预算、编码器 tuple 修复… | ⚠️ 修的是**工具的诚实性**，不是**产出能力** |
| **自我更正** | D30 撤回、D38 §4b 方向反了、D40 farming 归因错、构建年代推断错、`onlyValidSecret` 的密码学故事错 | 必要，但说明前置判断质量差 |
| **真正让测试变多** | **几乎没有** | — |

**最后一行是这份审计的核心。** 我这五天跑的绝大多数命令，是在**读已经落盘的产物**
（census、gate、gap_lines、funnel、run_stats）——它们几秒返回、不跑 esbmc、
成本极低，**这正是我一直做它们的原因**。它们回答"多少"，几乎不回答"为什么"，
**完全不回答"怎么让它变多"**。

### 8.2 方法论上的根本错误，分五类

**(1) 把审计当进展（最重）。** 度量的诚实性是必要条件，不是充分条件，更不是产出。
**用户问了两次"转化率"我才切换口径**——在那之前我一直在报 F/U 和 canonical
decision，那是代理指标。

**(2) 修现象层，不修因果层。** 见 3.6。代价是三天的换后端/加预算，全部失败。

**(3) 从形状推结论，不打开算它的代码（本轮三次）。**
用 `U_reasons` 键集合推"构建年代"（实为零路径运行的写出分支**形状**）；
说被杀单元让"分子分母一起消失、百分比变高"（实为 gate 分母来自 **AST**，
只吃分子、**方向相反**）；说 farming 被杀是"字符串循环"（因为 3104 是那行**最大**
的数，而**有区别**的是 ratio 15——其他单元全是 1.00）。
**三次都是：看数字形状编一个机制，而不打开产生它的那段代码。**

**(4) 全量扫描代替最小复现。** 我唯一削对的是 D36（60 行、几秒一格），
**它一次就定位了卡点**，而之前三小时的整合约矩阵在关键那格有洞。
**证据摆在那里，我却没把"先削"变成默认动作。**

**(5) 上下文重置后的失忆。** `notes/SESSION_STATE.md` 是常驻状态文档，
**我整整一个会话没打开过**，同时在重新调查可能已有答案的东西。~60 份 md、1 MB，
**没有索引**。（本轮已建 `notes/INDEX.md` 并写入长期记忆。）

### 8.3 本该怎么排这五天

| 天 | 本该做 | 实际做了 |
|---|---|---|
| 1 | 拿**一个最简单的合约**（不是 1inch），端到端跑出**一个**带断言的 PUT，**哪怕手工补全缺的环节**。先让 **B ≥ 1** | 采集语料、修采集器 |
| 2 | 把 B=1 那条链上每一环自动化；此时才知道哪一环**真的**缺 | 修报告诚实性 |
| 3 | 用 st1inch 检验泛化，**发现构造器守卫问题** | 换后端、加预算 |
| 4 | 实现"具体部署 + 符号单元体"，重测 | 语料对账、gate 归因 |
| 5 | 量转化率、写论文 | 量转化率（被催）、写审计 |

**核心差别：先把最窄的一条端到端链打通（B ≥ 1），再横向铺开。**
我做的是先横向铺满（六个 benchmark、110 次运行、31 条决策归因），
**而纵向的链从第一天到第五天始终是断的**。

这正是我自己长期记忆里那条 **`funnel-before-generalisation`（先转化率再泛化）**——
**我写下过这条规则，然后违反了五天。**

---

## 第 9 部分 · 补救方案（按优先级，具体到判别器）

### P0 —— 让 B 从 0 变成 1（不到一天）

**目标**：一个合约、一条路径、一个带 `vm.assume(lo <= x && x <= hi)` 和一条
post-state 断言的 `.t.sol`，`forge test` 跑绿。**允许手工补任何一环。**

**理由**：链上有四个环节，现在**没有任何一条链是通的**，所以"哪一环真的缺"全是
推测。一条通的链会把推测变成事实。

**现成材料**：`regression/R0.cov.t.sol` 已存在；aqua 有 **12 个真正有宽度的坐标**
——从里面挑一个。

### P1 —— 具体部署（3.7 的修法，也是 st1inch 0/86 的修法）

**做法**：给 harness 一个模式，构造器参数取**具体值**，构造器**具体执行**
（或其守卫被简化器以闭公式判真），单元体仍然符号执行。

**判别器（先写、后改）**：在 D36 的 `r4_vpcalls` 那一格上
- 现在：`F 0, U 4`
- 期望：`F 4, U 0`，求解时间回到毫秒级

**这一格几秒钟出结果，不需要跑真实合约。翻了再上 st1inch。**

**⚠ 必须先确认的前提**：具体部署会不会让路径域窄到不诚实？——**不会**，
因为问题定义里路径域就是"测试能给出的输入"，而测试必然携带一个具体部署
（用户在构造器作用域裁定里已阐明）。

### P2 —— 认真评估「用 forge fuzz 做 stage-1」

见 4.3。**这是一个从未被论证过的默认。** 评估成本极低：让 forge fuzz 在
`setFeeReceiver` 上跑 10 秒，看它命中几条路径。若秒杀，就把符号执行的力气全部
挪到 ②③。

### P3 —— `--incremental-bmc` / `--k-induction`（用户指出的空洞）

ESBMC 自己的截断警告在建议它们，**我一次没试过**，而 `--unwind 4` 这个 4
**不是论证出来的**。先读 `coverage/unwind-vs-strategy.md`（33 KB，未读），
再在 D36 的 fixture 上跑一格——**PoC，不是全量**。

### P4 —— `certify_all.py` 保留输出

见 7.4。不修这个，重跑一遍还是查不到 59 次 abort 的原因。

### P5 —— 只有以上都通了，才回到 branch-coverage 闸

31/31 缺口已归因，**只有 farming 的 5 条 + 三个被杀单元**是工程上可回收的。
**剩余空间很小，不值得再投入。**

---

## 第 10 部分 · 已知空洞（本审计自身的，不许当完整交出去）

1. **`--incremental-bmc` / `--k-induction` 从没试过。**
2. **分层验证（covered-set）** 之前漏列，本文 6.11 补上，但**未做实测审计**。
3. **多输入 `--focus-function`** 之前漏列，本文 6.6 补上。
4. **`--solidity-max-tx` 的阶梯增长与"怎么关"** 未答（`0` 不是关，是最浅）。
5. **`--path-cov-max-goals` 从没见它开过火**，也没见它被披露——可能是与外层超时
   同级的披露漏洞。`[推断]`
6. **`notes/path-coverage-invocation-contract.md`（1053 行）本轮未重读**——
   所有 `[沿用]` 需对照它。
7. **`coverage/unwind-vs-strategy.md`（33 KB）未读**——它很可能已答 P3。
8. **`interval-input-scope-and-plan.md`（37 KB）未读**——subgoal 3 的计划文档。
9. **`SESSION_STATE.md`（19 KB）未读。**

---

## 第 11 部分 · 一句话总结

**我们花了五天，把一个"该执行的东西"（部署）当成"该求解的东西"，
于是合约里每一个单元都继承了构造器的难度；与此同时我把绝大部分精力投在
"度量是否诚实"上，而不是"让一条链先通"，所以项目名字里承诺的 PUT 至今是 0。**

**最便宜的验证在 D36 的 `r4_vpcalls` 那一格，几秒钟。那是下一步。**
