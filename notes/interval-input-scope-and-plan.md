读完了。以下是结论，全部带 `file:line`。（回复用中文，代码标识符保留英文。）

---

# 1. 今天真正存在的"区间输入"是什么

## 1.1 一个 coordinate 是什么

`resolve_coord`（`src/goto-programs/goto_coverage.cpp:3324-3397`，函数内 lambda）把一个**名字**解析成 `expr2tc`，只有三种前缀，靠显式前缀区分而不是猜：

| 名字形状 | 解析成 | 行 |
|---|---|---|
| `msg.* / tx.* / block.*` | `c:@msg_value` 等全局符号 | 3326-3339 |
| `state.<field>[.<sub>…]` | 合约实例对象 `sol:@_ESBMC_Object_*` 的 struct 成员，经 `walk_fields` | 3340-3367 |
| 其它 `name[.field…]` | 本 unit 的形参，经 `walk_fields` | 3383-3395 |

`walk_fields`（`:550-579`）按 `.` 逐段下降，段名匹配 `comp.get_name()` 或 `#base_name`（`:565-566`）；任一段匹配不上就整体失败（`:572-573`）。

解析成功后还要过 `coord_expressible`（`:2285-2345`）白名单。

## 1.2 一个 box / region 是什么

**每坐标一个闭区间，可选减去一个有限洞集**：`R = ∏_c ([lo_c, hi_c] \ H_c)`。定义在 `goto_coverage.h:760-781`（Definition 5 / 打洞区间），spec 解析在 `goto_coverage.cpp:2697-2709`（certify 的 `box`）和 `:2597-2626`（outer-box 的 `coords`）。`lo/hi/holes` 一律是**十进制字符串**，理由写在 `:2526-2529`（JSON number 会被截成 double）。

## 1.3 box 怎么被提出（IMPLEMENTED）

两级，工具只测量、driver 定策略（`scripts/solidity_path_generalise.py:1-48` 开宗明义）。

**(a) OUTER BOX 批量测量** — `goto_coverage.cpp:4994-5374`（`solidity_path_coverage()` 末尾的第一个分支）：
- 在函数入口把每个坐标快照进 ghost 符号 `__ESBMC_outer$N`（`:5070-5096`），理由 `:5008-5012`（形参中途可能被重赋值）。
- 发布坐标自己的类型区间并作为免费外界种子（`:5098-5120`），同时把 `TYPE RANGE` 打到 stdout 给 driver 用。
- 每条路径在**它自己的出口**发 `assert(tr!=enc || cnt!=depth || <pin 不成立> || snap<=v)` 及 `>=` 孪生探针（`:5225-5235`, `:5342-5362`）。只放自己出口的理由在 `:5145-5148`。
- 超出坐标类型的探针值被**丢弃并计数**（`:5278-5341`）。
- 探针值去重（`:5313-5316`），因为重名 claim 会在 `all_claims`（`std::set`）里静默丢一条。

**(b) 读判决 + 减兄弟** — `report_outer_boxes()`（`:605-1102`）：
- `'P'` 收紧界，`'F'` 记录最紧的被反驳值形成 bracket，缺席 = undecided（`:652-701`）。三态是显式的。
- 被拒绝的坐标在任何 box 之前先打印（`:703-720`）。
- 减法：对每个相交兄弟做**一次**切割，优先"打洞"（兄弟在该坐标上是单点且本路径 CE 不等于它，`:936-950`），否则取一侧（`:952-977`）；合法性规则是"必须保留本路径自己的 CE"（`:929-931`）。无法分离的兄弟计入 `degenerate` 并在 region 行尾输出 WARNING（`:979-987`, `:1066-1072`）。
- 空 region 有两条路（`lo>hi` 和洞把区间打空），同一个 `path_cov_kept_in`（`:588-603`）判定，并在同一行标注（`:1028-1064`）。

**driver 侧策略**：几何 bracket（`scripts:501-508`，一档一个 2 的幂）→ 线性 refine（`:1855-1883`）→ level-0 等式候选（`:571-604`, `:1720-1822`，候选值取自兄弟自己的 CE，零额外查询）→ 按"发射的 claim 数"做 ladder 稀释（`:528-568`，实测该轮是 emission-bound 不是 solve-bound）。

## 1.4 box 怎么被认证（IMPLEMENTED）

CERTIFY 分支 `goto_coverage.cpp:5376-5669`：
1. 发射前的**结构性拒绝**（全部 `exit(1)`，不是 abort）：空 box `lo>hi`（`:5409-5491`）、同名被 bound 两次（`:5444-5448`）、打洞打空（`:5449-5476`）、坐标不可解析/不可表达（`:5493-5539`）、十进制不落在坐标自己类型内（`:5542-5586`）。
2. 在 `instructions.begin()` 插入 `ASSUME(lo<=c<=hi && c!=h…)`（`:5591-5618`）；`holes_emitted` 在**合取处**自增而不是读 spec 长度（`:5599-5606`，这是实测的故障注入结论）。
3. 在**每一个出口**断言 `tr==enc && cnt==depth`（`:5624-5650`）。放每个出口的理由在 `goto_coverage.h:722-729`：只放 pi 自己出口会真空成立。
4. 匹配不到 unit 时 `exit(1)`（`:5798-5825`，第五条假证书路径）。

