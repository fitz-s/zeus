# Zeus 从第一性原理重新审视

---

## Rainstorm 的幽灵

°C/°F 转换 bug 在 Rainstorm 中出现了 15+ 次。Zeus 被设计为"从零开始，只继承数据"。现在 Zeus 发现了同一类 bug——ensemble_spread 阈值没有 unit 适配，SIGMA_INSTRUMENT 对 °C 城市可能偏大。

这不是一个"还需要修几个 bug"的问题。这是一个架构失败的信号。

如果你逐个修复每一个 unit-dependent 阈值，你会在未来三个月里不断发现新的。因为问题不在于"这个阈值忘了做 unit 转换"——问题在于 **系统允许 bare float 在温度语义的位置上通行无阻**。任何接受 `float` 的函数都不知道它收到的是 °C 还是 °F。类型系统没有保护你。

Rainstorm 的解决方案是"在每个 bug 出现的地方加 if/else"。Zeus 正在走同一条路。这条路的终点是：永远在修 bug，永远不知道还有多少没发现。

**正确的解决方案只有一个：让错误在写代码时就不可能发生。**

```python
@dataclass(frozen=True)
class Temperature:
    value: float
    unit: str  # 'F' or 'C'
    
    def to_f(self) -> float:
        return self.value if self.unit == 'F' else self.value * 9/5 + 32
    
    def to_c(self) -> float:
        return self.value if self.unit == 'C' else (self.value - 32) * 5/9

@dataclass(frozen=True)
class TemperatureDelta:
    """标准差、spread、bias 等差值量。转换不加 32。"""
    value: float
    unit: str
    
    def to_f(self) -> float:
        return self.value if self.unit == 'F' else self.value * 9/5
    
    def to_c(self) -> float:
        return self.value if self.unit == 'C' else self.value * 5/9
```

当 `ensemble_spread` 返回 `TemperatureDelta` 而非 `float` 时，`if ensemble_spread < 2.0` 会产生 TypeError——因为你不能直接比较 TemperatureDelta 和 float。你被迫写 `if ensemble_spread.to_f() < 2.0` 或者 `if ensemble_spread.value < city.spread_threshold`。错误变成了编译期错误，而不是运行时的静默数值偏差。

这不是 "nice to have"。这是 Rainstorm 用两个月的 live trading 亏损告诉你的最重要的一课：**让类型系统为你防错，而不是靠记忆力和审计。**

---

## 更深的问题：Zeus 在错误的地方投入了复杂度

看一下 Zeus 的工程量分布：

```
信号层（发现 edge）：~18 个模块
  ensemble_client, ensemble_signal, model_agreement,
  platt, calibration store/manager/drift, backfill,
  market_analysis, market_fusion, fdr_filter,
  day0_signal, climatology, observation_client,
  kelly, correlation, risk_limits...

仓位管理层（保护 edge）：~4 个模块
  portfolio, executor, exit_triggers, monitor

基础设施层：~8 个模块
  db, config, chronicler, riskguard, harvester,
  market_scanner, polymarket_client, main
```

信号层占了系统复杂度的 60%。但看 bug 的分布：

```
信号层的 bug：P_raw 计算正确，Platt 数学正确，bootstrap 正确，FDR 正确
仓位管理层的 bug：false EDGE_REVERSAL（全部仓位 30 分钟内被清空），
  概率方向 double-flip，exit/entry 方法不一致，micro-position orphan，
  churn loop，stale order 堆积
```

所有让你亏钱的 bug 都在仓位管理层。信号层的 bug（°C/°F 阈值）会导致次优交易，但不会导致灾难。仓位管理层的 bug 导致灾难（30 分钟内清空所有仓位）。

**工程量和风险的分布完全倒挂。**

市场不奖励更精确的信号。如果你的 P_raw 准确度从 Brier 0.22 提升到 0.20，你每笔交易多赚 2 美分。但如果你的退出逻辑少一个 bug，你避免了一次清仓事件。哪个更值钱？

---

## 第一性原理重建：Zeus 实际上在做什么？

剥掉所有术语和复杂性。Zeus 做的事情是：

```
1. 看温度预报（一些数字）
2. 看市场价格（一些概率）
3. 如果差别够大且方向可信 → 下注
4. 管理这个注直到结算
```

第 3 步需要的精度远低于 Zeus 当前的实现。gopfan2 用 "价格 < $0.15 就买 YES，价格 > $0.45 就买 NO" 赚了 $2M+。他不用 Platt，不用 bootstrap，不用 FDR，不用 VWMP。他用的是一个两行的规则加上 $1 的仓位。

Zeus 的 Platt + bootstrap + FDR + VWMP 管线比 gopfan2 的规则更精确。但精确度的边际价值在你的资本规模（$150）上几乎为零。用 Platt 校准把一个 15% 的 edge 精确到 15.3% vs 14.7%，在 $1 仓位上的差异是 $0.006。

