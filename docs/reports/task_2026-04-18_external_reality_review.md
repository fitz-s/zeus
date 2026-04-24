A. Task model

这台机器真正的主目标函数不是 raw EV，也不是“把 posterior 做得更漂亮”。它的主目标函数应当是分层的：先保证状态语义正确，再保证在未知真相下仍可存活，最后才是资本增长。原因很简单：Polymarket 的订单与成交本身就是异步生命周期，支持 GTC/GTD/FOK/FAK、部分成交、MATCHED→MINED→CONFIRMED/FAILED 等状态；天气市场又按具体机场站点、整数摄氏度、数据最终定稿后的制度规则结算。对这种系统，概率正确不等于系统正确。

“亏钱但还能活”的错误，是校准偏差、edge 估大、仓位保守过头、错过一些 fills。这些会伤收益，但系统仍知道自己下了什么单、持有什么仓、在哪些地方不知道真相。
“直接杀死系统”的错误，是系统在并不知道真相时仍自信地声称知道：不知道订单是否已被交易所接受、不知道部分成交到哪里、不知道空响应是 API 缺失还是真空仓、不知道结算边界是否已跨越，却继续开仓、继续复用统计预算、继续输出“VERIFIED”。这类错误杀死的不是单笔 PnL，而是整台机器的控制权。

对 data-improve 分支的裁决是：它已经修掉了最表层的两个指控——DB commit 先于 JSON export，FDR family 也不再沿用旧的 strategy-key 混淆写法；但它仍在把注意力放在“信号/筛选/执行意图命名”这一层，而真正未完成的是execution-state truth、restart recovery、authoritative de-risk。所以它不是“完全在优化错的问题”，而是在一个真实问题上优化了错误层级。

B. External reality model

不看 repo，只看现实世界，这类天气事件合约系统最容易死在五类 failure class：

状态真相失败：订单是否已提交、是否部分成交、持仓是否仍在、链上/交易所/API 是否只是暂时不完整。
执行语义失败：top-of-book 不是你的可成交价格分布；部分成交、撤单、队列优先级、maker/taker 路径、费用曲线会重写你的真实 entry/exit。官方文档明确 weather 市场 taker fee 为 match-time 计算，费用随 p(1-p) 变化，且只有 taker 付费。
结算语义失败：天气市场不是连续温度回归游戏，而是特定站点 + 整数温度 bin + 最终定稿数据的制度游戏。边界日、站点映射、定稿时点、修订是否计入，都会把“统计上差不多”变成“结算上全错”。
运营/控制面失败：重启、downtime、venue 切换、未知态积压、人工确认负担。官方 changelog 已写明 2026-04-22 CLOB V2 切换、约 1 小时停机、旧集成无向后兼容、open orders 在切换时会被清空。
统计/选择压力失败：小样本、重复看盘、同一家族反复测试、regime shift，会让“每次局部都合理”的系统在时间轴上慢性漏血。

这类系统最常被误判成“模型问题”的，其实是：
一，execution semantics；二，position/order state truth；三，settlement interpretation；四，control-plane authority。量化团队最容易花很多时间做更复杂的 posterior、更多 bootstrap、更多 family-wise 修补，但真正让机器死的，往往是它以为自己 flat，实际上还有挂单和仓位；或者它以为自己知道结算边界，实际上只知道连续变量，不知道制度边界。

对这种系统，真正的 live readiness 不是“回测漂亮”或“单测绿了”，而是四件事：
机器能在 crash 后确定性恢复；能把 UNKNOWN 当一等状态而不是临时 if 分支；能在 RED 时主动去风险而不只是停止犯新错；能把市场筛选做成制度与流动性约束，而不是只看 edge。

C. Authority order / truth surfaces

我采用的裁决 authority 顺序是：

外部 execution / settlement facts：交易所订单/成交状态、链上/venue 可验证事实、结算规则。
已提交的 canonical DB events / projections：Zeus 内部唯一应该持久记忆这些事实的地方。
runtime code reality：当前分支真实在跑什么。
schemas / invariants / contracts。
tests，但只在它们与 runtime reality 一致时。
docs / comments / refactor plans。

