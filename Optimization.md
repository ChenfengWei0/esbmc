# 形式化验证建模优化指南

**——从 BMC 工具实战经验中提取可迁移的设计原则**

---

## 0. 前言：为什么写这份文档

形式化验证（formal verification）的工程师容易陷入一个误区：**只关注 soundness（可靠性）和 completeness（完备性），而忽略效率**。然而在工业界，一个语义正确但耗时数小时的验证器，价值远低于一个速度快十倍但偶尔需要重新检查的验证器——前者根本不会被使用。

本文从一个实际的 BMC（Bounded Model Checker，有界模型检查器）工具的优化经验中抽取**可迁移的、与具体工具无关的**设计原则。读者应当能在阅读后回答：

1. 为什么语义等价的两个程序，在同一个 BMC 工具下成本可以相差数百倍？
2. 在前端、中间件、后端三个层级，各有哪些常见的优化模式？
3. 为什么"空间换时间"在形式化验证中往往是反模式？
4. 如何在 soundness/completeness 与效率之间做精明的取舍？

本文**不**讲：某个具体工具的命令行参数、某次具体 bug 的修法。这些是工程性的、易过时的。

本文**讲**：建模选择如何影响 SMT 公式的规模与求解器的工作量；为什么某些直觉上"应该更快"的设计实际上更慢；以及一个抽象意义上"高效"的形式化验证模型应该长什么样。

---

## 1. 背景知识

如果你已经熟悉符号执行、SSA、SMT，可以跳到第 2 章。

### 1.1 BMC 工作流概览

一个典型的 BMC 工具把源代码变成"求解器能消化的逻辑公式"，大致经过五步：

```
源代码 ── 解析 ──▶ AST ── 编译 ──▶ IR (中间表示)
                                    │
                                    ▼
                        符号执行 (symbolic execution)
                                    │
                                    ▼
                                  SSA
                                    │
                                    ▼
                              SMT 公式
                                    │
                                    ▼
                                求解器 ──▶ SAT / UNSAT / UNKNOWN
```

- **AST (Abstract Syntax Tree, 抽象语法树)**：源码的结构化表示。
- **IR (Intermediate Representation, 中间表示)**：去糖化、去模板、统一化的低层表示，便于后续分析。常见的 BMC IR 是 GOTO 程序：每条指令是 `ASSIGN / GOTO / ASSERT / ASSUME / FUNCTION_CALL` 之一。
- **符号执行 (symex, symbolic execution)**：把每个变量当作"未知数"，沿着所有可能的执行路径走一遍，把每条路径上的赋值和分支条件记录下来。
- **SSA (Static Single Assignment, 静态单赋值形式)**：每个变量只赋值一次。`x = x + 1` 在源码里看起来重新赋值，但在 SSA 中变成 `x_2 = x_1 + 1`——两个不同的变量。这让符号推理变得局部化、可组合。
- **SMT 公式 (Satisfiability Modulo Theories formula)**：一阶逻辑公式，外加针对特定数据类型（bit-vector、array、real number 等）的"理论"。求解器判断这个公式是否可满足（SAT）；不可满足（UNSAT）则证明断言永远成立。

### 1.2 关键术语速查

| 术语 | 含义 |
|---|---|
| **VCC** (Verification Condition Claim) | 一条待验证的断言。一个程序通常生成几十到上千个 VCC。 |
| **切片** (slicing) | 删去那些不影响任何断言的 SSA 赋值。优化的核心机制之一。 |
| **havoc** | 把某个变量"擦除"为不确定的值。常用于循环不变量、过程间分析、逃避完全建模。 |
| **soundness** | 验证器报告"无 bug"时，程序确实无 bug。**没有假阴性**。 |
| **completeness** | 程序确实无 bug 时，验证器能在有限时间内报告"无 bug"。**没有假阳性 + 必终止**。 |
| **bounded soundness** (有界 soundness) | 在某个深度（unwind 上限）以内 sound；超出深度的执行路径不保证。 |
| **过近似** (over-approximation) | 把模型的状态空间放大。用于证明：如果过近似都找不到反例，真实程序也找不到。可能引入虚假反例。 |
| **欠近似** (under-approximation) | 把模型的状态空间缩小。用于找漏洞：找到的反例必为真。可能漏掉真实漏洞。 |

### 1.3 成本模型：每条 SSA ≈ SMT 一条等式

这是本文最核心的观察：**每条 SSA 赋值在 SMT 编码后变成一条等式约束**。验证 1000 条 SSA 的程序意味着求解器要面对 1000 条公理。

公式规模与求解时间的关系不是线性的——**最坏情况是指数级**（SAT 问题本身是 NP-完全的）。所以建模时多产生一倍 SSA，求解时间最坏会指数级膨胀。

> **核心 takeaway**
>
> "**正确建模**" 不等于 "**高效建模**"。一个 sound 的模型可以让求解器面对 100 条等式，也可以让它面对 100 万条。**两者结果相同，时间相差万倍**。优化建模的本质是：**让 SMT 公式尽可能小，结构尽可能贴合求解器原生擅长的理论**。

---

## 2. 前端建模 — 类型表示与降级

前端把源语言的高层概念翻译为 IR。这一步的选择决定了后续所有阶段的成本上限。

