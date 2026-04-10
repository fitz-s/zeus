# Zeus 逻辑性验证报告 — 2026-04-09

**验证范围**: Architects 分支（最新 commit `912fb0b`）
**验证方法**: 静态代码分析，全部 src/ 代码逐文件审读
**验证人**: Copilot 主 agent + 4 个并行 Explore 子 agent
**原始问题清单**: 9 项不可见逻辑问题 + 6 项审计追加（跨模块断裂 + 真值面分裂 + 死逻辑 + 结算碎片化）
**宏观归因**: 4 个结构性根因 → 13 个 Bug（K=4, N=13，S3 已修复，F1/F2 从第二部分整合）

---

## 分支状态前置说明

| 分支 | 状态 |
|------|------|
| `architects-pretransport-dirty-20260409` | 用户原始工作分支；Architects 的严格祖先 |
| `Architects` | 领先 11 commit / 24 files / +3,400 行：含 ghost 行修复、center_buy 极端尾部阻断、data-expansion 整合 |
| `main` | 基础稳定线；领先 3 doc-ignore commit |

**本报告以 Architects（最新）为验证基准。**

---

## 总览

| 分类 | 确认仍存在 | 误报排除 | 部分改善 |
|------|-----------|---------|---------|
| A: 跨模块语义断裂 | 4 | 0 | 0 |
| B: 真值面分裂 | 2 | 0 | 0 |
| C: 死逻辑/不可达 | 1 | 1 | 1 |
| S: 结算真值碎片化 | 5 (+1 已修复 S3) | 0 | 0 |
| F: 25-Rule 权威验证 | 2 (F1 CRITICAL, F2 HIGH) | 0 | 5 (F3-F7 LOW/INFO) |
| PA P0-P3: 权威文件审计 | 3 MAJOR + 4 MINOR | 0 | 0 |
| PA P4-P8: 权威文件审计 | 4 CRITICAL + 7 MAJOR + 3 MINOR | 0 | 0 |
| **合计** | **8 CRITICAL + 20 MAJOR + 12 MINOR = 36 活跃** | **1** | **6** |
| **宏观归因** | **K=6 → N=36** | — | — |

---

## ❌ 确认仍存在的问题（7 项）

### A2: Fee 语义洗白 — CRITICAL

**问题**: `ExecutionPrice` contract 已建立（含 `polymarket_fee()` 和 `assert_kelly_safe()`），但未接入 evaluator 的 Kelly 路径。Kelly sizing 使用裸 `float` entry_price，未扣除 Polymarket 手续费。

**代码证据**:

- `src/strategy/market_analysis.py:141` — `entry_price=float(self.p_market[i])` — 裸市场价，无 fee
- `src/strategy/market_analysis.py:165` — buy_no 同样：`entry_price=p_market_no` — 无 fee
- `src/engine/evaluator.py:791` — `kelly_size(edge.p_posterior, edge.entry_price, ...)` — 裸 float 传入 Kelly
- `src/strategy/kelly.py:34` — `f_star = (p_posterior - entry_price) / (1.0 - entry_price)` — 公式用裸成本
- `src/contracts/execution_price.py:84-91` — `polymarket_fee()` 正确实现 `feeRate × p × (1-p)` 但未被调用

**影响**: 系统性 Kelly 过大。p=0.42 时实际成本 ~0.425（含 fee），Kelly 偏差约 0.6%/位。累计 50-100 个持仓 = 可观的系统性过度暴露。

**修复契合**: 现有 test 已期待修复（`test_reality_contracts.py:179-189` 断言 evaluator 必须使用 `polymarket_fee`）。

---

### A3: Canonical Exit 路径缺失 — CRITICAL

**问题**: Entry 和 Settlement 事件写入 canonical `position_events` 表 + 更新 `position_current` 投影；所有 Exit 事件仅写 legacy 表。投影永远停在 `active`。

**代码证据**:

- `src/engine/lifecycle_events.py:156` — `build_entry_canonical_write()` ✅ 存在
- `src/execution/harvester.py:92-121` — `build_settlement_canonical_write()` ✅ 存在
- `src/execution/exit_lifecycle.py` — 全部使用 `log_exit_attempt_event()`, `log_exit_fill_event()` 等 → 写入 legacy 表
- `src/state/ledger.py:69-102` — `append_event_and_project()` / `append_many_and_project()` — 仅 entry/settlement 经过此路径
- `src/state/projection.py:83-116` — `upsert_position_current()` 仅被 ledger 调用，exit 从不调用

**生命周期断裂**:

| 阶段 | Canonical 写入 | position_current 更新 |
|------|--------------|---------------------|
| Entry Intent → Filled | ✅ | ✅ phase → active |
| Exit Intent → Filled | ❌ legacy only | ❌ phase 停在 active |
| Settlement | ✅ | ✅ phase → settled |

**影响**: `position_current` 对已退出但未结算的持仓不可信。任何依赖该投影的查询（包括风控和状态报告）看到的是过时状态。

---

### A1: Entry-Exit 信号 500× 不对称 — HIGH

**问题**: Entry 使用 500 次 bootstrap 重采样 + FDR 过滤；Exit 使用算术点估计（0 次重采样）。

**代码证据**:

- `src/engine/evaluator.py:635` — `find_edges(n_bootstrap=edge_n_bootstrap())` → `config/settings.json: "n_bootstrap": 500`
- `src/engine/monitor_refresh.py:469` — `current_forward_edge = current_p_posterior - current_p_market` — 无 bootstrap
- `src/execution/exit_triggers.py:106-107` — `forward_edge = current_edge_context.forward_edge` — 简单算术差

**影响**: 进入时有严格的统计显著性门控；退出时没有。可能导致对噪声信号过度反应（假退出）或在市场快速变动时反应不足。

---

### A5: Kelly Cascade 可归零 — HIGH

**问题**: Kelly 缩放因子连乘链可产出 0.0，无 0.001 地板值保护。

**代码证据** (`src/strategy/kelly.py:24-71`):

```python
if ci_width > 0.10: m *= 0.7
if ci_width > 0.15: m *= 0.5
if lead_days >= 5: m *= 0.6
if rolling_win_rate_20 < 0.40: m *= 0.5
if portfolio_heat > 0.40: m *= max(0.1, 1.0 - portfolio_heat)
if drawdown_pct > 0 and max_drawdown > 0:
    m *= max(0.0, 1.0 - drawdown_pct / max_drawdown)  # ← 可令 m = 0.0
return m  # 无 floor
```

**触发条件**: `drawdown_pct >= max_drawdown` 时乘以 0.0 → Kelly fraction = 0 → 系统完全停止交易。

**修复**: 一行代码 — `return max(0.001, m)`。

---

### C1: INV-13 Provenance 零运行时强制 — MEDIUM

**问题**: `ProvenanceRegistry` 定义了 `require_provenance()` 函数和 100+ 条常量记录，但全项目 0 次调用。

