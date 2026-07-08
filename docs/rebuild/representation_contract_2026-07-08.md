# Zeus 表示层契约(Representation Contract, 2026-07-08)

**Status: PROPOSED — 三路合成(7 路本地实测 wf_208347f7 958K tokens + consult REQ-20260708-013450 0.80 + 主线程独立立场,双盲后对撞);待操作员裁。**
**定位:** 这是 EXECUTION_MASTER R0-R8 每个 wave 携带的**第四维度**(前三维:功能收敛、删旧、registry 同步)。它管一切非可执行逻辑:注释、docstring、命名、元数据表面、AGENTS 树、锚点 ID。
**读者公理(操作员已裁):本仓库的第一读者是 LLM agent。** 词汇表 = agent 的本体论(维特根斯坦:语言的边界即世界的边界);每个跨层工件必须声明自己的诠释 schema(尼采:没有事实只有诠释 → float 不是概率,经 schema+provenance+freshness+authority-plane 诠释后才成为 q)。

---

## 0. 实测底账(2026-07-08,证据在 workflow journal)

| 量 | 实测 | 含义 |
|---|---|---|
| boot 表面 | **52 文件 274KB ≈ 68.6K tokens**(43 个 AGENTS.md!) | vs 5K 预算 = **13.7x 超支** |
| 元数据表面 | 184 个 / 3.2MB:**HAND 99 个 1.3MB(40%)**、CHECKED 76 个(49%)、GENERATED 9 个(11%) | 99 个无人保真的表面 = 99 个待烂的谎 |
| 注释 WRONG(对抗复核确认) | **4 条实锤**,全在决策面,全是**权威宣称**形("ONLY decision authority"/"NOTHING wires this shim yet"/"unconditional single selection path"/"unwired dead code" 实被 main.py 活调) | 最致命腐烂类 = 排他性/接线状态宣称,W3 seam 落地当天即变谎 |
| 注释 STALE_NARRATIVE | 29 条(dated 事故复盘/directive 引用/tx-hash/"Milan incident" 速记) | 历史该归 git;event_reactor_adapter.py 一文 2788 注释行中 403 行 dated 叙事 |
| 命名 | public fn p50=20 字符 p90=35 max=67;**最长 25 名调用点仅 1-6 处**(长度未被频度摊销);**196 个 module-echo 函数**;6 个同义词簇坐实(family/market/opportunity 175/187/17 defs;settle/resolve/grade 142/86/21;posterior/belief/q;certificate/receipt;edge/ev;is_fresh/is_stale 两处不相干定义) | 本体论 bug 实测在案 |
| 锚点 ID | 9 族只有 2 族有注册表(INV- 1359 refs/1 dead/6 unpadded 碰撞;**FC- 全仓最干净 0 悬空 0 死**);**7 族无注册**:K1 四义碰撞、R3 三义碰撞(EXECUTION_MASTER 的 R0-R8 与 topology.yaml 旧 R3-tag、workspace_map 的 R3 模块标签撞名)、W0-W5 仅散文、F-numbers 起源文档已删、BLOCKER-N 逐文档重编号、C1/C2/C3 四套、gate-135 全仓孤引 | 无注册的 ID = 反锚点(制造歧义而非消除) |
| doc 死链 | 7711 引用中 **1066 死(13.8%)**,聚簇在 docs/evidence/** 与 stash_extracts | 修法 = 出 boot 归档,非逐条修 |

### 实测推翻的先验(诚实记录,校准优先级)
1. **RESTATE 不是本仓的病**(决策面 0 命中;仅 portfolio.py 的 dict 分组标签)——注释法重点杀叙事+权威宣称,不是查 `# increment i`。
2. **TODO 腐烂几乎不存在**(top-25 执行文件 0 个 tagged TODO)——不需要 TODO 官僚。
3. **LOAD_BEARING 注释按体积是多数且质量高**(外部 API 怪癖/线程局部性/刻意排除)——**禁止批量删注释**;法必须按类外科手术。
4. doc 死链聚簇在证据废料 —— boot 相关表面较干净。

---

## 1. 四法

### 1.1 注释法(COMMENT LAW)
注释/docstring 存在的唯一许可:**陈述代码+类型+git 无法推导的事实**,白名单六类:
1. **world-fact**:单位、venue/WMO/HKO/DST/外部 API 语义、结算 preimage、市场惯例;
2. **invariant 引用**:稳定 ID(INV-xx/FC-xx),只写本地绑定,不复述 invariant 本身;
3. **why-NOT**:显然写法为何错,一个从句,不带故事;
4. **proof/顺序法**:真相源排序、commit-before-export、幂等边界、崩溃恢复顺序;
5. **并发法**:锁序、线程局部性、single-writer 宣称(暂,见 1.4——终态迁入 checked 表面);
6. **safety/rollback**:部分失败/重启/操作员中止时必须保持为真的东西。

**禁类(lint 可查):** 日期+事故叙事(`2026-\d\d-\d\d` 邻接 fix/incident/directive/hotfix/root cause)、commit SHA、tx-hash、PR 号、lifecycle 头(created/last_reviewed/last_reused/audited)、"authority basis" 指向 archive 计划、未来时("P2/K4 will add")、TODO/FIXME 无锚点、dict/dataclass 分组标签(RESTATE)。
**最高禁类 —— 权威/接线宣称:**"ONLY/sole/single path/nothing wires/unwired/dead code" 类排他性断言**禁止以注释形式存在**——4/4 实锤 WRONG 全是此形。排他性是系统事实,必须活在 checked 表面(invariants.yaml 带 enforced_by,或测试断言),不许活在会陈腐的散文里。
**docstring 形:** public 导出符号 = 一行契约(承诺什么,不是逐行做什么)+ 类型签名承载其余;private helper 默认无 docstring;多行散文仅限并发/安全/外部世界语义无法用类型/schema/ID 表达时。数学算法可保留不变量说明(算法的不变量,不是它的历史)。
**Consult 的 `# repr: kind=... ids=[...]` 每条标签头 —— 裁决:拒收。** 理由:标签使每条幸存注释 token 翻倍,违反 max-info-per-token,且是 consult 自己"cut over-engineering"节谴责的官僚形。替代:涉 ID 的注释必须含 ID 字面量(lint 双向验证,见 1.4),其余幸存注释只受禁类 lint。机器可判性来自禁类模式+ID 存在性,不来自标签头。

### 1.2 命名法(NAMING LAW)
优化目标不是"短"——是**保留 canonical 域概念与 authority plane 的最短名**。实测:最长 25 名调用点仅 1-6 处,长度纯浪费;但砍到丢失域锚定(把 settlement/grade/observation/venue-echo 塌成一个词)比长名更贵,因为 agent 会推错本体论。
1. **一概念一名。** canonical vocabulary 进 checked YAML(`architecture/canonical_vocabulary.yaml`,schema 见 consult 答案,按需增概念):q(served 概率,决策/执行用)/belief(serving 前分布对象)/posterior(仅真贝叶斯后验)/q_lcb/q_ucb;certificate(solver/repair 证明)vs decision_receipt(决策不可变证明)vs order_receipt(venue 证明);settle(过程动词)vs settlement_grade(终值);family_key(治理键);is_fresh 谓词单一定义。禁用别名表 + migration allowlist,lint 查新代码。
2. **禁 module-echo**(196 实测):module 即命名空间;`replacement_forecast_materializer.py` 里定义 `materialize()`,调用侧 `replacement_forecast.materialize(...)`。
3. **禁世代前缀**(replacement_/edli_/v2_):单世代法(R-wave 删旧)下,无修饰名就是现行世代。R7 决策面清代 = 本条的批量执行点。
4. **禁事故形名**:`_replacement_cycle_availability_poll_if_needed` 的名字就是五关切缝合的供词;R3 拆解时按关切重命名(`_poll_cycle_availability_if_due` —— "due" 锚定调度谓词,"needed" 藏谓词)。
5. **长度 ∝ 作用域距离**:local 1-2 token、module 内 2-3、跨模块 API 全限定域词汇。
6. **包本体论 = authority plane 归一**(与 R7 合流):forecast(构建快照)/probability(概率变换+校准+界)/decision(准入+receipt)/solve(优化+证书)/risk(硬约束)/execution(副作用+venue 事实+对账)/engine(仅编排,零域权威)/strategy(仅配置 profile)。七个像权威的包名 = agent 世界里七个权威 —— R7 清代即本体论修复。

### 1.3 元数据法(METADATA LAW)
**硬规则:每个表面必须 `writer != none AND drift_detector != none`,否则删。** 三许可类:
- **hand_kernel**(手写不可约内核):操作员公理、域法、why-NOT 判词。token 预算受查(root AGENTS ≤2.5K tokens,scoped ≤500)。
- **checked_policy_input**(手写但机器查):invariants.yaml(每行必有可执行 enforced_by —— R8 既判)、negative_constraints、canonical_vocabulary、本契约。无 checker 的行无效。
- **generated**(生成式):module 索引、DB 读写权属(R5 从 domains.py 生成 —— 既判)、废弃索引、boot card。writer = 生成脚本,checker = 确定性重生成 + diff。
**Consult 的新 `build/repr/zeus_repr.sqlite` 并行元数据库 —— 裁决:拒收。** 理由:与 codegraph(结构查询)+ 现有 doctor(治理查验)职能重叠,正是蓝图五缺陷之五"治理面比被治理面长得快"的第四代形。替代:词汇/注释/锚点检查作为 `topology_doctor` 新 domain(R8 本就按域拆 doctor),生成物只有 boot card 一个新工件。
**AGENTS.md 树(43 个,68.6K tokens boot 超支的主体):** root = 公理+钱路一段+词汇指针+路由协议(≤2.5K);scoped 仅当含本地域法(src/solve/AGENTS.md 的 Domain rules+Common mistakes = 目标形;其 Key files 表删 —— codegraph 代劳);纯 file-table 的 AGENTS.md 整删。**boot card 生成式**(§3)。

### 1.4 锚点法(ANCHOR LAW)
稳定 ID 是语法→语义之桥,但**无注册的 ID 是反锚点**(实测 7/9 族无注册,K1 四义、R3 三义)。
1. **一注册表一族**:INV-(已有,修 INV-41 死项+unpadded 碰撞)、FC-(已有,最干净——它是全仓模板)。
2. **wave ID 入册**:R0-R8/W0-W5 在 EXECUTION_MASTER 内定义即注册(§H 加一行锚点声明);旧 R3-tag(topology.yaml)随 R8 topology 退役死;workspace_map 的 R3 模块标签改名。
3. **无主 ID 族清算**:BLOCKER-N(逐文档重编号)→ 文档内局部编号必须带文档前缀或不用;F-numbers(起源文档已删)→ 幸存引用改指 invariants 或删;C1/C2/C3 → 保留 EXECUTION_MASTER 的 AUTO/PREPARE/NEVER 语义,其余三套随载体文件死;gate-135 → 删(全仓孤引)。
4. **双向 lint**(doctor 新 domain):代码/文档里的 ID 必须存在于注册表;注册表每行必须有 ≥1 代码/测试绑定点。unpadded 形(INV-1 vs INV-01)按错误处理。
5. **事件/证书 schema = 真接口文档**(consult 采纳):跨模块 intent-flow 靠十个 typed 事件名(ForecastSnapshot→ServedBelief→CandidateSet→DecisionReceipt→SolveCertificate→ExecutionIntent→VenueCommand→OrderFact→PositionEvent→SettlementGrade),不靠 module summary 散文。ADR 不立新实体:git + 蓝图 + 冻结证书已覆盖"why"。

---

## 2. Boot card(理想前 5K tokens,生成式)

生成物 `build/repr/boot_card.md`(writer=生成脚本,checker=token 计数+来源 diff;进 .gitignore,session hook 注入):
- 0-600:公理内核(钱路一句、authority 序、no-stale-as-fresh、no-shadow、本契约一行);
- 600-1300:查询路由(结构→codegraph;契约→schema;法→invariants ID;现状→runtime DB/git;**禁从 docs 推现状**);
- 1300-2100:canonical 本体(词汇表正名+禁用别名,从 canonical_vocabulary.yaml 生成);
- 2100-3100:invariant 索引(仅 ID+一句话+查询柄,从 invariants.yaml 生成);
- 3100-4100:intent-flow 骨架(十事件链 = 钱路);
- 4100-4700:查询命令列表;
- 4700-5000:stop rules(缺 authority/缺 freshness/词汇碰撞/未注册副作用 → 停)。
现有 52 个 boot 表面中,root AGENTS 缩内核、scoped 留域法、其余出 boot(archive 或删)。**68.6K → ≤5K。**

## 3. 逐 surface 判决(合成 consult 表 + 实测 HAND 名单;与 R8 既判兼容并细化)

| Surface | 判决 |
|---|---|
| root AGENTS.md(276L) | KEEP-SHRINK → 手写内核 ≤2.5K tokens;路由/file 表 → boot card |
| 43 个 nested AGENTS.md | 含域法者 KEEP-SHRINK(≤500 tokens,solve 形);纯 file-table 者 DELETE |
| architecture/invariants.yaml | KEEP + schema v2(每行 enforced_by 可执行;剥叙事注释块——INV-02 的 CITATION_REPAIR 段即注册表自身的疤);INV-41 死项裁决 |
| negative_constraints / fatal_misreads | KEEP,每行绑 checker/test,无绑定行删 |
| canonical_vocabulary.yaml | **NEW**(唯一新增手写表面,checked) |
| module_manifest.yaml | DELETE/GENERATE(实测为空壳注册头) |
| topology.yaml 5936L | GENERATE/退役 → import-linter(R8 既判);旧 R3-tag 随死 |
| source_rationale.yaml | 只留绑 ID 的 hazard badge;per-file 散文删 |
| script_manifest.yaml | 生成式清单 + 手写 hazard override |
| history_lore 110K | 出 boot 归档(R8 既判:剖析进 docs、幸存不变量进测试) |
| docs/evidence/**、stash_extracts | 出 boot 归档(1066 死链主巢;不逐条修) |
| docs/operations/current/plans+reports 的已完成 HAND 大件 | 波次收尾即收编为 receipt 或归档(实测 top-15 HAND 里 9 个是已落地工作的叙事) |
| 代码内注释 | 按 §1.1 六类白名单;权威宣称迁 checked 表面 |

## 4. 执行与 lint(不立第四代治理)

- `topology_doctor --repr`(新 domain,R8 doctor 拆分时归位):禁类注释模式、module-echo(新代码)、禁用别名(新代码)、ID 双向存在性、AGENTS token 预算、新元数据表面必须声明 writer+checker。
- **changed-files blocking / legacy advisory**:法只咬新增与被触碰文件;不批量退修 R-wave 将删的尸体(唯幸存者值得整形)。
- boot card 生成脚本 = 唯一新 writer;确定性、不读 live 交易态、commit-before-export 法保持。

## 5. 载入 R0-R8(骑行,不加新 wave)

- **R0-h〔AUTO〕本契约落地包**:①修 4 条实锤 WRONG 注释(src/solve/solver.py:873、src/engine/event_reactor_adapter.py:41+:18959、src/decision/family_decision_engine.py:15、src/state/portfolio.py:1741)——注释-only,bytecode-identity 证据(编译前后 .pyc 同)+ 测试 diff 0 新增;②`canonical_vocabulary.yaml` 初版(6 实测簇);③doctor --repr 骨架(禁类+别名,advisory);④EXECUTION_MASTER §H 加 R0-R8/W0-W5 锚点注册行。
- **R1-R2**:被重写子系统的注释/docstring 随写随迁(事故头删、权威宣称迁 invariants);R2 的 31-pass 考古叙事正是最大叙事巢,谓词表化即注释法执行。
- **R3**:ingest 拆解时按关切重命名(五关切 tick 神函数、replacement_ 前缀族)。
- **R4**:main.py 8K job body 外迁时 module-echo 与 LONG 名单批改。
- **R5**:DB 读写权属从 domains.py 生成(既判)= 元数据法在 state 域的执行。
- **R7**:决策面清代 = 本体论修复主战役(七代→两包;q/belief/posterior 词汇归一;certificate/receipt 分立;世代前缀死)。
- **R8**:doctor --repr 转 blocking(quarantine 名单外全仓);boot card 上线;AGENTS 树缩到预算;锚点无主族清算;HAND 99 表面终态 = 生成/查验/删三分。

## 6. 杠杆排序(consult 序,实测修正)

1. **权威宣称注释清除 + changed-files 注释法**(最致命失败模式实锤 4 条全在活决策面;lint 最便宜);
2. **boot card + AGENTS 缩身**(68.6K→5K = 13.7x,每 session 都省);
3. **canonical vocabulary**(6 簇实测;R7 载体);
4. **事件/证书 schema 化 + DB 写权生成**(R5/R6 载体);
5. 生成式 module 索引(最后,codegraph 已覆盖大半)。

## 7. 对 consult 的三处拒收(已思考的理由,非搁置)

1. `# repr:` 每条标签头 → 拒收(token 翻倍违反自身目标;禁类模式+ID 存在性已给机器可判性)。
2. 新 SQLite 元数据库 → 拒收(第四代治理形;doctor 新 domain + codegraph 已覆盖)。
3. consult 未读到 EXECUTION_MASTER/蓝图(untracked,GitHub 404)—— 其 R0-R8 映射按本地权威版重排(它的 R5-R6 包本体论包序与本地 R7 决策面清代对齐)。

**证据索引:** 实测 journal `~/.claude/projects/-Users-leofitz-zeus/ce5821ff-a917-4ece-ab1c-008d235a2639/subagents/workflows/wf_208347f7-701/journal.jsonl`;consult 答案 `/tmp/cgc/answer_REQ-20260708-013450-6e2e05.txt`(裁决已全部并入本文);主线程冻结立场 scratchpad `repr_position_local.md`。

## 8. R0-h 落地结果(2026-07-08,`scripts/topology_doctor_repr_checks.py` + `scripts/gen_boot_card.py`)

| 项 | 结果 |
|---|---|
| Boot card 实测 token | **3375**(4 chars/token 估算,与 `check_agents_token_budgets` 同口径)vs 68.6K 底账 = **20.3x** 压缩,达标 §2 的 ≤5K |
| 注释法 — 禁类(changed-files scope) | banned_lifecycle_header / dated_incident_narrative / authority_claim 三类模式已实现;全仓自扫 1 条(本文件对自身正则说明文本的自指命中,advisory 噪音,非违法) |
| 注释法 — stale 引用(新) | 文件路径引用:不存在路径判 stale;符号引用:反查全仓 def/class 索引(2604 文件 <1s)判 stale |
| 命名法 | forbidden_aliases 反查(排除"別名恰是他概念 canonical"歧义词);canonical_vocabulary.yaml 沿用既有 6 簇 15 词,未新增(时间预算内未测出新反复出现词) |
| 元数据法 | 范围收窄为契约 §1.3 明列的 3 个 checked_policy_input 登记表(invariants.yaml/negative_constraints.yaml/fatal_misreads.yaml)逐行 enforced_by/tests 完整性;实测 2 条命中(`INV-Harvester-Liveness` 无 enforced_by;`negative_constraints`/`fatal_misreads` 现存行均有绑定) |
| 锚点法 | INV-/FC- 双向 lint(9 族中仅 2 族有注册表,契约 §1.4.3 明言其余 7 族登记是 R8/operator 事项,本包不造);实测:unpadded 形 37 处(INV-1..INV-9 style)、未注册引用若干、`INV-41` 零绑定(与 §0 底账"INV-41 死项裁决"吻合)、`INV-Harvester-Liveness` 1 处精确匹配绑定(非数字后缀走 exact-grep 分支,避免数字位掩码假阳性) |
| 未能机器可判的部分 | 无 — 4 法在本包范围内(comment/naming/metadata/anchor)均已产出 file:line 级 finding;元数据法未覆盖的是 184-surface 全量 HAND/CHECKED/GENERATED 普查(该分类数据来自独立 workflow journal 实测,不在本 worktree 内,故仅对 3 个契约明列表面逐行核查,未做全仓 184 表面分类) |
| Regression | `tests/test_topology_doctor.py::test_repr_audit_is_always_advisory_and_flags_banned_patterns` 因 `_registered_fc_ids` 未做存在性检查而失败一次,已修复(见 commit);17 个新测试(`tests/test_topology_doctor_repr_checks.py`)全过 |