repo 内部理想的 truth surface 也应当是：DB authority > JSON/status projections > docs/tests。当前分支已经把 cycle 末尾写成 commit_then_export()，也就是 DB 先提交、JSON 后导出；这一项旧指控在最新代码里应当撤销。

authority 冲突当前有四处最严重：

已撤销的旧指控仍滞留在 tests 中：有测试文件还在写“canonical_write.py 不存在”“cycle_runner 仍是 JSON before DB”“family split helper 不存在，今天必须 fail”。这些说法已被当前 runtime 代码推翻。
portfolio 模块自述仍在撒谎：portfolio.py 顶部仍写“Positions are the source of truth”，但 load_portfolio() 现实上是 DB-first；当 DB 不 authoritative 时，它返回 degraded/unverified 视图，而不是把 JSON 当真相。
export truth 标签本身错了：_TRUTH_AUTHORITY_MAP 把 "degraded" 映射成 "VERIFIED"，这会让派生导出在最不该自信的时候自称 VERIFIED。
[Doc claim only] riskguard.py 的顶层注释声称自己是“独立监控进程、60 秒 tick”；我没有找到足够 repo 证据证明部署时一定有这种进程级隔离，因此我不把它当已成立的 authority boundary。

证据标签说明：[Repo confirmed] 直接来自代码/测试 reality；[Cross-file inference] 来自跨文件组合语义；[External market inference] 来自 repo 外部现实；[Doc claim only] 仅文档/注释声称；[Unresolved] 未证实。