### 2.1 选对类型：让 SMT 看到 native theory

现代 SMT 求解器内置高度优化的"理论"（theories）：

- **bitvector**：定长二进制串，支持位运算、算术、比较。
- **array**：从一种类型到另一种类型的全函数（select/store）。
- **datatype** (代数数据类型)：tuple、enum、递归 ADT。
- **uninterpreted function** (未解释函数)：仅有一致性公理 `x = y → f(x) = f(y)`。

**原则**：**让源语言的类型尽量直接落到求解器的 native theory 上**。

举例：以太坊的 `bytes32` 是 32 字节的固定长度二进制串。三种建模方式：

1. **结构体** `struct { uint8 data[32]; size_t length; }`
2. **未解释类型 + uninterpreted function**
3. **256-bit bitvector** —— 直接落到 SMT bitvector 理论

方案 3 最优。求解器对 bitvector 有 BV-decision-procedure，可以用电路风格的位级推理高速判定。方案 1 把每个字节都当作独立的 8-bit bitvector，求解器看到的是 32 个独立约束加 1 个 length 字段——每次"复制" `bytes32` 都变成 32 条赋值。方案 2 完全交给求解器穷举，是最慢的。

很多 BMC 工具受历史遗留影响选了方案 1。一旦确定下来，后续每个使用 `bytes32` 的位置都要面对 32× 的成本。这是**前端类型选择的复利效应**——一个早期的次优选择会被后续每条指令放大。

### 2.2 编译期常量折叠：字面量必须在前端消失

考虑一个非常常见的模式：源代码里有 `bytes32(uint256(1))`——一个嵌套类型转换，把整数 1 转为 bytes32。

如果前端足够聪明，这应该等价于一个 32 字节的常量 `0x00...01`，在 SMT 公式里是一个零成本的字面量。但很多前端不这么做。

实际遇到的一个 bug：某 BMC 工具在处理嵌套类型转换时，**把外层调用方期望的目标类型 (`literal_type`) 错误地透传给内层转换的参数**。具体行为：

- 调用方上下文期望 `bytes32`
- 外层 `bytes32(...)` 处理时把 `bytes32` 类型 hint 传给内层 `uint256(1)`
- 内层处理时**继续把这个 hint 传给字面量 `1`**
- 结果：`1` 被当作 bytes32 来降级，先变成结构体 `BytesStatic{data=...01, length=32}`
- 内层 `uint256(<BytesStatic>)` 调用 `bytes_static_to_uint(<BS>)` 把它转回 256-bit 整数
- 外层 `bytes32(<uint>)` 调用 `bytes_static_from_uint(<u256>, 32)` 又转回 bytes32

净结果：**一个常量经过 64 步往返**，最终回到原值。这 64 步每步是一条 SSA 赋值，每个 `bytes_static_*` 调用都包含 32 步循环展开。

修复方法只有 33 行代码：**当外层 hint 与本层目标类型不一致时，剥离它，使用本层自己的目标类型**。一次显式的类型转换"切断"了外层 hint 的传递。

实测影响：

- 测试 `napp_struct_multifield_fail` 的 raw SSA 从 49,155 降到 32,633（-34%）
- 6 个测试从 TIMEOUT 变成 PASS

> **核心 takeaway**
>
> 编译期常量必须在前端就 fold（折叠）成 SMT 字面量。任何**会让常量经过运行时 helper 函数往返**的降级逻辑都是性能杀手。设计前端时，对每个类型转换路径都要问：**如果输入是字面量，输出会是字面量吗？**

### 2.3 库函数 vs 内联：何时各自适用

前端有两种降级策略：

**A. 调用库函数**：把复杂操作封装成预编译的 IR 函数（GOTO 子程序），调用方只插入一条 `FUNCTION_CALL`。

**B. 内联展开**：直接把操作展开为多条 IR 指令，无函数调用。

各自的优劣：

| | 库函数 | 内联展开 |
|---|---|---|
| **代码体积** | 小（一次定义、多处复用） | 大（每个调用点都展开） |
| **维护性** | 高 | 低 |
| **切片器友好度** | 中（函数边界限制了跨函数切片） | 高（直接在调用点内消除死代码） |
| **常量传播** | 低（参数对函数体不可见） | 高（参数即调用点的字面量，simplifier 可立即折叠） |
| **循环展开** | 受限于函数体里的 `--unwind` 上限 | 在调用点编译期完全展开 |

**经验法则**：

- **常量大小、固定步数的小操作**（如固定 32 字节的位运算）：内联展开。求解器看到的是直接的等式链，可以在前端 simplifier 阶段就 fold 成常量。
- **状态相关、复杂控制流的大操作**（如 mapping pool 维护、heap 分配器）：库函数。多次复用减小 IR 体积，且这些操作通常不能被 simplifier 折叠。

实战案例：某工具早期把 `bytes_static_from_uint(val, len)` 之类的辅助函数实现为带 `for (i=0; i<len; i++)` 循环的库函数。当 `len` 是符号值时，循环要靠 `--unwind` 配置展开，每次访问都生成 32+ 条 SSA。后来改用 32 步硬编码的 macro：

```c
#define _UNROLL_32(STEP) STEP(0); STEP(1); ... STEP(31)
```