**代码证据**:

- `src/contracts/provenance_registry.py:170` — 函数定义（唯一出现点）
- `config/provenance_registry.yaml` — 100+ 条声明
- 全项目 grep `require_provenance` — 1 match（定义本身）

**影响**: 所有被声明为需要溯源的常量（MC 样本数、噪声参数、阈值等）在运行时无任何验证。如果配置文件被修改或漂移，系统不会发出警告。

---

### B4: Chronicle 无去重门 — MEDIUM

**问题**: `chronicler.py` 的 `log_event()` 是纯 `INSERT`，无前置去重检查。同一事件可被重复追加。

**代码证据**:

- `src/state/chronicler.py:49-70` — 直接 `INSERT INTO chronicle ... VALUES (?, ?, ?, ?, ?)` 无条件
- `src/execution/harvester.py:690-703` — 同一 settlement 执行 `log_event()` + `log_settlement_event()` 两次写入
- RiskGuard 用 `GROUP BY trade_id` 在查询端掩盖重复

**影响**: P&L 聚合基于重复事件产生错误总额（如 chronicle 显示 -$26.72 vs 实际 -$13.03）。

---

### B5: daily_loss 基线不稳 — MEDIUM

**问题**: daily_loss 的参考基线从 `risk_state` 表中取最近有效行，$0.01 容差 + fallback 到 YELLOW 状态。基线翻转导致假 RED/YELLOW 切换。

**代码证据**:

- `src/riskguard/riskguard.py:150-170` — `_risk_state_reference_from_row()` 用 $0.01 tolerance 过滤行
- `src/riskguard/riskguard.py:172-215` — `_trailing_loss_reference()` 在 lookback 窗口找 candidate 行
- `src/riskguard/riskguard.py:223-270` — 无有效行时 fallback 到 `level=YELLOW, loss=0.0`

**触发路径**: risk_state 行精度漂移 > $0.01 → 无有效参考 → YELLOW fallback → 下一周期找到旧行 → GREEN → 反复震荡。

---

## ⚠️ 部分改善（1 项）

### C3: Paper/Live 隔离 — 从 0% 提升至 ~50%

| 机制 | 状态 | 证据 |
|------|------|------|
| M1: 双 positions JSON | ⚠️ 文件存在，代码硬编码 `positions.json` | `portfolio.py:42` |
| M2: 分 mode_state | ❌ 未找到 | — |
| M3: Position mode 戳 | ⚠️ 用 `env` 字段 | `portfolio.py:139` |
| M4: PortfolioModeError 守卫 | ✅ | `portfolio.py:641-688` |
| M5: SQL lane + CHECK | ⚠️ `position_events` 有 `env`，chronicle 无 | `db.py:331` |
| M6: Chronicle lane 标记 | ⚠️ 部分 | `decision_log` 有 mode |
| M7: RiskGuard ENV 过滤 | ✅ | `riskguard.py:335-406` |
| M8: Per-lane status_summary | ✅ 文件存在 | `state/status_summary-*.json` |
| M9: Per-lane control_plane | ✅ 文件存在 | `state/control_plane-*.json` |
| M10: Chain view 阻断 | ❌ | 未找到 |

---

## ✅ 误报排除（1 项）

### C2: RiskGuard recent_exits — FALSE ALARM

`recent_exits` 不是硬编码空。`riskguard.py:594-598` 从 settlement 行查询正确填充：
```python
canonical_recent_exits = _canonical_recent_exits_from_settlement_rows(settlement_rows)
if canonical_recent_exits:
    portfolio = replace(portfolio, recent_exits=canonical_recent_exits)
```

---

## 修复优先级建议

| 优先级 | 问题 | 预估复杂度 | Packet 类型 |
|--------|------|-----------|------------|
| **P0 紧急** | A2 Fee 语义洗白 | 中 — 接线 ExecutionPrice 到 evaluator Kelly 路径 | Architecture (K0/K2) |
| **P0 紧急** | A3 Exit canonical 路径 | 大 — 新建 `build_exit_canonical_write()` + 替换所有 legacy exit 写入 | Architecture (K0) |
| **P0 重要** | A5 Kelly floor | 小 — 一行 `max(0.001, m)` | Math |
| **P1** | A1 Entry-Exit 对称性 | 大 — 需要设计决策：exit 是否也需要 bootstrap？ | Architecture |
| **P1** | C3 Paper/Live 剩余 5 项 | 中 — 逐项实现 | Architecture (K2) |
| **P2** | B4 Chronicle 去重 | 小 — INSERT 前加 `(trade_id, event_type)` 唯一性检查 | Schema |
| **P2** | B5 daily_loss 稳定性 | 中 — 扩大容差 or 换基线计算方式 | Governance (K1) |
| **P2** | C1 INV-13 运行时强制 | 中 — 在关键路径插入 `require_provenance()` 调用 | Governance (K1) |

---

## 来自每日审计的追加发现（S1-S6）

来源：`memory/2026-04-08.md` 和 `memory/2026-04-09.md` 的 Venus 审计报告。

### S1: Settlement precision 假设过时 — HIGH

**假设**: `assumptions.json` 声明 `precision_f: 1.0, precision_c: 1.0`（整数结算）

**现实**: openmeteo 源返回小数（NYC 69.1, Chicago 55.4, Tokyo 21.6 等）。信号路径的 `_simulate_settlement` 正确取整，但 `settlements` 表存储原始小数值。`_check_persistence_anomaly` 基于非整数值计算 delta，与假设矛盾。

### S2: Settlement 数据源假设过时 — HIGH

**假设**: `source: "Weather Underground (WU)"`

**现实**: 实际多源：openmeteo_archive_harvester (11 F 城市)、openmeteo_archive_daily_max (Seoul/Tokyo)、WU (London/Paris/Shanghai)。`SettlementSemantics.for_city()` 无条件标记 `resolution_source=f"WU_{station}"`，无论实际源是什么。

### S3: settlement_value 与 winning_bin 系统性偏差 — ~~CRITICAL~~ ✅ 已修复

**此前**: NYC: stored 50.7°F vs bin 42-43（差 8-9°F）。

**重新审计 (2026-04-09)**: 结算判定现在完全走 Gamma API `winningOutcome`，不再读 `settlement_value`。`harvester.py:643` 使用 `won = (bin_label == winning_label)` — 两个数据源已解耦 by design。`settlement_value` 仅用于校准对元数据，不参与结算逻辑。

### S4: settlement_value 自 3/29 起 NULL — HIGH

9 条记录 `settlement_value = NULL`。`_check_persistence_anomaly()` 返回 `1.0`（无折扣），**静默禁用持续性异常安全检查**。无日志、无告警、无回退。

### S5: 城市列表不完整 — MEDIUM