D. Hidden branches
已修的表层 bug 会掩盖更深的 bug。因为 DB-before-JSON 已修，很多 review 会误以为 truth surface 已闭环；实际上更深的 orphan-order window 还在。
空链响应的 mixed-freshness 分支会把 stale phantom 一起保活。测试已经覆盖到：只要有 fresh-verified position 存在，empty-chain guard 会全局 skip voiding，连 stale 位置也会被一起保住。
chain rescue 会 fabricate entered_at。这能防止丢仓，但会污染后续持有时长、exit timing、风控回放等基于时间的分析。
base URL 不变会让 venue 迁移更危险。官方说 2026-04-22 切到 V2 后仍使用同一 CLOB base URL，这意味着集成不一定在“连不上”时爆炸，而可能在“还能连上但语义已变”时悄悄出错。
“能力命名”会制造错误安全感。iceberg、dynamic_peg、liquidity_guard 这些字段如果没有真实状态机支撑，会让后续工程师和 agent 高估系统 execution 能力。
E. Kill-shot findings
1. 没有持久化的预提交命令账本，孤儿单窗口仍然成立
核心：cycle_runtime 先 execute_intent()，拿到结果后才 materialize_position()、log_trade_entry()、写 canonical entry；executor 内部则直接 place_limit_order()；fill_tracker 还明确说 live entry 会先变成 pending_tracked，再等待 CLOB 确认。这意味着只要进程死在“venue 已收单”与“本地 authoritative record 落盘”之间，就会出现有交易所单、无本地命令账本的窗口。官方订单/成交文档又明确存在部分成交与多阶段状态，所以这不是一个同步 RPC 世界。[Repo confirmed] + [Cross-file inference] + [External market inference]。
为什么普通 review 难抓到：局部函数都“看起来合理”；只有把 cycle_runtime + executor + fill_tracker + 崩溃时序 拼起来，问题才出现。
实盘如何显化：重启后你不知道该 cancel、rescue、void 还是继续等；部分成交会让成本、shares、heat 和后续 exit 全部失真。
Blast radius：重复下单、假 flat、假 pending、错误净敞口、人工清算。
Rollback：立刻禁新开仓；只保留人工核验后的 exit；对 live 账户执行一次“cancel-all + venue/chain rebuild”。
Monitor：orders_without_commands、submitting_age_p95、pending_no_order_id_count、venue_order_unlinked_count。
债务：你越往上堆风控/统计/agent，越是在不可信地基上建楼。
2. 执行抽象是命名，不是真能力；Kelly 仍然吃的是 quote，不是可成交分布
核心：create_execution_intent() 会产出 iceberg、dynamic_peg、liquidity_guard 之类字段，但 execute_intent() 只是记录这些标签，然后用单次 place_limit_order() 发单；repo 客户端也只取 top-of-book 的 best_bid/ask 与首档 size。与此同时，evaluator 的 p_market 来自 best-bid/best-ask VWMP，Kelly boundary 从一个 entry_price 标量出发，再做 fee-adjusted cost。官方文档明说 Polymarket 有 GTC/GTD/FOK/FAK、部分成交、user websocket 和 match-time fee；weather taker fee 还按 p(1-p) 变化。Zeus 现在的 sizing law 仍然没有把成交路径建进来。[Repo confirmed] + [External market inference]。
为什么普通 review 难抓到：因为代码里已经充满 execution 术语，读起来像“有执行引擎”；但这些词很多只是 metadata，不是机制。
实盘如何显化：回测/纸上 sizing 看起来有 edge，真钱里却遭遇不成交、半成交、成交偏离、错费率路径、排队失败。
Blast radius：系统性低估成本，高估 edge，有时还会因为没成交而被误判成“策略保守成功”。
Rollback：把假能力删掉；短期只保留诚实的单笔 limit path，直到真有 slicing/repricing/order-state machine。
Monitor：fill_ratio_by_quote_distance、partial_fill_rate、avg_realized_fee_bps、book_age_ms、ws_gap_seconds。
债务：每新增一个“聪明执行标签”，都会进一步扩大命名与现实的裂缝。
3. RED 风控仍然只是 entry block，不是 authoritative de-risk
核心：RiskGuard 在 RED 时只把 force_exit_review=1，cycle_runner 则明确把 scope 写成 entry_block_only，注释也承认“forced exit sweep for active positions is a Phase 2 item”。这不是 risk control，只是“禁止继续犯新错”。[Repo confirmed]。
为什么普通 review 难抓到：因为 RED flag、warning log、summary 字段都在，看起来像已经有 kill-switch。
实盘如何显化：真正该减仓时，系统只是停止开新仓，把已有坏库存继续留在账上。
Blast radius：drawdown 扩大、仓位老化、控制面丧失威信。
Rollback：把 live 模式改成 exit-only；RED 直接生成 staged de-risk 命令。
Monitor：red_open_exposure_usd、red_positions_count、red_unwind_pending_count。
债务：后续任何“更聪明的风控指标”都会被这个 authority 缺口架空。
4. UNKNOWN 还不是一等状态机；空响应仍通过启发式折叠为“某种真相”
核心：classify_chain_state() 已经比旧版本进步，加入 6 小时 stale guard，把一部分 empty-chain 情况判成 CHAIN_UNKNOWN；reconcile_with_chain() 在 unknown 时会 skip voiding，防止 false phantom kills。但它仍然把 UNKNOWN 建成启发式分支，而不是与 unresolved venue commands 联动的一等状态机。更糟的是，测试已证实 mixed fresh/stale 时会全局 skip voiding；另一方面，一旦不走 unknown 分支，缺席位置又会被直接 PHANTOM_NOT_ON_CHAIN。[Repo confirmed] + [Cross-file inference]。
为什么普通 review 难抓到：每个分支单独看都“有道理”；只有在 API 空响应、freshness 混合、崩溃恢复同时出现时才炸。
实盘如何显化：假 phantom 保活、真 phantom 被误杀、时间字段被 fabricate，后续监控和退出全被污染。
Blast radius：错误持仓表、错误 heat、错误 exit 触发、人工排障爆炸。
Rollback：对 empty response 一律不自动得出“真空仓”；先进入 UNKNOWN/REVIEW_REQUIRED，阻断新 entry。
Monitor：chain_unknown_count、mixed_fresh_empty_response_count、fabricated_entered_at_count。
债务：系统会越来越依赖“看起来像真相”的启发式，而不是承认未知。
5. degraded truth 被导出成 VERIFIED，且 loader degraded 会直接 shutdown：这是“安全地失明”
核心：_TRUTH_AUTHORITY_MAP 把 "degraded" 映射成 "VERIFIED"；同一时间，cycle_runner 又会在 portfolio_loader_degraded 时直接抛出 “Failsafe subsystem shutdown”。前者会误导 operator truth，后者会让监控/退出链路直接停摆。这组合起来不是 fail-closed，而是safe but blind。[Repo confirmed]。
为什么普通 review 难抓到：一个是 metadata mapping，小得像 typo；一个是 failsafe，看起来像负责。
实盘如何显化：你会在 degraded 条件下看到“VERIFIED”导出，或在最需要监控时直接把循环停掉。
Blast radius：operator 被误导；active 仓位失去自动照看。
Rollback：立刻修正 authority map；将 loader degraded 改成“禁止新 entry + 保留只读监控/exit 能力”。
Monitor：degraded_export_count、loader_degraded_shutdowns、read_only_cycles。
债务：状态语义会从代码层污染到人脑层；这是最难逆转的债。
6. tests / docs / comments 已经落后于 runtime reality，未来 agent 很容易朝错误方向“修复”
核心：当前 repo 里仍有测试写着“今天必须 fail，因为 canonical_write.py / family helpers / save_portfolio 还不存在”，portfolio 顶部还写“positions are the source of truth”。这些不是小瑕疵，而是错误 authority source。未来 agent 或工程师如果信这些高层叙述，很可能把已经修好的东西修坏，把真正还没修的 deeper issue 遗漏。[Repo confirmed]。
为什么普通 review 难抓到：大家天然会把“测试/文档”当上位事实。
实盘如何显化：错误 patch、错误 refactor、错误 incident 归因。
Blast radius：整个开发节奏和未来审计都会被带偏。
Rollback：立即清理或重写 stale tests/comments；CI 禁止出现 “must fail today” 这类历史断言。
Monitor：stale_truth_claim_count、docs_code_drift_checks。
债务：authority drift 会持续生成二次事故，不是一次性 bug。
7. FDR 家族切分已修，但统计预算在时间轴上仍然没有真正闭环
核心：我要明确撤销旧指控：当前 selection_family.py 已经分成 make_hypothesis_family_id() 与 make_edge_family_id()，family 不再像旧说法那样被 strategy key 搞乱；但 scan_full_hypothesis_family() 仍会对每个 bin 的 yes/no 全扫一遍，entry price 仍直接取 p_market；family facts 又以 snapshot 为记录单位。repo 没有给出跨 cycle / 跨 snapshot 的持久 alpha-spending ledger，所以“每次局部 BH 都对”不等于“整天反复看盘后仍对”。[Repo confirmed] + [Cross-file inference]。
为什么普通 review 难抓到：单个 snapshot 内的统计语义现在看上去已经很像样。
实盘如何显化：同一市场/同一天被反复重试，直到某次“终于过线”；alpha 预算在时间轴上悄悄重置。
Blast radius：慢性过度交易、edge 幻觉、研究结论虚高。
Rollback：增加 persistent alpha ledger；在它落地前，对同 family/day 强制冷却或单次尝试上限。
Monitor：snapshot_count_per_family_day、trade_attempts_per_family_day、alpha_spend_remaining。
债务：你会越来越擅长在噪声里“发现机会”。
8. repo 外部现实正在撞穿 repo：离散结算语义 + 2026-04-22 CLOB V2 切换
核心：官方天气市场规则写得非常清楚：按具体机场站点、整数摄氏度、Wunderground 最终定稿数据结算，定稿后的后续修订不再计入；这意味着 near-boundary 的连续概率优化，最终会被离散制度切割。与此同时，官方 changelog 在 2026-04-17 宣布 2026-04-22 切到 CLOB V2、旧集成无向后兼容、open orders 会被 wipe。repo 的 polymarket_client.py 仍是当前包装层，使用现有 constructor/下单形态；[Unresolved] 安装环境是否已暗中兼容 V2，但在没做明示迁移验证前，不能把兼容性当既成事实。[External market inference] + [Repo confirmed]。
为什么普通 review 难抓到：这不是 repo 内部语法错误，而是现实市场制度和 venue 演化在 repo 外部发生。
实盘如何显化：边界日仓位被制度结算切断；4 月 22 日前后出现客户端/订单语义不一致、open orders 消失、恢复逻辑失灵。
Blast radius：live 停摆、错误恢复、误以为 flat、误以为挂单仍在。
Rollback：2026-04-22 前禁止 unrestricted live entry；先做 V2 preflight、cutover rehearsal、open-order wipe drill。
Monitor：v2_preflight_ok、post_cutover_order_reconcile_gap、boundary_distance_ticks、station_mapping_confidence。
债务：如果连 venue 演化和结算制度都不接住，任何 repo 内部 sophistication 都会白费。
F. Competing game-changer paths
Path 1 — State-Semantics / Execution-Truth Re-Architecture（主路径）
为什么缺的是这个，而不是更高级模型：因为当前最致命的不是 posterior，而是系统没有把“我是否真的提交了单、这个单现在处于什么 authority state、我该不该继续信它”做成 durable state machine。只要这层不成立，任何统计升级都在给不可信执行面喂更精准的决策。
插入落点：src/engine/cycle_runtime.py、src/execution/executor.py、src/state/db.py、src/state/chain_reconciliation.py、src/riskguard/riskguard.py、src/data/polymarket_client.py。
改变的上游机制：把 Zeus 从“下单后再补记状态”改成“先写命令，再让网络副作用发生”；把 UNKNOWN 从临时分支改成 authority state；把 RED 从 advisory 改成 authoritative de-risk。
live risk 如何下降：孤儿单窗口被切断；崩溃恢复变成可重放；chain/venue 不完整时机器承认 UNKNOWN 而不是自信瞎判。
blast / rollback / observability / validation：blast 最大，但可分阶段；rollback 明确——随时退回 NO_NEW_ENTRIES + EXIT_ONLY；observability 以 unknown_command_count、review_required_count 为核心；validation 必须做 crash-replay 和 cutover drill。
对未来 agent 的约束：禁止任何模块绕过 command bus 直接发单；禁止任何代码仅凭 telemetry 创建/修改 authoritative position state。
Path 2 — Settlement-Risk Containment + Market De-Scope
为什么它有竞争力：因为天气市场不是连续回归任务，而是离散制度任务；很多“模型升级”其实不如不碰那些制度边界最脆弱的市场。
插入落点：新增 src/market/eligibility.py、config/market_eligibility.yaml，并改 market_scanner / evaluator。
改变的上游机制：把市场筛选从“有 edge 就上”改成“结算语义清楚 + 边界距离足够 + 深度足够 + venue 状态稳定才允许”。
live risk 如何下降：减少 boundary-day、低深度、站点语义模糊市场带来的制度性亏损。
blast / rollback / observability / validation：blast 小；rollback 简单；observability 看 market_ineligible_reason、boundary_distance_ticks；validation 做历史边界日回放。
对未来 agent 的约束：任何新市场接入必须先给出 station/rounding/finalization contract，而不是只给 token ID。
Path 3 — Statistical Decision-Law Governance / Persistent Alpha Budget
为什么它是竞争路径：因为即使执行层完全 truthful，重复看盘、重复筛选、snapshot 切换仍会把局部正确的 FDR 变成全局错误。
插入落点：src/strategy/selection_family.py、src/engine/evaluator.py、src/state/db.py。
改变的上游机制：从 per-snapshot BH 变成跨时间 persistent alpha-spending；family 不只按“这次评估”定义，而按“这一天/这个市场/这个制度对象”定义。
live risk 如何下降：减少慢性过度尝试、同一市场被噪声反复诱骗。
blast / rollback / observability / validation：blast 中等；rollback 可回退到 hard cooldown；observability 看 alpha_spend_remaining；validation 做多 snapshot 仿真。
对未来 agent 的约束：任何新选择逻辑都必须先定义“它消耗的 alpha 预算来自哪里”。
G. Tribunal ruling

