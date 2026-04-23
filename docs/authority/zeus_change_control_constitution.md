# Zeus Change-Control Constitution

版本: 2026-04-23
Status: durable deep governance constitution
Default-read: no
Use when: authority rewrite, kernel boundary changes, anti-entropy governance, packet design, long-horizon safety doctrine
适用对象: 人类负责人、Claude Code、Codex、GPT 类 coding agent、reviewer、operator

This file explains why Zeus is governed this way. It is not the default
delivery entrypoint; use `docs/authority/zeus_current_delivery.md` for current
delivery law.

---

## 0. 目的

这份文件不定义 Zeus 做什么交易。
它定义 **任何未来改动如何不破坏 Zeus 的内核**。

Zeus 的核心风险不再是“缺少想法”，而是：

1. 自然语言里的宏观意图，在 LLM 实现时被压缩成局部补丁；
2. 语义边界在分解、编码、review、refactor 中持续流失；
3. 系统表面越来越高级，但 authority / attribution / lifecycle / protection 反而重新变松；
4. 新功能和重构在数月后悄悄打穿最基础的原子规则。

这份宪法的目标不是提高写代码速度，而是让 Zeus 在长期 LLM coding 下仍能保持：

- 单一 authority
- 单一 lifecycle grammar
- 单一 governance key
- 单一 point-in-time learning truth
- 单一 protection substrate
- 单一 unit / probability semantic discipline

---

## 1. 核心判断

**不能依赖任何 LLM 一次性“理解并正确实现整个最终形态”。**

可以依赖的不是模型的聪明，而是：

- 内核冻结
- 可机器检查的语义约束
- 原子 work packet
- 限定变更区域
- 事务边界
- 架构回放与 parity
- CI 级别的禁止规则

正确目标不是：
> 让 Claude/Codex 一次性写出完美系统。

正确目标是：
> 让任何 Claude/Codex/其他 LLM 即使不完美，也**无法静默破坏核心架构**。

---

## 2. Zeus 的内核分区

未来所有改动都必须先判断自己落在哪个分区。

### K0 — Frozen Kernel（冻结内核）
这些层不允许自由改写，只允许在明确 packet 下做受控演进：

- semantic value types（Temperature / TemperatureDelta / HeldSideProbability / NativeSidePrice 等）
- canonical lifecycle grammar
- canonical authority write path
- strategy_key governance grammar
- point-in-time snapshot semantics
- event append + projection transaction boundary
- unit / probability / direction 边界

### K1 — Governance Layer（治理层）
可以演进，但必须服从 K0：

- RiskGuard policy substrate
- control overrides
- strategy gating / allocation multipliers / threshold multipliers
- reconciliation semantics
- operator command durability

### K2 — Product / Runtime Layer（产品运行层）
可以中速演进：

- CycleRunner orchestration
- monitor refresh
- status summary projections
- diagnostics
- migration helpers

### K3 — Extension Layer（扩展层）
可模块化变动，但不得反向侵蚀 K0/K1：

- signal modules
- feature add-ons
- external integrations
- analytics views
- experiment harnesses

### K4 — Experimental / Disposable Layer（试验层）
必须与主系统隔离：

- notebooks
- one-off scripts
- temporary backfill diagnostics
- ad hoc reports

规则：
**K3/K4 绝不能直接成为 authority 或 governance source。**

---

## 3. 不可打破的宪法级不变量

### CONST-01
退出意图不是经济关闭；经济关闭不是结算。

### CONST-02
一切 canonical lifecycle truth 必须由 append-only event + deterministic projection 表达。

### CONST-03
`strategy_key` 是唯一治理键。
`edge_source`、`discovery_mode`、`entry_method` 都是 metadata，不得竞争治理中心。

### CONST-04
point-in-time snapshot 永远优先于 hindsight snapshot。

### CONST-05
缺失/过期/限流/不可用数据是第一类事实，不是日志噪音。

