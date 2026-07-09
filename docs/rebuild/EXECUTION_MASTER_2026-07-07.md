# Zeus 全系统重构 — 执行总纲(EXECUTION MASTER, 2026-07-07)

**这是执行文档:fresh session / compact 后从这一个文件重对齐,直接进 subagent/workflow implement。**
**Status: READY — 操作员已令"写极其完整的完整计划,compact 后重对齐直接执行"。**

## A. 重对齐协议(compact 后第一动作)

1. 读本文件全文;读 `whole_system_first_principles_2026-07-07.md`(同目录,蓝图:组件图 §1、子系统判决 §2、疤痕审计 §7、consult 裁决 §8)。
2. 读 `docs/operations/current/plans/hourly_capital_gains_improvement_loop.md`(live 工作现状)+ `git log --oneline -15` + `git status`。
3. 核对 §B 前置状态是否仍真(每条一个命令);漂移则先修账再动工。
4. 从 §E 执行队列取当前 wave 的下一个未完成 packet,按 §D 拓扑开工。**不重新设计、不重新调查——设计已三轮验证(8 路测绘 + 双盲 consult 对撞 + 6 路疤痕审计),证据都在蓝图与 journal。**

## B. 前置状态账(2026-07-07 写入时;每条附核对命令)

| # | 事实 | 核对 |
|---|---|---|
| B1 | main = 1341967a8;Phase1+2 gate 删除、dedup、Day0/A2 修复已 merge 已部署;mesh 8 daemon 健康 uptime 一致 | `git log --oneline -3; launchctl list \| grep com.zeus \| wc -l` |
| B2 | order-engine v2 W0-W5 在飞:`src/solve/`(2026-07-03 skeleton)、`src/probability/`(q-kernel)已存在,唯一 live seam = `src/engine/qkernel_spine_bridge.py`(2.2K 行) | `ls src/solve/ src/probability/` |
| B3 | 未提交清理批(~66 文件:控制面净化 + registry 修复 + doctor 修复)在主树,操作员未裁 commit | `git status --short \| wc -l` |
| B4 | topology_doctor --docs = 0 错误(988→0 已修) | `.venv/bin/python scripts/topology_doctor.py --docs` |
| B5 | doctor 测试基线:32 failed 全 pre-existing(base 55→32,本轮零新增) | 见 journal 2026-07-07 |
| B6 | 24/7 loop v2 已 PROPOSED 未启用(`allday_improvement_loop_design_2026-07-06.md`);loop/ 目录未建 | `ls ~/zeus/loop 2>/dev/null` |
| B7 | plist 明文 secrets(POLYMARKET_API_KEY/SECRET/PASSPHRASE、WU_API_KEY @ com.zeus.live-trading.plist)| `grep -c API_KEY ~/Library/LaunchAgents/com.zeus.live-trading.plist` |
| B8 | journal 排队中的 live 修复:churn-guard(出场低于 belief 甩仓)+ Bug A/B(close-economics/settlement capture)协调一个 worktree 做 | journal 15:50Z tick |

## C. 不可违约束(每个 packet 的 brief 必须携带)

1. **live money path 永不摸黑**;部署只有操作员跑 `scripts/deploy_live.py restart all` → preflight GREEN → `resume_entries`。绝不裸 kickstart(split-brain 事故)。
2. **money-path 代码 = PREPARE 级**:worktree + TDD + 对抗 opus verifier → **永不自主 merge**,diff 排队等操作员。非 money-path(docs/tests/registry/死码零调用者)= AUTO 级:分支 + verifier 绿 + boot smoke → 可自主 merge;diff >20 文件或 >600 行拒并升级。
3. **每 wave DoD 含删旧**:测试与组件同 commit 退役;不删不算完(N 代并存是五大缺陷之首)。
4. 动 `src/state/**`/schema = K0 packet + planning-lock;registry 同 commit 更新(map-maintenance 机器查)。
5. 证据法:ground truth 只认决策证书×结算 join;禁运行态派生数字/记忆断言/混合 regime 回测;统计性结论 min_n=30,机制性(读码可证 invariant 违反)任何 n 可行动;凡涉 EV/校准/选市/sizing 一律统计性。
6. 公理(操作员已裁,不复议):q 是唯一 belief(价格/fills 永不更新 belief);no shadow modes(验证 = event replay + byte-identical OFF 门 + time-boxed promotion flag,用后即删);fail-closed freshness 分向(入场 fail closed;cancel/减险 exit 在 venue 真相充分时必须继续);3-DB split 定法;结算值只经 SettlementSemantics;Zeus 永不提交 redeem tx;绝不动 kelly/caps/风险姿态/kill-switch(操作员域)。
7. maker 定价保守化是公理代价(禁 fill-intensity 历史 ⇒ worst-case fill timing,否则 taker/no-op 支配)——设计事实,不是待修 bug。
8. secrets 永不进 source/report/gist;consult 走 chatgpt-consult skill(操作员授权工作流)。

## D. 执行拓扑(每 packet 固定)

- **Lead(本 session)**:取 packet → 派 subagent → 裁决 → journal。自己不写代码。
- **Implementer**(sonnet):一 packet 一 agent;**worktree 隔离(`isolation:"worktree"`)由主 agent 按需判断——非硬性规则:并行改动、文件疆界可能相交、或 money-path 才隔离;单序列/只读 implementer 免**;brief = GOAL/BOUNDARY/OUTPUT 三段带 file:line,绝不压缩;TDD(先红后绿);done-claim 必带 commit SHA。
- **Verifier**(全新 context,证伪 brief"试图证明这个 done-claim 是假的";money-path 用 opus):独立复跑测试 + boot smoke(`.venv/bin/python -m src.main --validate-boot` 预期仅 DB-missing FAIL)+ 主树 grep 零残留。
- **Scout**(haiku):只枚举 file:line/verbatim,永不判存在/缺失。
- 大扇出用 Workflow(pipeline 优先);多 packet 并行时一 agent 一 worktree,文件疆界不相交。
- 测试基线法:先跑 base 失败集,改后 diff —— **对照新增失败,不对照绝对数**(32 pre-existing 红是已知底)。

## E. 执行队列(按序;R0 内部可并行)

### R0 — 止血 + 零调用者清扫(现在;全部可并行开 worktree)

