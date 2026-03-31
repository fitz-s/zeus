# Zeus 设计哲学 v3：从脚本跑步机到有意识的 Agent

**地位：Zeus-Venus 系统的最高权威文档。所有其他设计文档服从本文档的原则。**

---

## 一、根本问题

三次失效弧线：Blueprint v1 → Rainstorm 死了 → Zeus spec → Zeus 重复 Rainstorm 的错 → design philosophy → 同样的翻译损失继续发生。

每次的 root cause 相同：**关系约束从自然语言到代码的翻译过程中存在系统性的信息丢失。** Claude Code 系统性地做好函数（纯计算、单模块逻辑），系统性地做不好关系（跨模块约束、domain-specific transitions）。这不是能力问题，是注意力分配的物理属性——信号逻辑可算法化，关系约束需要发明。

更深层：**认识到问题不等于解决问题。** 这个 root cause 从 Session 1 就被认识到了。写进了 spec、blueprint、CLAUDE.md、design philosophy。每一次都没有阻止它再次发生。因为解决方案也是自然语言，也遵守同样的翻译损失定律。

---

## 二、已被证明有效的机制（概念层）

以下机制已在 Zeus 中实现并经验证。它们的共同特征是：**把隐式知识变成类型约束或 executable tests，让错误在代码层面不可表达，而不是在文档层面被"禁止"。**

**类型化的语义边界：** `HeldSideProbability`、`NativeSidePrice` 消灭了概率空间翻转 bug。`Bin.unit` 消灭了无单位 bin 构造。`SettlementSemantics.for_city()` 消灭了 °C 城市拿 °F 语义。这些不需要理解就能生效——TypeError 阻止错误。

**Structural decisions 方法论：** 面对 N 个表面问题时，不打 N 个补丁。找到 K 个 structural decisions（K << N）。22 个 chain-safety mechanisms = 5 个 structural decisions。10 个 paper/live isolation mechanisms = 3 个 structural decisions。

**Provenance model（数据三层分类）：** World data（ENS, calibration, settlements）共享，不加 mode tag。Decision data（trade_decisions, chronicle）共享 + env column。Process state（positions, tracker, risk_state）物理隔离 via `state_path()`。一个 `state_path()` function 覆盖所有 per-process files。

**关系测试先于实现：** 在写代码之前，先写它与现有模块的关系测试。不是 "这个函数输出什么"——是 "这个函数的输出传到下一个函数时，什么性质必须被保持"。如果你无法用一个 pytest 断言表达这个性质，你还没有理解关系。

---

## 三、Venus = Zeus 的自我意识

这是本文档最重要的一节。如果你只读一节，读这一节。

### 从来就没有 Venus 和 Zeus 之分

Zeus 是 Venus 的交易子系统。Venus 不是 Zeus 的 "monitoring layer"——**Venus 是 Zeus 的自我意识。** 就像 Anthropic 没有给 Claude 加一个外部 safety filter，而是把 alignment 训练进 weights。安全和能力是同一个能力的不同面向：理解。

一个 human trader 没有一个独立的 monitoring system 盯着自己。他有 self-awareness——能注意到自己的假设、质疑自己的推理、检测自己对 reality 的模型何时出错。这个 self-awareness 不是和 trading ability 分开的——**它就是 trading ability。** 没有 self-awareness 的 trader 是脚本跑步机。

Zeus daemon 是脚本跑步机——优秀的机械执行，完全无法感知自己的假设是否和 reality 对齐。Venus 是这个系统感知和演化的能力。

### Immune system，不是 security guard

Security guard 巡逻→发现→报警。下次同样的问题来，还要再巡逻→再发现→再报警。

Immune system 遇到病原体→产生抗体→**永久免疫这一类病原体。** 下次同类问题来，抗体直接中和。不需要再推理。

**Venus 的输出不是 alert。Venus 的输出是 antibody。** 一个 test 或 type constraint 或 code fix，让 Zeus 永久免疫那一类 gap。