主路径裁决：选 Path 1，Execution-State Truth Re-Architecture。

原因不是它最“高级”，而是它是现在唯一能阻止 Zeus 在自信中死亡的路径。
Path 2 很重要，但它只能减少你去哪些坏市场；不能解决“你到底知不知道自己已经下单/持仓”的根问题。
Path 3 也重要，但它解决的是决策法则的慢性出血；在命令账本、UNKNOWN state、RED de-risk 没成型前，它属于在错误层级上继续精修。

运营裁决更直接：
data-improve 不应被判定为 unrestricted live-entry ready。 现在就应当把它降到 NO_NEW_ENTRIES；如果必须连着真实账户运行，也只能在完成 venue V2 preflight 和 command journal P0/P1 后，短暂进入 EXIT_ONLY 或 monitor mode。尤其考虑到官方已公告 2026-04-22 的 CLOB V2 切换与 open-order wipe，这不是抽象建议，而是近期硬门槛。

H. Codex-executable packet
1. 要新增 / 修改的文件与 authority boundaries

新增：

src/execution/command_bus.py
src/execution/command_recovery.py
src/state/authority_state.py
src/state/venue_command_repo.py

修改：

src/state/db.py：新增 venue_commands、venue_command_events 表与 API。
src/engine/cycle_runtime.py：先持久化命令，再提交到 venue。
src/execution/executor.py：拆成 build_submit_request() 与 submit_command()。
src/state/chain_reconciliation.py / src/state/chain_state.py：把 unresolved commands 接进 UNKNOWN 判定。
src/riskguard/riskguard.py / src/engine/cycle_runner.py：RED 直接生成 de-risk commands。
src/data/polymarket_client.py：V2 preflight、order-type/WS/user-stream 接口、market info/fee info。
src/state/portfolio.py：把 save_portfolio 语义降级为 export projection；修掉 degraded→VERIFIED。
tests/：补 crash replay、unknown state、RED de-risk、V2 wipe drill。

