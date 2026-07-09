# Zeus 全系统第一性原理重构蓝图 (2026-07-07)

**Status: PROPOSED(疤痕审计过门)— 本地 8 路测绘 + consult 双盲对撞 + 第二轮疤痕根除审计(6 路对抗 + consult round-2,§7)已全部合成;待操作员裁 R0/R1 开工。**
**方法:** 复用 order-engine v2 的方法(公理 → 最小组件图 → REUSE/ADAPT/BUILD/K0 判决 → 波次 DAG),推广到全项目。已有两个局部第一性原理重建是给定输入,不重做:q-kernel(`consult_build_spec.md` → `src/probability/`)、order-engine v2(`order_engine_first_principles_design_2026-07-02.md` + implementation architecture,W0-W5 在飞)。
**证据:** 8 路只读子系统测绘(wf_11ddbec6-ab3,975K tokens,每项判决带 file:line;journal 存证)+ 主线程独立结构底账。

---

## 0. 总账

| 层 | 现状 | 备注 |
|---|---|---|
| src/ | 358K 行 / ~30 包 | 头四包 data/engine/execution/state = 212K(59%) |
| tests/ | **577K 行 / 1535 文件** | 54% flat 文件无 topology 分类;子目录测试结构性豁免 |
| architecture/ 注册表 | 36.6K 行 / 49 个 yaml | doctor 家族本身 16.2K 行 |
| 决策面 | **七代包并存** 38.5K | strategy(最老)→ decision → decision_kernel → solve/probability(最新,skeleton)|

系统的不可约任务一句话:**比市场更快摄取气象数据 → 每 bin family 服务诚实后验 q → 从 q vs 可执行订单簿成本解订单动作(单一 bankroll)→ 结算按冻结证书评分 → walk-forward 学习。**

## 1. 最小组件图(目标形态)

```
[SourceContract 表] → [通用时钟轮询/取数/解析 调度器] → SOURCE_RUN_ARRIVED
        ↓ (一个 materialize reactor:on new input, recompute affected keys)
[概率权威内核] —— debias_authority + bayes_precision_fusion + day0_conditioner + emos
        ↓ forecast_posteriors (q + q_version hash,单一写者)
[事件面] opportunity_events(append-only,幂等)← 三个 WS/poll 桥
        ↓
[SOLVE] (order-engine v2 §3.3,单 seam:qkernel_spine_bridge)
        ↓ venue_commands + CAS 预留账本
[Venue 边界] 类型化 client + response-contract 层 + FC-03 + rate budget
        ↓ 链上真相
[一个收敛环] fetch chain truth → diff vs projection → append corrective event
        ↓ settlement
[SettlementSemantics 断言] → [grade_receipt × 冻结证书] → calibration pair append(walk-forward)
[资本/风险] CAS 账本(唯一美元真相)+ RiskLevel(31 行,capital-only 输入)+ RED 清扫
```

每个组件:单一职责、单一 canonical state、单一写者。跨组件只走事件/DB 行,不走 import。
两个显式补充组件(consult 对撞采纳):**Contract Registry**(family 身份 = city × metric × date 类型化,metric identity 必须进 family key/q_version/证书/校准 key —— 高低轨永不串);**Observability/Verifier**(freshness/账本恒等/事件确定性/可重放性的 fail-closed tripwire,零经济权威)。**决策证书是记账原子**:每个 command、no-op、cancel、exit、结算 grade 都指回授权它的冻结证书;校准只消费 settled + walk-forward-eligible 证书。

## 2. 八个子系统判决(本地测绘,每项 file:line 在 workflow journal)