### CONST-06
unit 语义必须由类型和静态/AST 检查共同保护，不能靠 reviewer 记忆。

### CONST-07
phase 只能来自受控 lifecycle grammar，不能由任意字符串赋值产生。

### CONST-08
risk / control 如果不能改变 evaluator / sizing / execution 行为，就是 theater。

### CONST-09
任何 shadow persistence 都必须有删除计划，否则默认禁止。

### CONST-10
LLM 输出永远不是 authority；只有 spec、tests、machine checks、runtime evidence 才是 authority。

---

## 4. 语义层设计：如何防止自然语言到代码的流失

任何架构意图在进入编码前，必须经过三层翻译：

### 4.1 Truth layer
问：**什么会成为更强的真相源？**

例：
“让 RiskGuard 变成 strategy-aware”

必须翻译成：
- `strategy_key` 是 canonical governance key
- `risk_actions` 是 durable policy truth
- evaluator 读取 resolved policy，而不是日志后分析

### 4.2 Control layer
问：**谁会因此真正改变行为？**

例：
- evaluator 会拒绝该策略
- sizing 会缩小 allocation
- execution 会进入 exit-only

### 4.3 Evidence layer
问：**运行时如何证明这是真的？**

例：
- gate `center_buy` 后，其它三策略继续工作
- 下一 cycle 中相关 NoTradeCase 被标为 risk gate
- DB 中能查到 active risk action

如果一个需求不能写出这三层，它还没有准备好进入编码。

---

## 5. LLM coding 的禁止幻想

以下三种方式都不可靠：

### 幻想 A：长 prompt = 可靠实现
错误。长 prompt 只能提高覆盖面，不能防止局部最优化和隐式语义丢失。

### 幻想 B：大模型足够强，所以不用原子 packet
错误。模型越强，越容易写出“看起来完整”的大补丁，但这类补丁最容易跳过边界语义。

### 幻想 C：tests 通过 = 架构落地
错误。tests 主要证明已知路径；架构要靠 authority、transaction boundary、grammar、policy actuation 来落地。

---

## 6. Work packet 纪律

### 6.1 一个 packet 只能有一个主目标
禁止这样的任务：
- “实现 ledger refactor”
- “完成 riskguard upgrade”
- “把 spec 全做完”

允许这样的任务：
- “新增 `position_events` schema 与 append/project 事务 API”
- “将 monitor exit 从 local close 改为 exit intent + pending exit lifecycle”
- “新增 strategy policy resolver，并让 evaluator 在 sizing 前读取”

### 6.2 每个 packet 必须回答
- 为什么现在做这个
- 为什么不采用另一个方案
- 它加强了哪个 truth surface
- 它削弱/删除了哪个旧 surface
- 它触碰了哪些 invariants
- 哪些文件允许改，哪些文件禁止改
- 需要哪些 tests / parity / runtime evidence

### 6.3 一个 packet 的默认上限
- 普通 packet: <= 4 个文件
- authority-bearing packet: <= 2 个 authority 核心文件

大于上限需要显式理由。

---

## 7. Repo 层面的硬边界

### 7.1 文件所有权原则

- `ledger.py` 只负责 canonical append/project
- `projection.py` 只负责 fold / rebuild
- `lifecycle_manager.py` 只负责 transition legality
- `cycle_runner.py` 只负责 orchestration
- `evaluator.py` 只负责 signal + decision，不负责 authority write
- `riskguard.py` 负责 policy emission，不负责 signal math
- `status_summary.py` 只读 projection，不得自持状态

### 7.2 不允许的跨层写法

- signal module 直接写 canonical lifecycle state
- evaluator 直接改 phase
- control plane 用内存 dict 充当 durable state
- analytics JSON 反向成为 authority
- UI / report 反向决定 runtime truth

---

## 8. AST / 语义 / 静态检查：必须机器化的约束

这是这份文件最重要的部分之一。