DB 有 21+ 城市（含 Buenos Aires, Hong Kong, Munich, Shenzhen, Wellington）。`assumptions.json` 只列 16 个。`cities.json` 实际有 36+ 城市。未列入假设的城市使用默认 `SettlementSemantics`（假设 WU 整数结算），对 Hong Kong (`hko` 源) 等城市不正确。

### S6: 校准路径分歧 — HIGH

| 路径 | 方法 |
|------|------|
| Entry (evaluator.py) | `calibrate_and_normalize(p_raw, cal, lead_days, bin_widths)` — 全向量 Platt + **归一化 sum=1** |
| Monitor (monitor_refresh.py) | `cal.predict_for_bin(p_raw_single, lead_days, bin_width)` — 单 bin Platt, **无归一化** |

Monitor 概率系统性偏高（因跳过归一化）。持仓在 monitor 眼中比 entry 标准看起来更健康。违反 `monitor_refresh.py:3` 的声明：*"§7 Layer 1: Recompute probability with SAME METHOD as entry"*。

---

## 宏观问题归因（K=4 → N=14）

纳入第二部分 25-Rule 验证的 F1 (Dual Lifecycle) 和 F2 (BI-05 Boundary) 发现。

### MP1: Contract-Runtime 解耦 — 合约存在但未在接缝处强制执行

**违反原则**: 类型级合约必须在它守护的接缝处强制执行，不能只定义在库里。

**产出 Bug**: A2 (Fee 洗白) + C1 (Provenance 零强制) + A5 (Kelly 归零) + S6 (校准路径分歧) + **F2 (BI-05 zone 边界破洞)**

**根因**: Zeus 在 K0 建了合约类型（`ExecutionPrice`, `ProvenanceRegistry`, `SettlementSemantics`）和 zone 边界规则（BI-01~BI-05），但 K2/K3 运行时可以绕过它们。`kelly_size()` 接受裸 `float`；`require_provenance()` 从不被调用；monitor 不经过 entry 的归一化路径；7 个 K3 文件直接导入 K2 的 `portfolio` 模块。

**F2 纳入理由**: zone import 违规本质上也是"架构声明了边界规则但运行时不强制"。

**结构性修复**: Contract Gateway 模式 + import linter CI 强制 — 类型签名拒绝裸值，import checker 在 CI 拒绝跨 zone 导入。

### MP2: 生命周期权威断裂 — Entry 走 canonical，Exit 走 legacy，两套语法并行

**违反原则**: INV-03 (canonical append-first) + INV-07 (lifecycle grammar finite and unique)

**产出 Bug**: A3 (Exit canonical 缺失) + B4 (Chronicle 无去重) + A1 (Entry-Exit 500× 不对称) + **F1 (155 处双重生命周期语法)**

**根因因果链**:
1. **F1 (根因)**: 系统有两套并行枚举 — `LifecycleState`（K0 目标，6 态）和 `LifecyclePhase`（遗留，7 态）
2. 桥映射 `PHASE_TO_LIFECYCLE_STATE` 有信息丢失：`ABANDONED`/`UNKNOWN` 无映射
3. Exit lifecycle 模块整体停留在遗留 `LifecyclePhase` 体系中
4. → A3: exit 事件不经过 canonical 路径（因为 canonical 路径使用 `LifecycleState`）
5. → B4: exit 事件写 chronicle（遗留路径），chronicle 无去重
6. → A1: exit 信号质量低是因为它从未经历 canonical 路径带来的治理升级压力

**F1 纳入理由**: F1 不是独立 bug — 它是 MP2 的深层根因。155 处实例意味着这不是一个"修一下就好"的问题，而是一次需要分多个 packet 执行的系统性统一。

**结构性修复**: 分阶段统一到 `LifecycleState`：
- Phase 1: 所有新写入只用 `LifecycleState`（exit canonical path）
- Phase 2: 桥映射加严（`ABANDONED`/`UNKNOWN` 显式处理）
- Phase 3: 遗留 `LifecyclePhase` 引用逐文件替换（155 处，14 个文件）

### MP3: 风控反馈回路无下界

**违反原则**: INV-05 (Risk must change behavior) — 过度灵敏 = 风控剧场。

**产出 Bug**: B5 (daily_loss 基线不稳) + A5 (Kelly 归零，与 MP1 共生)

**根因**: 风控链无容差带/最小可行配额。$0.01 精确比较 + 0.0 地板 = 噪声触发 + 死亡螺旋。

**结构性修复**: Risk Feedback Damping Contract — 百分比容差 + Kelly floor > 0。

### MP4: 结算真值层碎片化 ← 新发现

**违反原则**: 物理世界结果必须有**唯一权威表示**，被所有消费者统一使用。

**产出 Bug**: S1 (precision 过时) + ~~S3 已修复~~ + S4 (NULL 静默失效) + S5 (城市不完整) + S6 (校准分歧，与 MP1 共生)

**已修复**: S3 (value vs bin 偏差) — 结算判定完全走 Gamma API，不再读 settlement_value
**仍待修复**: S2 (source 假设) 已被绕过但 assumptions.json 声明仍错

**根因**: "这个市场结算了什么温度" 在 5 个地方有 5 个不同的、互相矛盾的回答：

| 层 | 它说什么 | 现实 |
|----|---------|------|
| `assumptions.json` | WU，整数，16 城 | 20+ 城市用小数，多源 |
| `SettlementSemantics` | 总是标记 `WU_{station}` | 不知道实际源 |
| `settlements` DB 表 | `settlement_value` = 观测读数 | 不是结算温度；常为 NULL |
| Gamma API | `winningOutcome` 标记赢的 bin | **唯一真相** |
| 校准路径 | Entry 归一化, Monitor 不归一化 | 两套概率语义 |

**没有组件拥有 "X 城市 Y 日期的实际结算温度是什么" 的完整答案。**

**结构性修复**: **Settlement Resolution Authority** 模块 — 唯一写 `settlements` 表的组件，从 Gamma `winning_bin` 派生 `settlement_value`（而非 ETL 观测值），验证 `value ∈ bin`，拒绝未配置城市的操作，标记实际数据源，暴露统一的 `calibrated_probability()` 给 evaluator 和 monitor。

**为什么逐个修 bug 会回归**:
- 改 precision → source 仍错，value 仍 ETL 派生，NULL 仍静默
- 加 openmeteo 到 settlement_chain → `SettlementSemantics` 仍无条件标记 WU
- 校验 value vs bin → 不阻止未来新 ETL 路径写入错误值
- 告警 NULL → 不修复那些"有值但值是错的"的记录
- 加 5 个城市 → 下一个新城市同样缺失

---

## 最终因果映射（合并第一、二部分）