每个 STEP(i) 在 i 是字面量的情况下被求解器或 simplifier 立即 fold 成单条赋值。**净效果：每次调用从 32 条循环 SSA 降到 1 条直接 SSA**（其他 31 条被 simplifier 消除，因为它们的条件 `i < length` 是常量）。

### 2.4 多维数据结构展平：bit-shift composition vs 嵌套 store-select

考虑 `mapping(uint256 => uint256[8])`——键是 256 位，值是 8 元素数组。两种建模：

**A. 层级嵌套**：外层 SMT array 索引为 256-bit key，元素是另一个 8 元素数组。访问 `m[k][i]` 需要 `select(select(map, k), i)` —— 两次 array 操作 + 一个隐含的 array 类型构造。

**B. 单一展平**：一个 SMT array 索引为 `(zext(k) << 3) | i`（k 左移 3 位为 i 腾出空间），元素是单个 256-bit 值。访问 `m[k][i]` 是一次 `select(map, composed_key)`。

方案 B 的优势：

1. **少一层 array sort**。求解器对每条 array 操作都要做"读后写一致性"推理；少一层就少一组公理。
2. **更易被 array decision procedure 优化**。SMT 求解器对 1D array 有成熟的 decision procedure，对嵌套 array (array of array) 通常退化到通用一阶推理。
3. **常量索引可以前端 fold**。`m[k][0]` 中的 `0` 在方案 B 中合并到 composed key，前端可以静态计算 `(k << 3) | 0 == (k << 3)`。

实测案例：某工具把 `mapping(uint256 => uint256[8])` 从方案 A 改为方案 B，相同测试**从 60 秒降到 11 毫秒**——五千倍提升。这并非常见量级，是因为方案 A 的旧实现还涉及一个慢哈希 fold 和懒分配 ITE 链；方案 B 一举消除了这些。

> **核心 takeaway**
>
> 多维结构应展平成 1D。**用 bit-shift 组合索引**，让求解器只面对一层 array。嵌套 array 的成本不仅是"多一层"，还会触发求解器从专用 decision procedure 退化到通用推理。

### 2.5 哈希表与 memoization：SMT array + 哨兵零初始化

某些操作（如 `keccak256(x)`）建模时不能给出真实的密码学哈希——求解器没有相应理论支持。常见的 sound 过近似是：**把 keccak 当作 uninterpreted function**，仅保留"同输入同输出"的一致性公理。

但这不够。需要保证 *不同* 输入产生 *不同* 输出（密码学碰撞抗性）才能让某些断言通过。在 SMT 中可以这样建模：

1. 用一个 SMT array 作为"记忆体"：`keccak_table[input] = nondet`。
2. 第一次访问某个 input：返回 nondet 值，写回 array。
3. 同 input 的后续访问：从 array 读出，得到同一个值（一致性）。
4. **额外公理**：`assume(input1 != input2 → keccak_table[input1] != keccak_table[input2])`，对每对调用点显式声明。

这是 SMT 风格的 memoization。它"把空间换时间"——但**额外的状态是公理而非状态变量**。差别巨大：

- 公理：求解器一次性消化，对后续 SAT 求解几乎无成本。
- 状态变量：每条公理都进入 path constraint，扩大搜索空间。

> **核心 takeaway**
>
> Memoization 在 BMC 中可行，但要严格遵守："**结果必须是对求解器免费可见的公理**"。如果你引入的"缓存"在求解器看来是又一组 SSA 赋值，那它在拖慢，不在加速。

---

## 3. 中间件 — 符号执行与 SSA

前端给出一份"完整但啰嗦"的 IR，中间件的任务是**在保持语义的前提下让它变小**。

### 3.1 切片器：能消除什么、不能消除什么

切片（slicing）把不影响任何断言的 SSA 赋值标记为"忽略"。基本算法：

1. 从所有 ASSERT 语句的条件出发，反向追踪它依赖的变量。
2. 把所有"被依赖到"的赋值标记为 live。
3. 其余标记 ignore，编码到 SMT 时跳过。

切片器**能消除**的典型例子：

- 局部变量的中间计算结果 (`tmp = x + 1; y = tmp * 2;` 如果 `tmp` 没有别的用途)。
- 死分支的副作用。
- 调用了但其返回值未使用的 pure 函数。

切片器**不能消除**的：

- **全局变量写**：因为它可能被任何后续读取，切片器一般保守地保留所有全局写。
- **副作用调用**：函数体不透明，无法判断是否影响断言。
- **跨函数依赖**：函数边界处通常缺失精确的别名/逃逸信息。
- **循环边界的写入**：循环变量本身被守卫使用，无法纯局部分析。

**优化原则**：

- **优先用局部变量而非全局**：局部变量切片成本低，全局变量是切片器盲区。
- **避免用副作用函数**：`logger.log(x)` 这种调用如果不影响断言但有副作用，切片器无法删除。
- **让循环可被静态展开**：常量边界的循环展开后，每个迭代独立切片。

### 3.2 循环展开：常量边界 vs 符号边界

BMC 处理循环靠**展开**：把 `for (i=0; i<N; i++)` 展开成 N 次内联拷贝。