判决读法：driver 的 `verdict()` **整行**匹配（`scripts:876-911`），因为 ESBMC 每次 bounded 运行的 WARNING 里就含 `VERIFICATION SUCCESSFUL` 子串——这曾让认证门恒绿。

## 1.5 反驳做什么

`audit_certify_witness`（`goto_coverage.cpp:~250-532`）：
- 打印 **SHRINK SUGGESTION**：切口落在 witness 上而不是二分（`:492-504`）。
- 打印 **PUNCH SUGGESTION**：把 witness 这一个值挖掉（`:474-491`）。
- 反驳但无 witness → `abort()`（`:516-531`）。

driver 只解析 SHRINK（`SHRINK_RE`，`scripts:642`；`shrink_target`，`:1357-1373`），**从不解析 PUNCH**（`scripts:636-657` 的正则表里没有它）。这是一条已实现但从未接上的输出。

## 1.6 循环在哪终止

`scripts:1935-1972`：`--shrink-rounds` 默认 4（`:1446`）。SUCCESSFUL→收（`:1943`）；UNKNOWN→停且**不收缩**（`:1947-1954`，"没查出来"不等于"被反驳"）；无切口→停（`:1955-1963`）；否则耗尽预算（`:1966-1972`）。最后做认证区域两两不交的硬检查（`:1974-1989`）。

## 1.7 只在笔记里、没有实现的部分

- `notes/path-cov-assert-plan.md` 全篇（stage-3 后状态断言 `--path-cov-assert`）：**零代码**，文档自己在第 7 行声明 "No code was written"。
- entry-liveness **witness**（每个 unit body 头上一条必须被反驳的 `assert(false)`）：`goto_coverage.h:308-313` 说 "planned"，树里没有。
- post-constructor state havoc：`notes/probe-entry-state-havoc.md:19-45` 逐个候选排查后结论是"树里任何模式下都不存在"，并且被回归测试 `state_var_default_init_no_setter_pass` 锁死（同文件 `:322-343`）。
- `--all-witnesses`（每个可行路径多拿几个域内成员）：`notes/path-coverage-invocation-contract.md:226-232` 记为"存在且从未使用"。

---

# 2. 一个 coordinate 能是什么、不能是什么

`coord_expressible`（`goto_coverage.cpp:2285-2345`）**只接受 `is_unsignedbv_type`**（`:2288`）。即：`uintN`、`address`（160 位无符号）、`bytesN`。白名单方向的理由在 `:2273-2278`——三个项目在三种**不同**类型上以同一形状崩掉，黑名单只会抓住写下来的那一个。

| 被拒类型 | 行 | 声明的理由 | 是根本限制还是没实现 | 补上需要什么 |
|---|---|---|---|---|
| signed bv | 2316-2324 | 界用 `constant_int2tc` 建在坐标自己类型上，`2^256-1` 在 int256 下即 `-1`，`a>=0 && a<=-1` 不可满足 → 真空认证（附一对 must-flip 实测） | **没实现**，注释自己说了（`:2311-2315`） | (a) 把 spec 十进制校验改成对 `[-2^(w-1), 2^(w-1)-1]`（现在的校验 `:5557-5586` 硬编码 `[0, 2^w-1]`）；(b) `:5098-5120` 的 TYPE RANGE 发布只在 `is_unsignedbv_type` 下触发，signed 坐标**一个 TYPE RANGE 行都不发**，driver 于是回落到 2^256——这正是 `notes/geometric-ladder-wraps-on-narrow-types.md` 那个 wrap bug 换个位置重演；(c) 几何梯子要出对称版本 |
| array | 2325-2328 | 前端把 string/bytes/mapping/动态数组都降成 array | **混合**。string/bytes/动态数组：作为**标量区间**是根本不可表达的，但"长度"和"第 i 元素"是可实现的新坐标种类。mapping：连解析都到不了（见下） | 长度坐标 ~30 行；元素坐标需要 key 表达式 |
| struct / union | 2329-2332 | 需要每字段一个坐标 | **已绕开**：`walk_fields` 让 `param.field`、`state.cfg.limit` 成为合法坐标（`:3370-3382`）。聚合体本身被拒是**正确的**，不是限制 | 剩下的缺口在 driver：`struct_fields`（`scripts:162-216`）只拆**深度 1**，嵌套聚合被跳过（`:207-210`）；工具侧的镜像实现同样只做深度 1（`goto_coverage.cpp:378-411`） |
| pointer | 2333-2336 | 合约/接口句柄，值是模型分配器里的地址，不是测试能设的输入 | **在当前模型下是根本限制**。注意 Solidity 的 `address` 是 unsignedbv、是被接受的；只有合约句柄是 pointer | 要支持需要"部署一个合约并把它的地址作为坐标"，那是发射器的事不是 box 的事 |
| bool | 2337-2340 | 两点域没有区间可测，诚实形状是等式坐标，本阶段没有 | **没实现，而且是最便宜的一条** | `coord_expressible` 接受 bool 且 `lo,hi∈{0,1}`；`:5591-5593` 和 `:5348-5350` 的 `<=`/`>=` 对 bool 是畸形的，要特判成 `equality2tc`。`path-cov-assert-plan.md:768-771` 已经独立指出同一件事："`coord_expressible` 对 R1 是错的门" |
| 其它 | 2341-2343 | 不是 bit-vector | 兜底 | — |