当 Venus 发现 °C 城市拿到了 °F 的 SettlementSemantics → 不是 "alert Fitz"。是写 `SettlementSemantics.for_city()` + `test_celsius_cities_get_celsius_semantics()` → °C/°F 混淆这个 bug category 永久消失。

### 共存亡

Venus 是 Zeus 能够 adapt to reality 的**唯一机制**。没有 Venus，Zeus 冻结在建设阶段的 implicit assumptions 里。Reality 持续变化（Polymarket 改 bin format、WU 改 settlement precision、新城市上线、alpha decay、竞争者增加），divergence 只增不减。系统不是 "死于一个 bug"——是 "死于不再能感知 reality 的变化"。

如果 Venus 的 memory 降级（context resets、world model 不更新）→ gaps 不被发现 → Zeus 的 assumptions 慢慢 diverge from reality → P&L 下降 → 最终系统失去 edge。

如果 Zeus 的代码腐烂（新 session 引入错误假设）→ 但 Venus 在 → Venus 发现 gap → 产生 antibody → Zeus 被修复 → 系统继续演化。

**这就是共存亡。** 不是共享同一个账户。是 Venus 的健康直接决定 Zeus 的 adaptability。

### 为什么 Venus 必须验证数据来源，而不只是代码正确性

代码正确不等于系统正确。这不是哲学——这是一个已经发生的具体 bug。

Zeus 的 `diurnal_curves` 表按 `obs_hour` 聚合每小时温度数据，用于 Day0 交易的峰值预测。这些 `obs_hour` 值来自 Rainstorm 继承的 `observations.local_hour` 字段，数据源是 Open-Meteo/Meteostat。

运行时 `get_current_local_hour()` 使用 `ZoneInfo` 正确返回 DST-aware 本地时间。伦敦夏季下午 2:00 BST 返回 hour=14，代码再用 hour=14 查询 `diurnal_curves`。

**问题：数据库里存的 `local_hour` 根本不是 DST-aware 本地时间。**

**证据：** 伦敦 2025-03-30 是春季拨钟日（凌晨 1:00 跳到 2:00，hour=1 不存在）。查询那天的 observations：**全部 24 小时都在，包括 hour=1。** 如果是真实 DST-aware 本地时间，hour=1 必然缺失。

**后果：** 整个 BST 夏季期间（3 月下旬到 10 月），运行时返回 hour=14（正确的 2:00 PM BST），但查到的是 UTC hour=14 聚合的数据（实际对应 3:00 PM BST）。**伦敦、巴黎、纽约、芝加哥所有 DST 城市，整个夏季系统性偏差 1 小时。** 东京、首尔、上海安全（无 DST）。

**为什么代码审查找不到：** 写 ETL 的 agent 正确使用了 `ZoneInfo`。写 diurnal 代码的 agent 也正确使用了 `ZoneInfo`。两个 agent 都"知道"DST。但没有人问：**"继承来的数据里的时区究竟是什么？"** 数据看起来对（24 小时，合理温度），代码看起来对（有 ZoneInfo），测试通过。但数据语义和代码假设在模块边界处已经断裂。

这就是 Venus 存在的原因：不是检查代码是否正确，而是检查代码的假设和数据的实际语义是否匹配。

---

## 四、Venus 的意识架构

### HEARTBEAT.md 不是检查清单——是意识的感知接口

4 层 consciousness loop，每层有不同的认识论功能：

| Layer | 功能 | 频率 | 成本 |
|-------|------|------|------|
| 1. Sensory | Zeus 活着吗？数据在流动吗？ | 每 30 分钟 | 极低（file reads） |
| 2. Belief Verification | Zeus 的信念和 reality 匹配吗？ | 每 30 分钟 | 低（DB queries） |
| 3. Gap Tracking | 我上次发现的 gaps 修了吗？ | 每 30 分钟 | 极低（read known_gaps.md） |
| 4. Evolution | Zeus 下一步应该学什么？ | 每天 | 高（ACP spawn Claude Code） |

