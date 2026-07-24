# strategy_key 下游消费面完整清单(killmap agent,2026-07-23)

Base: origin/live 86b1342f3. 迁移(策略标签 → 唯一入场律 + 变体元数据)动刀前
的 required-touch 清单。分类器 6 站点(evaluator/cycle_runner/reactor_adapter/
edli_bridge/attribution_drift)见 DERIVATION.md §6,此处只列下游。

## 需 schema 迁移的 10 张表(strategy_key/strategy 列)

| # | 表 | DDL | 写点 | 备注 |
|---|---|---|---|---|
| 1 | strategy_health | db.py:2324,6179 | refresh_strategy_health db.py:10993 | RiskGuard PnL rollup 依赖 |
| 2 | decision_events | db.py:2367,3745 | decision_events.py:268 | 决策审计主链 |
| 3 | readiness_state | db.py:2751,4254,4694 | (三版 DDL,先确认活版) | |
| 4 | position_events | db.py:5472 | 16 个 INSERT 站点 / 8 文件 | 最高扇出 money-path 写面 |
| 5 | position_current | db.py:5550 | 18 个 UPDATE(projector-owned,无裸 INSERT) | 从 position_events 物化 |
| 6 | execution_fact | db.py:5601 | db.py:10120 | |
| 7 | outcome_fact | db.py:5965 | db.py:10196 | PnL/结算归因 |
| 8 | opportunity_fact | db.py:6114 | db.py:9770 | |
| 9 | risk_actions | db.py:6164 | riskguard.py:1673 | RiskGuard 行动账本 |
| 10 | trade_decisions.strategy | db.py:3232 | (列名 "strategy",命名分叉本身待并) | |

SQL CHECK 已被 _migrate_trade/world_strategy_key_checks(db.py:4958/4567)剥除,
现无 SQL 层阻挡新值;执法在 Python 侧 CANONICAL_STRATEGY_KEYS。

## 4 个 registry 外硬编码税表(迁移必触,registry-only 迁移够不到)

1. **command_recovery.py:133** `_CANONICAL_STRATEGY_KEYS` 4 值硬编码
   (settlement_capture/shoulder_sell/center_buy/opening_inertia)——
   2026-05-22 风险报告已标未修;day0_nowcast_entry 在 :4013,:4094,:4191,:5712
   静默误分类。**本迁移应顺手关死此缺口,不只是绕过。**
   tests/test_authority_rebuild_invariants.py:766 明文禁止此模式。
2. **live_admission.py:32** LIVE_QKERNEL_EXACT_YES_STRATEGY_KEYS 硬编码 frozenset。
3. **promotion_proof_router.py:59-82** Pipeline A/B 第三套独立 key 税表;
   未知 key 静默落 Pipeline B(:106)。
4. **riskguard.py:1095** `strategy != "chain_only_reconciliation"` 字面量哨兵。

## 单锚点(迁移低风险)

- Kelly:phase_aware_kelly_multiplier 唯一活调用 evaluator.py:6330;
  registry 契约保持则单点收口。
- 入场价格/利润 floor:reactor_adapter 经 registry 读(:5145-5151→24252-24499),
  executor.py 只复验上游值,非独立 gate。
- settlement_capture_verifications 门:按 (city,metric) 查询,strategy-agnostic;
  停止打标只会 count 不足 → NOT_READY(fail-closed by count)。

## registry 加载面

- strategy_profile.py get(:501)/try_get(:516)/live_safe_keys(:536)/
  live_allowed_keys(:545)/reportable_strategy_keys(:580)。
- CANONICAL_STRATEGY_KEYS 四处定义:db.py:94、portfolio.py:76、
  cycle_runtime.py:108(均 registry-driven)+ command_recovery.py:133(硬编码,见上)。
- 守门测试:test_strategy_profile_registry.py:131-179(registry/control_plane
  parity)、test_live_safe_strategies.py(跨模块一致性)、
  test_promotion_proof_router_wave.py(路由穷尽)。

## 报表/归因

- settlement_guard_report.py:292-643 用 strategy_label(已是与 key 分离的抽象)。
- attribution_drift.py:373:"persisted label ≠ inferred strategy" 双轨并存,
  迁移设计须显式合一。
- scripts/replay_parity.py:47-99 = 本迁移类的现成回归检测器,改身份后必跑。

## 测试冲击面

字面量计数:center_buy 95 文件、opening_inertia 63、settlement_capture 61、
shoulder_sell 30、day0_nowcast_entry 14。
载重前十:test_riskguard(180)、test_db(163)、test_pnl_flow_and_audit(157)、
test_p1_findings_evidence_risk(119)、test_command_recovery(89)、
test_runtime_guards(80)、test_exit_safety(77)、test_lifecycle(62)、
test_live_safety_invariants(57)、test_ws_poll_reaction(56)。

## 汇总

36 surfaces;10 需 schema 迁移;0 纯删除(全部为 safe-as-is 或需主动迁移)。