**还有一条不在 `coord_expressible` 里的更硬的拒绝**：mapping / 动态数组根本**不是**合约对象的 component，前端把它们降成 contract-scope 全局 `sol:@C@<C>@<name>`（`:3308-3318`）。所以 `state.balances` 在 `resolve_coord` 就返回 false，报的是"名字解析不到"（`:5034-5041`）。而 CE harvest **会**把它们报进 `entry_storage`（`bmc.cpp:3285-3319`），于是 driver 相信报告、工具拒绝——这正是 `scripts:1036-1046` 和回归 fixture `test_solidity_path_generalise.py:242-253` 里那条 `state._DOCKED` 的实测。补上它需要一个"槽"坐标（key 表达式），是设计问题不是查表问题（`:3315-3318` 明说）。

---

# 3. 区域的覆盖面：N 个自由坐标里到底 bound 了几个 —— 直接回答

## 3.1 N 本身不是 unit 的元数

坐标集合完全来自**反例载荷**（`coord_values`，`scripts:219-283`）：`env` + `inputs`（标量 + 深度 1 的 struct 字段）+ `entry_storage`。载荷里没有的东西**从来就不是坐标**：

- 外部调用返回值：`extcall_returns` 字段**永远是空的**，`bmc.cpp:1650-1695` 用 60 行说明为什么（三种不同语法形状、三种不同原因），并明确 "An empty list means UNKNOWN"。
- 嵌套聚合字段：`scripts:207-210` 跳过。
- mapping/动态数组的值：`bmc.cpp:3300-3319` 只渲染元素写；整存写只留名字进 `state_written_unrendered`。
- 从未被写过的状态变量：entry 快照就是"压栈那一刻累积的 `last_state`"（`bmc.cpp:3219-3226`, `3436-3452`），没有写过就不出现。

## 3.2 从 N 到"实际被 bound"的漏斗（每一级都有代码位置）

1. **env 整类被移出**：`coords = 全部 - pins - env_names`（`scripts:1634-1635`）。理由是梯子成本对坐标数是乘性的、而 env 有十五个（`:151-159`）。开 `--pin-env` 才变成 pin（`:1611-1627`），开 `--env-coord` 才逐个提升为自由坐标（`:1465-1478`）。**默认两个都关**，于是 `scripts:1628-1632` 打印："非 payable 函数的 ABI 门是对 `msg.value` 的判定，它不受约束时该路径认证不了。"
2. **immutable / constant → 改成 pin**：`unsettable_coords`（`:471-498`）+ `:1665-1678`。数据在 `notes/coordinate-settability-census.md`：**六个 benchmark 输入共 143 个状态变量声明，只有 24 个 mutable，即 17%；其中三个输入是 0%**（EscrowDst、EscrowSrc、MakerTraitsLib）。`EscrowSrc.cancel` 的两个自由坐标 `state.FACTORY` / `state.RESCUE_DELAY` **都是 immutable**，所以它 0-of-4 的认证结果"从来就不是搜索能力问题"。
3. **降级产物（padding）→ 直接丢弃**：`lowering_artifacts`（`:451-468`）+ `:1655-1663`，实测 `immutables.anon_pad$2`。
4. **工具拒绝**：解析不到（`goto_coverage.cpp:5034-5041`，主要是 mapping/动态数组）、`coord_expressible` 不通过（`:5042-5043`）、所有探针都出类型（`:5328-5341`）。
5. **梯子没夹住**：只有 `have_l && have_u` 都成立的坐标才进 box（`:851-856`），一个都没有就整条路径不产 region（`:857-865`）。

## 3.3 没被 bound 的那些怎么办 —— 直接回答

**不是"留空导致 region 比认证的更宽"。恰恰相反。**

- 在 **region 文本**里它们是**缺席**的：`show()`（`:746-771`）只打印 `bounds` 里有的坐标；被拒绝的坐标**先于所有 box 单独报出来**并明说"缺席是拒绝、不是测量"（`:703-720`），`goto_coverage.h:876-902` 把这条写成规矩。
- 在 **certify 查询**里，缺席 = **不受约束 = 被全称量化**。`assume(box); assert(tr==pi)` 里没提到的坐标取遍整个类型，所以证书是"对该轴上的所有值都成立"——这是**更强**的陈述，不是更宽的区域。所以"少 bound 一个坐标"损失的是**产出率**（查询更难通过），不是**正确性**。
- 在 **subtraction** 里，兄弟缺少某坐标的界就不能用它分离（`:886-887`），全缺则计 `degenerate`（`:897-908`），region 行尾挂 WARNING "STILL OVERLAPS"（`:1066-1072`），driver 照样送去认证并明说"查询才是裁判"（`scripts:1906-1910`）。也是产出率损失。
- **CERTIFY 侧的不对称是刻意的**：outer box 拒一个坐标继续测其它（`:5046-5057`），certify 拒一个坐标就**拒整条查询 `exit(1)`**（`:5508-5538`），理由是"丢掉一个被请求的界就等于认证了一个更宽的 box"。这条方向是对的。

## 3.4 那么"区间输入"现在到底 sound 不 sound

**对"未被 bound 的坐标"这一维度：sound。** 真正的不健全在别处，四条，按严重度：