### 8.1 Phase 语义
禁止在内核外直接写：
- `state = "holding"`
- `state = "entered"`
- `phase = "..."`

必须通过：
- `LifecyclePhase` enum
- `fold_event(...)`
- `apply_transition(...)`

**AST/semgrep 规则：**
- 在 `state/ledger.py`, `state/projection.py`, `state/lifecycle_manager.py` 之外，禁止对 `phase/state` 赋字符串字面量。

### 8.2 Strategy 语义
禁止：
- fallback 到 `opening_inertia`
- 任意字符串作为 strategy
- 下游重新推断 strategy 覆盖 evaluator 输出

**AST/semgrep 规则：**
- 禁止 `strategy = "opening_inertia"` 作为 default/fallback
- 禁止在 evaluator 之外新建未知 strategy string
- 仅允许从 `StrategyKey` enum 取值

### 8.3 温度/单位语义
禁止：
- raw `float`/`int` 充当温度语义比较
- 跨单位直接比较
- 裸 `2.0`, `3.0`, `5.0` 等温度阈值与普通浮点直接比较

**AST/semgrep 规则：**
- 在 `types/temperature.py` 之外，发现 `temperature/spread/std/bias/noise` 相关变量与裸数字比较时报警
- 禁止 `unit == "F"` / `unit == "C"` 的散弹式分支在非授权模块扩散
- 强制温度阈值以 `Temperature` / `TemperatureDelta` 或显式 helper 构造

### 8.4 概率空间语义
禁止：
- 在 semantic boundary 外手写 `1 - p` 用于 held/native flip
- 在非 contracts 模块进行 raw probability-space flip

**AST/semgrep 规则：**
- 对命名包含 `p_`, `prob`, `price` 的变量出现 `1 - x` 做警报
- 白名单仅限 semantic contracts / explicit helper

### 8.5 Authority 写路径
禁止：
- 在 canonical path 外直接写 lifecycle current state
- event append 与 projection update 分离事务
- 多处 direct SQL 更新 current row

**AST/semgrep 规则：**
- 只有 `append_event_and_project` / `append_many_and_project` 可同时触达 `position_events` / `position_current`
- 禁止其它模块直接 `INSERT INTO position_events`
- 禁止其它模块直接 `UPDATE position_current`

### 8.6 Shadow persistence
禁止新增：
- 新的 `*_tracker.json`
- 新的 `*_summary.json` 被运行时读取作为 authority
- 新的“暂时先存一份”的并行状态面

**AST/CI 规则：**
- 新增 JSON 持久化文件必须显式标记 `export-only` 或 `compat-cache`
- 若被 runtime 读取参与决策，CI 失败

### 8.7 Memory-only control state
禁止：
- `_control_state = {}` 之类内存态成为控制 authority

**AST 规则：**
- control / risk / override 目录内禁止模块级 mutable dict/list 作为长期状态源

### 8.8 Import graph
禁止：
- K3/K4 反向 import K0 authority internals 然后修改 state
- experiments 目录 import canonical write internals

**静态规则：**
- 用 import-linter / custom graph 检查层级依赖

---

## 9. Machine-enforced architecture kit（建议落地）

建议新增一个目录：

```text
architecture/
  kernel_manifest.yaml
  invariants.yaml
  zones.yaml
  ast_rules/
  packet_templates/
```

### 9.1 `kernel_manifest.yaml`
描述：
- canonical enums
- authority tables
- governance keys
- lifecycle events
- legal transitions
- protected modules

### 9.2 `invariants.yaml`
每条 invariant 包含：
- invariant_id
- meaning
- spec_section
- canonical writers
- canonical readers
- tests
- AST rules
- runtime evidence query

### 9.3 `zones.yaml`
定义：
- K0/K1/K2/K3/K4 路径
- 哪些 zone 可以被哪些 packet 类型修改
- 哪些 zone 永远禁止 broad prompt 改写