### 2.1 Ingest(~34K → 目标 ~8-10K)
- **内核已存在:** `source_clock_update_probe.py`(239 行)就是最小形态(cursor-diff → 幂等事件),只是被窄化到 OpenMeteo;`hole_scanner`/`collection_frontier`/`ingestion_guard` 接近目标形。
- **病:** 两代 provider-contract 抽象未合并(老协议 933 行只有 2 个实现者;新管线 14.5K 行零共享契约);`_replacement_cycle_availability_poll_if_needed` 一个 tick 函数缝了 5 个关切(每个都是事故补丁,带 06-11/06-13/06-16 直令日期);双调度器构建模式并存;dual_run_lock(Phase-1 迁移脚手架,过期 2 个月,14 个调用点);~2.2K 行 tick 脚本与 daemon 任务重复第二调用面。
- **判决:** SourceContract 表 + 通用调度器 + 单一 materialize reactor(收编 seed_discovery/seed_builder/request_builder/queue/cycle_advance_trigger/fusion_upgrade_trigger 六个各自发明幂等的触发模块)。DELETE:legacy 调度模式、dual_run_lock、tick 脚本、ownership toggle。

### 2.2 概率权威(16.6K → 目标 ~7K)
- **内核已存在且对法:** `debias_authority.py` + `bayes_precision_fusion.py` 与 replacement_final_form §1a/1b 逐行对应;`day0_conditioner` REUSE(order-engine doc 已判)。
- **病(最重一条):** **两条并行 q 生产链** —— DB-materializer 链(写 forecast_posteriors)与 spine 链(`predictive_distribution_builder`)各自组装 center+fusion+emos;同 family 可产不同 q 语义(这正是 consult_build_spec 的 BLOCKER-1,q-kernel 重建就是为解它,但 materializer 侧从未切换)。ENS/Platt/EDLI-bias 遗产 ~2.4K 行 inert-but-armed;死模块 ~1K(observation_precision_fusion 等,零调用者)。
- **判决:** 一条链。materializer 调 `predictive_distribution_builder`(或反之),删第二套组装。calibration/ 的 store+manager+retrain(~7.6K)KEEP_AS_KERNEL(结算评分/ETL 是真复杂度)。

### 2.3 State/DB(41.4K → 目标 ~15K)
- **内核已存在:** `domains.py`(176 行,typed Domain→table ownership,2026-06-30 自带"收编其余两处 ownership"设计意图)、`canonical_projections.py`(401 行纯函数)、`owner_routed_write.py`。
- **病:** 三重 ownership 注册(init-code CREATE-lists + 2939 行 yaml + runtime 路由)手工同步;db.py 12.8K = 四个焊死的关切(连接工厂/schema DDL+migration/fact-logging 29 个 log_* /读查询 15 个 query_*);**7 个写者各自 UPDATE position_current**(Bug A/B 的土壤);7 个跑过一次的 migration 以常驻函数形态活着(~700 行);write_coordinator.py 485 行零生产调用者;37 处 ad-hoc ATTACH 绕过 sanctioned 工厂。
- **判决:** domains.py 成唯一 ownership 源(yaml 与 CREATE-lists 从它生成);db.py 按四 seam 切开;position_current 收敛到 projection.py 单写者;migration 进 ledger 归档。

### 2.4 执行恢复/对账(22.6K → 目标 ~4K)
- **内核已存在:** `chain_mirror_reconciler.py`(1033 行)整体就是目标模板 —— classify + 4 个 apply_* + reconcile();`fill_dedup.canonical_trade_fact_cte` 是正确的共享 dedup 原语。
- **病(全项目最重):** command_recovery 16.6K 中 **~14K 是 31 个手写 per-incident reconcile pass**,手工定序(:15224-15513 的 290 行 pass list);"local truth/venue truth" 三个文件三种定义(不同 SQL、不同 dedup CTE 拷贝 3-4 份);Bug A/B 根因 = 三个独立 close-projection builder 用 `SimpleNamespace(**current)` 重建对象、漏 pnl key;EDLI 前缀平行 lane ~2.5K 重复同一比较。82-103 个 reject 字符串 vs 2 个注册状态。
- **判决:** 一个 diff 引擎(单一 local-truth snapshot 契约 + 单一 chain-truth snapshot 契约 → diff → corrective event),chain_mirror_reconciler 为模板;31 个 pass 的 venue-quirk 判词(~800-1K 行)提炼成谓词表 KEEP。**这是 Bug A/B 类事故的结构性根治。**