**(U1) 语义不可满足 ⇒ 真空认证。这是最大的洞，而且今天完全没有防线。**
发射前的四道闸只看**语法**：`lo>hi`（`:5433`）、同名重复（`:5444`）、洞打空（`:5461-5475`）、十进制不落类型（`:5566-5585`）。它们看不见"这个假设在语义上不可满足"：
- 状态变量**不被 havoc**（`bmc.cpp:711-713`，`notes/probe-entry-state-havoc.md` §1 全篇），`--solidity-max-tx 1` 下 `state.x` 在入口就是构造器留下的常量。一个 well-formed、在类型内、非空的 box `state.x in [0,0]`，若构造器把它设成 7，则 `ASSUME` 不可满足 → 每个出口断言真空成立 → `VERIFICATION SUCCESSFUL` + exit 0。
- 同样地：两个坐标的合取不可行、pin 与 ABI 门冲突、pin 把该路径整个排除掉。
这**不是**假设出来的：`scripts:1081-1100` 的 `empty_coords` 注释就写着"工具自己的非真空论证——把 assert 放在每个出口——处理的是**另一种**真空，不覆盖不可满足的假设"；`scripts:1761-1812` 记录了 level-0 上同一现象的实测（EscrowSrc.cancel enc=2，1 个候选值时看起来是 `[5,5]`，2 个候选值时区间反转暴露为真空）。**工具侧对这条一行代码都没有。**

**(U2) named obstacle 的单元照常被认证。**
`unit_has_lost_decision`（`:3917`）和 `unit_calls_gated_unit`（`:3468`）在两个 stage-2 分支之前就算好了，但 `:5000` 和 `:5387` **都不看它们**。更糟：`named_obstacle_paths` 是在 `to_insert` 插入循环里填的（`:5732-5739`），而两个 stage-2 分支都在 `:5373` / `:5668` `continue` 掉了，所以 certify 模式下这个 map 是**空的**——报告也说不出来。`goto_coverage.h:384-396` 的规矩是"被标记的路径必须被排除，并且不得变成测试；只标记不排除毫无价值"。今天 certify 对它毫不设防。

**(U3) 出口种类在 stage-2 模式下全被标成 normal。**
同样因为 `continue` 在 `:5672-5693` 之前，`revert_paths` / `rollback_revert_paths` / `undetermined_exit_paths` 在 certify/outer-box 运行里是空集，而 `bmc.cpp:1498-1507` 就是读这三个集合决定 `exit_kind` 的。于是一次 certify 运行的 `cov-report.json` 会把每条 claim 标成 `"normal"`。区域本身仍然真（"box 里每个输入都走 pi"），但 pi 可能是 revert 路径，而没有任何东西说得出来。

**(U4) 模型自身的边界。**
`SUCCESSFUL` 恒为 bounded：`path_cov_can_prove_unreachable()` 恒返回 false（`bmc.cpp:722-725`）。且 `--unwind` 被强设为 4 并**无条件**打开 `no-unwinding-assertions`（`notes/path-coverage-invocation-contract.md:169-178`），超界执行被静默剪掉。更要紧的是 §5（`:180-203`）：`no-standard-checks` + `no-assertions` 默认开，`--overflow-check` / `--div-by-zero-check` / `--bounds-check` **任何 collector 都没传过**，所以溢出/除零/越界的 revert 边根本不在决策集里。一个被"认证"在区间里的输入，链上可能因为溢出 revert 而根本走不到 pi 的出口。该文件把这条列为**未决的健全性问题**，不是调参问题。

## 3.5 一句话的窄化结论

> 今天的"区间输入"**只对这一类坐标是健全且有产出的**：出现在反例载荷里、且是 mutable（或形参/env）、且在源码里声明过、且能被 `resolve_coord` 解析成合约对象的标量 component 或形参（含深度 1 struct 字段）、且类型是**无符号位向量**的坐标。按语料统计，这至多是已声明状态变量的 **17%（143 中的 24）**，且在六个输入中的**三个上是 0%**。而即使在这一类上，认证的"成功"也仍然受 U1–U4 四条约束。

---

# 4. 区域的形状：product-of-punched-intervals 表达不了什么

现在的形状：`R = ∏_c ([lo_c, hi_c] \ H_c)`。

