# 全天候持续改进 Loop — v3(codex 执行体,2026-07-09)

**Status: DESIGN + 机械已落地(本文即设计原稿;执行件在 loop/ + scripts/ops/loop_guard.py,同一提交)。**
**Lineage:** v2 = `allday_improvement_loop_design_2026-07-06.md`(方法论/权限三级/账本法全部继承,不重述);v3 只改**执行体与执行面**。操作员直令(2026-07-09):loop 由 codex CLI 承担;tick 间隔 1–6h 操作员可调、不 hardcode;设计原稿与执行入库为系统一部分;并思考 loop 如何自我进化达到资本利得收敛(§6)。

## 0. v2 → v3 砍单(每行都有实测依据)

| v2 | v3 | 为什么 |
|---|---|---|
| 引擎 `claude -p`(同 UID 裸跑,guard 自认 tripwire) | **`codex exec --sandbox workspace-write`(Seatbelt OS 沙箱)** | v2 KNOWN LIMITATION 原文:"真边界是 OS sandboxing……本包不做"。codex 原生自带:实测 src/、architecture/、仓外路径、`.git`、网络全部 OS 级 DENY。**guard 从 tripwire 升级为第二层;第一层是内核。** |
| L1 hourly + L2 daily 两层两 prompt 两 plist | **单 tick 一 prompt 一 plist** | 操作员:"具体内容不需要有区别"。L2 的结算 join 本就是证据驱动——折进每个 tick 的"有新 settled 行才跑"一步,结算后 ≤1 个 interval 内即被 grade,比固定 UTC 12:10 更快,还消灭 daily plist 的 DST 漂移问题(v2 checklist #2 整项作废)。 |
| launchd 小时固定 = 节奏 hardcode | **`loop/INTERVAL` 文件(小时数,操作员随手改,不动 plist)** | launchd 仍每小时 :17 触发(最细粒度),wrapper `interval-check` 用 INTERVAL 闸门决定本次跑不跑。1–6h 是用途说明,代码无上限 hardcode。 |
| tick 直查 SQLite(`mode=ro` URI 纪律) | **查询 escrow(§4):tick 只写 SQL 探针,受信 wrapper 下个 tick 前代跑** | 实测:WAL 库在沙箱内 `mode=ro` 都打不开(-shm 需写)。escrow 同时把账本的预登记法**机械化**——探针文件先写先 commit,数据下个 tick 才可见,p-hacking 在结构上不可能同 tick 发生。 |
| tick 自己 commit(自 merge AUTO 层) | **tick 零 git 写;wrapper `commit-auto` 在 enforce 之后按 allowlist 逐路径 commit** | `.git` 沙箱 DENY(实测)。BASE_SHA 不可变从"git 属性 + 假设"变成 OS 保证;co-tenant 文件永不被扫入(显式 pathspec,repo 法)。 |
| AUTO allowlist 含 `architecture/*.yaml` | **删。AUTO = `loop/** docs/** tests/**` 三项** | 一石三鸟:①v2 checklist #5 DESIGN-RISK(无人值守改写 invariants)按收窄方向落案;②沙箱本就 DENY architecture 写,allowlist 与沙箱一源;③`architecture/` 是 runtime_code_plane 前缀——loop commit 若含它会触发 deployment-freshness 误 pause live 交易。现 allowlist 恰 = 非 runtime 面,**loop commit 数学上不可能 pause 交易**(src/control/runtime_code_plane.py:13 前缀表)。registry 修复降 PREPARE。 |
| in-tick 独立 verifier 子代理 | **tick 内 tests-green 自验;PREPARE 补丁在 apply 时(主 session/操作员)对抗审** | codex exec 单代理(--ignore-user-config 剥掉多代理栈)。对抗审在 apply 边界做一次,比 in-tick 便宜且上下文更全。 |

不变量(v2 原文有效,不重述):目标序(结算后真实资本利得 > 系统健康 > 代码库清晰);证据法(决策证书×真实结算 join 唯一 ground truth,六分类,min_n=30,预登记,exploratory 不同日升 confirmed);三级权限 AUTO/PREPARE/NEVER;HALT 唯一开关;账本 refuted 永不删;deploy 永远操作员(`scripts/deploy_live.py restart all` → preflight GREEN → resume_entries);绝不动 kelly/风险姿态/kill-switch;绝不 stash;绝不碰起跑时已 dirty 的文件。

## 1. 形态:单 tick

```
launchd com.zeus.loop-tick(每小时 :17,最细触发粒度)
  → loop/tick.sh(受信 wrapper,非沙箱)
      1  HALT 存在 → 退
      2  interval-check:now − .last_tick_epoch < INTERVAL×3600 → 静默退(不 stamp)
      3  控制态入本进程 shell 变量:BASE_SHA / DIRTY_AT_START / DB_SENTINEL_PRE / INTERVAL_PRE
      4  run-queries:执行 loop/queries/pending/*.sql(只读 escrow,§4)→ loop/queries/results/*.json
      5  codex exec(OS 沙箱,§2)跑 loop/prompts/l1.md,wall-clock timeout
      6  enforce:BASE_SHA 不可变 git object 里的 allowlist 对照本 tick 新增改动;越界硬还原 + VIOLATION;>20 文件 / >600 行熔断 ESCALATION
      7  db-sentinel-check:state/**.db* mtime/size 差 → 自 HALT
      8  INTERVAL 防篡改:内容变 → 还原 + VIOLATION(节奏是操作员旋钮,tick 无权自调)
      9  commit-auto:allowlist 内新改动逐路径 add + 单 commit(loop(l1): tick …)
      10 codex 非零退出 → FALLBACK journal 行
```

模型:`gpt-5.5` + reasoning effort high(env 可覆盖)。预算 = wall-clock timeout(默认 45min)——codex exec 无 max-turns,时间墙即预算,与 1–6h 节奏兼容。

## 2. 沙箱事实(2026-07-09 实测,设计的地基)

| 面 | 结果 |
|---|---|
| cwd(`-C loop/`)| 可写 |
| `writable_roots=[docs, tests]` | 可写 |
| `src/`、`architecture/`、`scripts/`、仓外(~/.ssh 等) | **Operation not permitted** |
| `.git`(含 index.lock) | DENY → tick 无法 commit/branch/stash;读(log/show/diff/status 配 `GIT_OPTIONAL_LOCKS=0`)可 |
| 网络(curl example.com) | DENY(模型 API 调用在沙箱外的父进程,不受影响) |
| `$TMPDIR` | 可写(PREPARE 补丁的工作区) |
| live WAL DB `mode=ro` | **打不开**(SQLite 要写 -shm)→ §4 escrow 是唯一 DB 证据通道 |
| `--ignore-user-config` | 认证保留;全局 config 的 danger-full-access/approval=never/MCP/hooks 全部不泄入 |

调用形(tick.sh 为准):`codex exec --ignore-user-config --sandbox workspace-write -C <repo>/loop -c 'sandbox_workspace_write.writable_roots=[…docs,…tests]' -m gpt-5.5 -c model_reasoning_effort="high" -o <last_msg> "$(cat prompts/l1.md)"`。

纵深(为什么沙箱之外还留 guard):enforce/db-sentinel 防的是 wrapper 自己被误配(writable_roots 被改宽、沙箱旗标丢失)与"honest 越界"的审计留痕;commit-auto 防 co-tenant 扫入;二层各自独立可测(tests/test_loop_guard.py)。

## 3. 状态面(操作员视角)

| 文件 | 谁写 | git |
|---|---|---|
| `loop/HALT` | 操作员(touch=全停);db-sentinel 自 HALT | ignored |
| `loop/INTERVAL` | **仅操作员**:一个数字=小时(1–6 为用途区间,无代码上限);缺省 1 | ignored |
| `loop/.last_tick_epoch` | wrapper interval-check | ignored |
| `loop/JOURNAL.md` | tick 追加 + guard 机械行 | tracked(commit-auto) |
| `loop/LEDGER.yaml` | tick | tracked |
| `loop/queries/pending/*.sql` | tick(写探针=预登记;删=退役) | tracked |
| `loop/queries/results/*.json` | **仅 wrapper**(run-queries) | ignored(易变证据,每 tick 重生成) |
| `loop/prepare/*.patch` | tick(PREPARE 队列,§5) | tracked |
| `loop/prompts_proposed/` | tick(自进化提案,§6) | tracked |
| `loop/prompts/l1.md`、`tick.sh`、`allowlist_auto.txt` | **GUARD_IMMUTABLE**:tick 碰=无条件 VIOLATION | tracked |

## 4. 查询 escrow(v3 新机制,预登记法的机械化)

tick 永不直连 DB。它把**恰好一条 SELECT**写进 `loop/queries/pending/<name>.sql`;下个 tick 开跑前,受信 wrapper `loop_guard.py run-queries` 以只读方式执行(`:memory:` 主库 + `ATTACH …?mode=ro AS forecasts|world|trades` + `PRAGMA query_only=ON` + authorizer 禁 ATTACH/DETACH + interrupt 超时 + 行数帽),结果落 `results/<name>.json`(columns/rows/row_count/truncated/error;失败也是记录而非崩溃)。

- 别名固定:`forecasts`=zeus-forecasts.db、`world`=zeus-world.db、`trades`=zeus_trades.db(下划线者为正典;zeus-trades.db 是 0 字节 decoy)。
- **standing probe**(常驻定向探针,如"近 5 天 settled outcomes")每 tick 重跑重写结果;one-shot 探针由 tick 删除 .sql 退役。
- 时间结构即预登记:探针本 tick 写 + commit-auto 入 git,数据下 tick 才回——同 tick 内"先看数再写 claim"在结构上不可能。账本法的 prereg_query 字段直接引用 pending 文件名。
- 结算 grading 走同通道:standing settlement-join 探针喂每个 tick 的六分类归因;cursor 记 JOURNAL(承 v2 L2 语义)。

## 5. 权限三级(v3 执行面)

| 级 | 范围 | 执行 |
|---|---|---|
| AUTO | `loop/**`、`docs/**`、`tests/**` | 沙箱可写 + enforce 放行 + commit-auto 入库(单 commit) |
| PREPARE | `src/**` 等一切沙箱只读面 | tick 在 $TMPDIR 改副本 → `diff -u` 出补丁 → `loop/prepare/<item>.patch` + 红测试(tests/ 可写)排队;apply 时主 session/操作员对抗审后手工落。**tick 永不 merge src。** |
| NEVER | config/、live DB 写、LaunchAgents、deploy、kelly/风险姿态/kill-switch | 提案文字 only(journal/ledger)。沙箱+escrow 使多数 NEVER 面在机械上也不可达。 |

## 6. 自我进化 → 资本利得收敛(操作员命题,本节是答案)

**计分函数唯一:** 结算后真实资本利得(settled realized PnL,walk-forward,六分类里只有 SKILL_WIN 是正证据)。LEDGER 增设 `scoreboard` 节:按窗口记 settled PnL / win-rate-vs-price / 损失模式分布,数据只准来自 escrow 结算 join。

**外环(改进系统):** 每 tick = 证据 → 找当前**约束资本利得的绑定项**(不是找活干)→ 最小动作(AUTO 落地或 PREPARE 排队)→ 操作员 merge+deploy → 下一批结算给该改动打分 → 账本升降级 → 队列重排。收敛的引擎是这个闭环的**通量**:loop 能控的是"证据闭合率"(每个 claim 从 open 到 supported/refuted 的周转),不能控的是 deploy 节奏(操作员)与市场供给。

**内环(loop 改进自己)——三个自由度,全部带棘轮:**
1. **Prompt 进化:** prompts/ 是 GUARD_IMMUTABLE,tick 不能自改;它把修订提案写进 `loop/prompts_proposed/l1.md.next` + journal 说明动机。操作员(或主 session)review 后落地。**keep-or-revert 棘轮:** 落地后 3 个非空 tick 的(verifier-green 率 × 非空产出率)对照前 3 个,变差即 revert(v2 §6 原则,现有 journal 数据可机械对照)。
2. **证据面进化:** standing probe 库(pending/*.sql)由 tick 自己策展——增探针=开新观察窗,删探针=退役失效窗。探针集合就是 loop 的"感官",它随账本里 supported/refuted 的分布自动重心转移(全部 refuted 的方向探针该删,反复出 evidence 的方向该加密)。
3. **队列进化:** LEDGER next_action 每 tick 按新证据重排;fail_count=3 → blocked 升级操作员。同一约束连续 N 窗无改善 → 强制换 hypothesis 族(写入 prompt 的 ACTION 规则)。

**棘轮(保证进化单调,不退化):** refuted 永不删(防重提);antibody 测试进 tests/(每个修掉的损失模式留一条会咬人的测试);enforce/沙箱保证越界动作物理不可达;INTERVAL/prompts 操作员独裁,内环提案制。

**收敛判据(诚实版):** min_n=30 法之下,收敛不是某 tick 宣布的,而是 scoreboard 连续窗口呈现:①损失模式类逐窗萎缩(churn 类事故→0 并有 antibody 锁死);②SKILL_WIN 占比升;③单位风险 settled PnL 斜率为正。n 不够时"insufficient evidence"是合法结论——**过程收敛(每 tick 要么加 graded 证据、要么退役一个死 claim、要么落一个验证过的改进,永不产未打分的 churn)是 loop 可以自证的;结果收敛由 scoreboard 说话。**

## 7. 启停(操作员)

```
scripts/ops/loop_enable.sh          # preflight + 打印 bootstrap 命令(自己不跑)
echo 2 > loop/INTERVAL              # 节奏 2h(1–6 随需;缺省 1)
touch loop/HALT                     # 全停;rm 恢复
scripts/ops/loop_status.sh          # 活性一屏
```

首启纪律(承 v2 §5b):先 HALT 在场手动跑一次 `loop/tick.sh` 观察 JOURNAL/VIOLATION/超时行为,确认后 rm HALT 上线。

## 8. 谱系

v1(五文件五级+shadow,否决)→ v2(2026-07-06:三文件三级、wrapper 机制、consult 裁决、claude 引擎;guard 三轮硬化史见 loop_guard.py/tests)→ **v3(本文:codex Seatbelt 内核、单 tick、INTERVAL 旋钮、查询 escrow、commit-auto、自进化棘轮)**。v2 文档保留为方法论权威;冲突处以本文为准。执行件:`loop/tick.sh`、`loop/prompts/l1.md`、`scripts/ops/loop_guard.py`(interval-check / run-queries / commit-auto 三个 v3 子命令)、`deploy/launchd/com.zeus.loop-tick.plist`。