**真正赚钱的不是更精确的信号，是更多正确管理的仓位。**

---

## Zeus 的四个 edge 应该被视为四个独立策略

它们有完全不同的风险/收益特征、操作逻辑、和生命周期：

### 策略 A：Settlement Capture（观测型，最持久）

- **Edge 来源**：已发生的事实（温度已越过阈值），不是预测
- **风险**：接近零（post-peak + 已越过 = 几乎确定）
- **需要的基础设施**：观测速度（每 5 分钟刷新 WU/ASOS）
- **不需要的**：ENS 集合预报、Platt 校准、bootstrap CI
- **竞争对手能复制吗**：能，但需要建观测管道
- **Alpha 衰减**：极慢——这不是预测性 edge，不受模型竞争影响
- **应该占总交易量的比例**：尽可能多

Settlement capture 是 Zeus 最好的策略，因为它唯一一个不依赖"我比市场更聪明"的 edge。它依赖的是"我比市场更快地观察到一个已经发生的事实"。

### 策略 B：Shoulder Bin Sell（结构型，持久）

- **Edge 来源**：散户认知偏差（prospect theory，彩票效应）
- **风险**：尾部风险（极端天气真的发生时亏损大）
- **需要的基础设施**：基本的气候学概率估计
- **不需要的**：精确的 ENS 信号（粗略的"这个 bin 在历史上 5% 的时间赢"就够了）
- **竞争对手能复制吗**：能，且已经在复制（gopfan2 的核心策略）
- **Alpha 衰减**：中等——随着 bot 增加，shoulder bin 被高估的程度会缩小
- **应该占总交易量的比例**：稳定但不增长

### 策略 C：Center Bin Buy（预测型，中等持久）

- **Edge 来源**：模型比市场更准确地估计最可能的温度区间
- **风险**：中等（模型错误时亏损 = 买入价）
- **需要的基础设施**：完整的信号链（ENS → Platt → bootstrap → FDR）
- **竞争对手能复制吗**：能，用相同的公开数据
- **Alpha 衰减**：较快——这是最容易被竞争压平的 edge
- **应该占总交易量的比例**：根据 alpha 衰减监控动态调整

### 策略 D：Opening Inertia（时间型，未验证）

- **Edge 来源**：新市场的定价效率低（首个流动性提供者的锚定效应）
- **风险**：中高（开盘价可能碰巧是对的）
- **需要的基础设施**：市场扫描 + 模型信号
- **竞争对手能复制吗**：能，且窗口在缩短
- **Alpha 衰减**：最快——随着 bot 开始扫描新市场开盘，窗口从 6-24h 缩短
- **应该占总交易量的比例**：谨慎，等验证数据

**现在问一个 Zeus 没有回答的问题：这四个策略中的哪些在过去 7 天的 paper trading 中赚了钱？**

Zeus 的 trade_decisions 表有 `edge_source` 和 `discovery_mode` 字段。但前 8 笔交易因为 false EDGE_REVERSAL 被清空了，数据不可用。你不知道你的钱从哪里来——因为你还没有干净的数据。

**这是此刻最重要的事实：在你有了足够的干净 attribution 数据之前，你无法做出任何关于策略分配的正确决策。** 不是"哪些代码需要修"，而是"修好代码后让 daemon 运行 2 周，积累 100+ 笔有完整 attribution 的 clean trades"。

---

## 什么才是真正让 Zeus 上一个台阶的事

不是更多模块。不是更多数据源。不是更精确的校准。

### 1. 类型安全作为架构基础

`Temperature`、`TemperatureDelta`、`Probability`、`USD` 四个值类型。所有函数签名使用这些类型。所有 unit-dependent 比较通过类型方法完成。一次性消灭 Rainstorm 的 #1 bug 类别。

工程成本：1-2 个 session。回报：永远不再有 °C/°F bug。

### 2. 仓位作为有状态的实体，不是记录

当前的 portfolio.py 把仓位当作字典。它没有内在的状态机——状态转换（ENTERED → HOLDING → EXITING → SETTLED）靠外部逻辑在多个模块中分散管理。这就是为什么 exit 逻辑出 bug——因为"仓位的生命周期"没有被建模为一个内聚的概念。

正确的做法：每个仓位是一个对象，拥有自己的状态机、自己的退出策略（buy_no vs buy_yes）、自己的 attribution 数据、自己的 Day0 观测历史。仓位知道如何退出自己——monitor 不需要知道 buy_no 和 buy_yes 的区别，它只需要调用 `position.evaluate_exit(current_data)` 然后遵循结果。