### 9.4 由 manifest 反向生成
理想状态下，以下内容尽量从 manifest 生成而不是 hand-maintained：
- enums / constants
- semgrep rules
- test stubs
- packet checklist
- operator query docs

这一步非常关键：
**要减少“自然语言 spec -> 手写代码” 的自由度，用 declarative kernel 降低解释空间。**

---

## 10. CI 合法性门禁

未来所有 PR 至少分五层门禁：

### Gate 1 — Formatting / typing
- ruff / black / mypy / pyright

### Gate 2 — AST / semgrep / import graph
- forbidden raw phase assignment
- forbidden strategy fallback
- forbidden raw probability flip
- forbidden shadow persistence
- forbidden control-plane memory authority
- forbidden cross-zone imports

### Gate 3 — Invariant tests
- cross-module invariants
- phase legality
- authority replay parity
- point-in-time snapshot protection

### Gate 4 — Runtime evidence tests
- example lifecycle replay
- DB query proving new behavior
- parity if migration-related

### Gate 5 — Reviewer checklist
- 这个 patch 缩小还是扩大了 truth surfaces
- 增加了 actuation 还是只增加了 reporting
- 是否引入新的 shadow state
- 是否让 signal/system complexity 重新失衡

---

## 11. 变更分类制度

### Red change（红区）
涉及以下任一内容：
- authority
- lifecycle grammar
- strategy_key
- semantic contracts
- unit/probability semantics
- transaction boundary

要求：
- principal architect 批准
- 不可 broad prompt
- 必须 packet 化
- 必须带 schema/test/evidence/rollback

### Yellow change（黄区）
涉及：
- RiskGuard policy
- operator controls
- learning facts
- reconciliation policy

要求：
- packet 化
- 至少一名 integration reviewer

### Green change（绿区）
涉及：
- pure diagnostics
- derived reports
- extension modules
- experiments

要求：
- 不得反向触及 K0/K1

---

## 12. Review checklist（长期固定）

每次 review 必须回答：

1. 这次改动让 authority 更单一还是更分散？
2. 这次改动改变的是行为，还是只新增一个表面视图？
3. 这次改动是否把 point-in-time truth 保留了下来？
4. 这次改动是否引入了新的 fallback / re-inference？
5. 这次改动是否扩大了 taxonomy 漂移？
6. 这次改动是否新增 shadow persistence？
7. 这次改动是否要求 LLM 在多个高风险类别里同时动刀？
8. 这次改动是否能用一个具体 runtime query 证明其生效？

任何一项答得模糊，这个 patch 就不应合并。

---

## 13. 外部因素：容易被忽略，但会长期侵蚀系统

### 13.1 模型版本漂移
Claude/Codex/GPT 在几个月后的补全习惯、默认风格、错误类型会变。不能把“当前模型懂这个 repo”当长期资产。

### 13.2 检索偏置
未来 agent 可能先看到非 authority 文档、旧注释、旧 helper，而不是当前宪法。必须通过 file zoning + required reads + packet 绑定 authority。

### 13.3 局部 diff 最优化
LLM 天然偏好“最小改动让测试过”。这与 principal architecture 的需要经常冲突，因此必须有 packet 的 why-not 和 truth/control/evidence 三层。

### 13.4 reviewer 疲劳
如果没有 machine-enforced checks，长期项目里 reviewer 最终会默认相信“看起来合理”的 patch。

### 13.5 dependency drift
第三方 API、依赖库、SQLite schema、工具链变化会诱发“先加 fallback 再说”的坏习惯。宪法必须明确禁止 silent fallback 侵蚀 authority。

### 13.6 context truncation
长对话中，早期约束会被压缩掉。真正关键的限制必须放进 repo、CI、AST rules，而不能只存在于聊天历史。

---

## 14. Zeus 终局原则

Zeus 的终局不是“模型更复杂”。
Zeus 的终局也不是“系统有更多层”。

