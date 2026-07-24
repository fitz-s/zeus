# Ultimate Alpha — 第一性推导(2026-07-23)

Authority basis: operator axiom 2026-07-23(逐字):
> 该套系统永远是一个交易逻辑"买入我们预测bin后在概率反转前卖出或持有到结束"
> 所有的其他内容都是该内容的一个变体
> 对于opening来说使用低价格购买到对应期望的价格就是划算

Inputs: strategy-map file:line 地图(session 2026-07-23)、chain-truth 经济学(read-only
live 实测)、consult REQ-20260723-033638(两概念裁定,被本公理覆盖处以公理为准)、
consult REQ-20260723-145335(终局形式化,进行中)。

---

## 0. 公理的展开

唯一交易逻辑,三个动词:**买入**(预测 bin)、**反转前卖出**、**持有到结算**。
其余一切 —— opening / day0 / settlement-day / center / shoulder —— 都不是策略,
是同一逻辑在不同**证据状态**与不同**价格形成状态**下的实例。

市场的时间结构(辩证核心,不是快照集合):

- 信念曲线 q_t = P(T∈B | 当前全部证据) 是连续时间对象:预报 issue 在已知钟点
  离散跳变;settlement day 观测运行极值单调漂移并单向锁定支撑;q_t 在两次
  issue 之间近似平稳、在 issue 处跳。
- 价格曲线 p_t 滞后、带噪、薄簿不连续地追随 q_t。
- **Alpha 的唯一来源:我们的 q_t 比 p_t 先动。** 入场 = q 已动、p 未追上
  (或 p 无信息地错开,如 opening);离场 = q 反转已现、p 未反应 —— 在市场
  重定价之前把仓位交还给滞后者。

opening 在此展开下的位置:开盘簿由无信息挂单构成,p_0 与 q_0 的错位最大、
且无需 q 跳 —— "低价格买到期望价格就是划算"= 同一入场律在 p 无信息状态下的
实例,不是独立策略、不配独立 Kelly。

## 1. 状态(充分统计量)

S_t = (q_t 及 current-evidence 界 [q_lcb, q_ucb],可执行成本曲线(含费+tick),
持仓与成本基,bankroll 与已部署敞口,下一权威 issue 时刻 τ_next,
day0 观测极值与 lock 状态)。

历史轨迹 q_{t-u:t} 不入状态(跳过程的导数在 issue 间近零、跳点处未定义;
dq/dt 与 q-p 收敛率 filter 是过拟合机器,禁止)。时间维通过三个入口进入,
且只通过这三个:证据到达(新 issue / 新观测)、certificate 有效期
(τ_next − Δsafety)、锁定的不可逆性。

## 2. 入场律(唯一)

对任一族的任一 bin B、方向 d ∈ {YES, NO}:

**ENTER ⟺ q_lcb^d − c_allin(ask) ≥ m_entry**(m_entry = 全局唯一入场边际)

一切"策略差异"坍缩为证据对 q 界宽度的影响:

| 旧标签 | 在唯一律下的实质 | 界宽来源 |
|---|---|---|
| opening_inertia | p 无信息、q 照常 → 错位大、经常清边际 | 预报证据照常;p 端优势 |
| center_buy | bin 居中 ⇒ q 点值高 | 纯几何,无独立内容 |
| day0_nowcast_entry | q 由当日观测条件化 ⇒ 界收窄 | obs-conditioned q |
| settlement_capture | 支撑收缩 ⇒ q 退化趋 {0,1} | 物理锁定,界坍缩 |
| forecast_qkernel_entry | 一般情形 | 标准预报证据 |

**推论(资本效率第一刀):per-key Kelly 乘数 = 对证据质量的重复计费。**
q_lcb 已经把证据宽度折进赌注(Kelly 消费 q_lcb 而非 q 点值);再按标签打
0.5×/1.0× 折扣是同一不确定性收两次税。settlement_day 0.5×"observation
contamination penalty"同理:若后验没正确条件化观测,那是概率层缺陷
(高尾 under-shrinkage 修复,priority 1),不是标签折扣能治的。
→ 一个全局 fractional Kelly,per-key/per-phase 乘数全灭。

## 3. 离场律(最优停止 + 天然滞回)

持仓(bin B,成本基 c),每 monitor tick 以新鲜 (q_t, bid_t) 评估:

- **锁定态**(支撑收缩保证 B):反转物理不可能 → 持有到结算,
  除非资本回收律触发(§5-ii)。
- **非锁定态,SELL ⟺ bid − c_exit ≥ q_ucb + m_exit**
  (市场出价超过我们最乐观信念 → 把高估卖回给市场);