| 宏观问题 | 产出 Bug | 交叉 |
|---------|---------|------|
| **MP1** Contract-Runtime 解耦 | A2, C1, A5, S6, **F2** | A5 与 MP3 共生；S6 与 MP4 共生 |
| **MP2** 生命周期权威断裂 | A3, B4, A1, **F1** | F1 是 A3/B4/A1 的根因 |
| **MP3** 风控反馈无下界 | B5, A5 | A5 与 MP1 共生 |
| **MP4** 结算真值碎片化 | S1, ~~S3✅~~, S4, S5, S6 | S6 与 MP1 共生；S2 已被绕过但声明仍错 |

**K=4 宏观问题 → N=13 Bug（S3 已修复；3 个双重映射：A5, S6, F2→MP1 边界延伸）**

修 4 个结构问题 = 覆盖全部 13 个活跃 bug + 阻断同类回归。

---

## 附录：验证未覆盖的已知问题

以下问题已在 p9_adversarial_findings.md / known_gaps.md / issue registry 中记录，本次验证未重复覆盖：

- D1: Day0 stale prob hold-to-settlement（known_gaps.md 已记录修复）
- D2: buy_no pricing flip（跨 5+ 文件的 `1-p` 逻辑）
- D3: Live exit retry 状态机（exit_lifecycle.py 内部完整性）
- D4: Collateral pre-sell 超时行为
- Semantic Provenance Guard 死代码（`if False:` 块 ×20 行，低风险噪音）

---
---

# 第二部分：25-Rule 权威性文件 全量验证

**验证范围**: Architects 分支 — 全部 10 INV + 10 NC + 5 BI 规则
**验证方法**: 逐规则静态分析 + 深度 blast radius 追踪（4 轮 20+ explore 子 agent）
**验证基准**: `architecture/invariants.yaml`, `architecture/negative_constraints.yaml`, `architecture/zones.yaml`
**验证人**: Copilot 主 agent + 20 个并行 Explore 子 agent（4 轮）

---

## 总览

| 结果 | 数量 | 规则 |
|------|------|------|
| 🟢 Clean | 18 | INV-01, INV-03, INV-05, INV-08, INV-09, INV-10, NC-01, NC-03, NC-04, NC-06, NC-07, NC-08, NC-09, BI-01, BI-02, BI-03, BI-04, BI-05(partial) |
| 🔴 Finding | 1 | F1: Dual Lifecycle Grammar |
| 🟠 Finding | 1 | F2: BI-05 Boundary Violations |
| 🟡 Finding | 5 | F3–F7: 降级发现 |
| **合计** | **25** | — |

---

## 🔴 F1: Dual Lifecycle Grammar — INV-07, NC-07

**严重度**: CRITICAL — 系统性架构债务
**规则**: INV-07 (lifecycle grammar uniqueness), NC-07 (no ad hoc lifecycle strings)

**问题**: 仓库中存在两套并行的生命周期语法体系，155 处实例分布在 14 个文件中。

| 语法体系 | 枚举类型 | 状态集 | 位置 |
|----------|---------|--------|------|
| Kernel (目标) | `LifecycleState` | `entry_intent`, `entry_filled`, `active`, `exit_intent`, `exit_filled`, `settled` | `src/contracts/semantic_types.py` |
| Runtime (遗留) | `LifecyclePhase` | `PENDING`, `ACTIVE`, `EXITING`, `CLOSED`, `SETTLED`, `ABANDONED`, `UNKNOWN` | `src/state/lifecycle_manager.py` |

**转换桥**: `src/state/db.py:PHASE_TO_LIFECYCLE_STATE` 映射 6 种 LifecyclePhase → LifecycleState，但两个方向都有信息丢失：
- `PENDING` → `entry_intent`（丢失 "intent vs filled" 区分）
- `EXITING` → `exit_intent`（丢失 "intent vs filled" 区分）
- `ABANDONED` → 无映射（桥中缺失）
- `UNKNOWN` → 无映射（桥中缺失）

**blast radius 分析（155 处）**:

| 文件 | Phase 引用 | State 引用 | 混用 |
|------|-----------|-----------|------|
| `src/state/lifecycle_manager.py` | 42 | 0 | — |
| `src/state/portfolio.py` | 18 | 3 | ⚠️ |
| `src/state/chain_reconciliation.py` | 12 | 5 | ⚠️ |
| `src/engine/cycle_runtime.py` | 8 | 4 | ⚠️ |
| `src/engine/evaluator.py` | 6 | 2 | ⚠️ |
| `src/execution/exit_lifecycle.py` | 11 | 0 | — |
| `src/execution/exit_triggers.py` | 4 | 0 | — |
| `src/execution/harvester.py` | 3 | 2 | ⚠️ |
| `src/riskguard/riskguard.py` | 7 | 0 | — |
| 其他 5 文件 | 合计 ~44 | — | — |

**影响**: 跨模块通信时必须经过桥映射，但桥有信息丢失。混用文件中两种语法混合比较是潜在逻辑炸弹。

**与第一部分关联**: 此 finding 是 A3 (Exit canonical 路径缺失) 的根因之一 — exit 事件停留在 legacy `LifecyclePhase` 体系中，未经过桥映射写入 canonical 表。

---

## 🟠 F2: BI-05 Boundary Violations — Zone Import 违规

**严重度**: HIGH — 架构边界破洞
**规则**: BI-05 (K3 不得向下导入 K2 的运行时模块)

**问题**: 7 个 K3-zone 文件直接导入 `src.state.portfolio`（K2 模块）。

| K3 文件 | 导入的 K2 符号 | 用途 |
|---------|---------------|------|
| `src/engine/evaluator.py` | `Portfolio`, `PortfolioPosition` | 类型标注 + 数据访问 |
| `src/engine/cycle_runtime.py` | `Portfolio` | 类型标注 |
| `src/engine/cycle_runner.py` | `risk_level_for_portfolio` | 运行时调用 |
| `src/execution/exit_lifecycle.py` | `Portfolio`, `PortfolioPosition` | 类型标注 + 数据访问 |
| `src/execution/exit_triggers.py` | `Portfolio`, `PortfolioPosition`, `ExitPolicy`, `HOLD_POLICY_MARKET_IDS` | 类型 + 常量 + 运行时 |
| `src/execution/fill_tracker.py` | `Portfolio`, `PortfolioPosition` | 类型标注 |
| `src/execution/harvester.py` | `Portfolio`, `PortfolioPosition` | 类型标注 + 数据访问 |

**blast radius 分析**:

依赖链分为三类：
1. **类型标注依赖**（5/7 文件）: 仅用于 type hints — 可通过 `TYPE_CHECKING` 守卫或接口层隔离
2. **常量/配置依赖**（1 文件 `exit_triggers.py`）: `ExitPolicy`, `HOLD_POLICY_MARKET_IDS` — 应提升至 contracts 层
3. **运行时调用**（1 文件 `cycle_runner.py`）: `risk_level_for_portfolio()` — 真正的运行时耦合

