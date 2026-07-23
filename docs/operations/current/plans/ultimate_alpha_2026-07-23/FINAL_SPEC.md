# Ultimate Alpha 终局规格(consult R2 REQ-20260723-145335,置信 0.92)

Authority basis: operator axiom 2026-07-23(唯一交易逻辑)+ consult 终局形式化
(全文 /tmp/cgc/answer_REQ-20260723-145335-8c3c06.txt 已核;本文件为可实施浓缩,
与 DERIVATION.md、SURFACE_INVENTORY.md 同目录成套)。

## 唯一决策律:predicted_bin_ev_v1

新 Zeus 决策行:`decision_law_id="predicted_bin_ev_v1", strategy_key=NULL,
position_origin="zeus_decision"`。外部 co-trade:`decision_law_id=NULL,
position_origin="operator_cotrade"|"external_wallet"`。历史 strategy_key 只读,
永不回填因果身份。

充分状态:S_t = (q⁻,q,q⁺ native 侧界; A(x) 深度加权买入成本曲线;
B(x) 深度加权卖出所得曲线【exit 停止问题的必要新增】; 持仓 h(含外部,只计风险);
自由现金 F 与 bankroll W; τ_next 下一权威 issue; Day0 极值 O 与 lock 态 L;
RiskGuard 姿态 R)。

NO 侧界必须 native:q⁻_NO = 1−q⁺_YES(禁止 1−q⁻_YES)。
锁定:不可能 → (0,0,0);保证 → (1,1,1)。center/shoulder 几何属于 payoff
preimage,不属于身份或 sizing。

## 入场律(一条)

C⁺(x) = A(x)+fees+cost-uncertainty;G⁻(x) = x·q⁻ − C⁺(x) − M_e(x)(M_e=全局
唯一摩擦边际,~1 tick/share)。
**ENTER ⟺ x* > 0**,x* 为全部当前合格 claim 的联合 fractional-Kelly 解。
正 robust EV 必要;数量由资本+相关性定 —— 正 EV 拿零资本是合法最优,不是第二策略。
非 alpha 门只剩普适集:有效 claim/几何、开市未决、概率+报价+观测 freshness、
certificate 在完成前有效、价带 [0.05,0.95]、RiskGuard、族排他/重复 token、
数值健全、venue 最小单。**无 opening/day0/center/lock/方向许可。**

## 离场律(最优停止,含天然滞回)

L(x) = B(x)−fees−exit-uncertainty;H⁻(x) = x·q⁻(**当前 q⁻ 已积分全部剩余
天气/预报事件到终值概率 —— 禁止再乘 observed-day-fraction / 剩余小时 / issue 衰减**)。
J_t(F) = 联合 allocator 对现金的稳健确定性等值增量。
**SELL(x*) ⟺ max_x [(h−x)q⁻ + L(x) + J(F+L(x)) − M_x] > h·q⁻ + J(F)**
只在 bid-depth 断点上求值(部分退出免费获得)。两个审计码同一律:
SELL_REVERSAL(清算本身占优)/ SELL_REALLOCATE(替代机会使其占优)。
滞回带:b⁻(1+r_t)−m_x < q⁻ < a⁺+m_e,宽 (a⁺−b⁻)+m_e+m_x−b⁻r_t。
入场价/入场 q 是沉没信息,不入停止规则。dq/dt、速度、重复周期确认全废。
**锁定≠无条件持有**:q⁻=1 时 recycle ⟺ r_t > 1/b⁻ − 1(bid .985→需 1.52%,
.995→0.50%);现金不紧 r_t=0 时卖锁定赢仓"释放资本"是错的。
**Day0 RED 豁免必须删**(portfolio.py:1045-1047):RED 与证据相位正交。

## certificate validity

valid_until = min(概率/报价/观测 freshness deadline, τ_next−Δ_cancel, 收盘)。
taker 仅当最坏完成时刻 < valid_until;maker 仅带 issue 前硬到期/撤单缓冲;
过期 certificate 不因 book 改善复活;issue 到达走九步原子序
(过期→撤单→对账 race→更新持仓现金→存快照→新冻结 certificate→合新 book→
一律+allocator→带 certificate+epoch 幂等键提交)。缺 next-issue 元数据 → fail closed。

## 联合 Kelly(最小相关形式,无协方差学习)

孤立解保持对角情形:f_i = κ(q⁻−c)/(1−c)(即现行分数 Kelly)。
Σ_ij = ρ_ij·√(v_i v_j),v=(1−c)/c;ρ 只用现有结构权威:
同 city×metric×date 族仅一 claim 得新资本;同城同日 HIGH/LOW ρ=1;
已知 synoptic 簇同日 ρ=1;异城同日用现有 get_correlation;异日 v1 零相关额度;
同日关系未知 ρ=1(不是 0);负相关永不放大敞口;外部仓计固定敞口
(无 q 者按最保守方差)。非 PSD → 特征值零地板+对角重整(数值卫生);
solver 失败 → 该 epoch 不下新资本,禁止回退独立 Kelly。
求解:max_f≥0 [μᵀf − (1/2κ)((f₀+f)ᵀΣ(f₀+f) − f₀ᵀΣf₀)] s.t. 1ᵀf ≤ F/W +
深度/前缀/族排他/venue 最小/价带/certificate 约束;确定性投影坐标上升。
公平性 = 调度性质(发现/监控服务),不是资本均等。
INV-05 = 数值完整性契约;f_i=0 是合法优化结果不是坍缩。

## 资本回收