- **常量边界**：`for (i=0; i<32; i++)` —— 完全展开，零运行时损耗，每次迭代独立切片。
- **符号边界**：`for (i=0; i<n; i++)` 当 `n` 是符号 —— 必须靠 `--unwind K` 配置硬上限，展开 K 次后插入 `assert(i==K → never_reach)`（unwind 假设）。
  - 如果 K < 真实最大值：要么 sound 但不完整（assertion 触发），要么用 `--no-unwinding-assertions` 静默截断（unsound）。

**反模式 1：在符号边界循环中做大批量操作**

```c
// BAD: 当 size 是符号，每条赋值都被 unwind 倍率放大
for (size_t i = 0; i < size; i++) {
  arr_dst[i] = arr_src[i];   // 每个 unwind 迭代生成一条 SSA
}
```

如果元素是 32 字节的 `bytes32`，且通过字节级 memcpy 实现，单次拷贝就是 32 条 byte stores × unwind 上限——把 1 次拷贝爆炸成 1000 条 SSA 完全可能。

**优化模式：typed-element store 替代 byte-level memcpy**

```c
// GOOD: 单条 typed store，等价于 SMT 的 array store
arr_dst[i] = arr_src[i];   // 1 SSA, 哪怕元素是 32 字节
```

实测案例：`bytes32[]` 的 push 操作，原本是 `realloc + memcpy` 的字节链，每次 push 生成 ~64 SSA（32 在 realloc 内部 preservation chain，32 在写新元素的 memcpy 中）。改用 typed-element store：

```c
new_arr[old_len] = element;  // 单条 SSA，等价于一次 SMT array store
```

**32× 缩减**。这个 32× 不是巧合——它来自字节级 vs 元素级的本质差异：求解器对 bitvector 的 native 操作单位是元素，不是字节。让 IR 与求解器的 unit-of-work 对齐。

### 3.3 SSA 版本管理：fresh symbol 防止跨帧串扰

SSA 内部每个变量有一个版本号 (`x#0`, `x#1`, ...)，符号执行用它跟踪"在哪一时刻读哪个值"。

**陷阱**：如果一个表达式在两个执行帧之间被传递（如函数调用边界、动态内存分配点），它依赖的版本号可能在另一帧被错误重命名。

实战案例：动态数组 `realloc(arr, size)` 中，新分配块的大小被记录在 `arr.size` 元数据里。如果 `size` 是 `old_len + 1`，符号执行第一次记录这个表达式时 `old_len` 版本是 `#3`；后续读元数据时，已经处于不同的 SSA 帧，那条表达式被错误地重命名为 `old_len#0`（未初始化版）。

修复：在记录元数据**之前**，引入一个全新的 SSA 符号：

```
fresh_size = old_len + 1   // 强制创建 fresh symbol
arr.size_metadata = fresh_size  // 后续读取看到的是稳定的 fresh_size
```

这把"版本敏感"的表达式锚定在一个具体的 SSA 时刻，跨帧无虞。

> **核心 takeaway**
>
> 当 SSA 表达式需要跨帧传递（例如存入元数据后被另一处读取），先**冻结**到 fresh symbol。"冻结"在这里指：把表达式赋给一个新引入的、版本号确定的 SSA 变量。

### 3.4 Value-set 分析：在 havoc 模式下保留指针身份

Value-set analysis (VSA, 值集合分析) 静态地估计每个指针在每个程序点可能指向的对象集合。BMC 工具用它做：

- **强更新 (strong update)** vs **弱更新 (weak update)** 决策：写入 `*p = x` 时，如果 VSA 知道 p 指向唯一对象 o，可以做 `o = x`（强更新）；否则要做 `for each possible o': o' = (p == &o') ? x : o'`（弱更新，成本随 VSA 大小线性放大）。
- **过程间分析**：函数返回时哪些状态是局部、哪些逃逸到调用者。
- **havoc 优化**：在 k-induction 等需要"假设性 havoc"的模式下，只 havoc VSA 实际指到的字段，不是整个对象。

**关键经验**：在涉及循环不变量、过程间归纳的模式下，**默认开启 VSA**。否则求解器要面对粗糙的"全部字段 havoc"，导致：

- 过近似过强：明显成立的不变量也证不出来。
- 求解器看到的 nondet 字段数爆炸。

实战案例：某工具在 k-induction 模式下默认未开 VSA。一个 `nested_array_1d` 测试因此 timeout——struct havoc 把整个对象的所有字段 nondet 化，包括那些循环里根本没改的字段。开启 VSA 后，havoc 仅限于"循环体里实际写过"的字段，立即 PASS。

### 3.5 SSA-cost 测量：不要靠想象

优化最重要的工具不是某种花哨技巧，而是**精确测量**。每次改动后，记录这些数字：

- **Raw SSA 数量**：符号执行结束时的总赋值数。
- **Post-slice SSA**：切片后剩余的活赋值数。
- **VCC count**：断言数量。
- **Encoding time**：把 SSA 翻译成 SMT 公式的时间。
- **Solving time**：求解器实际工作时间。

**典型瓶颈定位法**：在切片之后，按 SSA 的源位置（哪个函数）做直方图。前 5 名通常是优化的主战场。

实战案例：某次诊断中按函数分布的 post-slice SSA 是：