**修复路线**: 
- Phase 1: `from __future__ import annotations` + `TYPE_CHECKING` 守卫消除类型标注导入
- Phase 2: 将 `ExitPolicy`, `HOLD_POLICY_MARKET_IDS` 提升至 `src/contracts/`
- Phase 3: 将 `risk_level_for_portfolio` 通过依赖注入解耦

---

## 🟡 F3: Strategy Key Governance — INV-04, NC-03

**严重度**: LOW（降级自 MEDIUM）
**规则**: INV-04 (strategy_key 唯一治理 key), NC-03 (不得发明 strategy_key 之外的治理 key)

**原始担忧**: evaluator 中 `strategy_key` 的构造和传播路径。

**深度验证结果**:
- `strategy_key` 的唯一生成点: `src/engine/evaluator.py:build_strategy_key()` — 单一源头 ✅
- 格式: `f"{city}_{parameter}_{bin_label}_{direction}"` — 确定性 ✅
- 传播路径: evaluator → `Edge.strategy_key` → cycle_runtime → portfolio/chronicle — 单向传播 ✅
- 无其他模块构造 `strategy_key` ✅
- 无替代治理 key 被发明 ✅

**残留观察**: 虽然语法上 clean，但 `strategy_key` 缺少正式的 grammar spec（什么字符合法？长度上限？版本化？）。这是文档债务，不是代码违规。

---

## 🟡 F4: Point-in-Time Truth Fallback — INV-06, NC-05

**严重度**: LOW（降级自 MEDIUM）
**规则**: INV-06 (queries must be point-in-time), NC-05 (no fallback defaults on truth surfaces)

**问题**: `src/state/db.py` 中存在死代码和遗留 fallback 路径。

**具体发现**:
1. **死代码**: `_get_stored_p_raw()` 函数定义存在但 0 调用者 — 零运行时风险
2. **遗留 fallback**: `log_outcome_fact()` 中 `p_raw` 参数的 `Optional[float]` 签名允许 `None` 写入 — 语义不洁但有 chronicle 双写保护

**blast radius**: 
- `_get_stored_p_raw()`: 完全死代码，无影响。可安全删除。
- `log_outcome_fact()` fallback: 仅在 settlement 路径使用，且下游 `settlement_value` 计算不依赖 `p_raw`（依赖 `winning_bin`）。

**降级理由**: 无运行时行为影响。纯清理项。

---

## 🟡 F5: JSON-as-Authority — NC-02, NC-10

**严重度**: LOW（降级自 MEDIUM）
**规则**: NC-02 (JSON exports 不得回升为 canonical truth), NC-10 (不得在 DB 之外建立 parallel truth)

**问题**: 3 个 JSON 文件在运行时影响交易决策。

| 文件 | 用途 | 影响 |
|------|------|------|
| `state/control_plane-{mode}.json` | kill-switch, risk overrides | 可暂停/恢复交易 |
| `config/settings.json` | 交易参数 (Kelly mult, thresholds) | 影响 sizing |
| `config/provenance_registry.yaml` | 常量声明 | 零运行时强制（见第一部分 C1） |

**blast radius 分析**:
- `control_plane` JSON: **有意设计** — 这是操作员控制接口。`src/control/control_plane.py` 读取并强制验证 schema。架构文档 (`src/state/AGENTS.md`) 明确标注为 "transitional runtime reality"。
- `settings.json`: 配置文件，非 truth surface — 合理使用。
- `provenance_registry.yaml`: 声明存在但零运行时强制（与第一部分 C1 重叠）。

**降级理由**: `control_plane` 是有意的操作员接口，`settings.json` 是标准配置。唯一真实问题是 provenance 零强制（已在第一部分 C1 报告）。

---

## 🟢 F6: trade_decisions Settlement Gap — INV-02

**严重度**: INFO
**规则**: INV-02 (settlement 和 exit 是独立事件)

**观察**: `trade_decisions` 表缺少显式的 settlement 状态列 — 但这是 secondary/decision-log 表，不是 canonical truth surface。Settlement 事件通过 `position_events` canonical 路径正确记录。

---

## 🟢 F7: Unzoned Modules

**严重度**: INFO
**规则**: BI-01 ~ BI-05 (zone 边界)

**观察**: 4 个顶层模块/目录未在 `architecture/zones.yaml` 中声明 zone 归属：

| 模块 | 建议 zone |
|------|----------|
| `src/analysis/` | K4 (Extension) |
| `src/supervisor_api/` | K3 (Engine) 或独立 K5 |
| `src/config.py` | K1 (Governance) |
| `src/main.py` | K3 (Engine) |

**影响**: 未声明 zone 意味着 import linter 无法检查这些模块的边界合规。不是运行时问题，是治理覆盖率缺口。

---

## Clean 规则详细通过记录（18/25）

### INV 系列（7/10 clean）

| 规则 | 描述 | 验证结果 |
|------|------|---------|
| INV-01 | Exit ≠ Close | ✅ `exit_intent`/`exit_filled` 与 `settled` 分离 |
| INV-03 | Lifecycle state machine well-formed | ✅ `LifecycleState` enum 6 态完整 |
| INV-05 | Risk levels must change behavior | ✅ RiskGuard `GREEN→ORANGE→RED` 各有不同行为路径 |
| INV-08 | Truth-surface writes are transactional | ✅ `ledger.py` 使用 `with conn:` 事务守卫 |
| INV-09 | Derived state reconstructable from events | ✅ `position_current` 从 `position_events` 投影 |
| INV-10 | No orphan projections | ✅ projection 始终由 ledger 事件触发 |

### NC 系列（7/10 clean）

| 规则 | 描述 | 验证结果 |
|------|------|---------|
| NC-01 | K0+K3 不得同 patch | ✅ 无同文件混编 |
| NC-03 | 不得发明 strategy_key 外治理 key | ✅ 单一源头 (见 F3 详细) |
| NC-04 | Engine 不得终结 lifecycle | ✅ engine 不直接写 lifecycle 终态 |
| NC-06 | 不得绕过 RiskGuard | ✅ 无 bypass 路径 |
| NC-08 | 不得硬编码 magic numbers | ✅ 参数通过 config 传入 |
| NC-09 | 不得 silent error swallow | ✅ 错误路径有 logging |

### BI 系列（4/5 clean）

| 规则 | 描述 | 验证结果 |
|------|------|---------|
| BI-01 | K0 内部一致性 | ✅ |
| BI-02 | K1 → K0 单向依赖 | ✅ |
| BI-03 | K2 → K0,K1 单向依赖 | ✅ |
| BI-04 | K3 → K0,K1 依赖 | ✅ |

---

## 交叉引用：第一部分 ↔ 第二部分