| 需要的东西 | 现在能表达吗 | 最小扩展 |
|---|---|---|
| **两坐标之间的关系** `a < b` | **不能。** 而且工具自己知道：`:2561-2570` 的 `outer_pins` 注释写着"当一个 guard 把两个坐标绑在一起（`bal >= amt`）时域是一条对角线，没有 box 能紧紧包住它，减法也分离不了任何东西；把除一个之外都 pin 住，问题就退回一维，那里区间是精确的"。今天的做法就是 pin。`scripts:594-597` 把它列为"改变 definition 6 的开放方法层条目"，明确不做 | **certify 侧极便宜**：spec 加一个 `rel: [{lhs, op, rhs}]`，两次 `resolve_coord`，在 `:5591` 多一个 `ASSUME` 合取项，约 20 行。**outer-box 侧才是难点**：要测量差值需要 `a-b<=v` 形式的探针，而两个无符号量的差会 wrap，需要和 `path-cov-assert-plan.md:388-393` 里 delta rung 同样的 `a>=b` 保护合取 |
| **析取（并）** | **单个 spec 不能**，一次 certify 只吃一个 box。**但整体上几乎免费**：认证是逐个独立查询，若干个各自被认证的 box 的**并**仍然是被认证的。缺的是 driver：`scripts:1955-1965` 在拿到切割建议后**替换** box，把另一侧直接扔掉 | **零工具改动**。driver 把 `box` 变成 box 列表，refutation 时保留**两侧**分别递归认证；`certified_overlap`（`:952-980`）的 key 从 `enc` 改成 `(enc, piece)`。这是我看下来**产出率上最大的一笔、且不需要工具支持**的改动。要加 `--max-region-pieces` 上限 |
| **域是一组点** `{3,7,11}` | **格式支持一半**：certify 的 `holes` 已经是任意集合（`:2703-2704`），所以 `[3,11] \ {4,5,6,8,9,10}` 是可写的。**但没有任何东西会产生它**——`report_outer_boxes` 只在"兄弟在该坐标上恰是单点"时才打洞（`:936-950`），多点兄弟一律走侧切（`:839-844` 明说这是"陈述过的限制而非疏忽"）。而且 driver 根本不读 PUNCH SUGGESTION（见 §1.5） | 两步：(a) driver 加 `PUNCH_RE` 并在排除集较小时应用洞；(b) 允许对多点兄弟逐值打洞，需要一个"打几个点之后侧切更划算"的策略旋钮——`:839-844` 说这个旋钮背后有个从未测过的产出率问题 |
| **域依赖合约状态** | **不能**（product 形式）。今天的答案是把状态坐标 **pin** 住，并把 pin 连同 region 一起打印（`:722-731`, `:5123-5143`），于是每个 region 是"这个 slice 上的陈述"。pin 未被应用时会被从标签里剔除并告警（`:5129-5142`）——这一条已经做对了 | 与 `rel` 同一条扩展；或者把结论**如实**表述成"以被 pin 的状态为索引的区域族"，这其实已经是 pin note 的语义，只是没有被当成一等输出 |

---

# 5. "完美"应该意味着什么 —— 全部写成运行期检查

本项目的规矩是"方法赖以成立的命题要写成运行期检查"（`scripts:965-970` 自己写了这条：域两两不交、区域只会变窄、F+I+U 等于路径数，"都是可执行的一致性检查"）。下面每条都能被**检查**而不是被**论证**。

**C1 非真空性（今天完全缺）** — certify 运行必须证明"这个 region 至少允许一次到达 pi 出口的执行"。做法：在 `ASSUME` 之后、body 第一条指令处，发一条独立 claim（comment `<uid>:path:<enc>#nonvacuous`），其 guard 恒假，要求它的 `claim_outcome` 必须是 `'F'`。`'P'` 或缺席 ⇒ 假设不可满足 ⇒ `exit(1)`。可行性已核实：`claim_outcome` 对**任何** `is_path_cov` 运行逐 claim 记录（`bmc.cpp:2904-2921`），certify 模式也不例外。
⚠ 副作用必须一起处理：一条被反驳的 claim 会把整轮的 `VERIFICATION SUCCESSFUL` 翻成 `FAILED`，而 driver 正是按整行读判决的（`scripts:876-911`）。所以工具必须自己打一行 `--path-cov-certify: RESULT: CERTIFIED | REFUTED | VACUOUS`，driver 改读这一行。这和 `path-cov-assert-plan.md:100-105`/`:508-511` 对 stage-3 的论证是同一条（"运行的 verdict 行不是结果"）。

**C2 CE 成员性** — 被认证的 region 必须包含本路径自己的反例：对每个 bound，`lo<=ce<=hi` 且 `ce∉H`。纯算术、零查询。这正是让每一次切割"合法"的那条不变量（`goto_coverage.cpp:929-931`, `:816-822`），而 driver 手里就有 `ce`（`scripts:1398-1402`）却**从不检查**。注意洞是跨轮携带的（`scripts:1893-1898`），所以违反是可能的。

**C3 区域单调性** — `|R|` 跨 shrink 轮次只能不增。算法就是现成的 `path_cov_kept_in`（`:588-603`）。今天"only ever narrower"只出现在注释里（`goto_coverage.h:898-899`, `:779-780`）。

**C4 分区 + 覆盖** — 两两不交已实现（`certified_overlap`，`scripts:952-980`，而且是从一个真实假阳性里长出来的）。补上另一半：`Σ|R_i| <= |类型乘积|`，并把这个比值当成产出率指标输出（现在没有任何"覆盖了域的多大比例"的数字）。

**C5 坐标账目闭合** — 载荷里的每个名字必须**恰好**落进 {bounded, pinned, refused-with-reason, dropped-as-artifact, unsettable-pinned} 之一。今天每一类都被打印（`scripts:1584-1590`, `:1656-1663`, `:1672-1678`, `:1587`），但集合等式从未被断言。一行 set 运算。这是"缺席读成已测量"这类错误的通用闸。

**C6 spec→公式往返** — 工具打印**实际进入公式**的完整假设（`c in [lo,hi] \ {…}`，逐坐标），driver 与自己写出去的 spec 逐字段比对。`holes_emitted` 在合取处自增（`:5599-5606`）已经是这条规则的一个实例，且是实测出来的；把它推广到 `lo/hi/坐标名`。

