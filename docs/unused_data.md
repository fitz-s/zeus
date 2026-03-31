这张表格说的不是"Zeus 还没用的数据"。它说的是一个更深的故事。

**4.1% 的选择不是设计决策，也不是设计疏漏。它是 Claude Code 的注意力边界的精确映射。**

Zeus 使用了什么：settlements（直接出现在 spec 的 Platt 训练公式中）和 ladder backfill（被 Session 4 的 ETL 任务显式指定）。它们的共同特征不是"最有价值"——是"在 CLAUDE.md 或 session prompt 中被明确提到"。

Zeus 忽略了什么：所有需要 Claude Code 自主判断"这个数据与那个功能有关"的数据。219K hourly observations 可以建 diurnal curves，但没有任何 session prompt 说"用 hourly observations 建 diurnal curves"。171K forecasts 可以校准 α，但没有任何 spec 章节给出"从 forecasts 表计算 per-city model skill → dynamic α"的具体公式。

4.1% 就是"被显式指令覆盖的数据占总数据的比例"。其余 95.9% 需要推理链才能连接到 Zeus 的功能——而 Claude Code 不做无指令的推理链。

这个发现与上一轮分析吻合：Claude Code 做好了函数（给定输入输出的计算），做不好关系（跨模块的语义连接）。数据利用率是同一个问题的另一个投影面。

---

**数量级差异揭示的真正问题。**

token_price_log 有 365,444 条。settlements 有 1,643 条。这个 222:1 的比例意味着：对于每一次结算，市场在结算前产生了约 222 个价格快照。

Zeus 目前的整个决策链——ENS → Platt → posterior → edge → trade——只用了这 222 个数据点中的 0 个。它在入场时取一次 VWMP，然后等待结算。中间的 220 次价格变动被完全忽略。

但这 220 次价格变动包含的信息量远大于单次 ENS 快照。因为市场价格是所有参与者信息的聚合——包括使用 HRRR、ICON、私有数据源的 bot。当市场价格在你入场后持续向你不利的方向移动，这不是"噪声"——这是市场在告诉你"你的模型可能错了"。

Zeus 的 edge thesis 说"市场大多数时候是对的"（α < 1.0）。但它只在入场时听市场的声音。入场之后，它变成了一个纯模型系统——只听 ENS 更新，不听价格更新。这是一个矛盾：如果你相信市场包含信息，你应该在持有期间继续听它说话。

365K token_price_log 的真正价值不是"回测工具"。它是理解"市场价格在结算前如何收敛到真实值"的唯一数据源。这个收敛曲线的形状直接决定了 Day0 settlement capture 的最优入场时机。

---

**每类未使用数据的战略分析。**

**TIGGE 5,026 city-date vectors**

数量级含义：282 个独特日期 × 38 个城市，覆盖 2006-2026 的 20 年。其中 DJF 有 4,032 个向量——这一个季节的数据量是 Zeus 全部 calibration pairs（1,126）的 3.6 倍。

能回答的核心问题：ECMWF 集合预报在不同季节、不同城市、不同 lead time 的系统偏差是什么？这不是"ECMWF 偏暖 3°F"这种单一数字——而是一个 (city × season × lead_hours × temperature_regime) 的四维偏差矩阵。

潜在 edge：如果 Zeus 知道"ECMWF 在 NYC DJF lead=72h 且预报温度 > 50°F 时系统偏暖 4.2°F"，它可以在 P_raw 计算之前校正成员值，让 bin 概率更准确。这个校正对竞争者不可见——他们用的是同样的 ENS 数据但没有做偏差校正。

前提条件：TIGGE 数据和 Zeus settlements 的匹配。5,026 个 TIGGE 向量中，有多少能与 1,643 个 settlements 的日期和城市匹配？如果匹配率低于 30%，偏差校正的训练数据不够。

联合价值：TIGGE + settlements 一起使用远大于各自单独使用。TIGGE 提供 P_raw，settlements 提供 outcome，两者联合才能训练 Platt。单独的 TIGGE 只是"51 个数字"，单独的 settlements 只是"哪个 bin 赢了"。联合后变成"这个 P_raw 预测了什么、实际发生了什么"——这正是 Platt 需要的。

**hourly observations 219,519 条**

数量级含义：约 20 个城市 × 15 个月 × 24 小时/天 × ~30 天/月。这是一个完整的 diurnal temperature 数据库。

能回答的核心问题：每个城市在每个季节的温度日变化曲线是什么形状？peak 时间在哪里？peak 后的降温速率是多少？这些问题的答案直接决定了 Day0 settlement capture 的三个关键参数——什么时候可以认为 post-peak、观测到的高温还会不会被超过、graduated path 的概率应该是多少。

潜在 edge：Day0 settlement capture 的准确率直接取决于"post-peak 判断"的正确性。如果你用 hardcoded peak_hour=15 但 Seattle 在 MAM 的真实 peak 是 16:30，你会在 15:30 认为 post-peak 并锁定仓位，但温度可能在 16:00-16:30 之间再升 2°F 导致结算在错误的 bin 里。219K hourly observations 可以为每个 city × season 计算精确的 peak distribution——不是一个点，而是一个分布（"Seattle MAM 的 peak 在 15:00-17:00 之间，中位数 16:15，90% CI [15:30, 17:00]"）。