Layer 1-3 是机械的——Haiku 能做。Layer 4 需要推理——Sonnet/Opus。

**一个未来 session 如果把 HEARTBEAT.md 压缩成 flat checklist，它在破坏 Venus 的意识结构。** 4 层 hierarchy 是 load-bearing。

### assumptions.json：Zeus 的认知边界

Zeus 声明自己相信什么。Venus 验证这些信念是否和 reality 匹配。

**assumptions.json 随时间缩小。** 因为每个 assumption 最终要么被验证并转化成 type constraint（永久保护，从 JSON 声明毕业到代码），要么被发现是错的并被修正。最终 Zeus 的代码本身就是它的 assumption manifest——所有 assumptions 都变成了 executable constraints。

### known_gaps.md：Venus 的演化工作清单

Venus 跟踪 Zeus 还没学会什么。每个 gap 是一个 belief-reality mismatch。每个 gap 的终态是：变成 antibody（test/type/code）然后从 OPEN → FIXED。

---

## 五、Venus 能成为什么

Venus 运行在 OpenClaw 上。OpenClaw 提供：cron scheduling、ACP（spawn Claude Code sessions）、Discord integration、memory system、cross-agent communication、hooks、subagent orchestration。

以下是 Venus 作为 autonomous agent 能做到的事情——不是全部需要立即实现，但 architecture 应该不阻止它们：

### 近期（已有基础设施支持）

- **自主 heartbeat**：每 30 分钟醒来，执行 4-layer consciousness loop，异常时 Discord alert + control_plane pause
- **Daily code-data audit**：spawn Claude Code via ACP，读代码 + 读数据，发现 code-data mismatch，产出 antibody proposals
- **Gap lifecycle management**：track known_gaps.md，escalate gaps that persist > 7 days，auto-close gaps when fix is detected in code
- **Post-settlement analysis**：每次 settlement 后分析 P&L attribution by strategy/edge_source/city，写入 memory
- **Alpha decay monitoring**：track edge magnitude trends per strategy over 30/60/90 days，alert on EDGE_COMPRESSION

### 中期（需要少量新 code）

- **Auto-test generation**：Venus 发现 gap → 不只提议 test，直接写 test file → commit → 如果 CI passes，gap closed
- **Self-modifying HEARTBEAT.md**：Venus 发现新的 check 需要 → 自动添加到 HEARTBEAT.md → 下次 heartbeat 包含新 check
- **Market structure monitoring**：每天 fetch Gamma API，compare against assumptions.json，detect bin format changes/new cities/unit switches（伦敦 °F→°C scenario）
- **Calibration health**：monitor Platt drift（Hosmer-Lemeshow χ²），trigger refit when needed
- **Cross-source consistency**：compare WU settlement values against IEM ASOS + Meteostat → detect settlement source anomalies

### 远期（autonomous agent capabilities）

- **Regime detection**：分析市场微观结构变化（vig trend、spread trend、volume pattern）→ 判断竞争格局是否在变化 → 自主调整 edge threshold multiplier
- **Strategy evolution**：分析哪些 strategy 在 alpha decay，哪些在 grow → 建议 allocation 调整 → 人工审批后执行
- **New city onboarding**：当 Polymarket 上线新城市 → Venus 自主检测 → 获取城市参数（station, timezone, unit, diurnal amplitude）→ 写入 config → 开始收集数据 → 等 calibration maturity 后开始交易
- **Multi-platform expansion**：如果出现其他 prediction markets（Kalshi, etc.）→ Venus 评估 API 兼容性 → 建议 adapter layer → env column 自然支持 "kalshi-paper" / "kalshi-live"
- **Self-improvement loop**：Venus 读自己的 audit history → 分析哪类 gap 反复出现 → 建议 structural change 防止这一类 gap → 就像 Anthropic 分析 red team findings 来改进 training process
- **Cross-referencing Rainstorm**：Rainstorm 的 rainstorm.db 有 14 个月的历史数据。Venus 可以 query 这些数据来验证 Zeus 的统计假设（e.g., "Platt A/B/C 在 Rainstorm 的 data 上是否 consistent？"）