| 函数 | SSA 数 | 占比 |
|---|---|---|
| `_ESBMC_array_push` | 2763 | 34.2% |
| `bytes_static_from_uint` | 1224 | 15.1% |
| `bytes_static_to_uint` | 1188 | 14.7% |
| `pushHashes` (用户) | 1098 | 13.6% |
| `__memcpy_impl` | 555 | 6.9% |

**直接告诉你哪三个函数吃掉了一半的成本**。后续优化精准命中前两名（push 的 typed-element 改写 + bytes_static 的 round-trip 消除），获得 50% raw SSA 缩减。

> **核心 takeaway**
>
> 优化前先测量，按热点排序。**直觉常常错**——你以为的瓶颈可能根本不是。一份按函数分布的 SSA 直方图能在 30 秒内告诉你下一步该改哪里。

---

## 4. 后端 — SMT 求解器与编码

### 4.1 求解器各有所长

主流的 SMT 求解器在不同问题类型上性能差距巨大：

| 求解器 | 强项 | 弱项 |
|---|---|---|
| **Z3** | 通用、整数/实数算术（LIA/LRA）成熟、量词支持好 | bitvector 算术中等，256-bit 慢 |
| **Bitwuzla** | bitvector 之王，特别 256+ 位算术 | 量词、复杂数据类型支持有限 |
| **CVC5** | QF_ABV (quantifier-free arrays + bitvectors) 强、native datatype tuples | bitvector 极简模式略慢于 Bitwuzla |
| **Boolector** | 历史上的 bitvector 标杆，现在被 Bitwuzla 接棒 | 量词、新理论支持少 |

**工程经验**：以太坊智能合约里 256 位算术非常常见，Bitwuzla 比 Z3 通常**快 5-10 倍**。某些涉及多维 array of bitvector 的问题，CVC5 又比 Bitwuzla 快——它的 QF_ABV decision procedure 处理 array nesting 更好。

**自动选择策略**：成熟的 BMC 工具应该根据问题特征自动 hint 求解器。例如：

- 检测到 256-bit bitvector 操作占主导 → Bitwuzla
- 检测到 ≥3 维嵌套 array → CVC5（开启 native tuples）
- 检测到大量量词 → Z3
- 默认 → Z3（兼容性最好）

### 4.2 编码格式：SMT-LIB vs 求解器原生 API

绝大部分求解器接受 SMT-LIB 文本格式作为输入。但许多求解器还有 **native C++ API**，可以构造内部数据结构、跳过 SMT-LIB 解析、利用某些只在原生 API 暴露的特性。

例：CVC5 的 `mkTupleSort` / `APPLY_SELECTOR` 让用户直接构造代数数据类型 tuple，避免在 SMT-LIB 里把它平铺成多个独立字段。对于嵌套 struct/array 类型，原生 datatype 编码可能比 SMT-LIB flattener 快几倍。

**经验法则**：

- 默认 SMT-LIB（兼容性好、易调试）。
- 当你的问题域恰好对应某个求解器的原生扩展（datatype、字符串、浮点……）时，启用原生 API。

### 4.3 增量与 k-induction：base / forward / inductive 三阶段成本

**增量 BMC** 不一次性求解大公式，而是逐步加深：先求 k=1 的（小）公式，UNSAT 后加深到 k=2，重用之前的部分结论。求解器内部保持 push/pop 上下文，避免每轮重置。

**k-induction** 进一步加入归纳证明：

- **Base case**：所有 k 步以内的执行路径都满足断言。
- **Forward condition**：超出 k 步后路径都"消失"（典型用于循环 — 假设循环最多执行 k 次）。
- **Inductive step**：假设第 k 步成立，证明第 k+1 步也成立。

后两步成本通常远高于 base case：

- **forward** 在 dispatcher 风格（`while(nondet) { handle_call(); }`）中**永远不会成立**（loop 永远可以再迭代一次），所以这一步浪费时间在不可能的目标上。
- **inductive** 需要从最一般的 hypothesis 出发证明 step——求解器要面对所有可能的 k 步状态，VCC 数量 O(2^k) 增长。

**优化经验**：

- **dispatcher 风格的程序自动禁用 forward condition**——它注定不会成立，跳过节省大量时间。
- **当 base case 已经发现反例**，立即终止（k-induction 总流程可能被设计成"一旦反例出现就停"）。
- **inductive step 的 SSA 大小**通常是 base case 的 100×+。如果 base case 已经几秒，inductive step 数分钟乃至 timeout 都是常态。

---

## 5. 取舍 (Trade-offs)

工程上的"完美 sound + complete + 高效"往往是不可达三角。明智的取舍能让工具在实际使用中可用。

### 5.1 Precise modeling — 假设上限

**问题**：动态数组、链表、合约实例数等没有静态上界。理论上要建模到无穷。实践中绝大部分 bug 在很小的状态空间就能找到。

**模式**：暴露一个 `--precise-modeling` 之类的开关，假设某些维度的上限（如"合约实例数 ≤ 16"）。低于上限的所有行为都精确建模；超出上限时给出警告或 unsound 标记。

**适用标准**（实战经验）：

> 只有"**truly extreme cap**"（真正极端的上限）才适合做开关。如果一个小型现实测试就能命中这个上限并暴露 bug，那它**不是**合理的上限——应该把上限调高甚至取消，或重新设计建模。