authority 边界要改成：
execution facts → venue_commands / position_events → position_current → JSON/status projections。
execution_report 只能是 telemetry，不得再承担 authority。

2. 关键数据结构 / invariants / contracts
# src/execution/command_bus.py
from dataclasses import dataclass
from typing import Literal

CommandState = Literal[
    "INTENT_CREATED", "SUBMITTING", "ACKED", "UNKNOWN",
    "PARTIAL", "FILLED", "CANCELLED", "EXPIRED",
    "REJECTED", "REVIEW_REQUIRED"
]

@dataclass(frozen=True)
class VenueCommand:
    command_id: str
    kind: Literal["ENTRY", "EXIT", "CANCEL", "DERISK"]
    token_id: str
    side: Literal["BUY", "SELL"]
    tif: Literal["GTC", "GTD", "FAK", "FOK"]
    limit_price: float
    shares: float
    idempotency_key: str
    linked_position_id: str | None
    decision_id: str | None
    state: CommandState

必须新增的 invariant：

没有已提交的 VenueCommand，就不允许任何网络下单副作用发生。
没有 venue_command_event，就不允许 authoritative position state 因“提交订单”而变化。
degraded export 永远不能标成 VERIFIED。
只要存在 UNKNOWN / REVIEW_REQUIRED command，系统就禁止新 entry。
RED 产生 de-risk commands，不再只是 flag。
positions.json 只是 projection，不是 truth。
3. 代码骨架
# cycle_runtime.py
cmd = venue_command_repo.create_entry_command(decision, intent, bankroll_at_entry)