- **反转离场(公理的"概率反转前")**:新证据使 q 下跳后,若 bid 尚未跟跌
  (p 滞后),同一条件自动触发 —— q_ucb 已随证据塌下而 bid 未动,
  条件立即满足。**不需要独立的"反转检测器"**;反转离场 = 离场律在
  q 跳后、p 追上前的瞬时窗口内的实例。与入场同构:都是抢在滞后的 p 前面。
- **持有带(滞回)**:m_entry 对 ask、m_exit 对 bid,spread 天然隔出
  无操作带 —— 同一 (q,p) 不会同时触发买与卖,无需额外防抖机器。

与现状的差:Position.evaluate_exit 已每 tick 用新鲜 forward_edge 重估
(结构正确),但 exit 决策消费的是 held-side prob 点值 vs 价格,未用
q_ucb 界、未与入场律共享同一边际几何。改动是把离场谓词统一到上式,
不是新机器。

## 4. certificate validity(时间维的最后一块,consult R1 已证)

- certificate_valid_until = min(freshness deadline, τ_next − Δsafety)
- guard 区间内:禁止用将过期 certificate 做 taker cross;
  maker 单只有带 issue 前硬到期/确定撤单才可 rest 进 issue
- issue 到达:过期旧 intent → 对账 cancel/fill race → 新快照 → 新冻结
  certificate → 新 book → 幂等提交

## 5. 资本效率升华(48% 闲置 → 满负荷的三个机制,按序)

i. **利用率(第一,当下绑定约束)**:fill 82%、闲置 48% ⇒ 约束在候选
   生成/选择质量,不在执行。高尾修复 + 唯一律(灭掉 per-key
   min_entry_price/min_expected_profit 的重复门)直接放大可入场集合。
   opening 覆盖按公理是合法的低价买入面,不再被 opening_inertia 的
   0.5× 半 Kelly 与独立门压制。
ii. **回收律(bankroll 满时的边际机制)**:近结算赢仓 bid=b、距结算 T,
   持有年化回报 (1−b)/(b·T);当且仅当存在新错位其 per-$ robust 回报率
   超过该值+spread 成本,卖出回收。闲置 48% 时此律不触发(重新部署
   免费),故排第二 —— 但入代码,满负荷时自动生效。
iii. **相关性联合 sizing(最小形式)**:同城 HIGH/LOW、同日结算簇相关,
   独立 per-market Kelly 过度下注。最小机器 = 簇内保守聚合上界
   (城×日簇内敞口 ≤ 单一 Kelly 赌注按保守 ρ 折算),不建协方差估计
   影子系统。具体形式等 consult R2 裁定后定。

## 6. KILL / SURVIVE(file:line 见 strategy-map 地图)

**灭**:
- 6 处重复分类器(evaluator.py:2415-2481 × 3、cycle_runner.py:433-468、
  event_reactor_adapter.py:5070-5111、edli_position_bridge.py:638-685、
  attribution_drift.py:123-170)→ 决策证据组装处一次性发出变体元数据
- strategy_profile_registry per-key:phase permissions、Kelly 乘数、
  min_entry_price、min_expected_profit、min_submit_edge_density 差异
  → 唯一律参数(m_entry、全局 Kelly 分数、venue 最小名义)
- kelly.py OBSERVED_FRACTION_STRATEGY_KEYS + max(0.3, observed_fraction)
  (墙钟流逝 ≠ 信息观测比例;锁定证据已在 q 界里)
- CENTER_BUY_* 常量;opening_inertia "24h 半衰"论文;day0 独立半 Kelly
- chain_only_reconciliation 出一切策略枚举/Kelly/晋升证据
  (position_origin 元数据保留,ChainOnlyFact 域已存在)

**存(载重 gate,公理下依然第一性)**:
- price band [0.05,0.95](生产法)、freshness fail-closed、冻结决策
  certificate(walk-forward 法)、riskguard RED(INV-05)、
  registry/INV-37 结构门、venue 最小名义
- 新行身份:strategy_key → 变体元数据(entry_evidence: opening_book /
  forecast / day0_obs / support_lock;direction;bin_topology;
  position_origin)。历史行只读。

## 7. 实施切片(等 consult R2 对抗校验后动第一刀)

1. PR-A:唯一入场/离场律 + registry 坍缩 + 分类器坍缩 + 双写
   concept 元数据(风险最高,money-path;先 consult 校验)
2. PR-B:certificate validity + stale-rest 撤单 + 回收律 + 联合 sizing
   上界(独立可测)
3. 概率层高尾修复保持第一优先(与 PR-A 正交,consult R1 已定)