**C7 witness 落在被假设的集合内** — `outside_assumed`（`scripts:1195-1234`）已经实现，但只当**报告过滤器**。升级：对形参/状态坐标做硬检查（不一致 = 工具缺陷）；对 env 保持警告——`notes/env-bound-not-applied.md` 已证明 env 的载荷值可能是 post-wrapper 值或未被读取的自由符号，那里的不一致不是缺陷。**这条不对称必须保留**，否则会重演那篇笔记里被撤回的诊断。

**C8 障碍与出口种类闸** — certify 前检查 `unit_has_lost_decision`（`:3917`）/ `unit_calls_gated_unit`（`:3468`），命中则拒；并从 `to_insert` 的 `std::get<3>(e)` 与 `rollback_exits`/`undetermined_exits` 局部量取出 pi 的出口种类打进 region 标签。**陷阱**：三个公开集合在本分支里是空的（`:5672-5693` 在 `continue` 之后），照抄 `goto_coveraget::revert_paths` 会恒不开火——`path-cov-assert-plan.md:571-575` 已经把这个陷阱写下来了。

**C9 类型区间往返** — driver 在写 spec 之前就用工具发布的 `TYPE RANGE` 行（`:5112-5119`）校验每个十进制，使失败点指向 driver 而不是工具。今天工具在 certify 侧检查（`:5557-5586`）、在 outer-box 侧丢弃（`:5278-5327`），driver 侧只用它裁剪几何梯子（`scripts:795-797`）。

**C10 决策集完整性** — 运行时断言"创造 revert 边的检查是开着的"，或者把这个缺口写进每个 region 的标签。见 `notes/path-coverage-invocation-contract.md:194-203`。

---

# 6. 排序后的计划

排序依据：先关"绿色但是假的"，再关"真但说不清的"，最后才是产出率和新坐标种类。

---

### S1 — 非真空性见证（C1）+ 工具自报 RESULT 行
**改什么**：`goto_coverage.cpp:5620`（`ASSUME` 循环之后、出口断言循环之前）发一条 `#nonvacuous` claim；新增一个 gate（可放在 `audit_certify_witness` 旁）读它的 `claim_outcome`；`bmc.cpp:1158-1168` 的 certify arm 里打印 `RESULT: CERTIFIED|REFUTED|VACUOUS`；driver `verdict()`（`scripts:876-911`）改读该行，旧的整行判决作为回退。
**可能出错**：(a) 反驳该 claim 会翻转整轮 verdict —— 必须和 RESULT 行**同一次**改，否则每个成功认证都会被 driver 读成 FAILED；(b) 该 claim 的 comment 前缀必须仍是 `<unit-id>:path:` 开头，否则 CE harvest 的 scope 测试全灭（`:5635-5644` 的实测）；(c) 若某个 unit 有多个出口/多次调用，`assert(false)` 的位置要在 assume 之后、body 之前。
**怎么证明它开火**：`solidity_path_cov_certify_vacuous_state_box_refused` —— 构造器把 `uint256 s` 设成 7，spec 给 `state.s in [0,0]`（well-formed、在类型内、无重复、无洞）。今天这一定返回 SUCCESSFUL/exit 0。
**必须的 must-flip 对**：孪生 `..._nonvacuous_state_box_certified`，同一合约、`state.s in [7,7]`，必须 CERTIFIED。只做前一半的话，一个"永远说真空"的检查也能通过。
**是否需要新 fixture**：是，一对。

---

### S2 — 障碍闸 + 出口种类标签（C8）
**改什么**：`goto_coverage.cpp:5387` 的 certify 分支开头、`:5000` 的 outer-box 分支开头，读已在作用域内的 `unit_has_lost_decision`（`:3917`）/`unit_calls_gated_unit`（`:3468`）；certify 命中则 `exit(1)` 并具名；outer-box 命中则给每条 region 行加标签。同时从 `to_insert` 匹配项的 `std::get<3>(e)` 与 `rollback_exits`/`undetermined_exits` 取 pi 的出口种类。
**可能出错**：读成公开的 `goto_coveraget::revert_paths` → 三个集合在本分支恒空 → 检测器恒不开火（"恒真判读器"的镜像）。
**怎么证明它开火**：复用 `solidity_path_cov_library_require_obstacle` 的合约，配一个 certify spec，必须拒绝；孪生用 `solidity_path_cov_residual_unit_call_obstacle` 覆盖第二条障碍路线——两条在 `:5844-5869` 是分开计数的，只写一个会让另一条静默回归。
**must-flip 对**：干净 unit 的同形 spec 必须仍然 CERTIFIED。
**新 fixture**：是，两对。

---

### S3 — 反驳时保留**两侧**：区域变成 box 的并（Q4 的析取）
**改什么**：纯 driver。`scripts:1955-1965` 现在把 `box` 替换成 `nb` 并丢弃另一侧。改成把 `box` 拆成两片，各自入队递归认证；`ok`/`ok_holes` 的 key 从 `enc` 变成 `(enc, piece)`；`certified_overlap`（`:952-980`）跟着改 key；输出 `=== CERTIFIED REGIONS ===`（`:1991-2007`）按 enc 分组打印若干片。加 `--max-region-pieces` 上限。
**可能出错**：认证查询数量爆炸（每次反驳 ×2）；分片之间可能重叠（必须靠 `certified_overlap` 兜住）；`empty_coords` 要对每片单独跑。
**怎么证明它开火**：纯函数回归（本项目已有这套纪律，`test_solidity_path_generalise.py` 全是纯函数测试，不跑 solver）：喂一个两片的并，断言两片不交、每片各含一个 CE、且报告行数是 2。
**must-flip 对**：单片输入必须**逐字节**产生和今天一样的输出行（"closed by default"，这是本文件反复用的手法，见 `:583-588`, `:676-679`）。
**新 fixture**：是，纯函数级。