r_t(x) = [J(F+L(x))−J(F)]/L(x) 是唯一因果机会成本信号;现金不紧 → r=0 →
不回收;静态日门槛不需要。原子回收序:不可变 reallocation plan(源仓+数量+
目标 certificate+组合/银行版本)→ 卖源 → 对账 fill → 重验目标 → 重跑 allocator →
仍中选才提交目标;目标失效则持现金,永不自动重开源仓。
**运营目标:cash-constraint 错失稳健价值 ≈ 0,不是 idle ≈ 0**(强制部署=
把无边际转成负选择)。仪表盘:cash 影子回报、因缺现金/相关性/certificate
过期被拒的稳健 EV、到期前提交合格名义、实现换手、allocator 选择的利用率。

## $/周 期望(规划量级,不可相加)

1. 高尾 q 修复+受影响 cell fail-closed:~$30(10–50)/周 —— **仍是第一优先,
   位置在一律之前的概率权威层;joint Kelly 让它更紧迫(偏置 q⁻ 会跨城相关配资)**
2. 标签经济学坍缩入一律:~$8(4–14)
3. certificate 到期+release 边界撤单:~$4(2–8)
4. 联合相关 Kelly:~$10(5–18)+几何增长/回撤改善
5. 统一离场滞回:~$4(2–9)
6. 资本回收:~$2(0–8,48% 闲置时多数 epoch 不触发)
替换为生产估计所需最小数据集:带时间戳反事实 replay(每候选 q⁻/q/q⁺、成本/
bid 曲线、certificate 寿命、拒因、持仓、现金、最终 fill)。

## KILL(逐面,file:line 详见 consult 全文 + SURFACE_INVENTORY.md)

DELETE evaluator.py:2415-2481 双 dispatch;reactor_adapter.py:5070-5192 +
9353-9379 + 24226-24525 事件分类器/registry 准入/per-key floor;
kelly.py:65-223 策略/相位/observed-fraction 乘数 + 424-516 乘数链→全局 κ;
family_decision_engine.py:247-335 绝对概率/利润/密度门(真固定成本入 C⁺);
portfolio.py:907-1805 exit 树坍缩 + 3892-4059 方向/近结算/速度分支
(应急保护归 RiskGuard);hold_value.py 静态日门槛+相关附加费→注入 ΔJ;
riskguard/policy.py:160-367 StrategyPolicy→DecisionLawPolicy;
registry yaml + strategy_profile.py 出 money path(留只读历史解码);
全部 strategy_key 写入停止(新行 decision_law_id);6+2 键全部退出活身份。
另:SURFACE_INVENTORY 的 4 个 registry 外税表(command_recovery.py:133、
live_admission.py:32、promotion_proof_router.py:59-82、riskguard.py:1095)
必触;10 张表 schema 迁移(nullable 化+新列)。

## BUILD(最小集)

new src/decision/predicted_bin_law.py(纯函数入场+停止);
decision_evidence.py 扩 certificate 字段(valid_until、next_issue、lock_proof、
组合/银行版本)+ continuous_redecision 接线;new src/strategy/joint_kelly.py;
allocation_epoch(冻结候选/组合/现金/相关态,提交前 CAS);
reallocation plan(exit 执行器+allocator);身份/来源迁移(10 表)。

## 存活 guardrail

价带 [0.05,0.95](锁定也不豁免入场)、freshness fail-closed、冻结 certificate、
certificate validity(新增)、RiskGuard RED(全局、相位无关)、INV-05 数值契约、
typed ExecutionPrice、族排他/重复 token、结算语义/观测源权威、position
provenance(外部仓计钱包/相关风险不计 alpha)、公平调度、不可变决策/离场证据。

## 两 PR 迁移序(rollback 内建)

**PR-1(一律一身份一停止)**:nullable 新列(decision_law_id/position_origin/
valid_until/next_issue/lock_proof/版本)+ DB 约束(常量或 NULL)→ 高尾 cell
fail-closed → predicted_bin_law.py → 同冻结态双算(先不改单)→ 换掉两个分类器
→ certificate 到期+release 撤单 → evaluate_exit 统一 → RiskGuard 全局化 →
单个临时回滚 flag → 一个完整结算 cohort 后拆 flag。存量仓不重定尺寸。
**PR-2(联合资本+回收)**:结构 Σ + PSD 卫生 → 联合解 → allocation_epoch →
shadow 对比现行独立 sizing → 启用 → ΔJ 暴露给 exit → SELL_REALLOCATE+部分
退出 → 停写 strategy_key → registry/旧 health 归档 → 拆死分支与迁移 flag。
回滚:PR-1 关新执行器(证据双写保留);PR-2 关联合配置/回收;回滚永不恢复
过期 certificate 执行;旧列可查不可获权。

## 并发/边界(实施时的验收清单)

CAS on portfolio_version/bankroll_version;幂等键含 certificate_id+
allocation_epoch_id+token+side+action;旧单终态未知不提交替代资本;晚 fill
更新 f₀ 并作废 epoch;预报+观测同时到 → 合一状态一次决策;乱序 issue/极值
回退拒绝(除权威更正);同证据快照重复周期不算独立确认;solver 不收敛不下单。
无 bid → 持有(除 RED);q 缺/stale → 不入场,持仓 EVIDENCE_UNAVAILABLE;
锁输 q=0 → net bid>切换成本即卖;锁赢 q=1 → 默认持有,recycle 按不等式;
含极值的有限 bin ≠ 锁定;NO 用 native 界;开 shoulder 只在结算语义证明保证时
吸收;目标失效持现金;部分 fill 先对账再目标单;f=0 合法;外部仓未知信念按
最大保守方差;价带外锁定也不入场。