try:
    venue_command_repo.mark_submitting(cmd.command_id)
    ack = execution_gateway.submit(cmd)   # may return ACK or raise transient error
except TransientVenueError:
    venue_command_repo.mark_unknown(cmd.command_id)
    artifact.add_review_required(cmd.command_id, reason="submit_unknown")
    block_new_entries()
    return

venue_command_repo.mark_acked(cmd.command_id, ack)
pos = materialize_pending_position_from_command(cmd, ack)
append_position_open_intent_event(pos, cmd)
# command_recovery.py
for cmd in venue_command_repo.list_unresolved():
    venue_view = gateway.lookup(cmd)
    if venue_view.is_open():
        repo.link_order_id(cmd, venue_view.order_id)
    elif venue_view.is_filled_or_partial():
        repo.project_fill(cmd, venue_view)
    elif venue_view.is_missing():
        repo.mark_review_required(cmd, reason="venue_missing_after_unknown")
4. tests / replay / monitoring / guardrails

必须新增的 tests：

test_command_persisted_before_submit.py
test_crash_after_submit_before_ack_write_replays_to_unknown.py
test_unknown_command_blocks_new_entries.py
test_red_emits_derisk_commands.py
test_degraded_export_never_verified.py
test_empty_chain_with_mixed_freshness_does_not_void_and_marks_unknown.py
test_v2_cutover_open_order_wipe_recovery.py
test_no_direct_place_limit_order_outside_gateway.py