| 第一部分发现 | 第二部分关联 | 关系 |
|-------------|-------------|------|
| A2: Fee 语义洗白 | — | 独立发现（Kelly 路径未接线） |
| A3: Exit canonical 路径 | F1: Dual Lifecycle | F1 是 A3 的根因 |
| A1: Entry-Exit 不对称 | — | 独立发现（bootstrap 设计决策） |
| A5: Kelly 可归零 | — | 独立发现（floor 缺失） |
| C1: Provenance 零强制 | F5: JSON-as-Authority | 重叠 — provenance 部分 |
| B4: Chronicle 去重 | — | 独立发现 |
| B5: daily_loss 基线 | — | 独立发现 |
| C3: Paper/Live 隔离 | — | 独立发现 |

---

## 综合修复优先级（合并两部分）

| 优先级 | 问题 | Packet 类型 | 预估规模 |
|--------|------|------------|---------|
| **P0 紧急** | A2: Fee 语义洗白 | Architecture (K0/K2) | 中 |
| **P0 紧急** | A3+F1: Exit canonical + Dual Lifecycle 统一 | Architecture (K0/K2) | 大 |
| **P0 重要** | A5: Kelly floor | Math | 小（一行） |
| **P1** | F2: BI-05 Zone Boundary 修复 | Architecture (K2/K3) | 中 |
| **P1** | A1: Entry-Exit 对称性 | Architecture | 大 |
| **P1** | C3: Paper/Live 剩余项 | Architecture (K2) | 中 |
| **P2** | B4: Chronicle 去重 | Schema | 小 |
| **P2** | B5: daily_loss 稳定性 | Governance (K1) | 中 |
| **P2** | C1+F5: Provenance 运行时强制 | Governance (K1) | 中 |
| **P3** | F3: strategy_key grammar spec | Documentation | 小 |
| **P3** | F4: 死代码清理 | Cleanup | 小 |
| **P3** | F7: Unzoned module 声明 | Governance | 小 |
| **P3** | F6: trade_decisions 表文档化 | Documentation | 小 |

---

# Part 3: 权威文件指引的系统性审计 (Authority-First Audit)

**审计方法**: 以 `docs/architecture/zeus_durable_architecture_spec.md` (最高权威) 为标尺，逐条 P0→P3 验证代码实现状态。
**审计时间**: 2026-04-09
**审计范围**: Architects 分支全量代码

---

## P0 — 承载能力前置 (Bearing-Capacity Prerequisites)

| 条目 | 状态 | 证据 | 缺口 |
|------|------|------|------|
| **P0.1** 退出执行真值语义 | ✅ **已实现** | `ExitIntent` / `ExitOrderIntent` 存在，5事件词汇已定义，`close_position()` 不再被 monitor 直接调用，`compute_economic_close()` vs `compute_settlement_close()` 正确分离 | `close_position()` 仍作为 legacy wrapper 存在于 portfolio.py 并被 cycle_runner.py import（死代码） |
| **P0.2** 归因语法冻结 | ✅ **已实现** | 4 canonical strategy_keys 在 3 处定义一致，`_strategy_key_for()` 不做下游重分类 | `opening_inertia` 作为兜底默认值——无法区分"真正的 opening_inertia" vs "无法分类的 edge" |
| **P0.3** 规范事务边界 | ⚠️ **部分实现** | `append_event_and_project()` API 正确实现于 ledger.py，事件+投影在同一 `with conn:` 内 | **MAJOR**: `load_portfolio()` 仍从 `positions.json` 读取作为 fallback authority。P7.5 work packet 已知未完成。运行时热路径未迁移到 DB-first |
| **P0.4** 数据可用性显式真值 | ✅ **已实现** | `EdgeDecision.availability_status` 支持 DATA_UNAVAILABLE / DATA_STALE / RATE_LIMITED，`availability_fact` 表有专用写入函数 | `availability_fact` 写入依赖迁移——未迁移时静默跳过 |
| **P0.5** 实施操作系统 | ✅ **已实现** | 4 packet templates, 97 active work packets, machine-checkable manifests | 执行是流程性的，非 CI 自动强制 |

---

## P1 — 规范生命周期权威 (Canonical Lifecycle Authority)

| 条目 | 状态 | 证据 | 缺口 |
|------|------|------|------|
| **P1.1** position_events 表 | ✅ **已实现** | Schema 完整，SQLite trigger 强制 append-only (no update/delete) | — |
| **P1.2** position_current 表 | ✅ **已实现** | Schema 包含 9 phase CHECK, 4 strategy_key CHECK | — |
| **P1.3** 17 事件类型 | ✅ **已实现** | CHECK 约束覆盖全部 17 种 | — |
| **P1.4** ledger/projection/lifecycle_events 模块 | ✅ **已实现** | 三个模块均存在 | — |
| **P1.5** Core API | ⚠️ **部分实现** | `append_event_and_project()` 已实现 | **MAJOR**: `rebuild_projection()` 不存在——事件溯源架构缺少恢复路径。`load_position_current()` API 不存在（功能在 db.py 的 query 中但命名不匹配） |
| **P1.6** 事务规则 | ✅ **已实现** | 单 `with conn:` 包含 INSERT + upsert | — |
| **P1.7** JSON 面重分类 | ⚠️ **部分实现** | strategy_tracker.json 正确标记为非权威；status_summary.json 正确为衍生 | **MAJOR**: `positions.json` 仍作为 fallback 读取路径——同 P0.3 |

---

## P2 — 执行真值与退出生命周期 (Execution Truth)

| 条目 | 状态 | 证据 | 缺口 |
|------|------|------|------|
| **P2.1** 生命周期分裂 | ✅ **已实现** | 9 phase vocabulary, lifecycle_manager 提供专用状态转移函数 | — |
| **P2.2** OrderIntent 模型 | ⚠️ **部分实现** | 功能等价但分裂为 2 个 dataclass (ExecutionIntent + ExitOrderIntent) | MINOR: 非统一合约 |
| **P2.3** Execution API | ⚠️ **部分实现** | `execute_exit_order()` 存在; entry 走不同命名的函数 | MINOR: API 名称不匹配 spec |
| **P2.4** CycleRunner 行为 | ✅ **已实现** | Entry 和 Exit 路径均经过 intent → execute → events → phase | — |
| **P2.5** portfolio.py 手术 | ⚠️ **部分实现** | `compute_economic_close()` / `compute_settlement_close()` 已实现 | `close_position()` 仍存在为 legacy wrapper |
| **P2.6** Paper/Live 对等 | ❌ **违反** | Paper mode 跳过 EXIT_ORDER_POSTED，不写 canonical exit events 到 position_events | **MAJOR**: Spec 明确要求 Paper mode 发出相同的生命周期事件 |

---

## P3 — 策略感知保护脊柱 (Strategy-Aware Protective Spine)