Zeus 的终局是：

- 核心 authority 极少且极硬
- 生命周期语法有限且不可随意变形
- 策略治理键唯一且不允许再推断污染
- 学习链条是 point-in-time 且可审计
- 保护系统是真正改变行为的，而不是看起来懂很多
- LLM 只能在受控走廊里写代码，而不能直接改写宪法

如果未来做到了这一点，算法和附加功能的模块化变动，就不会再轻易动摇整体系统。

---

## 15. 最终执行建议

对当前 repo 的实际建议不是继续写更多宏观文档，而是：

1. 保留 principal architecture spec 作为路线图
2. 把本文件落成 repo 内长期宪法
3. 新增 `architecture/` manifest 层
4. 把 AST/semgrep/import-linter/CI gate 真正装上
5. 从此禁止 broad vibe coding 直接改红区内核

**从这一刻开始，Zeus 不该再依赖“模型是否足够聪明”。**
它该依赖的是：

**内核冻结 + 机器门禁 + 原子 packet + runtime evidence。**

---

## 7. Zero-context agent routing（强制）

任何零上下文或弱上下文 agent 必须先阅读：

1. `architecture/self_check/zero_context_entry.md`
2. `architecture/self_check/authority_index.md`
3. `architecture/kernel_manifest.yaml`
4. `architecture/invariants.yaml`
5. `architecture/zones.yaml`
6. principal spec 中与任务直接相关的 section
7. 然后才允许读目标代码

若任务 packet 中 `required_reads` 未被满足，则禁止编辑。

---

## 8. Zone-based edit law

### 8.1 K0 edits
只允许：
- `schema_packet`
- `refactor_packet`
并且必须包含：
- spec 引用
- invariant 引用
- schema/manifest diff
- tests
- parity/replay evidence（如尚未可用，必须写 staged waiver）

### 8.2 K1 edits
允许：
- `feature_packet`
- `refactor_packet`
但必须明确说明对 evaluator / sizing / execution 的实际行为影响。

### 8.3 K2 edits
允许正常 packet，但不得越权定义新的 truth surface。

### 8.4 K3 edits
可以高频发生，但不得：
- 触碰 canonical schema
- 创建新 lifecycle phase
- 重新定义 strategy governance key
- 修改 point-in-time truth semantics

### 8.5 K4 edits
默认不可进入主路径；若要晋升，必须显式 promotion packet。

---

## 9. Negative permission doctrine

未来所有 human/LLM/coding-agent 都必须默认：

- **能改到 ≠ 允许改**
- **理解个大概 ≠ 获得 authority**
- **局部实现看起来合理 ≠ 系统语义合法**
- **tests 绿 ≠ 架构未被侵蚀**

因此：
- 没有 packet 的 broad refactor 默认非法
- 跨 zone broad edit 默认非法
- 对 K0 的 implicit semantic change 默认非法
- 没有证据束的 PR 默认不构成完成

---

## 10. Required evidence by change class

### Schema / authority change
- migration diff
- invariant references
- replay/parity output
- concrete example row
- rollback plan

### Governance / policy change
- policy resolution example
- evaluator effect example
- strategy-specific proof
- expiry/preference proof

### Math change
- no authority diff
- no schema diff
- replay/parity against captured snapshots
- invariant preservation statement

### Operator/control change
- restart survival proof
- durable storage path proof
- no memory-only durability proof

---

## 11. Long-horizon rule

很久很久以后，如果某个 agent 想加新功能、重构、迁移、替换模型、替换执行器、替换数据源：

它首先必须证明的不是“功能能工作”，而是：

1. 没有改变 K0 semantic atoms
2. 没有引入新的 shadow authority
3. 没有让 K3/K4 反向控制 K0/K1
4. 没有破坏 point-in-time truth
5. 没有让 operator 看到的 surface 与 canonical truth 分裂

否则，该变更即使表面正确，也被视为架构违规。