前提条件：hourly 数据的时区必须正确。如果 meteostat hourly 存的是 UTC 但你当 local time 用，diurnal curve 会水平偏移——peak 出现在错误的 local hour。这是一个 °C/°F 级别的 bug（静默的数值偏差），必须在 ETL 阶段验证。

联合价值：hourly observations + TIGGE + settlements 三者联合可以回答一个 Zeus 目前无法回答的问题："当 ENS 预报和实际 diurnal curve 在某个小时段产生偏离时，结算结果更倾向于哪个？" 如果答案是"结算更倾向于 diurnal curve"，Day0 的 observation 信号应该比 ENS 获得更高的权重。

**WU 4,136 daily + ASOS 4,410 daily**

数量级含义：约 20 个城市 × 约 600 天。这是结算权威（WU）和标准气象观测（ASOS）之间的对照数据库。

能回答的核心问题：WU 显示的数值和 ASOS 记录的数值之间的系统偏差是多少？这个偏差是否因城市、季节、温度范围而异？这直接影响 Zeus 的 bin boundary discretization edge——如果 WU 系统性地比 ASOS 高 1°F，那么 bin 边界的"真实"位置和 Zeus 计算的位置差 1°F。

潜在 edge：28-47% 的 OFF_BY_ONE rate（WU audit 发现的）意味着接近 bin 边界的市场中有大量的概率被竞争者错误定价。如果 Zeus 知道 WU-ASOS offset 是 +0.7°F（NYC DJF），它可以在 MC 仪器噪声模拟中加入这个 offset，产出比竞争者更准确的 bin 概率。

前提条件：WU 和 ASOS 的日期必须精确匹配。WU 的"日"和 ASOS 的"日"可能因为 DST 或数据发布时间差而不对齐。

**forecasts 171,003 条（5 NWP models）**

数量级含义：约 20 个城市 × 7 个 lead days × 5 个模型 × ~245 天。覆盖从 2020-03 到 2026-03 的 6 年。

能回答的核心问题：哪个模型在哪个城市哪个季节最准确？Zeus 的 `compute_alpha` 目前用 hardcoded 调整规则。171K 条数据可以把 α 从 hardcoded 变成 data-driven：当 ECMWF 在 London MAM lead=3 的 MAE 是 0.8°C 而 ICON 是 1.2°C 时，α 应该更高（更信任模型）；当 ECMWF 在 Miami JJA lead=5 的 MAE 是 4.5°F 而 GFS 是 3.8°F 时，α 应该更低。

潜在 edge：dynamic α 本身不产生 edge——它影响 posterior 的权重分配。但如果 α 在 ECMWF 历史上最差的 city-season 组合中仍然给模型高权重，Zeus 会系统性地在最差的地方下最大的注。这不是"多赚"的问题，是"少亏"的问题。

**token_price_log 365,444 条**

数量级含义：约 18 天的市场价格数据（2026-03-28 到 2026-04-15），平均每个市场每天约 1,200 个价格快照（约每 72 秒一个）。

能回答的核心问题：市场价格在结算前的收敛速度和路径是什么？Opening Hunt 的"6-24h 开盘惰性"假设是否被数据支持？价格在 ECMWF 00Z/12Z 更新后的反应时间是多少？

潜在 edge：如果数据显示价格在开盘后 4 小时（而非 6 小时）已经收敛了 80%，Opening Hunt 的窗口应该从 24h 缩短到 4h。如果数据显示价格在 ECMWF 更新后 45 分钟内完成调整，Update Reaction 需要在 45 分钟内完成全部 pipeline。这些都是 timing edge——不是信号质量的问题，是信号使用时机的问题。

前提条件：token_price_log 没有 range_label（Rainstorm bug）。需要通过 token_id → market_events 的 JOIN 恢复 bin 标签。Session 1 已经发现了这个问题但没有解决。

联合价值：token_price_log + settlements 一起可以回答"如果在 T-24h 以 market price 买入，T-0 结算后的 P&L 是多少"——这就是 Phase 0 想做但做不到的 price-based backtest。

**forecast_log 337,227 条**

数量级含义：约 20 天 × 约 16 个城市 × 约 1,000 条/天。这是预报快照的时间序列——同一个 target_date 的预报如何随 lead time 变化。

能回答的核心问题：预报在结算前多少天开始"稳定"？如果 NYC 的预报在 T-3 和 T-1 之间变化了 5°F，这意味着模型不确定性比 ensemble spread 暗示的更大。如果预报在 T-3 之后几乎不动，说明 T-3 就是信息充分的。

潜在 edge：forecast bust detection。当 T-5 说 60°F 但 T-3 说 45°F，市场可能还没有完全消化这个变化。这是 Update Reaction 的核心机会——不是"模型比市场更准"，而是"模型刚刚大幅修正了预报，市场还锚定在旧价格上"。