---

### S4 — 接上 PUNCH SUGGESTION
**改什么**：`scripts:636-657` 增加 `PUNCH_RE`（匹配 `goto_coverage.cpp:479-490` 那行的 `<coord> != <value>`），在排除集小时把它当成洞加进 `holes` 而不是走侧切。
**可能出错**：洞与后续侧切叠加时可能把区间打空——`empty_coords`（`scripts:1081-1111`）已经能看见这种，但要确认它在新路径上被调用；工具侧 `:5449-5476` 也会拒，双保险。
**怎么证明它开火**：纯函数测试，输入是一条逐字带 PUNCH 行的 log，断言产出 hole；**must-flip**：只有 SHRINK 行的 log 必须仍然走侧切、结果逐字节不变。
**新 fixture**：是，纯函数级一对。

---

### S5 — bool 坐标（Q2 里最便宜的一条）
**改什么**：`goto_coverage.cpp:2337` 接受 `is_bool_type`；`:5591-5593`（certify 的 `>=`/`<=` 合取）和 `:5348-5350`（outer-box 探针）对 bool 特判成 `equality2tc`；`:5098-5120` 为 bool 发布 `TYPE RANGE [0,1]`。
**可能出错**：`<=` 建在 bool 上会畸形并在 SMT 层 abort——这正是白名单存在的原因（`:2266-2271`）。必须两个建 guard 的点**同时**改，漏一个就是一次 SIGABRT。
**怎么证明它开火**：`solidity_path_cov_certify_bool_coord` —— 一条被 `bool flag` 守卫的路径，`flag in [1,1]` 必须 CERTIFIED。
**must-flip 对**：`flag in [0,0]` 必须 REFUTED。
**新 fixture**：是，一对。

---

### S6 — C2/C3/C5 三条纯 driver 检查
**改什么**：`scripts` 新增三个纯函数 `ce_in_region(box, holes, ce)`、`region_size(box, holes)`（照抄 `path_cov_kept_in` 的算术）、`coordinate_accounting(payload, coords, pins, refused, artifacts, unsettable)`；在 `:1887-1972` 的认证循环里调用，违反即 `return 1` 并具名。
**可能出错**：C2 在 pin 与 CE 冲突时会误报——`unsettable` 的 pin 取的就是 CE 值（`:1668-1670`），所以一致；但 `--pin` 显式指定时可以冲突，那时应该报"pin 把这条路径排除出 slice 了"，与 S1 的 VACUOUS 是同一件事，措辞要一致。
**怎么证明它开火**：纯函数测试三对（含/不含 CE；变宽/变窄；账目闭合/漏一个名字）。
**新 fixture**：是，纯函数级三对。

---

### S7 — signed 坐标
**改什么**：`:2316` 接受 signedbv；`:5557-5586` 的 `[0, 2^w-1]` 换成按符号性选范围；`:5098-5120` 的 TYPE RANGE 发布加 signed 分支（**否则 driver 拿不到范围、回落 2^256、几何梯子重演 wrap bug**，见 `notes/geometric-ladder-wraps-on-narrow-types.md`）；`geometric_values`（`scripts:501-508`）出对称版本。
**可能出错**：这是那条被明确警告过"不得靠放宽类型测试偷偷塞进来"的改动（`:2311-2315`）。任何只改 `:2316` 不改另外三处的做法都会重新打开那个假证书。
**怎么证明它开火**：注释里那对实测——`uint256 a` 的 `[0, 2^256-1]` 是 FAILED，`int256 a` 同样十进制现在也必须是 FAILED（今天是 SUCCESSFUL）。
**must-flip 对**：`int256 a in [-5, 5]` 且路径确实落在其中，必须 CERTIFIED。
**新 fixture**：是，一对（其中一半就是现成的 `solidity_path_cov_certify_bound_out_of_type_refused` 的 signed 孪生）。

---

### S8 — 跨坐标关系 `rel`（certify 侧）
**改什么**：`:2697-2709` 的 spec 解析加 `rel` 键；`:5591` 之后多插一条 `ASSUME`；`:5493-5539` 的拒绝逻辑对 `rel` 的两端各跑一次 `resolve_coord` + `coord_expressible`。outer-box 侧**不动**（无法测量差值，见 §4）。
**可能出错**：两端宽度不同时的比较需要显式扩展；无符号差值 wrap 需要 `a>=b` 保护合取（`path-cov-assert-plan.md:388-393` 的现成推理）；`rel` 会让"region 是 product"的表述不再成立，`certified_overlap` 的不交判定对带 rel 的区域**不再有效**（两个盒子相交但 rel 使它们不交），必须在有 rel 时降级为"不做该检查并明说"。
**怎么证明它开火**：一个 `require(bal >= amt)` 守卫的 unit，pin 全解开，`rel: [{bal, ">=", amt}]` 必须 CERTIFIED。
**must-flip 对**：`rel` 换成 `<` 必须 REFUTED。
**新 fixture**：是，一对。