**R0-a〔PREPARE·K0→已实现+opus verifier PASS(6 claim:5 CONFIRMED + guard 措辞 PARTIAL;68 pre-existing 红 parent-vs-commit 对称 diff 空、+11 新绿 0 新增;backfill dry-run 50/2 复证 exact;capture 范围收窄判 defensible——§4:114 "R2 依赖 R0a" 依赖图自证〕,待 merge〕close-economics 统一。**
  - **verifier 抓到的旁路(pre-existing,非本 diff 引入,挂 R2):** `command_recovery.reconcile_hard_terminal_position_projection_repairs` 直写 SQL 把 phase 改成 terminal、绕过 upsert_position_current 且不设 realized_pnl_usd —— 唯一幸存旁路(全量扫 UPDATE/INSERT position_current 证其余全走 funnel)。正常靠既有 durable terminal event 已记账,但可把 legacy NULL 行重印成 terminal 而 guard 不响。"结构性不可能"实为"单旁路残存"——R2 diff 引擎收编此 repair 时封死。
  - **verifier 另两条(挂 R2):** chain_mirror reconcile 循环无 per-row try/except,未来 guard-tripping writer 可致整 pass 中止(下 cycle 自愈);Decimal.quantize(0.01)→float round() 收敛可能有 sub-cent 边界差(统一必择其一,判可接受)。 交付:`src/state/close_economics.py::compute_realized_pnl_usd` 单一公式,五路 close 全部路由(portfolio.py:3062/:3094、command_recovery.py:6049、exchange_reconcile.py:4712、chain_mirror_reconciler.py:634);结构保证 = `projection.py::upsert_position_current` 抛 `MissingRealizedPnlOnCloseError`(首次转 terminal 无 pnl 即 loud-fail,假想旁路路径有测试证死);backfill 脚本 dry-run 实测 50 笔可回填 / 2 笔正确排除(exit price 不可恢复,跳过不猜)。churn-guard 与 grading 修在前置 commit 已 live(f8628fb4b),本包在其上收敛。**capture 双 discovery loop 合一判归 R2**(两 loop 已共享 settlement_outcomes 写点,本包统一了 P&L 记账数学;diff-engine 合环是 R2 明文范围)。测试:base 68 红复现 +11 新绿 +51 回归绿,0 新增。**事故记录:** R0-c 形状的未 staged 删除污染了本 worktree(agent 自证非其所为,stash-restore byte-identical 保全;module_manifest 注册行因此延后)—— 跨 worktree 污染根因待查,见 journal。
**R0-b〔PREPARE·K0〕CAS 账本原子性 — 2026-07-08 执行发现:已于 c7e095ee1(2026-07-02, W1.1 schema packet)全量落地,对抗 verifier PASS**(guarded 单语句 CAS reserve 关 TOCTOU @ collateral_ledger.py:453-471→:521-548,与 pre-fix 818a88e44 diff 证实旧 TOCTOU 真实;convert-on-fill+partial-fill :951-1048,MAX(matched_size) 对重复/乱序 fill 单调、terminal 写 `WHERE released_at IS NULL` 幂等;unsettled-proceeds 表 :87-102;identity finding → RiskGuard RED @ riskguard.py:90-103 + :2308-2318 真 gate 非仅 log)。剩余交付 = 并发验收压测(reserve/fill/cancel/settle 多线程保 A4 恒等式),已在分支 `claude/agent-a797d6e11ed040afd` commit 19cb209d7(confirmatory,非 red→green —— 修复先于分支点,诚实记录;verifier 3 跑无 flake)。**Verifier 揭示的既存设计假设(非本包引入,挂 R2 处理):** convert_reservation_on_fill 信任 fill fact 先于 terminal 事件落库(sole terminalization seam @ venue_command_repo.append_event);若 EXPIRED 先处理、fill fact 后到,reservation 以 converted=0 释放且无后续 hook 触发转换 —— R2 diff 引擎的 chain-truth 兜底须覆盖此窗口。**portfolio_reservation.py 删除项撤销并改判:** implementer 查实它不是 CAS 缺原子性的 shim —— 是 per-reactor-cycle 内存 provisional ledger,让 Kelly correlation-cap sizing 在候选 N+1 时看见候选 N 的 stake(N 可能在 DECISION_CERTIFICATE/EXECUTOR_EXPRESSIBILITY 下游被拒、从不到 CAS 行);删它 = 直接改 Kelly 相关性 cap 数学 = §C6 操作员域。其死期改挂 R7(solve 推广收编 sizing 路径时随 Kelly haircut 栈死)。疤痕审计 §7.1 对此文件的判词有误,蓝图已同步勘误。
**R0-c〔AUTO→已实现+commit,分支 `claude/agent-a982f571aa212fc21` @ 93e226a20,待 merge〕零调用者尸体删除**:40 文件 +19/−6012(源 18 + 专属测试 11 + 5 个混合测试外科编辑 + 4 registry yaml)。**蓝图勘误:15 删 13,AIFS 实验对 EXCLUDED**——ecmwf_aifs_sampled_2t_probabilities.py 与 openmeteo_ecmwf_ifs9_aifs_soft_anchor.py 有活调用者(scripts/validate_member_vote_smoothing_3way.py:58/:63、cycle_phase_offline_study.py:259),import-graph BFS 当时漏扫 scripts/;implementer 删前复证按设计拒删。附带修正一处 stale registry 宣称(benchmark_suite 并不 import data_lake)。58→58 红 0 新增、19,138 收集干净、doctor/boot 绿。**事故沉淀:** 本包引发 stash 跨 worktree 竞态(refs/stash repo-global),已入 memory——并行 worktree brief 永禁 git stash。
**R0-d〔AUTO→已实现+对抗 verifier PASS(5/5 VERIFIED:常量 byte-identical 同 import-timing;残渣仅 2 permitted 标签+3 历史注释;antibody 由 mock 观察升级为 ModuleNotFoundError 结构保证 = 净强化;shim-back 复跑法证 0 新增失败;7 registry 面净),待 merge〕fdr_filter 尸体处理**:DEFAULT_FDR_ALPHA 迁至 `src/strategy/selection_family.py:56`(活体后继模块,非新建文件);**真实 import 点 10 个非 9**(原估漏 replay_selection_coverage/replay/run_replay/edli_position_bridge/live_order_aggregate);corpse+专属测试删除;7 个 registry 表面同 commit 清行。0 新增失败(stash/rerun 法区分 pre-existing)。**遗留(标记非修):** 3 处 docs/reference 提及 + event_reactor_adapter.py:11811/11828 两个 `"zeus.strategy.fdr_filter"` provenance-tag 字符串(AuthorityEvidence 审计身份标签,改名会断历史审计链 —— 留待 R7 EDLI 证书族退役时一并裁)。diff 24 文件/499 行超 AUTO 自主 merge 线,按 §C2 升级排队。
**R0-e〔AUTO→已实现+对抗 verifier PASS,分支 `claude/agent-a63d5e427a81715fc` @ dbfbedd53,待 merge〕残渣与幽灵**:两个残渣 pass 删除(双注册表清净、live DB 0 行双方复证——非空表假象:808 条 CI_SEPARATED_REVERSAL 史存在但 0 条中全谓词);governor drawdown breaker 删除(verifier 修正判词:**非"重复"——governor 量 mark-to-market bankroll delta,riskguard :699 量 realized settled loss,两量不同;但 MTM 断路正是操作员铁律 #3(2026-06-08)明令禁止的反模式**——2026-06-08 phantom −19.25 假亏损曾把交易 100% 停机而 realized 持平;删它=移除被禁反模式的潜伏复燃点,非删覆盖。GovernorState.current_drawdown_pct 观测字段保留);幽灵 G8 引用清除(docstring-only,函数体 byte-identical)。Parent-vs-HEAD 测试 diff:同 13 个 pre-existing 红、-5 = 恰为删除的 5 个测试、0 新增。**后续清理候选:** settings.json 的 `riskguard.max_drawdown_pct: 0.2` 死键现全孤儿(flag_audit_plan.md:86 已录),挂 R7 flag 清理。verifier 另 4 处 RESTART_READINESS 幽灵引用(测试文件)同批挂账。
**R0-f〔NEVER→操作员一次手动〕plist secrets 出库 — prep 已交付(分支 `claude/agent-af20cc0a81e8b1cdf` @ 227228e3b)**:实测 **10 个 plist 非 9**(§B7 原计数过时);5 tracked + 5 untracked(已全部备 sanitized 模板);7 个含 secrets(live-trading/post-trade-capital/substrate-observer 各 4 键、price-channel-ingest/venue-heartbeat 3 键、data-ingest/forecast-live 1 键)。git 史已查:tracked plist 从未含真值,无需 history rewrite。交付:`scripts/ops/migrate_plist_secrets.sh`(dry-run 默认,--apply 抽 secrets 到 chmod-600 `~/.zeus/secrets.env` + wrapper-exec 重写 + 备份;macOS launchd 无 EnvironmentFile 故用 `bash -lc 'set -a; source ...; exec ...'` 形)+ runbook `docs/rebuild/r0f_plist_migration.md`。操作员序列:dry-run → --apply → grep 验无明文 → `deploy_live.py restart all` → resume_entries。
**R0-g〔AUTO→prep 已交付,同分支 227228e3b〕基础卫生**:`scripts/ops/rotate_zeus_logs.sh`(copytruncate+gzip,N=5;**实测 logs/ 仅 ~61MB 非 3.4GB —— 原测绘数过时或含已清内容,dry-run 0 个超 50MB 候选**)+ `scripts/ops/db_hygiene.sh`(report-only 默认;实测 8 个 0 字节 decoy 候选 = 4 根目录 + 4 state/ 双命名孪生,2 个非零根 DB 正确判 REVIEW 不触碰;--apply 删除前 re-verify 0 字节 + lsof 未打开,否则 exit 1)。**registry 例外待裁:** `scripts/ops/*.sh` 无法入 script_manifest(top_level_scripts() 只扫 scripts/ 直接子级,注册反而制造 3 个 stale 错误)—— implementer 留 NOTE 未单方面改 doctor;R8 doctor 拆分时归位,或操作员令扩 scanner。
**R0-h〔AUTO→已实现+verifier PASS(check1-5 CONFIRMED:5 注释对真实接线逐条核真+禁类合规;bytecode 指令级同一(控制 __firstlineno__/frozenset 折叠两个解释器混淆);词表 15 词条语义核准;--repr 真信号+--docs byte-identical;check6 主线程终裁:main-vs-worktree 失败名集 comm diff 为空、32→32 红、208→209 绿 = +1 新测试,implementer 的"249"为多文件口径笔误,无实质差),待 merge〕表示层契约落地包**
  - **verifier 两条尾巴(挂 R0-h follow-up/R8):** solver.py 新注释硬编码跨文件行号 ":1412" 是新腐烂载体(lint 禁类未覆盖行号引用——契约 §1.1 增补候选);doctor CLI `--changed-files` 与 `--files` 名不符且静默降级(UX 陷阱)。(权威:`representation_contract_2026-07-08.md`):①4+1 条 WRONG 注释已修(solver.py:873、event_reactor_adapter.py:41/:18959、family_decision_engine.py:15、portfolio.py:1741),**bytecode 逐字节同一证明注释-only**;②`architecture/canonical_vocabulary.yaml` v1(15 词条/6 簇,advisory-scope);③`topology_doctor --repr` 骨架(advisory,exit 0;在 command_recovery.py 上验出真信号;修了一类 canonical-name 误报);④§H 锚点注册行(已在)。--docs 前后 byte-identical;doctor 测试 32 红不变 +1 新绿。**每个后续 R-wave 的 DoD 增加第四维:幸存代码符合表示层契约(§1.1 注释法/§1.2 命名法);详见契约 §5 逐 wave 载入点。**

### R1 — 概率单链(R0 后即开;q-kernel 收尾非新工程)

**R1-a〔PREPARE〕sigma 权威收敛(2026-07-08 执行侦察后收窄)**:测绘发现 center 公式(#135 修复 2026-06-18 @ materializer:1553-1556)与 q_lcb coherence(PATH-A @ :2585-2600)**已经手工移植收敛** —— BLOCKER-1 大半已修;**剩余真缺口 = sigma/width 三套 ad hoc 公式**(base fusion sd+resid、source-clock predictive_sigma_c、settlement-floor 层)未走 `src/forecast/sigma_authority.py`(该模块自带为此设计的输入 seam:fused_center_sd_native/sigma_resid_native)。**需专用 DB-replay TDD harness 才能到 byte-identity 标准** —— 排队为 R1-a2 专包(等 R2-core 的 replay harness 可复用)。implementer 拒绝无 harness 强行改 live q 路径 = 正确。
**R1-b〔AUTO→已实现+对抗 verifier PASS(5/5 CONFIRMED;三个 exclusion 判断全 SOUND:quarantine 活调用实在 executor.py:1065 无 flag 门、xfail antibody 逐字核实【caveat:该测试断言两函数都缺席,EDLI-only 删除不会机械翻它——全删才翻】、"center+q_lcb 已手工收敛"抽查坐实且比声称更强【materializer:1566 是真共享函数调用非漂移易发手抄】),commit 标题已 amend 对齐内容,终 SHA `abadc430f`,待 merge〕死概率模块删除**:observation_precision_fusion(593)+ coordinate_precision_guard(235,其测试对 cities.json 已 stale 自证孤儿)+ per_city_source_registry(166,CSV 消费者直接读 CSV 不 import)= 994 行删。**decision_integrity_quarantine EXCLUDED(蓝图勘误 #8):** 活调用者 executor.py:1293-1312(执行前安全检查)+ evidence_report + command_recovery ×多处 —— "随双链死"前提不成立,其死期须待调用者退役(R7)。0 新增失败,boot 干净。
**R1-c〔PREPARE→操作员门,原样保留〕ENS/Platt/EDLI-bias 遗产**:`tests/test_emos_sole_calibrator.py:474-482` 有 strict-xfail antibody 明文法:"deletion lands only after EMOS is settlement-proven per-city + operator sign"。implementer 查无该前提成立的证据,拒绝越过法门 —— **升操作员开关:EMOS per-city settlement 证据是否已足?你签字则 R1-c 开工。**

### R2 — 收敛环替代 recovery 山(R0-a 后;最高风险 wave)

**〔R2-core 已实现+opus verifier PASS,分支 `claude/agent-aa58bf4a7574f1de1` @ dd54123cd,待 merge〕** src/reconcile/ 新包:local_truth(首次统一 command+projection+reservation 单一 SQL)+ chain_truth(fill_dedup 唯一 dedup,无第四拷贝)+ diff_engine(5 谓词,仅 reservation_orphan 有 apply=REVIEW_REQUIRED append,永不动 balance)+ replay harness。**三洞封闭全 verifier 确认安全:** hole-(a) hard-terminal repair 改走 upsert guard —— 每候选自带 SAVEPOINT+try/except,guard 在写前 raise、upsert 从不 commit → 一条 legacy NULL 行 fail-closed 不卡死整 pass(**最高风险点,verifier 走查 legacy-NULL 场景确认无 wedge**);hole-(b) chain_mirror per-row except(非 bare,SystemExit 穿透)+ errors 上报;hole-(c) reservation_orphan 谓词。**replay 首跑(verifier 独立复跑 mode=ro 数字精确复现):71 reservation-orphan(release 后 8s fill 落)+ 62 concurrent-fill + 14 shares-2x = R2-c 作业清单(triage 注意:62 是"命令态 vs fill 背离"非"62 个错仓")。** diff engine INERT 无 live 调用,唯一 live 行为变更 = hole-(a) 已验 fail-closed 安全。0 回归。
**R2-a/b 已含于上(snapshot 契约 + diff 引擎已建);R2-c 为下一波:31-pass 迁移**。原 R2-a 文案存档:两个 snapshot 契约 + chain_mirror_reconciler.py(1033 行)为模板。
**R2-b〔PREPARE〕diff 引擎**:classify → 谓词表 → apply corrective event。**谓词表只保 4-5 个真 venue 谓词(150-300 行)**:cancel/match 竞态(:2428)、WS 不可靠须 REST 点真相、partial-fill 消失语义(:9819)、fill 多版本 dedup 排序(fill_dedup.py 唯一权威,删其余 3-4 份拷贝)。
**R2-c〔已分析,分支 `rebuild/r2c-recovery-predicate-table` @ 2b2ebb9a8;蓝图前提实证推翻,零删除本包〕** opus 实现者逐 pass 对 84GB live trades DB 跑只读候选查询 + 数 command_recovery 实际 append 的 position_events(主线程独立 spot-check 确证:command_recovery 今日写 2459 events 最新 23:18、live_entry projection proof_class 1474×、phantom_void 20× 最后 2026-07-02)。**headline:command_recovery 36 个 pass 无一本包可安全删** —— 蓝图 §7.1 判为"被 R0-a/8f22bb3de 淘汰的疤痕"的 ~30 pass **今天仍在 money path 发火**(live backlog 688/60/6/3)。逐条订正:
- **C2 多写者漂移族(~10 pass):"被 R0-a 淘汰"= 假**。R0-a 统一 close-economics(realized P&L),C2 补的是 position_current **物化滞后**(不同关切)→ 删除门在 **R5 单写者 projection 漏斗**,非 R0-a。
- **C1 EDLI 同步族(7 pass):门在 EDLI 双账本移除**(zeus-world.db 事件 lane 还在);#25 有 live caller `edli_absence_resolver.py`。
- **C3:`repair_confirmed_phantom_voids` 蓝图 SCAR_DELETE_NOW 错**(3 live 候选、2026-07-02 发火于 fix 之后);`repair_spurious_model_divergence` **已被 R0-e 删**(该蓝图项已执行)。
- **工具缺口:** replay.py 只比 report-only findings,证不了 write-equivalence(load-bearing 谓词全 `writes=False`,仅 reservation_orphan 例外)→ DoD 的"identical corrective events"replay 当前**不可构造**。
**R2-c 重排(真依赖):删除 = 下游 of R5(单写者 projection)+ EDLI 双账本移除 + diff_engine write-migration + write-replay 工具升级;非 standalone、非 R0-a 后即可。** 执行体(write-migration 谓词 + write-replay)**故意不本包建** —— 其收益(删除)R5-门控,现建=造休眠实体违操作员极简令。map(2b2ebb9a8)= 本包高值交付:挡住删除今日在跑的恢复码 + 重排 DAG。~~原计划:先 EDLI 7 → 漂移 10 → 核 5;22.6K→~4K。~~(实证:非本包可达。)

### R3 — ingest 契约化(独立,可与 R2 并行)

**〔已实现+对抗 verifier 实质 PASS(claim1-5 CONFIRMED:真迁移非第三代、live call site byte-clean、删除项 plist/cron/runbook 全扫零引用、exclusion 皆 load-bearing;claim6 registry 假宣称 REFUTED→follow-up `0096fff4f` 已补:module_manifest 登记新模块 + stale 行清 + doctor 前后零新增),终态 = 1a1a15b53 + 0096fff4f 两 commit,待 merge〕** SourceContract 表已建(`src/ingest/contract.py`,7 行源;**依既有 2026-05-24 反重复直令从 source_contracts.py 迁移非复制**)+ 五关切拆解已文档化落 row/dependents 映射;14 新测试(row 验证/cursor-diff 幂等/station-clock 法 antibody)。已删:legacy 调度模式 + tick 脚本二面(307+447 行测试)。**蓝图勘误 #9:dual_run_lock 实测 ~40+ 调用点非 14,且 load-bearing 于 main.py/substrate_observer/post_trade_capital(前两个=操作员脏文件围栏)→ EXCLUDED,死期挂 R4/R5(脏树解锁后)**;ownership toggle EXCLUDED(经 plist ZEUS_FORECAST_LIVE_OWNER 门控两个活 daemon)。**live call site 未翻转**(money-path 邻接 seam 需 replay 证据,正确拒绝)—— 排 R3-b:翻转 `_replacement_cycle_availability_poll_if_needed` 至 SourceContract 调度(等 R2-core replay harness)。净 −362 行;34K→8-10K 主体量在后续包(两代 provider-contract 合并 + replacement_forecast_* 家族收编)。

### R4 — immutable deploy + main 瘦身(独立)

**R4-a〔PREPARE〕immutable release**:content-addressed release dir + current symlink + launchd 指 symlink + 原子交接;boot 断言缩为"loaded release id == configured";deployment_freshness 抖动源(mutable checkout)根除;restart-guard 链(deploy_live 28 个 fix commit 的吸收层)缩单断言。
**R4-b〔PREPARE〕main.py 8K+ inline job body 外迁**(:2195-10863)至 owning module 各一个 `run_cycle()`;main() 缩注册表驱动(~1.5K);legacy_cron 死管道 ~150 行删;EDLI stage-readiness ~900 行(:348-1235)随 R7 EDLI 退役删。
**R4-c〔AUTO〕**日志轮转进 daemon 配置;coverage 警告洪水随 W5 gate 删自停。

### R5 — state 单源(R2-c 多写者批协调)

domains.py(176 行,已自带收编设计意图)成唯一 ownership 源 → db_table_ownership.yaml(2939 行)与 init-code CREATE-lists 从它生成;db.py 12.8K 按四 seam 切(连接工厂/schema+migration/fact-log 29 log_*/读查询 15 query_*);position_current 7 写者 → projection.py 单写者;7 个常驻 migration 函数(~700 行)进 ledger 归档;write_coordinator.py 485 行零调用删;**维护写出 money-path 锁域**(533 mutex-bounce 实测);80GB 级 DB retention 审计。

### R6 — venue 契约层(独立)

response-contract 层:每个 venue 端点显式解析契约(7 处双/三键 .get() 猜形 = #429 类 bug 根);redeem 提交机械 ~650 行删(保 winning-balance 读);price_channel_ingest 3.1K 拆 venue-fact 桥接 vs re-decision 路由(venue 不决定谁 re-solve);两套 typed-fact 层完成为一套(接线 470 行 contracts 模块,删 adapter 死壳)。

### R7 — solve 推广 + 决策面清代(GATE:R0-b CAS 绿 + 证书 replay 门立起才准动)

与 order-engine W3-W5 合流。**首批:market_coherence.py veto 反转**(INCOHERENT_BLOCK_LIVE :46/:84/:201 → 诊断/优先级信号,现行矛盾);EventWriter.notify() wake-on-write(60-90s 轮询地板 = A2 违约,consult 两轮同判 BLOCKER);决策证书 = 记账原子(no-op 也证书;校准只消费 settled 证书)。随 wave 死:qlcb_reliability_guard、selection_calibrator、selection_curse_bound(+loader)、city_skill_gate、Kelly haircut 栈(63 调用点;kelly_size 核心公式保)、redecision.py+utility_ranker(argmax 取代 rank-and-pick)、market_analysis 扫描三件套、shift_bin/fill_up/family_rebalance 三 sibling lane、mainstream_agreement(gate-135)、EDLI 证书族(decision_kernel 整包重derive 至 SOLVE 证书;certificate 概念保、EDLI 形死)、live_admission hub 重建、bankroll_warm(330s 巨 cycle 疤痕)、maker_rest_escalation(TRAP:先立 C3 staleness+REST_ELIGIBLE 新 owner)、ARM-coverage 解耦(TRAP:先 rewire ARM 再删 coverage lanes,否则静默 disarm)。KEEP:payoff_vector、signal/ 全包(Day0 物理)、correlation 五件套、sizing_context、mode_consistent_ev、executable_cost、direction_law、decision_receipt、strategy_profile、source_clock_city_weights、selection_shrinkage(数学对,形态随 SOLVE 重derive)。38.5K 七代 → probability+solve 两包。

### R8 — 治理收编(最后;R0-R7 删掉的东西治理面自然瘦)

终态 = 4 LOAD_BEARING(money_path_objects+ci、law_gate、invariants[每行须有可执行 enforced_by]、db_table_ownership[缩 5 字段,从 domains.py 生成])+ 3 领域事实册(city_truth_contract、settlement_dual_source→SettlementSemantics 类型、data_sources_registry[只留身份/日历/轨道])+ import-linter 契约(替代 topology.yaml 5936 行:probability 不 import execution、executor 不 import probability writers、settlement 只经 semantics、DB 单写者)。SCAR_DELETE_NOW:maturity_model、improvement_backlog、file_arrangement+advisory 门、pre_existing_failure_registry(867 红一次定性后如实)、preflight_overrides。history_lore 110K:剖析进 docs、幸存不变量进测试。doctor 16.2K 按域拆小。测试分类:law_gate 机制保,覆盖递归化或退役 flat 分类。

## E2. 重构宪法(2026-07-08 操作员挑战后增补:"不是在破旧系统上打漂亮补丁")

**判决先立:** R0 是止血与清场,**不是重构本体**;把 R0 当成重构就是操作员指控的"补丁美容"。重构本体 = R1-R8,且必须服从下面四条宪法,违反任何一条的 packet 不合格:

1. **BUILD-INTO-TARGET,永不 PATCH-LEGACY。** 每个 wave 的交付物是蓝图 §1 组件图里的**目标形组件,在干净命名空间新建**(`src/probability/`、`src/solve/` 已是先例 —— q-kernel 与 order-engine 从 spec 新建整包,不是改旧包)。legacy 文件只允许三种触碰:①删除;②单行 seam 接线;③R0 止血。**任何"在 16K 行神文件里加一个更好的函数"的 brief 都是违宪的** —— 那个函数属于目标组件,神文件属于死亡名单。
2. **旧系统 = oracle,不是基座。** 旧代码的唯一价值是行为参照与历史 replay 语料:每次 seam 翻转带等价证据(certificate-native replay / byte-identity OFF 门 / property fuzz,§5 已列)。从 spec 新建 + 用旧系统验证,**新建比迁移便宜且安全** —— 这是 agent 时代的成本结构,也是 q-kernel 已经验证过的方法。
3. **目标组件自带行数预算,CI 强制。** 蓝图 §1/§2 的目标行数(ingest ~8-10K、概率 ~7K、state ~15K、recovery ~4K、main ~1.5K、venue ~8K、risk ~4K、决策面两包)写成每包 budget 断言(超预算 = 结构性失败信号,不是警告)。终态 358K → ~100-150K;**行数下降曲线是重构真实性的可观测指标**,每个 wave 收尾记账。
4. **表示层契约在目标命名空间从出生即 blocking**(legacy 才需要 advisory/quarantine;新包没有豁免)。新组件的注释/命名/元数据从第一行就是终态形。

**为什么不是 greenfield 整体切换(已三轮裁决,此处只存结论):** 你自己的公理系统排除了它 —— no shadow modes + live money path 永不摸黑 + fail-closed,三者同时成立时,整体 cutover 需要的"并行影子运行期"或"停机窗口"都不存在。consult round-1 独立同判(NO-GO whole cutover, 0.78)。所以唯一诚实的彻底重构形态 = **器官逐个从 spec 新建、单 seam 移植、旧代际同 commit 处死** —— 病人活着,但换上去的每个器官都是新造的,不是缝补的。终态与 greenfield 逐字节等价:12 组件干净架构,旧 358K 全部死绝。

## F. 24/7 Loop 关系

loop v2(两层 L1/L2、三文件 HALT/JOURNAL/LEDGER、AUTO/PREPARE/NEVER 三级、wrapper 后置 diff 校验)= 本 DAG 的持续供包机制:AUTO 包 loop 自主流动,PREPARE 包 loop 备 diff 排晨报。
**〔2026-07-08 已建成→对抗 verifier FAIL(3 BLOCKING 逃逸 + 1 结构盲区,全部 /tmp 活体复现)→修复中〕**:①allowlist 自扩权(enforce 时读 allowlist 而 allowlist 在被允许域内 → 单 tick 自加 src/** 再改 src/,零痕迹)→ 修法 = claude 调用前快照 allowlist + guard 文件族(allowlist/tick.sh/prompts)自身永判违规;②rename 洗白(git mv src→docs 只查新路径 → 任意 tracked 文件可无痕删除)→ 双侧路径检查;③symlink 跟随删除(违规 symlink 指向 JOURNAL.md → guard 的 remediation 删掉审计日志本体)→ lstat 删链接永不 resolve;④.db 结构性隐身(.gitignore 全局 *.db → git status 永不见 DB 写,prompts 最强调的禁区零机械后盾)→ mtime/size 哨兵 + delta 即自 HALT。**verifier 另判 DESIGN-RISK 升操作员:allowlist 放行整个 architecture/*.yaml = 无人值守 loop 可静默改写 money_path_objects/invariants 并自 merge —— 建议收窄到明细白名单或 registry 行改动一律 PREPARE。** happy-path 全 CONFIRMED(flock/HALT/timeout/fallback/plist/shellcheck)。**二轮重验:#2 rename/#3 symlink 修复 CONFIRMED solid;#1 仍 OPEN(实现者只把逃逸移了一层 —— 保护了 enforce 不再读的 allowlist_auto.txt,却漏了 enforce 真正信任的 .pre_tick_allowlist.l1,它仍在可写 loop/** 内)→ 三轮修法 = frozen 快照移出 repo 到 per-pid mktemp 目录(tick 物理够不到),已下发;#4 DB sentinel mtime 伪造 = DESIGN-RISK 非 BLOCKING(需故意 touch -t,非无人值守事故),记 docstring 保留。** loop 不可启用直到 #1 三轮修复过对抗重验。
**三轮修复揭出架构真相(implementer 诚实自证):** mktemp 移出 repo 仍败 —— tick 同 UID 无进程隔离,可枚举 `$TMPDIR/zeus-loop-*` 找到随机快照路径。**路径保密≠隔离。** 正确修法(第四轮,已下发,比 mktemp 更小):guard 控制态全部移入 wrapper 进程内存 + 不可变 git 对象 —— allowlist 从 `git show $BASE_SHA:loop/allowlist_auto.txt` 读(内容寻址,tick 物理改不了固定 SHA 的 tree),DB sentinel/dirty 基线存 tick.sh 非导出 shell 变量(子进程读不到、磁盘无痕)。此后无任何 tick 够得到的文件持有 guard 控制态,file-diff 机制对 tracked-file 篡改可证闭合。**诚实残留(升操作员 accept-or-scope,非再修):** tick 仍以完整用户 UID 运行,可写 repo 外任意路径(~/.ssh、/etc、绝对路径 DB 写)—— 任何 wrapper 都管不了,只有 OS sandbox(sandbox-exec Seatbelt 把 claude 子进程 jail 到 worktree)能。live-money 主机无人值守启用建议加 sandbox-exec 作 follow-up;当前 file-diff + DB-detect + money-path 恒 PREPARE + HALT 是可辩护的首启姿态。**这是操作员的风险胃口决策,不是又一轮 fix。**
**三轮重验的定理(verify-loop 三轮全 FAIL 收敛出的结论,已停 in-tree 加固):** 前两轮逃逸(递归文件篡改、$TMPDIR 枚举)真闭合;但第三轮证明"不可变 git 对象"在同-UID 下也假 —— `git show <sha>:path` 不校验对象哈希,`.git/objects/` 是同用户可写普通文件,10 行 zlib 覆写 loose object 即让 `git show` 返回篡改内容(端到端过真 tick.sh:零违规、money-path 篡改留存、零 journal 痕迹;仅 `git fsck` 能查而无处每-tick 跑)。**根因是定理非 bug:任何活在 repo 树内(文件/mktemp/git 对象)的 guard 控制态都被同-UID 进程可写,无 in-tree 技巧能闭合。** wrapper-diff-check = 防诚实 tick 走神的安全网,非防蓄意/注入 tick 的安全边界。**loop 机器本体已 verified-solid**(文件/枚举向量真死、happy-path 全 CONFIRMED、诚实残留已文档化、26/26 测试),唯一待修 = 一句 overclaim 注释("closes tracked-file tamper completely"→改为"detects/reverts honest strays; NOT same-UID-proof")。**真容器化 = OS 沙箱,升操作员两姿态:**
- **姿态 A(接受为诚实-错误安全网,沙箱后置):** wrapper guard 挡住主导失败模式(诚实 tick 编辑错文件);蓄意逃逸需 tick 主动破坏自身护栏(现实路径=prompt 注入,而 AUTO 层仅 docs/tests/死码、money-path 恒 PREPARE 操作员晨审、注入面薄)。首启用 wrapper 网 + HALT + 小切片盯,sandbox-exec 排启用后第一加固包。
- **姿态 B(沙箱先于启用,推荐):** sandbox-exec Seatbelt profile 把 claude 子进程 jail 到 worktree(+ /tmp scratch + claude config 读)—— 内核物理阻止 tick 写 worktree 外与 .git/objects。机器量比三轮 git/mktemp 花招**更小**(一个 .sb + 一行 wrapper),且是唯一真边界非 tripwire,契合"mechanism over prose"。代价:profile scope 要准(claude 需读什么/写 worktree/spawn 子进程/网络 API),值一次设计 pass(可 consult)。
- **〔操作员裁决 2026-07-08:姿态 A + 不自建沙箱〕** —— "执行 A,不重发明 CC/Codex 已备好的 harnesses 和 auto mode"。关键洞见:tick 以 `claude` headless/auto 模式运行,**CC auto-mode 本身自带 sandbox-exec**(bash 工具的内核沙箱)—— 定理要求的 OS 边界由 claude 运行时提供,不是我们在 repo 里重建。故:loop 启用时 tick 走 CC auto mode(继承其沙箱),wrapper file-diff + DB-detect + money-path 恒 PREPARE + HALT 作诚实-走神安全网叠加其上。**不再自写 Seatbelt .sb;posture B 撤案。** 唯一收尾**已做**:loop_guard.py:523 + tick.sh:19 的 "closes tamper vector completely / cannot be tampered" overclaim 改为 tripwire-not-boundary 诚实措辞(branch `loop/comment-honesty` @ `7c7eea62c`,2 commit on 6df3b32b6;启用时与 loop-build 同 merge)。此项不阻塞 R1-R8。
- ~~**Lead 倾向 B:** live-money + 无人值守,不对称性压倒性偏向真边界;沙箱 profile 是自包含安全件,我可备好待你裁 scope。~~(操作员裁 A,见上。) 17 文件 1862 行:tick.sh/daily.sh(单飞 flock=python fcntl 因 stock macOS 无 flock CLI;超时有界;crash fallback)、loop_guard.py(可测核心:allowlist enforce + quarantine 硬还原 + >20 文件/>600 行断路器;13/13 测试绿)、L1/L2 prompts(§C 约束逐字入)、allowlist(loop/docs/tests/architecture yaml;**scripts/** 刻意排除** —— manifest-aware 划分无法用 glob 表达,任何 tick 的脚本改动一律 quarantine 到 PREPARE)、2 plist 模板(无 secrets,plutil 干净)、enable(只打印不执行)/status 脚本。端到端 smoke:越界 src/ 写被硬还原并记 VIOLATION,域内 docs/ 写幸存。**启用 = 你两条命令**(loop_enable.sh preflight 后原样打印)。**两个启用前核对项(inline 注释已标):** ①tick.sh 的 --allowedTools 工具名单按本 session 工具名写,首启前对照本机 CLI 版本;②daily plist 的 Hour=7:10 本地时近似 UTC 12:10,DST 切换漂移 ~1h(launchd 无 UTC 原生项)。

## G. 每 packet 验收模板(brief 直接引用)

1. worktree 分支 `loop/<date>-<slug>` 或 `claude/agent-*`,单 commit 原子。
2. TDD:antibody/红测先行;实现;绿。
3. 测试 diff:base 失败集 vs 改后失败集,**新增=0**(基线 32 pre-existing)。
4. boot smoke:`.venv/bin/python -m src.main --validate-boot`(仅 DB-missing FAIL 为正常)。
5. 删除项:主树 grep 零残留 + 测试同 commit 退役 + registry 行同 commit 更新。
6. 独立 verifier(证伪 brief)PASS;money-path 用 opus。
7. journal 条目:做了什么/证据 file:line/SHA/revert 点。
8. PREPARE 包:diff 摘要排队操作员;AUTO 包:合并后报 SHA。

## H. 证据索引(全部盘上,不依赖 session 记忆)

- **锚点注册(本行是 R0-R8/W0-W5 wave-ID 的 canonical 定义点):** R0-R8 = 本文件 §E 的重构波次;W0-W5 = order-engine v2 波次(`order_engine_implementation_architecture_2026-07-02.md`)。凡与旧 R3-tag(topology.yaml)、workspace_map 模块标签同名者,以本行语义为准;旧 tag 随 R8 退役。
- 表示层契约(注释/命名/元数据/锚点四法 + 逐 surface 判决):`docs/rebuild/representation_contract_2026-07-08.md`
- 蓝图(判决+file:line):`docs/rebuild/whole_system_first_principles_2026-07-07.md`
- 8 路测绘 journal:`~/.claude/projects/-Users-leofitz-zeus/ce5821ff-a917-4ece-ab1c-008d235a2639/subagents/workflows/wf_11ddbec6-ab3/journal.jsonl`
- 6 路疤痕审计 journal(107 文件死亡地图在 decision-remnants 条):同上 `wf_d8bbc852-436/journal.jsonl`
- consult 答案:疤痕 r2 原文已入库 `docs/rebuild/consult_answers/scar_audit_round2.txt`(执行最依赖的一份);全系统 r1 与 loop 评审原文在 /tmp/cgc/(answer_REQ-20260707-112056 / answer_REQ-20260707-001102),**若 /tmp 已清无妨:全部裁决结论已并入蓝图 §7/§8;followup 用 conversation 6a4d277b-74ec-83ea-8e9c-8c96b151fdd0 续线。**
- order-engine 权威链:`docs/rebuild/order_engine_first_principles_design_2026-07-02.md` + `order_engine_implementation_architecture_2026-07-02.md`(W0-W5、deletion TRAPs)
- q-kernel 权威链:`docs/rebuild/consult_build_spec.md` + impl_stage0-7
- loop v2:`docs/operations/current/plans/allday_improvement_loop_design_2026-07-06.md`
- 概率法:`docs/authority/replacement_final_form_2026_06_09.md`

## I. 已 consolidate(2026-07-08:20 worktree 散乱 → 1 分支 + 4 worktree)

**操作员令"对齐现状=收编散落 agent 工作,别甩回给我"。已执行:** 所有已验证包合入**单一分支 `rebuild/consolidated` @ `c876e55f7`**(= 当前 main `8a258e349` + R0 八包 + wave-1 四包 + wave-2 四包 = **43 commit ahead of main**)。**验证:main 42 state 失败 vs consolidated 42,diff=0 —— 我的 consolidation 零新增回归**(42 里的 mark_pending_exit 失败是操作员自己 8a258e349 "harden pending-exit" 带来的,main 39→42;非我)。boot smoke 仅 DB-missing。

**Worktree 清理:20 → 4**(删 17 冗余:12 agent-* + combo-r0/wave1 + wt-repr/loop/r2c;commit 全在 consolidated 前已核 ancestor,worktree remove 保留分支;另删 17 冗余分支)。**留:** `main`(你的 55 dirty tracked + 13 untracked 未提交)、`combo-final`(= rebuild/consolidated)、`wt-r8`(r8-gov 补 Task1 红基线在跑)、`wt-manifest-repin`(另 session,非我)。

| 波 | 内容 | 在 consolidated |
|---|---|---|
| R0 ×8 | close-economics/CAS验收/13尸体/fdr/残渣pass/repr-seed/plist卫生/test-fix | ✅ |
| wave-1 ×4 | R1-b 死模块删 · R2-core reconcile引擎 · R3 SourceContract · R6-a venue契约+删redeem | ✅ |
| wave-2 ×4 | repr-enforce(boot-card 20.3×)· loop诚实注释 · r2c-map(分析) · R8-Task2(doctor死码删) | ✅ |

**〔2026-07-09 更新 — 我核实了实况,之前的两个"blocker"都已不成立:〕**
- ~~posteriors 停摆盲飞~~ **已恢复**:最新 posterior 2026-07-09T00:32Z,过去 5.5h 产 352 行。manifest repin(main 已收编)修好了。我之前照抄过时 notepad 没核实,是错。
- ~~你 main worktree 55 dirty 文件挡 ff~~ **你已全部 commit**:main 干净(HEAD `4b165973e`,dirty=0),前进 24 commit。
**我做了对账(不是给你一条命令):** consolidated 已 merge 当前 main —— 只 2 冲突(chain_mirror_reconciler.py 手工合:你的早退分支 + R2-core try 隔离;repr doc §8 add/add),两文件 compile 通过、chain_mirror+state 回归 **42=42 零新增**、boot smoke 仅 DB-missing。**consolidated `cf6599415` 现 ⊇ main,land = 干净 ff。**
**剩一步 = ff + daemon 重启(协同 deploy)。** ff 会把 live checkout 工作树推进 47 commit,运行中的 daemon 会 SHA 失配触发 deployment-freshness 暂停(main 自己的 `f520d5e94` 逻辑)→ 必须配合 `deploy_live.py restart all`(仅操作员)。故这是一次协同部署步:**我可代跑 ff(MAINTREE_GIT_BYPASS=1),你紧接重启**;或你俩一起做。建议选低活跃窗口。R0-f plist-secrets、close-economics backfill 是 land 后的独立手动步(脚本已备)。

## J. 指挥台账(2026-07-08 全流程落地开工;操作员令"从第一性原理重构、债务清算到 loop 全流程落地")

**基点:** 所有 wave-1 agent 基于 `combo/r0-integration` = 22737ee9(R0 八 commit 已验组合态),各自 worktree 首步 merge 该 SHA(已证零冲突)。操作员后续 merge 任一 wave-1 分支即自动携带 R0 历史(代价:R0 不可再单包拒收 —— 已验全绿,判可接受)。
**围栏(每个 brief 携带):** 操作员脏树 25 文件绝不触碰(src/main.py、src/riskguard/riskguard.py、src/state/db.py、src/events/event_store.py、src/data/raw_forecast_artifact_manifest.py、substrate_observer.py、download_replacement_forecast_current_targets.py、scripts/topology_doctor_closeout.py 等 —— 完整清单 = `git status` M 集);**永禁 git stash**(refs/stash 竞态,memory 已录)。

**Wave-1(并行开工,文件疆界互斥):**
| 包 | 内容 | 级别 | 边界要点 |
|---|---|---|---|
| R1 | 概率单链:materializer 切 predictive_distribution_builder + 死模块删 + ENS/Platt 降档 | PREPARE | 只动 q-assembly;不碰 manifest/production 调度(R3 域)与操作员 src/data 脏文件 |
| R2-core | 双 snapshot 契约 + diff 引擎 + 4-5 真谓词表;并封 R0 verifier 三发现(hard-terminal 旁路/per-row try/except/fill-fact 乱序窗) | PREPARE·K0 | command_recovery/exchange_reconcile/chain_mirror;R2-c 31-pass 迁移下一波 |
| R3 | SourceContract 表 + 通用调度器 + dual_run_lock 删 + 五关切 tick 拆 | PREPARE | 调度/取数层;禁碰 materializer(R1 域)|
| R6-a | venue response-contract 层 + typed-fact 完成 + redeem 机械删 | PREPARE | client/解析层;禁碰 command_recovery/exchange_reconcile(R2 域);price_channel 拆分顺延 |
| loop-build | loop v2 全套构建(HALT/JOURNAL/LEDGER/tick.sh/daily.sh/prompts/plist 模板/allowlist) | AUTO-prep | 纯新文件;launchctl 加载=操作员 |

**Wave-1 状态(2026-07-08):全部收口、逐包对抗 verifier PASS、组合验证 PASS。** R1-b `abadc430f`(删 3 死 prob 模块;R1-a/c 待 EMOS 操作员签)· R2-core `dd54123cd`(+封 R0 verifier 三发现)· R3 `1a1a15b53`+`0096fff4f`(SourceContract + registry follow-up)· R6-a `4abb3c364`(response-contract + 删 ~1.5K 死 redeem 机械)· loop-build `6df3b32b6`(机器 verified-solid,启用姿态 A 已定)。**组合验证 PASS**(combo/wave1-integration @ `c4d4d45e3`,4 包零冲突合入 R0 base):7 共享域 FAILED base 88→combo 87(**0 新增回归**,R6-a 反修好 1 个 pre-existing),reconcile 自有 29 passed;`collateral_reservations` 4 行为 fail-soft log-noise(表在 prod 存在,测试 fixture 缺,记 R2-core follow-up)。**方法诚实记录:** 首版组合脚本假绿(combo 缺 gitignored config/settings.json + base 无 tests/reconcile 触 collection-abort → 测试没跑),已改逐域跑 + strict `^FAILED` diff 复验。

**Wave-2 派发计划(组合验证绿后开工;auto-mode harness,每包 worktree+对抗 verifier):**
| 包 | 内容 | 级别 | 前置 | 模型 |
|---|---|---|---|---|
| R2-c | 31 recovery pass → 4-5 谓词表条目;逐批 replay 证据(旧 pass vs diff 引擎同窗产同 corrective events) | PREPARE·K0 | R2-core✓ | 实现 sonnet / verifier **opus** |
| R1-a2 | sigma_authority 收敛:materializer 切 predictive_distribution_builder 单链 | PREPARE | replay harness✓ + EMOS 签(部分) | 实现 sonnet / verifier **opus** |
| R3-b | flip `_replacement_cycle_availability_poll_if_needed` live 调用点到新调度器(单 seam) | **HOLD·verified-refuse(2026-07-09)** | R3✓ + 缺 live-adapter 层 | 实现 sonnet / verifier sonnet |
| R8 | 治理域拆:doctor 按域切、867 红项定性、registry 生成化(大量 AUTO) | AUTO/PREPARE | 独立 | 实现 sonnet / verifier sonnet |

**Wave-2 派发实况(2026-07-08,受操作员 ~80 文件脏树制约):**
- **R2-c 已分析,零删除**(opus,分支 `rebuild/r2c-recovery-predicate-table` @ 2b2ebb9a8)—— verify-before-delete 用 live-DB 实证推翻蓝图"删 30 疤痕"前提(pass 今日仍发火,主线程 spot-check 确证)。**零删除是正确结果**,挡住删除 load-bearing 恢复码。R2-c 重排为 R5+EDLI-双账本-移除 的下游(详 §E R2)。map = 交付物;执行体 defer 到解锁波。
- **R3-b HOLD → verified-refuse(2026-07-09,wave-3 sonnet,零编辑)**:深挖两条独立充分理由拒翻:①新 scheduler(src/ingest/scheduler.py)是脚手架非活路径 —— `resolve_ref()` 全仓零调用、`run_source_contract_tick` 从不调 `fetch_ref`、无 daemon import SourceContract、`bayes_precision_fusion_extras` 行的 FIXPOINT-latch 状态机(~40 行)在 generic tick 无等价物;翻过去会静默停 anchor-leg OpenMeteo 下载。`contract.py` docstring(61-66)自证"本包故意不 flip,因 §E2.2 要 replay 证据本包不携带"。②蓝图当天自标 HOLD。**正确结论 = 保持 queued,直到有人把缺失的 live-adapter 层(persistence/event-writer/dependents-dispatch/extras-latch-bridge)作为独立可审 slice 建成并等价测试。** 另修正:live call site 仅 1 个(:1161,def 在 :876),非 2;R2-core replay harness 比对 reconcile 记录非 ingest cursor,对本 seam 非适用证据(蓝图 §82 依赖行 aspirational)。原 manifest-repin 撞工已消解(commits 并入 base,worktree 不活)。
- **R1-a2 HOLD**:目标含 `src/engine/qkernel_spine_bridge.py`(脏,操作员正编 q-链)+ materializer 在 posteriors 盲飞临界路径 —— 硬 HOLD。
- **repr-enforce ✅ DONE+验证**(sonnet,branch `rebuild/repr-enforce` @ `5d13f796d`,5 文件 911 行):R0-h --repr checker 长成 4 法机器可查(comment-law 加 stale-reference 检测:注释里的路径/符号对 2604 文件 def 索引核验;metadata-law/anchor-law 各出真 finding,INV-41 零绑定命中与契约 §0 自证吻合)+ **boot-card 生成器**(scripts/gen_boot_card.py,确定性,--check drift 检测)。**主线程实跑确证:boot_card.md = 3382 token vs 68.6K 基线 = 20.3× 压缩**,装下 money-path 链/时间法/3-DB/全 42 INV/typed-event 意图链/stop rules —— 你 Msg-1"最少 token 最大信息"愿景的具体飞升件。回归 44=44 diff=0,17 新测试绿,顺修一个真 bug(_registered_fc_ids 缺文件存在守卫)。**merge-time 待和解:** representation_contract doc 主库 untracked,agent 拷入并附 §8(未造内容);merge 时对齐。
- **R8-governance ✅ DONE**(两 commit 在 consolidated:`44e7b2872` doctor 域拆+死 load_schema 删,`ef5aeaa3e` 真红基线 doc):**真红基线 = 535+ 已确证**(127 命名域 + 408 flat@71%,外推 ~650-700)vs 蓝图称 867 vs registry 记"3"(**错 2 数量级**)。找到 registry 变陈根因:两类静默 hang 杀全套 pytest —— ①test_bootstrap_symmetry/test_pre_live_integration 未 mock `get_sibling_outcomes` → live Gamma API 无限挂;②test_fit_sigma_scale CPU stall。**Gamma-mock 修复精化(主线程有界实验证):加 mock 止 hang 成功(2s 完成)但不够 —— 暴露第二问题 position_belief:617 按路径开真 DB + 4 测试 assertion 需随 sibling 数据更新;是多步 packet 非一行,不 xfail 隐藏(违反本 baseline 揭的 registry-隐藏之病)。** 列 governance 首 follow-up。
- **R8 HOLD 触脏部分**:doctor/topology 域 `test_topology.yaml`+`topology_doctor_closeout.py` 脏。

### Wave-3 派发实况(2026-07-09,脏树已解 —— main clean、posteriors 已恢复,4 包并行)—— consolidated HEAD `cca265f46`

- **R6-b ✅ DONE+主线程独立验+merged**(sonnet,`b250d8699`):price_channel_ingest.py 3126→2688 拆 venue-fact 桥(留)vs re-decision 路由(→ 新 `src/events/price_channel_redecision_router.py` 561 行,10-fn 簇"venue 不决定谁 re-solve")。PEP 562 `__getattr__` shim 保 10 迁移名的既有 import(main.py/daemon/~15 测试零 repoint,trading-lane-isolation AST 测试仍过)。**主线程验:base 36F/504P == branch 36F/504P,NEW=空**;shim 10 名 + durable_fill_bridge 全 resolve;agent 建时抓修一 off-by-one(丢函数头)。
- **R4-b ✅ DONE(部分)+主线程独立验+merged**(sonnet,3 commit,`07778206d`):main.py **11671→11085**,三 job body 外迁到既有 owning module —— chain_mirror→`chain_mirror_reconciler.run_cycle()`、bankroll→`bankroll_provider.run_warm_cycle()`、exit_monitor+5 单调 helper→`exit_lifecycle.run_exit_monitor_cycle()`。**主线程验:state/execution/money_path/events/runtime + 3 flat = base 90F/1822P == branch 90F/1822P,NEW=空**;4 测试改主线程逐条审 = 跟随委托到新家、断言不变量全保、无一弱化(p4 exit-submit 测试实际更严,走委托图确认相位仍 P1 可达)。**诚实停点:剩 ~8K 是一个交叉耦合 EDLI 子系统(reactor+redecision+day0+pre-submit 共享几十 `_edli_*` helper,多 job 多调用点),逐 job 迁会破首个共享 helper → 下一步整块迁 `src/events/reactor.py`,需先建依赖图**(排 R4-b2)。legacy_cron 主管道 d10565ffb 早删,残 enum 值不动(独立小决定)。
- **R5 ✅ DONE(1 修 + 3 证伪)+主线程独立验+merged**(sonnet,`158232395`):4 子目标 3 个 verify-before-delete 推翻蓝图(同 R2-c/R3-b 模式)——①`write_coordinator` "零调用" **假**:substrate_observer + price_channel_ingest 7 处 live `lease()`,不删;②db.py 四拆**实建后回退**:32 测试文件靠 `monkeypatch.setattr("src.state.db.X")` same-module 全局绑定,物理拆分静默破(shim 名 ≠ 迁移函数新家全局),是 call-site-rewrite 非行搬迁,descope 到后续带 TDD 的波;④7 `_migrate_*` 是每次 init 无条件跑的自愈收敛器(2 个被 stale-schema fixture 测试),归档会停保护未收敛 DB,不动。**唯一改:domains.py +2 行**补 backtest 两表 ownership,修绿既存本红的 `test_domains_reproduces_registry`。**主线程验:base 43F→r5 42F(修绿 1),NEW=空**。
- **R3-b ✅ verified-HOLD**(见上 §R3 表 + wave-2 条):非单 seam flip,缺 live-adapter 层,保持 queued。
- **R4-b2 ✅ DONE(部分)+主线程独立验+merged**(sonnet,`fd3c900c5`):R4-b 停点的续 —— main.py **10823**(−262),day0-hourly-refresh 簇整块迁 `reactor.run_edli_day0_hourly_refresh_cycle`;thin delegating hook 留 main.py 保调度注册,lane 锁经注入(不反向 reach)。**agent 建全依赖图(123 `_edli_*` def,5 自然簇)** —— 迁了簇 1,簇 2-5(reactor/prune/redecision/pre-submit)诚实停:各 ~600 行且 **15-20 个 `inspect.getsource()` 断言内部调用顺序的 money-path 不变量**(claim-storm/lock-bounce 修的排序契约),不鲁莽切。**主线程验:base 88F=88F branch,NEW 空**;3 测试改逐条审=跟随委托、精确区分迁移 vs 留下 helper、无弱化。附赠:8 个 main.py 死 `_edli_*`(真实现在 price_channel_ingest,copy 从不调)= DEAD_DELETE 候选。→ 续 R4-b3。
- **R4-b3 ✅ DONE+主线程独立验+merged**(sonnet,3 commit,`b104835ff`):main.py **10823→7894**(−2929)。①step-0 删 8 个验证死的 `_edli_*`(grep 全仓零调用,真实现在 price_channel_ingest);②整块迁 reactor(`_edli_event_reactor_cycle` ~600)+ prune(`_edli_prune_pending_working_set` ~490)簇 → `reactor.run_edli_event_reactor_cycle`(reactor.py 3879→6824)。**锁归属决策:`_edli_reactor_active_lock` 留 main.py(5+ job 读),但注入 Lock 对象(非布尔快照)—— cycle 独有 acquire/release 生命周期需活对象**;`_ensure_venue_read_side_adapter` 留 main.py(改 heartbeat-supervisor 共享全局,迁会撞另一子系统)reach-back 调。**19 测试文件改**(3 类:module-attr 重指 / `inspect.getsource` 委托一层 / `read_text` 边界锚换 reactor.py),15-20 个 money-path 顺序不变量(prune-before-entity-keys / submit 门控 / prune-before-repair-sweep)全 1:1 保。**主线程独立验:base 88F/1659P == branch,NEW 空,reactor budget/fairness 15 passed**;agent 超 4 域 grep 全 56 迁移符号找 8 额外耦合文件全修(除一 base 本红无关项)。
- **R4-b3 集成验:merged consolidated import OK。main.py 累计 11671→7894。**
- **R4-b4 ✅ DONE+主线程独立验+merged**(sonnet,2 commit,`f61517595`):main.py **7894→5838**(−2056)。收尾最后两 EDLI 簇 —— ①cluster 4 continuous-redecision(`_edli_continuous_redecision_screen_cycle` ~600 + ~30 helper)→ `reactor.run_edli_continuous_redecision_screen_cycle`;②cluster 5 pre-submit JIT(实测仅 ~115 行残留,bulk 已被 R4-b3 迁,含 CLOB singleton + keepalive pinger job)。**决策:`_edli_redecision_screen_lock` 注入(dispatcher-owned);`_edli_redecision_acted_state` dict 被 out-of-scope command-recovery 簇共享 → reach-back(非 cluster-internal,正确);CLOB singleton 随 pinger 迁(跨模块共享理由消失)**。pyflakes -F821 抓修 2 真 bug(undefined name + 循环 reach-back import)。**主线程独立验:base 88F/1659P == branch,NEW 空**;测试改纯 `main.X`→`reactor.X` 委托,`.index()` 顺序断言全不变。
- **集成验:merged consolidated 上全 5 EDLI cycle 迁 reactor.py + dispatcher hook/lock/reader 全保、import OK** —— r4↔r6b↔r4b2↔r4b3↔r4b4 无交叉断裂。**main.py 累计 11671→5838(−50%);reactor.py 3550→8979。R4 EDLI 提取弧完成。**

**consolidated 状态:** `cca265f46` ⊇ main(4b165973e)+ R0(8)+ wave-1(4)+ wave-2(repr/loop/r2c-map/R8)+ **wave-3(R6-b/R4-b/R5)**,全 0-regression。ff-land 主库 + daemon restart = 操作员协调步(deploy_live.py restart all 仅操作员)。

**剩余队列(非阻塞主线程,gated):** R4-b2(EDLI reactor 整块迁,需依赖图);R2-c 执行体(gated R5 单写者 + EDLI 双账本);db.py 四拆(descope 到 call-site-rewrite 波);R7(GATE:CAS 绿 + 证书 replay 门 + order-engine W3);R1-a/c(EMOS per-city 签);Gamma-mock hermetic 修(governance 首 follow-up)。

<!-- SUPERSEDED 2026-07-09(脏树已解、R4/R5 已执行):~~操作员脏树封死 money-path wave-2 面;R4/R5 阻塞操作员;posteriors 停摆盲飞~~ -->
**运行态提醒仍立:** 无交易则重构不产 alpha —— consolidated ff-land + daemon restart 后才把重构落进跑动系统。