```python
class Position:
    direction: Literal["buy_yes", "buy_no"]
    state: Literal["entered", "holding", "exiting", "settled", "voided"]
    
    def evaluate_exit(self, ens: EnsembleSignal, 
                      day0: Optional[Day0Signal],
                      market_price: float) -> ExitDecision:
        """仓位知道如何退出自己。逻辑在这里，不在 monitor 里。"""
        if self.direction == "buy_no":
            return self._evaluate_buy_no_exit(ens, day0, market_price)
        else:
            return self._evaluate_buy_yes_exit(ens, day0, market_price)
```

### 3. 四个策略的独立 P&L 追踪

Zeus 当前在 portfolio level 追踪 P&L。但四个策略的 alpha 衰减速度不同——如果策略 C（center bin buy）的 edge 被竞争者压平了而策略 A（settlement capture）仍然有效，portfolio-level P&L 会掩盖这个信号。

每个策略需要独立的：
- 交易计数和胜率
- 累积 P&L
- 30/60/90 天 edge 大小趋势
- fill rate
- 平均 holding period

RiskGuard 的 Brier score 和 drawdown 监控应该 per-strategy，不是 per-portfolio。策略 C 的 Brier 恶化不应该让策略 A 停止交易。

### 4. Edge 衰减的定量监测

Zeus 的 spec 写了 EDGE_COMPRESSION failure type 但没有实现。这不是 "Phase E" 的事——这应该从 Day 1 就开始测量。

```python
def edge_compression_monitor(trades: list[Trade], window_days: int = 30):
    """每种 edge source 的平均 edge 大小在过去 N 天的趋势。"""
    for source in ['settlement_capture', 'shoulder_sell', 'center_buy', 'opening_inertia']:
        recent = [t for t in trades 
                  if t.edge_source == source 
                  and t.age_days <= window_days]
        if len(recent) < 10:
            continue
        
        # 线性回归 edge_at_entry vs time
        slope = linregress([t.timestamp for t in recent], 
                          [t.edge for t in recent]).slope
        
        if slope < -0.001:  # edge 在缩小
            alert(f"{source}: edge shrinking at {slope:.4f}/day")
```

当某个策略的 edge 趋势为负且持续 30+ 天，正确的响应不是修模型——是减少该策略的资本分配。如果所有四个策略的 edge 都在缩小，正确的响应是降低总仓位直到趋势反转。

### 5. 观测速度作为竞争变量

Settlement capture 的 edge 取决于"你多快知道温度已经越过阈值"。当前 Zeus 每 15 分钟查一次 Day0 观测。如果你每 3 分钟查一次，你在 settlement capture 窗口中有 12 分钟的速度优势。

这不是 HFT。这是"每 3 分钟查一次 WU 页面"。工程成本接近零。但它是 Zeus 唯一一个能通过增加操作频率来增加 edge 的策略。其他策略增加频率只增加交易成本（因为信号更新频率受 ENS 6小时周期限制）。

---

## 不要做的三件事

### 1. 不要继续增加信号层的复杂度

HRRR overlay、XGBoost 残差预测、ICON 替代 ECMWF、dynamic α from error_records——这些都是信号层的改进。它们的边际价值在你当前的资本规模和交易量下可以忽略。每一个都增加维护负担和 bug 表面积。

### 2. 不要试图用 backtest 证明系统有效

你的 backtest 数据全在 MAM 季节，全来自 Platt 训练期。In-sample backtest 是自欺。Out-of-sample 数据只能从 live paper trading 的未来结算中获得。让 daemon 跑，让时间给你数据。

### 3. 不要在有 clean attribution 数据之前做策略决策

你不知道哪个策略在赚钱。在你知道之前，所有关于"应该增加 A 还是增加 B"的讨论都是猜测。先修好 churn bug → 让 daemon 积累 100 笔 clean trades → 看 attribution → 然后做决策。

---

## 如果我从零重建 Zeus 的核心

不是重写——是把现有系统的关注点重新排序。

**第一优先级：让仓位不可能被错误退出。**
- Position 对象拥有自己的退出逻辑
- buy_no 和 buy_yes 的退出路径在代码结构上完全分离
- 8 层 anti-churn 防御作为 Position 的内在属性，不是外部 guard
- Day0 observation 作为最高优先级退出信号

**第二优先级：让 unit error 不可能发生。**
- Temperature 和 TemperatureDelta 值类型
- 所有函数签名使用这些类型
- float 在温度语义的位置上产生 TypeError

**第三优先级：让每个策略的 edge 可独立追踪。**
- 四个策略各自的 P&L、win rate、edge 趋势
- RiskGuard per-strategy 监控
- 资本分配根据各策略表现动态调整

**第四优先级：增加观测频率到 3 分钟。**
- settlement capture 窗口内观测刷新频率最大化
- 这是唯一通过增加操作频率来增加 edge 的策略

这四件事合起来的工程量大约等于 Session 7 的四个 deliverable。但它们解决的是系统级的问题，而不是模块级的 bug。