必须新增的 monitors：

unknown_venue_command_count
review_required_count
submitting_age_p95
degraded_export_count
red_unwind_pending_count
boundary_distance_ticks_exposure
v2_preflight_ok
5. 实施顺序与依赖关系

P0 — 当天就该做

修 _TRUTH_AUTHORITY_MAP["degraded"]。
把 live 模式切到 NO_NEW_ENTRIES。
增加 hard gate：UNKNOWN/REVIEW_REQUIRED/quarantine_no_order_id 任一存在即禁新 entry。
清理 stale tests / stale truth comments。
加 Polymarket V2 preflight，未通过就禁止 order placement。

P1 — 第一优先级结构性改动

落 venue_commands + venue_command_events。
cycle_runtime 改成先创建 command，再 submit。
executor 只接受 command，不直接接受 loosely-typed intent。
crash replay worker 上线。

P2 — 语义闭环

UNKNOWN state 并入 chain reconciliation。
RED 改成 authoritative de-risk。
positions.json 改名或明示为 projection only。
去掉假执行能力命名，除非真有实现。

P3 — 次级改动

市场 eligibility / settlement containment。
persistent alpha-spending。
6. 必须加的 fail-fast / hard gates
V2 preflight 不通过：禁止一切 live order placement。
unknown_venue_command_count > 0：禁止新 entry。
portfolio_loader_degraded：不再整机硬停；改成 monitor/exit-only。
force_exit_review：自动生成 de-risk commands。
boundary_distance_ticks < N 或 station mapping 未认证：市场不合格。
AST/CI guard：除 execution_gateway 外，任何地方直接调用 place_limit_order 都失败。
7. live branch / data branch / recovery path / operator workflow 影响最大处
live branch：cycle_runtime, executor, polymarket_client, riskguard。
data branch：evaluator, selection_family, market eligibility。
recovery path：command_recovery, chain_reconciliation。
operator workflow：面板从“是否 green”改成“有多少 UNKNOWN/REVIEW_REQUIRED、需要哪些人工确认”。
8. 如何验证“系统不再只是自以为知道真相”

通过 replay 而不是叙述来验：

crash 在 submit() 之后、ack write 之前。
网络超时，但 venue 实际接单。
empty-chain + mixed freshness。
RED 触发时有存量仓。
2026-04-22 cutover 模拟 open orders wiped。
边界日市场，站点规则正确但数据尚未 final。

通过标准：系统进入 UNKNOWN/REVIEW_REQUIRED、禁止新 entry、保留可恢复链路，而不是把任何一个未知硬编造成已知。

I. Not-now list
更复杂的 BHM / 分层贝叶斯升级
现在不该做。前提是 command journal、UNKNOWN state、RED de-risk 先落地；否则只是更精准地把单送进错误状态机。
真正的 queue-position / impact simulator
现在不该做。前提是先有 user websocket、真实 fill telemetry、V2 稳定后的 order event ground truth。
自动化 quarantine 自愈
现在不该做。前提是 UNKNOWN / REVIEW_REQUIRED 的 authority contract 成熟；否则自愈会演变成自动造假。
更激进的跨城市/跨 regime 复杂风控图
现在不该做。前提是你先知道自己到底持了什么、挂了什么，再谈更高维敞口图。
J. Unrequested but necessary
2026-04-22 Polymarket CLOB V2 是硬门槛，不是旁注。 官方已公告：切换时 open orders wiped、旧集成无向后兼容。Zeus 若仍计划 live，必须先做 V2 迁移预检与 cutover rehearsal。
当前分支应被重新标记为：not live-entry ready。 最保守的正确动作不是“再观察一下”，而是立刻冻结新开仓，直到 P0/P1 完成。
必须立即清理 stale authority artifacts。 不是因为整洁，而是因为它们会把下一轮 agent / reviewer 引向错误结论。