例：合约实例数 16。一个真实的 DeFi 协议很少在一次交易里创建 17 个合约实例；如果某个测试**故意**创建 17 个来 stress 工具，那个测试不代表真实场景。**让一个真实场景永不命中的上限**承担"精确建模"的代价是合理的。

**反例**：动态数组上限 5。常见的列表长度在 10-100，5 太小，会被现实测试命中。这种上限不应该做成 trade-off 选项。

### 5.2 Sound over-approximation：nondet 替代复杂状态

某些操作（外部调用、密码学哈希、低层 assembly）的精确建模代价巨大。**用 nondet 替代是 sound 的过近似**：

- nondet 表示"任何值都可能"
- 求解器看到 nondet 后会探索所有可能值，包括最不利的
- 如果即使在最不利情况下断言都不被违反，证明就成立
- 反过来：如果 nondet 让一个本来不可能的反例成立，会出现**虚假反例 (spurious counterexample)**，需要人工 review

**适用场景**：

- 外部库的不透明接口（合约调用、系统调用）
- 模拟无法精确建模的物理操作（时钟、随机数、网络）

**风险**：过近似过强会让证明 fail，falsification 找出虚假反例。设计时给"关键 invariant"留有专用的精确建模，nondet 留给真正不能精确的部分。

### 5.3 Under-approximation 的危险：silent truncation

**欠近似** = 把状态空间缩小，使得在缩小后的空间里成立不代表原问题成立。

**最常见的反模式**：`--unwind N --no-unwinding-assertions`（或同类组合）。这个组合的语义是"所有循环最多展开 N 次，超出的路径**静默截断**（不报错）"。

危险：**用户以为程序被完整验证，实际超出 unwind 上限的路径根本没被检查**。如果 bug 在 N+1 步处发生，工具会自信地报告"无 bug"。

**正确做法**：

- **找漏洞**模式（bug-finding, falsification）：用欠近似可以接受——找到的反例真，找不到不代表无 bug。
- **证明无漏洞**模式（verification）：必须 sound；要么用 unwind assertion（超出时报失败），要么用归纳。

> **核心 takeaway**
>
> Sound 与 unsound 不是开关而是 spectrum。**bounded soundness** + 显式 unwind assertion 通常是工程上的甜区：在用户给定的深度内 sound，超出时显式提示。**最危险的设计是"看起来完整但实际静默丢路径"**。

---

## 6. 空间换时间 —— 在形式化验证中并不总是有效

### 6.1 直觉 vs 现实

经典的程序优化里，空间换时间是有效的：

- 哈希表：用 O(n) 空间换 O(1) 查找
- Memoization：缓存历史结果避免重算
- Lookup table：预计算函数值

这些技巧在**有运行时**的程序里能加速。但在**编译期分析**的形式化验证里，它们通常**让事情变慢**。

### 6.2 为什么缓存在 BMC 中往往是反模式

考虑一个想法："频繁访问的 mapping 加一个 cache 层，避免每次都走完整 SMT array select。"

实现：
```
cache_entry = ?
mapping_get(key) {
  if (cache_key == key) return cache_value;
  cache_key = key;
  cache_value = real_mapping[key];
  return cache_value;
}
```

在 SMT 编码后会发生什么？

- `cache_key == key` 是一个新的 if-then-else，每次调用都展开成 ITE 链。
- `cache_value` 是一个新的全局符号变量；每次写都生成 SSA 赋值，每次读都涉及 path constraint。
- 求解器原本只面对 `mapping[key]` 一条 array select；现在多了 cache 路径上的 5+ 条公理。
- 切片器无法消除——因为 cache 是全局状态，可能影响后续读。

**净效果：求解器要做更多工作来推理 cache 一致性**。原本的"加速"在静态分析里变成"加重负担"。

**类似的反例**：

- **过大的 lookup table**：256 entries × 256-bit 值 = 65536 bit 的 SMT 状态，每次访问都是一次 array select。如果原始函数本身简单（如 `sha256` 的 round 函数），直接用 nondet 或 uninterpreted function 反而便宜。
- **预分配的"对象池"**：如果对象数量不大，让求解器自由分配反而比预分配 + 索引管理便宜。

### 6.3 何时空间换时间在 BMC 中有效

并非所有"加内存"的优化都是反模式。**关键标准**：

> **替代物必须是公理 (axiom)，不是状态变量 (mutable state)**。

合格的"空间换时间"：

- **Memoization 用 SMT array + 哨兵零初始化**（见 2.5）：array 本身是单条结构，求解器对它有专用 decision procedure；额外的 distinctness 公理是一次性消化，不进入 path。
- **编译期常量预计算**：把运行时计算挪到前端，求解器看到的是字面量，零成本。
- **Lazy allocation 退化为 eager flat encoding**（见 2.4）：用 bit-shift 索引代替嵌套 array，状态量没变但求解器看到的结构更简单。

不合格的："空间换时间"：

- 任何在 IR 里加新全局变量、新条件分支、新切片器盲区的方案。
- 任何让求解器的 path constraint 变长的方案。