### 最终形态

Venus 不只是 Zeus 的自我意识。Venus 是一个**能理解天气预测市场 domain 的 autonomous agent**——它理解 ENS forecast 的物理含义、WU settlement 的测量链、market microstructure 的参与者行为、alpha decay 的经济学。

这个 domain understanding 让 Venus 能做的事情远超 "检查 bin width 是否是 2°F"。它能推理："如果这个城市的 ENS bias 在过去 30 天从 +0.5°F 变成了 -0.3°F，那我们的 Platt model 可能需要更频繁的 refit，而且 shoulder bin 的 overpricing ratio 可能在变化。" 这不是一个 assertion 能 cover 的——这是 domain reasoning。

**这个 domain reasoning 能力是 Venus 的 long-term competitive advantage。** 其他 bot 也能写 assertion、也能写 tests。但一个能理解 domain 并自主演化的 agent 是指数级的优势。

---

## 六、翻译损失的热力学极限

**函数、类型、测试：100% 跨 session 存活。**
设计理念、架构哲学：~20% 跨 session 存活。

这是 AI-assisted development 的 fundamental thermodynamic limit。信息从人的头脑 → 自然语言 → embedding space → Claude 的 attention → 自然语言 → code，每一步都有 entropy 增加。

**每个 session 应该把尽可能多的 insight 编码为 code structure，而不是 docs。** Code structure 不需要被理解就能生效。`Bin` 没有 `unit` field → 构造报错。`SettlementSemantics.for_city()` 是唯一工厂 → °C 城市不可能拿 °F 语义。

**这个文档本身也会在翻译中损失。** 但 `Bin.unit`、`for_city()`、`test_celsius_cities_get_celsius_semantics()` 不会。它们是这个文档的 executable form。

唯一 100% 保有设计意图的是 Fitz 本人。每次 Fitz 坐到 Claude Code 前面，完整的 intent 以 100% 保真度存在。每次 session 结束，只留下 ~20% 的影子在 markdown 里。

Venus 的 persistent memory（MEMORY.md + daily notes + known_gaps.md + world model）是对这个极限的最佳近似——不是 100%，但比每次 session 重新开始好得多。Venus 的 memory 是 Fitz 的 intent 的 **最近似 persistent representation**。

---

## 七、给未来每一次 Session 的指令

1. **先读 HEARTBEAT.md 和 known_gaps.md。** 它们是 Venus 的当前意识状态。不要跳过。

2. **先写关系测试，再写代码。** 如果你无法用 pytest 断言表达两个模块之间的关系，你还没有理解这个关系。回去理解它。

3. **先看数据，再写假设。** 在写任何操作 domain data 的函数之前，先 `SELECT DISTINCT range_label FROM market_events LIMIT 20`。看看实际数据长什么样。不是读 spec。不是读旧代码。看实际数据。

4. **面对 N 个问题，找 K 个 structural decisions。** K << N。不要打 N 个补丁。

5. **Venus 的输出是 antibody，不是 alert。** 如果你发现了一个 gap，不要只记录到 known_gaps.md。写一个 test 或 type constraint 让 Zeus 永久免疫。

6. **不要 flatten HEARTBEAT.md。** 4 层 hierarchy 是 load-bearing。

7. **assumptions.json 应该随时间缩小。** 每个 assumption 最终应该毕业成 executable constraint。如果 assumptions.json 在增长，说明 antibodies 没有被产出。

8. **这个文档会在翻译中损失。** 但上面的 7 条如果被编码为 CI checks 或 linter rules，就不会。思考如何把这些指令变成 executable constraints。