### 2.5 main.py/mesh(11.4K → 目标 ~1.5K)
- **病:** ~8-8.5K 行是 inline job body(main.py:2195-10863)该住进 owning module;legacy_cron 死管道残留 ~150 行;EDLI stage-readiness 旗帜时代装置 ~900 行;9 个 live daemon 只有 5 个 plist 进 git,**且 plist 内嵌 secrets**;两份独立维护的 sidecar-prerequisite 清单。
- **内核已存在:** `cascade_liveness_contract.yaml` + `_assert_cascade_liveness_contract`(声明式 registry-diff-failclosed)是 boot-guard 家族该长成的样子。
- **判决:** job body 全部外迁(每 module 一个 `run_cycle()`);main() 缩成注册表驱动;plist 全部进 git、secrets 出 plist(keychain/env 文件)。

### 2.6 结算/学习(~39K 触面 → 收敛)
- **内核对法:** `settlement_semantics.py`(WMO/HKO 舍入分发)+ `graded_receipt.grade_receipt` = 618 行,近最小,不碰;`settlement_writers.write_settlement_with_era_provenance` 已是强制单点。
- **病:** **五条平行 position-close 路径只有两条会算 realized P&L**(= Bug A/B 的准确结构表述);Gamma capture 与 chain-truth 判定是两条结构分离的捕获路径(33 笔未记账的洞);三代 calibration-pair schema/管线共存 ~3.8K。
- **判决:** 一个 close-economics 函数,所有 terminal 转换必经;capture 收敛为单管线 + chain 兜底;calibration 管线收敛到最新代。

### 2.7 风险/资本(~9K → 目标 ~4K)
- **内核对法:** `risk_level.py` 31 行(enum + max 聚合)就是理想形;CAS 账本核心(reserve/convert-on-fill/release)已符合 order-engine W1 目标形——W1 补的是原子性与 partial-fill。
- **病:** 两处独立 drawdown/kill-switch 计算;统计信号(brier/settlement quality)混进 capital RiskLevel(应下放到概率权威的 per-family fail-closed,不该全组合停机);per-strategy risk_actions + policy.py 全文件复制全局 RiskLevel 已管的事;Kelly haircut 栈 63 个散布调用点(order-engine W5 已排队删)。
- **判决:** RiskLevel 输入收窄到 capital-only;policy.py DELETE;exposure caps(governor.py:60-315)KEEP 作 solve 外圈护栏(order-engine doc 已裁)。