> **核心 takeaway**
>
> 在形式化验证中，**让求解器看到尽可能少的"中间状态"**。如果你在前端加一层缓存，问问自己：求解器看到的 path 是变短还是变长？变长就是反优化。

---

## 7. 抽象设计原则

把全文核心 takeaway 汇总成可迁移的 10 条：

1. **建模正确不等于建模高效**。同语义的两个模型，求解时间可能相差万倍。
2. **每条 SSA = 一条 SMT 公式等式**。优化 = 减少 SSA 数量。
3. **类型表示要贴合求解器原生理论**：用 native bitvector，少用结构体代理；用 native array，少用嵌套数组。
4. **常量必须在前端就 fold**。任何让常量经过运行时 helper 往返的降级路径都是杀手。
5. **编译期常量边界 >> 符号边界**。固定步数的循环要么硬展开，要么用 macro 展开，避免靠 `--unwind` 配置。
6. **切片器友好的 IR**：尽量用局部变量、避免无谓的全局写、避免不必要的副作用调用。
7. **求解器选择是建模的一部分**：你的问题域决定该用哪个 solver；让工具自动 hint 选择。
8. **Bounded soundness 通常足够**：精确建模到一个"真实场景永不命中"的上限，比追求无界完美高效得多。
9. **空间换时间在 BMC 中常常是反优化**：缓存只在它"是公理而非状态"的时候有效。
10. **测量优先**：raw SSA / post-slice SSA / VCC / encoding time / solving time 这五个数字告诉你瓶颈在哪。直觉常常错。

---

## 8. 调试与诊断方法

### 8.1 关键指标的层次

任何 BMC 工具都应该在每次求解前后输出这五个数字：

```
Symex completed in: 27.825s (49155 assignments)
Slicing time: 0.577s (removed 35458 assignments)
Generated 42 VCC(s), 42 remaining after simplification (13697 assignments)
Encoding to solver time: 22.952s
Solving with solver Bitwuzla 0.8.2: ...
```

**逐项含义**：

- `Symex 49155 assignments`：原始 SSA 总数。**反映前端+中间件的整体规模**。
- `Slicing removed 35458, 13697 left`：切片删了 72%，剩 28%。**切片有效性**——如果剩比例很高（>50%），可能是模型有大量"虚连"全局状态阻碍切片。
- `42 VCC`：断言数量。**用户语义复杂度**。
- `Encoding 22.95s`：SSA → SMT 翻译。**慢于 1-2 秒说明 SSA 量级巨大或类型转换复杂**。
- `Solving`：纯求解时间。**这才是求解器的真实工作量**。

### 8.2 等价语言对比作为后端 baseline

诊断"前端 vs 后端谁慢"最有效的办法：**把同一段语义用一种已知后端友好的语言重写**。

实战案例：某个 Solidity 合约 `napp_struct_multifield_fail` 在 BMC 工具里 timeout（symex 28s, 49K SSA）。猜测是后端 SMT 求解器的瓶颈。但用 C++ 写一个**byte-identical 等价**的程序（同样的 struct 嵌套数组、同样的 push/pop、同样的 dispatcher harness）：

| | symex 时间 | raw SSA |
|---|---|---|
| Solidity 版本 | 28s | 49,155 |
| C++ 等价版本 | 0.044s | 902 |

**640× SSA 膨胀**。这立即告诉我们：**问题不在后端**——同样的 SMT 工作量，C++ 前端只产生 1/640 的 SSA。瓶颈在 Solidity 前端的降级逻辑。

后续诊断聚焦于"为什么 Solidity 前端把同一段语义生成了 640 倍 IR"，定位到了 `bytes32(uint256(N))` 嵌套 cast 的 round-trip bug，修复后 raw SSA 降到 32K（-34%）。

> **核心 takeaway**
>
> 诊断 BMC 工具瓶颈时，**用一种你信任的低成本语言写一份等价程序**，对比关键指标。这能立刻分清"前端慢"还是"后端慢"。前者在你的工具里能修；后者通常要换求解器或改建模。

### 8.3 SSA-cost histogram：定位热点

在 SSA 切片完成后、SMT 编码开始前，遍历所有 live SSA，按 source location（哪个函数）分组求和。打印前 10-20 名。

伪代码：
```
histogram = {}
for ssa_step in target_equation.steps:
  if not ssa_step.is_assignment(): continue
  if ssa_step.is_sliced_out: continue
  fn = ssa_step.source.function_name
  histogram[fn] += 1

ranked = sorted(histogram.items(), key=lambda x: -x[1])
for fn, count in ranked[:20]:
  print(f"  {count:>6}  {100*count/total:.1f}%  {fn}")
```

**这 20 行代码价值连城**。每次想优化，先跑一次直方图。前 5 名通常吃掉一半成本。优化前后对比直方图，**精确量化**改动效果。

---

## 9. 结语：建模即设计

形式化验证的"建模"不只是"把语言翻译成逻辑"，更是一个**设计活动**：

- 设计每个数据类型的 SMT 投影
- 设计每个语言构造的 IR 降级路径
- 设计跨阶段（前端 / 中间件 / 后端）的成本分配
- 设计哪些维度精确建模、哪些过近似
- 设计哪些工具自动选择、哪些用户配置