---

### S9 — 决策集完整性（U4）
**改什么**：`scripts:81-89` 的 `run()` 加上 `--overflow-check --unsigned-overflow-check --div-by-zero-check --bounds-check`，**或者**（若代价不可接受）把这个缺口写进每条 region 的标签文本。
**可能出错**：**这会改变每条路径的身份**——`DECISION_SET_VERSION`（`goto_coverage.cpp:2443`，现值 4）必须 bump，于是所有 `--coverage-covered-set` 文件失效（fail-closed 丢弃，`:2492-2505`），所有已收的 enc 编号作废。这是一笔大而诚实的账，不能顺手做。
**怎么证明它开火**：一个只有溢出能 revert 的 unit：开关前后的 enumerated path 数必须不同。
**must-flip 对**：不含任何算术的 unit，开关前后路径集合必须逐字节相同。
**新 fixture**：是，一对。

---

### S10 — env 默认策略
**改什么**：`scripts:1541-1549` 的 `--pin-env` 改为默认开；或更精确地：从 AST 读函数的 `stateMutability`（`state_mutability` 已经在读 AST 了，`:354-404`），对非 payable 的 unit 自动 pin `msg.value = 0`。
**可能出错**：pin 会改变每个 region 的**含义**（变成 slice 陈述），这正是它默认关着的原因（`:1546-1549`）；必须保证 pin 一定被打印（`:5123-5143` 已经处理了"pin 未被应用"的情形）。
**怎么证明它开火**：一个非 payable unit，不加 pin 认证不了、加了就通过——这正是 `scripts:240-251` 记录的实测。
**must-flip 对**：payable unit 不得被 pin。
**新 fixture**：是，一对。

---

# 7. UNVERIFIED —— 读不出来的部分，以及能定案的文件

1. **`--path-cov-outer-box` 和 `--path-cov-certify` 同时给会怎样。** 我读了 `esbmc_parseoptions.cpp:4198-4203`，**两者只是各自被读入，没有互斥检查**；而 `goto_coverage.cpp:5000` 的 `outer_on` 先测，`:5373` 就 `continue` 掉了，所以 certify 永不开火 → `certify_units_matched == 0` → `:5814` 报错并把责任推给 **unit 名字**。这条我有静态证据但没有运行证据。定案文件：跑一次即可，或读 `esbmc_parseoptions.cpp:4118-4236` 全段确认没有别处的互斥。
2. **`rollback_exits` / `undetermined_exits` 在 `:5387` 处是否确实在作用域内。** `path-cov-assert-plan.md:571-575` 断言它们声明在 `:3970` / `:3973`，我没有直接读那两行；`:5690-5693` 的用法与之一致。定案：`goto_coverage.cpp:3950-3990`。
3. **一条真正被反驳的 `assert(false)` 在 certify 模式下是否会被 `--multi-property` 当独立 claim 判并进入 `claim_outcome`。** `bmc.cpp:2904-2921` 显示逐 claim 记录、与模式无关，所以我认为会；但我**没有读** `multi_property_check` 的主体（`bmc.cpp:2617-2880`），不知道 certify 模式下是否有别的提前返回。定案：`bmc.cpp:2617-2880`。
4. **`entry_storage` 是否会包含一个整条 trace 都没被写过的状态变量。** 按 `bmc.cpp:3219-3226`/`:3436-3452`，entry 快照就是压栈时累积的 `last_state`，没写过就不出现——所以"构造器是否给每个状态变量都发一次写"决定了坐标集合的完整性。定案：`src/solidity-frontend/solidity_convert_constructor.cpp`（`emit_ctor_deep_init_fixup`，`notes/probe-entry-state-havoc.md:206-210` 指到那里）。
5. **`resolve_coord` 用子串匹配挑合约对象是否真会挑错。** `:3346-3356` 取第一个 id 以 `sol:@_ESBMC_Object_` 开头、且（`scope_contract` 非空时）**包含**它的符号。`--contract Escrow` 会匹配 `sol:@_ESBMC_Object_EscrowSrc#`；`--coverage-whole-unit` 下 `scope_contract` 为空则任意合约的对象都可能被选中。`path-cov-assert-plan.md:724-736` 把这条列为条件 (A)。定案：需要一个双合约、名字互为前缀的 fixture 跑一次，或读 `goto_coverage.cpp:6601-6615` 的 `contract_of()` 确认可以改成精确匹配。
6. **`--all-witnesses`（`options.cpp:374-382`）是否真能在一轮里给同一条路径多个域内成员。** 从未使用过（`notes/path-coverage-invocation-contract.md:226-232`）。如果能，它是免费的 C2/C4 素材（多个已知域成员 = 更强的切割合法性约束）。定案：`bmc.cpp` 的 witness 枚举（`:3457-3465` 附近的 `enumerate` 分支）。
7. **certify 模式的 `cov-report.json` 里 `exit_kind` 是否真的全是 `"normal"`。** 我的推断链是：三个集合在 `:5672` 之后才填、两个 stage-2 分支在此之前 `continue` → 集合为空 → `bmc.cpp:1498-1507` 三个 `count()` 全 0 → `"normal"`。链条完整但没有运行证据。定案：跑一次 certify 并看 `cov-report.json`（或读 `:5672` 之前是否还有别处填这三个集合）。