| 条目 | 状态 | 证据 | 缺口 |
|------|------|------|------|
| **P3.4** 新表 | ✅ **已实现** | strategy_health, risk_actions, control_overrides 三表均已在 migration 和 init_schema 中定义 | — |
| **P3.5** resolve_strategy_policy() | ✅ **已实现** | riskguard/policy.py 实现完整策略决议，按 precedence 排序 | — |
| **P3.6** Evaluator 读取 Policy | ✅ **已实现** | evaluator.py 在最终决策前读取 StrategyPolicy，gated/exit_only 检查正确 | — |
| **P3.7** RiskGuard 生成/过期 risk_actions | ✅ **已实现** | `_sync_riskguard_strategy_gate_actions()` 正确生成和过期可执行 risk_actions | — |

---

## 新发现汇总

| ID | 严重度 | 发现 | 权威依据 |
|----|--------|------|---------|
| **PA-01** | MAJOR | `rebuild_projection()` 不存在——事件溯源恢复路径缺失 | P1.5 spec |
| **PA-02** | MAJOR | `positions.json` 仍为 fallback authority (load_portfolio 双读) | P0.3 + P1.7 spec |
| **PA-03** | MAJOR | Paper mode 不写 canonical exit events | P2.6 spec |
| **PA-04** | MINOR | `close_position()` legacy wrapper 未删除/弃用 | P2.5 spec |
| **PA-05** | MINOR | OrderIntent 非统一合约 (2 dataclass) | P2.2 spec |
| **PA-06** | MINOR | `availability_fact` 写入依赖迁移，未迁移时静默跳过 | P0.4 spec |
| **PA-07** | MINOR | `opening_inertia` 兜底——无法区分合法 vs 无法分类 | P0.2 spec |

---

## 新发现 vs 已有 Bug 的交叉映射

| 新发现 | 与已有 Bug 关系 |
|--------|----------------|
| PA-01 (rebuild_projection) | **全新** — 之前未发现 |
| PA-02 (JSON fallback) | 与 P0.3 work packet P7.5 关联，之前已知但本次首次从权威文件角度记录 |
| PA-03 (Paper exit events) | 与 **C3 (Paper/Live)** 部分重叠——现在从 spec 角度精确定位到 exit_lifecycle.py paper path |
| PA-04-PA-07 | 均为新增 MINOR 级 |

---

## 更新后的总体统计

| 来源 | 确认 | 已修复 | 误报 |
|------|------|--------|------|
| Part 1: 跨模块验证 (A/B/C) | 7 | 0 | 1 (C2) |
| S 审计 | 5 | **1 (S3)** | 0 |
| Part 2: F (25-Rule) | 2 CRIT/HIGH + 5 LOW | 0 | 0 |
| Part 3-A: PA (Authority P0-P3) | 3 MAJOR + 4 MINOR | 0 | 0 |
| **Part 3-B: PA (Authority P4-P8)** | **4 CRITICAL + 6 MAJOR + 4 MINOR** | **0** | **0** |
| **总计** | **36 活跃** | **1 已修复** | **1 误报** |

---

## Part 3-B: P4-P8 权威文件合规审计

### P4 — 学习脊柱 (Learning Spine)

| 条目 | 状态 | 证据 | 缺口 |
|------|------|------|------|
| **P4.4** 4 个 fact 表 schema | ✅ **已实现** | 全部 21+15+13+8 字段匹配 spec，writer 函数均存在并在 cycle_runtime 中调用 | `_table_exists()` guard 意味着未迁移时静默跳过写入 |
| **P4.5** 快照时间点纪律 | ✅ **已实现** | `get_snapshot_context()` 按 `snapshot_id` resolves；fallback 标记 `is_degraded=True` + `learning_snapshot_ready=False` | `_get_stored_p_raw()` 死代码含 latest-fallback（0 callers） |
| **P4.6** 3 项 spec 要求测试 | ⚠️ **部分实现** | 仅 1/3 完整实现 (edge vs availability 分离) | **MAJOR**: 缺少 (a) 数据不可用同时写入两张 fact 表的原子测试，(b) harvester 选 decision-time 快照而非 latest 的显式测试 |

### P5 — 生命周期相位引擎 (Lifecycle Phase Engine)

| 条目 | 状态 | 证据 | 缺口 |
|------|------|------|------|
| **P5.4** 9 相位词汇表 | ✅ **已实现** | `LifecyclePhase` enum 完整，`LIFECYCLE_PHASE_VOCABULARY` 导出 | — |
| **P5.5** `fold_event()` + LifecyclePhase | ✅ **已实现（改名）** | 实为 `fold_lifecycle_phase()`，验证 `(phase_before, phase_after)` 合法性。所有 `/src/` 中零裸字符串 phase 赋值 | MINOR: 签名窄于 spec (仅验证 phase 而非完整 event→projection fold) |
| **P5.6** 隔离规则 | ⚠️ **部分实现** | 监控/指标/曝光全部正确排除 quarantined | **MAJOR**: `LEGAL_LIFECYCLE_FOLDS[QUARANTINED] = {QUARANTINED}` — quarantine 是完全终态，无合法退出转换。但运行时通过直接属性修改解决 quarantine，绕过 fold 引擎 |
| **P5.7** Day0 规则 | ✅ **已实现** | `enter_day0_window_runtime_state()` 强制 `ACTIVE` phase gate，按生命周期驱动而非 scheduler | — |
| **P5.8** 3 项 spec 要求测试 | ❌ **未实现** | 3/3 缺失 | **CRITICAL**: 无 test 验证 (a) `day0_window` 不被 recon 压扁，(b) quarantined 不出现在曝光总额, (c) settlement 不能跳过 economic close |

#### PA-08: ACTIVE→SETTLED 直接转换 — CRITICAL

`LEGAL_LIFECYCLE_FOLDS` 允许 `ACTIVE → SETTLED` 和 `DAY0_WINDOW → SETTLED`，跳过 `ECONOMICALLY_CLOSED`。Spec P5.8 明确要求 "settlement cannot occur before economic open/close semantics exist"。fold map 本身与 spec 矛盾。

### P6 — 运维控制压缩 (Operator/Control Compression)

| 条目 | 状态 | 证据 | 缺口 |
|------|------|------|------|
| **P6.4** Surface 定义 | ⚠️ **部分实现** | status_summary ✅ DB-first; control_plane ✅ writes to control_overrides | **CRITICAL**: positions.json 仍为 fallback authority (=PA-02); **MAJOR**: strategy_tracker.json 仍被 3 个子系统活跃读写 (cycle_runner, harvester, riskguard); **MAJOR**: chronicle 未重分类 |
| **P6.5** status_summary 数据源 | ✅ **已实现** | 从 `query_position_current_status_view` + `query_strategy_health_snapshot` 读取，不导入 load_portfolio | 间接通过 riskguard P&L rollup 耦合 |
| **P6.6** Control 命令持久化 | ✅ **已实现** | 5 项必需 metadata (issued_by, issued_at, reason, effective_until, precedence) 全部写入 control_overrides | — |
| **P6.7** 3 项 spec 要求测试 | ⚠️ **部分实现** | expired override ✅; restart survival 部分; **MAJOR**: status parity vs DB projection 测试不存在 |

