# Fable × GPT-5.6 对撞裁定 → 最终实施契约(2026-07-23)

两个模型族独立推理同一问题后的合并裁定。5.6 终局(FINAL_SPEC.md)大部分成立;
四处被 Fable 以本地运行时事实/操作员法**覆盖**。本文件是实施的唯一契约,
与 FINAL_SPEC 冲突处以本文件为准。

## 接受(5.6 正确,直接采用)

一律 predicted_bin_ev_v1 与充分状态(含 bid 曲线 B(x) 补入);入场律
G⁻(x)>0 必要 + 联合 Kelly x*>0 充分;停止律 V_sell vs V_keep 与滞回带
b⁻(1+r)−m_x < q⁻ < a⁺+m_e;锁定≠无条件持有(recycle ⟺ r > 1/b⁻−1);
入场价为沉没信息;dq/dt/速度/重复确认全拒;certificate validity 九步原子序;
结构 Σ(ρ 只用现有权威,未知同日 ρ=1,负相关零额度,PSD 卫生,solver 败
则该 epoch 不下新资本);回收原子序;f=0 合法;Day0 RED 豁免删除;
NO 侧 native 界(q⁻_NO = 1−q⁺_YES);全部 kill/build 清单与存活 guardrail。

## 覆盖(Fable 裁定,基于 5.6 看不到的本地事实)

**C1 无 shadow —— 操作员法直接冲突。** 5.6 的"dual-compute 一个结算 cohort
不改单"与"PR-2 allocator run in shadow"均为 shadow 系统;操作员法
(no-shadow-no-gate-accretion 2026-06-13,shadow-diagnostic-extinction 先例)
明令禁止。替代:**离线 counterfactual replay**(scripts/replay_parity.py 是
现成的本迁移类回归检测器)+ 纯函数律的穷尽单测 + **直接切换**,单回滚 flag,
pause-window 部署(deploy_live.py restart all + resume_entries 既有机制)。

**C2 高尾 fail-closed 顺序反转:先测,后关。** 5.6 排它第一且要求立即
fail-close 受影响 cell。裁定:band-0.5/高尾 cell 的 **stake-weighted 结算
cohort verdict 脚本现在落地**,fail-closed 开关在 PR-1 就位但**默认不翻**;
verdict 负才翻。这是 evidence-first(操作员法:gates decide),不是拖延。

**C2 修订(tail-scout 实测 2026-07-23)**:verdict 脚本对活库首跑 —— 两个
era 均 INSUFFICIENT(unresolved stake 56-59% > 20% 门)。但方向信号为负:
resolved-and-held 子集 stake-weighted winrate 43.9%(jul15+)/44.2%(jul01+)
vs breakeven 56.0%/56.4% —— 与先前 0.731 count-weighted 数字分歧(那是
按仓位计数、未剔除结算前退出仓的口径)。"缺陷可能已被修复"的工作假设
**撤回**;开关仍不翻(证据不足),~2 天后 17 个尾 token 结算、unresolved
门清空时重跑,verdict 变载重。
机制已定位(tail-scout):under-shrink 不是 full_transport_v1 产者本身,
而是 **D2 bias-family unify 回归**(evaluator.py:3369-3433,flag
exit_bias_family_unify_enabled=true,settings.json:251):统一路径只施
bias 平移(edli_per_city_v1),从不加 total_residual_sd_c 的 scale 加宽 ——
预测误差层的一半被静默丢弃。修复面因此比 5.6 假设的窄:在统一路径恢复
residual 加宽,或对受影响 cell 让 _resolve_unified_entry_bias_native 返回
None(fail-safe 回退到 plain-p_raw+Platt 已结构性存在,只差 cell 门)。
serving-gate 挂点:evaluator.py:4876 的 cal_level>=4 CALIBRATION_IMMATURE 拒绝。

**C3 PR-1 exit = ΔJ≡0 特例,allocator 耦合推 PR-2。** 5.6 的停止律含
J_t(F)(联合 allocator 的现金影子值),但 allocator 是 PR-2 交付物 ——
PR-1 引用它就是循环依赖。裁定:PR-1 的统一 exit 为
**SELL ⟺ L(x) > x·q⁻ + M_x**(即 net bid 占优稳健持有值;SELL_REVERSAL 单码),
lock/RED/EVIDENCE_UNAVAILABLE 三态保留;PR-2 注入 ΔJ 后 SELL_REALLOCATE
自动开启。PR-1 因此独立可验证、独立可回滚。

**C4 registry 归档半径扩到晋升机器。** 无策略可晋升时,promotion 机器无对象:
promotion_proof_router.py(第三套硬编码税表,SURFACE_INVENTORY #30)、
promotion_readiness.py、settlement_capture_verifications 门(#31)随 registry
一起出活路径(archive,只读历史)。5.6 kill 清单漏列。

## 保号(SURFACE_INVENTORY 强制项)

4 个 registry 外硬编码税表必触:command_recovery.py:133(顺手关死 2026-05-22
遗留缺口)、live_admission.py:32、promotion_proof_router.py:59-82、
riskguard.py:1095。10 表 schema 迁移复用现有 rebuild 机器
(_migrate_trade_strategy_key_checks 的 detect-sentinel + rebuild + trigger 保留
idiom)。全局 κ 初值 = 现行 kelly_default_multiplier=1.0 档的有效分数,
切换日不发生 sizing regime 跳变;后续调 κ 是独立决定。
m_e = m_x = 1 tick(全局唯一摩擦边际,来自 spec)。

## PR-1 提交组(每组独立绿测,基线 = origin/live 的 289F)

- **A(并行,3 agent,文件互斥)**
  A1 law-builder:new src/decision/predicted_bin_law.py(纯函数:入场
  G⁻/native NO 界/lock 折叠/停止律+滞回)+ 穷尽单测。
  A2 schema-migrator:10 表 decision_law_id/position_origin nullable 列 +
  strategy_key 放宽(rebuild idiom)+ DB 约束(常量或 NULL)+ 迁移测试。
  A3 tail-scout:定位 full_transport_v1 高尾生产点与预报 issue-schedule
  权威源(file:line)+ band-0.5 stake-weighted cohort verdict 脚本(只读)。
- **B** 分类器坍缩(evaluator/reactor_adapter 双 dispatch → 常量律身份)+
  registry/kelly 乘数链/per-key floor/4 税表出活路径 → 一个 DecisionLawPolicy。
- **C** evaluate_exit 统一(C3 形式)+ RiskGuard 全局化 + Day0 RED 豁免删除。
- **D** certificate valid_until + release 边界撤单(continuous_redecision 接线;
  缺 next-issue 元数据 → 新预报条件敞口 fail closed)。
- **E** 测试迁移(载重前十文件)+ replay_parity + schema fingerprint 重 pin +
  milestone PR。

PR-2(联合资本):结构 Σ + joint_kelly.py + allocation_epoch + ΔJ 注入 exit +
SELL_REALLOCATE + 停写 strategy_key + registry/晋升机器归档。