差的设计让一个简单测试 timeout；好的设计让复杂合约毫秒级出结果。**这种差距来源于对每个层级成本的清晰认识，而非某种神秘的"工程功夫"**。

希望读完本文后，下一次遇到"我的 BMC 工具好慢"时，你不再问"求解器是不是太弱"，而是问：

1. SSA 数量是多少？切片切掉了多少？
2. 哪个函数贡献了 most live SSA？
3. 求解时间是不是远小于 symex+encoding 时间？
4. 我的类型表示是不是直接落在求解器原生理论上？
5. 我的常量在前端就 fold 了吗？
6. 我的循环边界是常量还是符号？
7. 这个 trade-off 有没有被一个真实测试命中过？

带着这些问题去看你的工具的 IR / 公式 / 求解器输出。**优化建模的本质，是让逻辑公式成为程序员的合作伙伴，而不是负担**。

---

## 附录 A：本文涉及的关键技术清单

为了快速回顾，这里列出本文讨论的所有具体技术，每条一行：

1. **类型选择**：用 native bitvector 表示固定长度二进制串。
2. **常量折叠**：嵌套类型转换中，每层独立处理 hint，不透传外层期望。
3. **库函数 vs 内联**：常量边界小操作内联展开，大状态操作库函数。
4. **Macro unroll**：`STEP(0); STEP(1); ...; STEP(31)` 替代符号边界 for 循环。
5. **嵌套数组展平**：`(zext(k) << bits) | offset` 索引代替嵌套 array。
6. **Memoization via SMT array**：哨兵零初始化 + 一致性公理。
7. **Per-pair distinctness assume**：让 SMT array memo 同时具有"无碰撞"性质。
8. **Typed-element store**：`arr[i] = elem` 单条 SSA，避免字节级 memcpy。
9. **Strong update via VSA**：用 VSA 让单点写入对应单条 SMT array store。
10. **Field-level havoc**：循环不变量推导只 havoc 实际写过的字段。
11. **Fresh symbol freezing**：跨帧表达式先冻结到 fresh SSA 变量。
12. **Eager static analysis vs lazy runtime check**：能在前端做的不留给后端。
13. **Forward-condition auto-disable**：dispatcher 风格的程序跳过 forward 阶段。
14. **Realloc copy-count cap**：常量大小用精确值，符号大小用配置上限。
15. **Inline modifier substitution**：函数修饰符直接替换源码，避免函数调用开销。
16. **Compile-time field write tracking**：循环体扫描决定 havoc 字段集。
17. **Wide-BV concat for hash inputs**：变宽 array 桶替代固定宽度溢出。
18. **Multi-arg fold (FNV-style)**：order-sensitive 多参数哈希用 multiplicative fold。
19. **Address-replicated XOR fold**：每实例 address 与索引交叉防碰撞。
20. **Per-mapping flat encoder**：替代全局 mapping pool 的索引展平。
21. **Solver auto-hint**：基于问题特征自动选择 Z3 / Bitwuzla / CVC5。
22. **Native datatype tuples**：CVC5 mkTupleSort 替代 SMT-LIB flatten。
23. **Bounded scope soundness**：确定性的有限上限优于无界的精确。
24. **Approximation warning visibility**：每次过近似留下显式日志。
25. **SSA-cost histogram**：按函数归类活 SSA，定位热点。
26. **Equivalent-language baseline**：用 C/C++ 等价程序对比定位瓶颈。
27. **Slicer-friendly IR**：局部优于全局，pure 函数优于副作用。
28. **Loop unwind clamp**：把符号循环边界硬限制到一个常量上限。
29. **Layered solving (k-induction)**：base / forward / inductive 三阶段，各自不同优化策略。
30. **Empirical measurement first**：永远先测量再优化，直觉是不可靠的。

---

## 附录 B：进一步阅读

经典与现代的 BMC / SMT 文献：

- *Decision Procedures: An Algorithmic Point of View* (Kroening & Strichman, 2008) —— SMT 求解器内部算法。
- *Handbook of Satisfiability* (Biere, Heule, et al., 2009) —— SAT/SMT 综述。
- *Satisfiability Modulo Theories* (Barrett, Sebastiani, Seshia, Tinelli, 2009) —— SMT 理论与工具。
- *CBMC: Bounded Model Checking for ANSI-C* —— BMC 教科书级实现。
- *Verified Software: Theories, Tools, Experiments* (VSTTE 系列会议) —— 工业实践。
- *CAV / TACAS / SAT* 会议每年的 SMT 求解器竞赛 —— 不同求解器在不同 benchmark 上的性能数据。

工具源码（学习不同优化策略）：

- **Z3** (`Z3Prover/z3`) —— 通用 SMT，干净的 C++ API，源码可读。
- **Bitwuzla** (`bitwuzla/bitwuzla`) —— bitvector 专用，看 BV decision procedure 实现。
- **CVC5** (`cvc5/cvc5`) —— QF_ABV、native datatype 实现的标杆。
- **CBMC / ESBMC** —— 工业级 BMC 工具，前端到后端的完整 pipeline 可读。

---

*本文从一段实际 BMC 工具的优化经验中提炼写就。具体工具可能不同，但其中讨论的原则、模式、陷阱都是 tool-independent 的——希望读者能在自己的工具上找到这些模式的具体投影。*