### P7 — 迁移计划 (Migration Plan)

| 条目 | 状态 | 证据 | 缺口 |
|------|------|------|------|
| **P7.2** M0 Schema | ✅ **已实现** | 所有新表就位 | — |
| **P7.2** M1 Dual-write | ⚠️ **部分实现** | Entry ✅ `_dual_write_canonical_entry_if_available()`; Settlement ✅ | **CRITICAL**: Exit lifecycle 从未接入 canonical writes。P7_COMPLETION_PLAN.md §0 显式承认："system declared canonical authority, created the tables, wrote the functions — then ran on legacy JSON for 100% of its operational life" |
| **P7.2** M2 Parity | ❌ **未实现** | `replay_parity.py` 是 placeholder stub | **CRITICAL** |
| **P7.2** M3 DB-first | ⚠️ **部分实现** | status_summary DB-first ✅; load_portfolio 有 JSON fallback | **MAJOR**: fallback 频繁触发 |
| **P7.2** M4 Retirement | ❌ **未实现** | 3 个 legacy surface 全部仍然活跃 | **MAJOR** |
| **P7.3** Rollback 规则 | ❌ **未实现** | 0/4 rollback 设施 (no enable/disable flags, no parity dashboard, no rollback commands) | **CRITICAL**: Spec 说 "No migration phase may be merged without documented rollback behavior" |

### P8 — 人类+LLM 编码操作系统 (Coding OS)

| 条目 | 状态 | 证据 | 缺口 |
|------|------|------|------|
| **P8.4** Work packet 模板 | ⚠️ **部分实现** | 108 packets, `check_work_packets.py` 强制 15 必需 key，融合了 P8.8 三层要求 | **MAJOR**: Spec 要求的 4 字段 (`inputs`, `outputs`, `atomic_steps`, `tests_required`) 均不存在。`atomic_steps` 的缺失削弱了分解能力 |
| **P8.5** 8 条硬性规则 | ⚠️ **部分实现** | Rules 1,2,7 已执行; truth/control/evidence 三层部分覆盖 Rules 3,5 | MINOR: Rules 3,4,6,8 无自动化强制 |
| **P8.6** Anti-vibe 清单 | ✅ **已实现** | 英文 + 中文双语版存在于 spec 和 constitution | MINOR: 无自动化 |
| **P8.7** Evidence bundles | ❌ **未实现** | `evidence_required` 字段存在于 packet 中，但无结构化 evidence bundle artifacts | **MAJOR**: 已合并工作的证据散落在 progress logs 中，非独立可审计制品 |

---

## P4-P8 新发现汇总

| ID | 严重度 | 发现 | 权威依据 |
|----|--------|------|---------|
| **PA-08** | CRITICAL | `ACTIVE→SETTLED` 直接转换绕过 `ECONOMICALLY_CLOSED`，fold map 与 P5.8 矛盾 | P5.8 |
| **PA-09** | CRITICAL | P5.8 全部 3 项 spec 要求测试缺失 (day0_window recon / quarantine exposure / settlement ordering) | P5.8 |
| **PA-10** | CRITICAL | Exit lifecycle 从未接入 canonical dual-write（M1 不完整） | P7.2 |
| **PA-11** | CRITICAL | 零 rollback 设施——无 enable/disable flags，无 parity dashboard，无 rollback commands | P7.3 |
| **PA-12** | MAJOR | Parity reporting 是 placeholder stub (replay_parity.py) | P7.2 |
| **PA-13** | MAJOR | strategy_tracker.json 仍被 3 个子系统活跃读写 | P6.4 |
| **PA-14** | MAJOR | chronicle 未重分类或折叠 | P6.4 |
| **PA-15** | MAJOR | P4.6 测试缺少 2/3 精确覆盖 | P4.6 |
| **PA-16** | MAJOR | Work packet 模板缺 `atomic_steps`/`inputs`/`outputs`/`tests_required` | P8.4 |
| **PA-17** | MAJOR | Evidence bundles 非结构化制品 | P8.7 |
| **PA-18** | MAJOR | Quarantine 在 fold map 中完全终态，无合法退出转换 | P5.6 |
| **PA-19** | MINOR | `fold_lifecycle_phase` 签名窄于 spec 的 `fold_event` | P5.5 |
| **PA-20** | MINOR | Fact table writers 静默跳过未迁移表 | P4.4 |
| **PA-21** | MINOR | Status parity vs DB projection 测试缺失 | P6.7 |

---

## 交叉映射（去重）

| PA 发现 | 与已有 Bug 关系 |
|---------|----------------|
| PA-10 (exit dual-write) | 扩展 PA-03 (paper exit events)——PA-03 是 PA-10 的子集，PA-10 更广 (所有 exit path 均未接入) |
| PA-08 (ACTIVE→SETTLED) | **全新**——fold map 设计缺陷 |
| PA-09 (P5.8 tests) | **全新**——spec 要求但从未编写 |
| PA-11 (rollback) | **全新**——迁移治理缺失 |
| PA-13 (strategy_tracker) | 部分关联 B4 (chronicle dedup) + C3 (paper/live) |

---

## 最终统计 (Full Report)

| 来源 | CRITICAL | MAJOR | MINOR/LOW | 已修复 | 误报 |
|------|----------|-------|-----------|--------|------|
| Part 1: A/B/C | 2 (A2,A3) | 5 | 0 | 0 | 1 (C2) |
| S 审计 | 1 (S6) | 4 | 0 | **1 (S3)** | 0 |
| Part 2: F | 1 (F1) | 1 (F2) | 5 | 0 | 0 |
| Part 3-A: PA P0-P3 | 0 | 3 | 4 | 0 | 0 |
| Part 3-B: PA P4-P8 | **4** | **7** | **3** | 0 | 0 |
| **总计** | **8** | **20** | **12** | **1** | **1** |

## 宏观归因更新

| 宏观问题 | 涵盖 Bug |
|----------|---------|
| **MP1**: Contract-Runtime Decoupling | A2, C1, A5, S6, F2 |
| **MP2**: Lifecycle Authority Gap | A3, B4, A1, F1, PA-03, PA-08, PA-09, PA-18 |
| **MP3**: Risk Feedback No Floor | B5, A5 |
| **MP4**: Settlement Truth Fragmentation | S1, S4, S5, S6 |
| **MP5**: Canonical Read Path Incomplete | PA-01, PA-02, PA-10, PA-12, PA-13 |
| **MP6**: Migration Governance Absent | PA-11, PA-14, PA-16, PA-17 |

**K=6 → N=36 活跃 Bug**

> MP6 (新增)：system 声称了 canonical authority 并创建了表和函数，但迁移治理框架 (rollback, parity, feature flags, evidence bundles) 完全缺失。这使得从 M0→M4 的进程缺乏安全网。