### 2.8 Venue(13K → 目标 ~8K)
- **病:** 死 redeem 提交机械 ~650 行(法律禁止 Zeus 提交 redeem,代码还留着构造/签名/广播);**response-contract 层缺失** —— 7 处双/三键 .get() 猜测 venue 返回形(#429 cancel_orders envelope bug 的整类根源);两套竞争的 typed-fact 层都没完成(adapter 死壳 vs 未接线的 470 行 contracts 模块);price_channel_ingest 3.1K 把 venue-fact 桥接与 re-decision 路由缝在一起(venue 不该决定谁 re-solve)。
- **内核对法:** batch_submit 的 fail-closed envelope 映射、safe_exec.py、rate_budget token-bucket、FC-03 双层。CTF split/merge/convert 已建未耗(W2 设计如此)。
- **判决:** 补 response-contract 层(每个 venue 端点一个显式解析契约);redeem 提交机械 DELETE(保留 winning-balance 读,外部赎回记账需要);price_channel_ingest 拆桥接/路由。

### 2.9 Tests/CI 治理(577K + 36.6K → 结构收敛)
- **内核对法:** money-path semantic-diff CI(objects.yaml + ci.yaml + classifier + 19 个编号测试)、test_topology law_gate(9 个法律主题)、antibody 测试自带 protects:/falsifying_proof。
- **病:** 54% flat 测试无分类 + 471 个子目录测试结构性豁免(非递归 glob)= 分类法只覆盖少数;script_manifest 262 个 live drift;doctor 家族 16.2K 行把 docs/tests/scripts/graph/closeout 六个不相关治理域捆在一个 3K 行 dispatcher;full-pytest-sweep 永久 advisory(continue-on-error 自出生起);pre_existing_failure_registry 3 条 vs 实际 867 个红项。
- **判决:** 治理检查按域拆分;分类法要么覆盖全部(递归)要么退役 flat 分类;867 红项一次定性(真失败 vs 死检查)后 registry 如实。

## 3. 五大结构性缺陷(from-scratch 设计不会有的)

1. **N 代并存永不删旧**(决策面七代、calibration 三代、provider-contract 两代、typed-fact 两代)—— 每次重建都加新层,迁移做一半。根治:每个 rebuild wave 的 DoD 包含删旧,不删不算完(order-engine doc "tests retire WITH components, same commit" 已是此法)。
2. **Per-incident patch 累积代替收敛环**(command_recovery 31 pass、ingest tick 五关切、venue .get() 猜形)—— 事故响应长成永久结构。根治:diff-engine / contract-table / response-contract 三个"一个机制吃掉 N 个补丁"的收敛。
3. **同一真相多写者**(position_current 7 写者、ownership 三注册、drawdown 两处算)—— Bug A/B 的直接土壤。根治:单写者 + 生成式注册。
4. **决策逻辑渗进边界层**(price_channel_ingest 决定 re-solve 谁、main.py 内联 8K 业务、riskguard 里混统计信号)。根治:边界只做类型化转译。
5. **治理面比被治理面长得快**(49 yaml/36.6K、doctor 16K、tests 577K 但 54% 未分类)—— 检查器自己成了无人维护的 legacy。根治:治理只保"代码无法自证"的(money-path semantic CI、law_gate、antibodies),其余生成或删除。

## 4. 统一迁移 DAG(与 order-engine W0-W5 合流)

原则:live money path 永不摸黑;每 wave 独立可 ship 可 revert;agent-session 包 ≤1 周(TDD + verifier);**每 wave 的 DoD 含删旧**。

- **R0(与 order-engine W0/W1 并行,即刻)— 止血内核:**
  a. 单一 close-economics 函数 + 五路 close 收敛(修 Bug A/B 的结构根,与 churn-guard 协调 —— journal 已排);
  b. settlement capture 单管线 + chain 兜底(33 笔未记账洞);
  c. CAS 账本原子性(= order-engine W1,已排)。
- **R1 — 概率单链:** materializer 切到 predictive_distribution_builder;删第二套组装;ENS/Platt 遗产降 archive;死模块删。(q-kernel 重建的收尾,不是新工程。)
- **R2 — 收敛环替代 recovery 山:** local-truth/chain-truth 两个 snapshot 契约 → 一个 diff 引擎(chain_mirror_reconciler 模板)→ 31 pass 逐批改写为谓词表条目 → command_recovery/exchange_reconcile 收缩到 ~4K。风险最高 wave:每批 pass 迁移带 replay 证据(旧 pass 与 diff 引擎在同一历史窗口产出相同 corrective events)。
- **R3 — ingest 契约化:** SourceContract 表 + 通用调度器 + 单 materialize reactor;删双模式/dual_run_lock/tick 脚本。
- **R4 — main 瘦身 + mesh 入册:** job body 外迁;legacy 死管道删;9 plist 全进 git、secrets 出 plist。
- **R5 — state 单源:** domains.py 生成 ownership;db.py 四切;position_current 单写者;migration ledger。
- **R6 — venue 契约层 + 边界纯化:** response-contract;redeem 提交删;price_channel 拆分。
- **R7 — 风险收窄 + 决策面清代:** RiskLevel capital-only;policy.py 删;七代决策包随 order-engine W3/W5 落地收敛为 probability+solve 两包。
- **R8 — 治理重构:** doctor 按域拆;分类法全覆盖或退役;867 红项定性;registry 生成化。

依赖:R2 依赖 R0a(close-economics 先统一,diff 引擎才有唯一写形);R7 依赖 order-engine W3(solve 上线才能删旧代);其余 wave 相互独立、可并行开 worktree。

## 5. 质量护栏(每 wave 不变)

worktree + TDD + 独立 verifier(money-path 用 opus);byte-identity/replay 证据(R1/R2 强制,标准 = certificate-native replay + 账本对抗并发 + 事件乱序 fuzz);planning-lock + K0 packet(动 src/state/** 或 schema);registry 同 commit 更新;部署只走 `deploy_live.py restart all`(操作员执行);每 wave 单 PR、批式 review。
**Fail-closed 分向**(重构不得丢):DB/q 真相不可用 → 新入场 fail closed;cancel 与减险 exit 在 venue 真相充分时必须继续跑。运营延迟量(cancel/submit p99、WS blind window、模型到达分布)是结构测量,不属 no-history-caps 禁区。

## 6. 与 24/7 loop 的关系

R0-R8 的包就是 loop 改进队列的主食:C2/AUTO 级包(治理、docs、死码)loop 自主流动;R0-R7 的 money-path 包全部 PREPARE 级(loop 备 diff,操作员 merge+deploy)。蓝图本身按 `loop/LEDGER.yaml` 的假设条目管理:每个 wave 的"预期质量收益"是可判伪 claim,结算数据累积后升降级。

## 7. 疤痕根除审计(2026-07-07 第二轮,操作员终门:"因流血而加的机制不属于目标架构")

**方法:** 6 路对抗审计(wf_d8bbc852,750K tokens,每个机制挖生日 commit + 事故出处,过 ideal-test:"上游理想机制存在时它还需要存在吗")+ consult round-2 独立同题(/tmp/cgc/answer_scar_round2.txt,总裁决 "WRONG to tolerate these scars in the target",0.84)。两路再次收敛。判据:疤痕补偿**我们自己的缺陷**(可上游修复);load-bearing 处理**世界的行为**(venue 真实怪癖、WMO、DST、真并发)。

### 7.1 核心量化修正(收紧蓝图原判)

- **〔勘误 2026-07-08(R2-c opus 实现者 live-DB 实证 + 主线程 spot-check 确证,分支 2b2ebb9a8/`docs/rebuild/r2c_pass_map.md`):本条的"~30 pass 是可删疤痕"前提对当前状态实证为假。** command_recovery 36 个 pass **今日仍在 money path 发火**(2459 events 最新 23:18;C2 projection 1474×;phantom_void 20× 最后 2026-07-02 在 fix 之后)。订正:(1)C2 多写者漂移族删除门在 **R5 单写者 projection**,非 R0-a(R0-a 只统一 close-economics realized P&L,C2 补的是 position_current 物化滞后);(2)C1 EDLI 7 pass 门在 EDLI 双账本(zeus-world.db 事件 lane)移除,#25 有 live caller;(3)`repair_confirmed_phantom_voids` 的 SCAR_DELETE_NOW 判**错**(3 live 候选、fix 后仍发火),`repair_spurious_model_divergence` 已被 R0-e 删;(4)replay.py 证不了 write-equivalence(谓词全 writes=False)→ 删除 replay 证据当前不可构造。**R2-c 删除 = R5+EDLI-双账本 下游,非 R0-a 后 standalone。** 下方原判词保留作历史。〕
- **R2 谓词表比原判小 3-5 倍。** 31+ 个 reconcile pass 逐个考古:真 venue 行为只有 **4-5 个**(cancel/match 竞态、WS 不可靠须 REST 点真相、partial-fill 消失语义、WS 多版本 fill 投递 dedup 排序)≈ **150-300 行**,非原估 800-1K。其余 ~30 个 pass 分三类自伤疤:EDLI↔venue_commands 双账本同步族(7 pass,#123/M2 gap 自供)、多写者 projection 漂移族(10 pass = Bug A/B 土壤)、兄弟模块自己 bug 的贴缝补丁(repair_confirmed_phantom_voids 修的是 chain_reconciliation 已在 8f22bb3de 修掉的缺陷;repair_spurious_model_divergence 清已修 buy-NO bug 残渣 → **SCAR_DELETE_NOW**)。
- **决策面 per-file 死亡地图完成(107 文件,import-graph BFS + git 考古):15 个文件今天零活调用者**(R0 即删候选):decision_kernel/adapters 全包(建了从没接线)、market_anchor.py(删单项,已孤儿)、exit 三件套(exit_constrained_posterior/exit_family_optimizer/exit_observation_constraint,唯一调用者 liquidation_value.py 自己就是死的;替身 solve/exits.py 已存在)、fees.py、post_peak 对、data_lake.py、bayesian_factors、markov_smoothing、promotion_ledger、AIFS 实验对。**fdr_filter.py 是戴常量名牌的尸体**:自 docstring 称 Legacy、evaluator.py:5428 记录调用已删、权威文档判其数学空洞,但 9 文件还 import 它只为 DEFAULT_FDR_ALPHA 常量 —— 迁常量删尸体。
- **活着的目标违背:market_coherence.py 今天仍 hard-veto**(INCOHERENT_BLOCK_LIVE :46/:84/:201),直接违反 §8 已采纳的"永不 veto" —— 现行矛盾,R7 首批。
- **governor.py drawdown kill-switch = SCAR_DELETE_NOW**(与 riskguard 平行的第二 drawdown 断路器,无独立锚点)。
- ~~**sizing/portfolio_reservation.py 死于 R0 非 R7**:CAS 账本缺原子性的内存 shim(P1 ZERO-SUBMIT FIX B 自供),W1 落地即删。~~ **勘误 2026-07-08(R0-b 执行发现,读码+调用点核实):此判词错。** 它是 per-reactor-cycle 内存 provisional ledger,让 Kelly correlation-cap sizing 在候选 N+1 看见候选 N 的 stake(N 可能在证书/expressibility 下游被拒、从不到 CAS 行);删它 = 改 Kelly cap 数学 = 操作员域(§C6)。死期回挂 R7(Kelly haircut 栈退役)。另核实:W1/CAS 本体已于 c7e095ee1(2026-07-02)全量落地(单语句 CAS reserve、convert-on-fill、unsettled-proceeds、identity→RED),R0-b 只余并发验收压测。

### 7.2 治理面判决(34K/36.6K yaml 无 CI 牙齿)

实锤:49 yaml 只有 **money_path_objects+money_path_ci 与 law_gate 机制**接进 GitHub Actions;其余 ~34K 行只被本地 doctor 读 —— 无强制即无法律。终判:**LOAD_BEARING(4):** money_path 两件套、law_gate、invariants.yaml(条件:每行有可执行 enforced_by)、db_table_ownership(缩 5 字段)。**领域事实另册(3):** city_truth_contract、settlement_dual_source(并入 SettlementSemantics 类型)、data_sources_registry(只留身份/日历/轨道)。**SCAR_DELETE_NOW(5):** maturity_model、improvement_backlog、file_arrangement+advisory 门、pre_existing_failure_registry(3 条 vs 867 红 = 行政豁免非安全)、preflight_overrides。**SCAR→R8(其余):** topology.yaml 5936 行缩为 import-linter 契约、history_lore 110K(剖析进 docs、幸存不变量进测试)、fatal_misreads/antibody_specs/failure_chains(变测试或死)、context-pack/task_boot 四件套(agent 工艺出 runtime 治理)、doctor 16.2K 按域拆小。

### 7.3 restart/boot 栈判决

- deploy_live.py 28 个同日 fix commit、preflight 5661 行 45 commit 三周长成 —— 全是**从可变工作树跑 live daemon**这一地基缺陷的吸收层(dev merge 与 deploy 不可区分 → SHA 失配 → SystemExit 抖动;06-17/06-29/07-01 重启风暴与 ThrottleInterval=30 吻合)。**"20 分钟重启"当前未复现**(全 mesh uptime 4h),但根因(mutable checkout)仍在。Consult round-2 同判:**immutable release dir + current symlink + 原子交接**(R4 扩容),restart-guard 链缩为一条 boot 断言,preflight 6 分支 pending-exit 分类器死于 R2。
- **FC-03 双层 = LOAD_BEARING**(真多线程 + venue book 真实移动,两层看不同时点)—— 对抗审计未能击杀,保留。
- 4 座 freshness 塔:collection_frontier 升唯一权威;hole_scanner 降离线 coverage 审计;ingestion_guard 改名 ObservationFactGuard(写入校验非 freshness);freshness_registry 并入 frontier。
- resume_entries 引用的 RESTART_READINESS_PLAN.md §5 G8 **全 git 历史不存在** —— 幽灵权威引用,SCAR_DELETE_NOW。

### 7.4 运行态效率地基(主线程实测)

1. **60-90s 轮询地板违反 A2 公理**:opportunity_events 只被 60s 扫描 drain,无 wake-on-write;07-03 注释把地板包装成 "axiom-derived SLA floor" 是错误框架。增加显式交付物:**EventWriter.notify() wake-on-write**(consult round-2 同判 BLOCKER)。
2. **World 写互斥竞争**:20MB 日志尾 533 次 mutex-bounce;reactor claim 3s 超时弹事件;维护 prune 与 money-path 抢锁。world_write_mutex 本身 LOAD_BEARING(SQLite 单写者真约束),但 R5 须把维护写移出 money-path 锁域。
3. **日志无轮转 3.4GB**;coverage 警告洪水(同 family 每秒 20+ 行)源头正是 W5 待删 gate —— gate 死洪水停;R4 加轮转。
4. **DB 命名决斗**:根目录 0 字节 decoy + state/ 双命名 + 80GB 级 DB 无 retention —— R5 加清理与 retention 审计。
5. **plist 明文 secrets(POLYMARKET_API_KEY/SECRET/PASSPHRASE、WU_API_KEY)→ R0 即刻项**。
6. WAL checkpoint job、JIT keepalive、settlement attribution boot+interval —— 审计判 LOAD_BEARING(各有真锚点),保留。bankroll_warm 60s 加热器 = 330s 巨 cycle 的疤痕,死于 R7。
7. "20 分钟重启"传闻本身已证伪(mesh uptime 一致 4h)—— notepad 过时观察,已清。

### 7.5 R-DAG 修订(疤痕审计增量)

- **R0 增补:** plist secrets 出库;15 零调用者文件删除(连测试);fdr_filter 常量迁移+尸体删;2 个 bug-残渣 pass 删;governor drawdown 断路器删;幽灵 G8 引用清理;portfolio_reservation 随 W1 死。
- **R2 收窄:** 谓词表目标 150-300 行;preflight pending-exit 分类器入死亡名单。
- **R4 扩容:** immutable release dir + 原子交接 + restart-guard 缩单断言 + 日志轮转。
- **R5 增补:** 维护写出 money-path 锁域;DB decoy 清理 + retention 审计。
- **R7 增补:** market_coherence veto 反转(首批);EventWriter wake-on-write;bankroll_warm 死。
- **R8 修订:** 治理终态 = 4 LOAD_BEARING + 3 领域事实册 + import-linter;其余生成或删。

### 7.6 对抗审计中被 KEEP 顶住的(诚实记录,防过删)

FC-03 双层、venue-heartbeat kill-switch(venue 协议要求)、pause/resume_entries 核心(fail-closed on exception 真法)、trading freshness 3-branch gate(真上游迟到)、hole_scanner coverage diff(真缺数)、ingestion_guard 物理校验(WMO/DST/单位)、signal/ 全包 Day0 物理(10 文件,真物理,包边界死数学不死)、correlation 五件套(Ledoit-Wolf 真统计)、payoff_vector、kelly 核心公式(haircut 栈死)、sizing_context、mode_consistent_ev、executable_cost、direction_law、decision_receipt(observability-before-behavior 正是波次模式)、strategy_profile(本身就是单源收敛)、source_clock_city_weights(最新代,已近目标形)。

## 8. 外部 consult 对撞裁决(REQ-20260707-112056,答案 /tmp/cgc/answer_REQ-20260707-112056-56fecc.txt,总裁决 CORRECT-BUT-SUBOPTIMAL 0.78 / NO-GO 整体 cutover / GO W0+W1)

**方法注:** consult 无法读 pinned commit(GitHub 404),用的是 public main(落后本地 ~20 个未 push commit:Phase 1/2 删除、dedup、Day0 修复不在其视野)。凡涉"现状"的量(cycle_runtime 8682 行)按本地为准;涉"目标形态"的推导不受影响。

### 双盲收敛(两边独立得出,定案)
- **12 组件最小图 ≈ 本地 §1**:两边组件一一对应(SourceContract→ingest、Probability Authority、事件路由、CAS 账本 K0、SOLVE+证书、执行网关、SettlementSemantics+grading、walk-forward 校准、Risk/ARM、venue mirror)。Consult 多出的显式组件:**Contract+SettlementSemantics Registry**(family 身份类型化,metric identity 进 family key/q_version/证书/校准 key)与 **Observability+Verifier**(零经济权威)—— 两条都采纳进 §1。
- **五大结构性缺陷完全一致**(独立措辞,同五条):多层重证经济、recovery 替代 canonical truth、scan 当主触发、类型边界不一致、god file 当权威边界。§3 定案。
- **W1(K0 CAS 账本)先于一切 solver/event 推广** —— 两边同判 BLOCKER;R0/W1 的排序得到独立确认。
- **删单一致**:q_lcb 运行时 admission、selection calibrator、market-anchor veto、Kelly haircut 栈、独立 exit lane、maker_rest_escalation(带同一 TRAP:先立新 stale-order owner)。

### Consult 独有、采纳(本地测绘没到位的)
1. **决策证书是记账原子**(每个 command/no-op/cancel/exit/结算 grade 都指回授权它的冻结证书;校准只消费 settled+walk-forward-eligible 证书)—— 比本地"冻结决策证书"表述更强:no-op 也要证书。并入 §1。
2. **maker 定价在公理下 under-specified**:禁历史 fill-intensity ⇒ maker 报价不可能"最优",只能**保守化**(worst-case fill timing 到下一 freshness 边界;否则 taker/no-op 支配)。这是真公理代价,记为设计事实,并入 R7 前提。
3. **fail-closed 要分入场与出场**:DB/q 真相不可用 → 新入场 fail closed,但 cancel/减险 exit 在 venue 真相充分时必须继续跑(authority doc 已有此法,重构不得丢)。并入 §5 护栏。
4. **market-coherence 反转为诊断/优先级路由,永不 veto**(q/book 分歧 = 提高 solve 优先级的信号 + 校准证据,不是不交易的理由)。order-engine doc 留的"操作员决定"项,consult 给出明确立场;**采纳**——与 no-shadow/直接 live 的操作员哲学一致。
5. **验证语言:certificate-native replay**(重放从冻结证书字节复原历史决策)+ 账本对抗并发测试 + 事件乱序 fuzz —— R2 的 replay 证据标准就用这套。

### Consult 的公理挑战,逐条裁决
- "no-shadow 让验证变难" → **部分接受但结论不变**:替代物 = event replay + byte-identical OFF 门 + time-boxed promotion flag(order-engine 已用)。不引入任何 shadow 决策系统。
- "no-history-caps 不应禁运营延迟量" → **采纳**:cancel p99 / submit p99 / WS blind window / 模型到达分布是**结构测量非 alpha caps**,公理窄读。写进 R0 instrumentation 范围。
- "q-only 可能 alpha-negative(book 先于本地 ingest 反映天气信息)" → **拒收改 q**:q 不动;分歧作诊断+优先级(见上第 4 条),这正是折中。

### 拒收/降级(带理由)
- consult 的质量估算表(55-85K/60-80K …)—— 方向与本地一致但基于过时 main + 无 import graph;**以本地 8 路测绘的 file:line 数为准**。
- "src/main.py keep launchd entry + service registry" 与本地 R4 相同,无增量。
- 其 W0-W5 与本地 R0-R8 是同一 DAG 的粗细两版;**保留 R0-R8**(覆盖 consult 未见的 ingest/state/venue/治理面),把 consult 的 W1-before-W3/W4 硬依赖写成 R 依赖:R7(solve 上线)依赖 R0c(CAS)+ 证书 replay 门。

### 终裁决
GO:R0(止血三件)+ R1(概率单链)现在开工 —— 与 consult 的"GO W0+W1"一致且更宽(R1 是已批 q-kernel 重建的收尾)。NO-GO:R7 solve 推广,直到 CAS 绿 + 证书 replay 门立起。整体节奏由 24/7 loop 的 PREPARE 通道供包、操作员逐包 merge。