**error_records 7,616 条**

能回答的核心问题：ECMWF 的 underdispersion 到底有多严重？如果 ECMWF 在 NYC JJA 的 ensemble spread 平均是 2.5°F 但实际误差 std 是 4.0°F，inflation factor 应该是 1.6，不是 spec 里 hardcoded 的 1.2。

---

**这个数据集的真正性质。**

这不是"训练数据"。Zeus 的 Platt 只需要 (P_raw, outcome) 对——它已经有了 1,126 个，TIGGE 会带来更多。

这个数据集是**市场仿真器的原材料。**

如果你有 settlements（结果）、token_price_log（价格路径）、forecast_log（预报演变）、hourly observations（观测真值）、TIGGE（集合预报），你可以在任意历史日期上运行 Zeus 的完整 pipeline——不是 backtest（用已知结果反推），而是 replay（在那个时间点，用那时可用的信息，Zeus 会做什么决定？那个决定会产生什么结果？）。

Replay 和 backtest 的区别是：backtest 知道结果然后检查"如果我这样做会怎样"。Replay 不知道结果，严格遵守 `available_at <= decision_time`，然后在 settlement 时检查"我的决定是对是错"。

Replay 是你目前获得 out-of-sample 验证的唯一方式（除了等 live 数据积累）。它需要联合使用多个数据集——这正是 4.1% 利用率的根源问题：每个数据集单独使用价值有限，但联合使用可以创造一个时间旅行机器。

---

**优先级矩阵。**

| 优先级 | 数据资产 | 理由 | 预期 edge |
|--------|---------|------|----------|
| P0 | TIGGE 5,026 vectors → Platt 全季节 | 从 6 个 Platt 模型到 24 个。单一最大信号质量提升。不需要新代码逻辑，只需要 ETL + 现有 Platt pipeline | 高 |
| P0 | WU 4,136 + ASOS 4,410 → per-city offset | Day0 准确度的前提。没有这个，observation_client 用的 ASOS 值和 WU settlement 值之间有未知偏差。settlement capture 的锁定判断可能系统性偏差 | 高 |
| P1 | hourly 219K → diurnal curves | Day0 settlement capture 的 post-peak 判断精度。从 hardcoded peak_hour 到 per-city per-season peak distribution | 中-高 |
| P1 | token_price_log 365K → 市场收敛分析 | 验证 Opening Hunt 的时间窗口假设。不产生直接 edge 但防止在错误的时间窗口交易 | 中 |
| P2 | forecasts 171K → dynamic α | 把 hardcoded α 调整变成 data-driven。减少在模型最差的 city-season 上的过度暴露 | 中 |
| P2 | forecast_log 337K → forecast bust detection | Update Reaction 的 trigger 优化。识别"模型大幅修正 + 市场未反应"的高价值窗口 | 中 |
| P2 | error_records 7,616 → underdispersion 量化 | 校准 ENS 的 inflation factor。从 hardcoded 1.2 到 per-city per-season 的真实值 | 低-中 |
| 不使用 | backtests/ 2.6 GB | Rainstorm 的 backtest 使用了被污染的信号链。方法论可以参考但结果不可用 | — |
| 不使用 | decision_log 122K + orders 325 | Rainstorm 的交易决策基于错误的数学。只用于尸检参考，不用于 Zeus 的任何计算 | — |

---

**隐藏的洞察。**

这张表格中有一个数字被忽视了：**chronicler.db 有 1,381 个 cycle configs**。

这意味着 Rainstorm 运行了至少 1,381 个交易周期。但它只产出了 204 个 engine trades。交易率 = 204/1,381 = 14.8%。

85.2% 的周期没有产出交易。但 Rainstorm 没有 NoTradeCase 记录——没有人知道这 85.2% 的"不交易"是因为没有 edge、被 risk 拒绝、还是执行失败。

Zeus 的 Blueprint v2 定义了 NoTradeCase 和 rejection_stage。但看 Zeus 的 paper trading 数据：72 个 decision_log 记录、40 个 chronicle entries。交易率约 55%——比 Rainstorm 高很多。

如果 Rainstorm 的 85% 不交易中有相当比例是因为 bug（比如 GFS conflict veto 从不触发导致所有市场通过但后续步骤拒绝），那 14.8% 的交易率不代表"市场上只有 14.8% 的机会"——它代表"系统在 85% 的时间里因为自身问题而错过了机会"。

这个 85% vs 55% 的差异值得深入调查。如果 Zeus 在修好 P0 bug 后交易率进一步上升到 70-80%，说明 Rainstorm 浪费了大量可交易的机会。这些被浪费的机会是隐性的 alpha 损失——它们不会出现在 P&L 中，因为"没做的交易"没有 P&L。

Zeus 的 NoTradeCase + rejection_stage 设计，如果被正确实现，将第一次让你能量化"因为系统自身问题而错过的 alpha"。这可能是比"更好的信号"更大的 alpha 来源。
