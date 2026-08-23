# Capital-Gains Loop — forward only

## 唯一目标
资本利得 = 结算后的实际收益。不是订单数,不是 uptime,不是历史回测数字。

## 现状(forward)

### 2026-08-22 B142 — supporting clock例外必须原子穿过current-observation seam
- **实时反例:** B140加载后的首个完整production auction覆盖174 families、82 eligible、1869 candidates；旧`GLOBAL_DAY0_FAST_OBSERVATION_ENTRY_STALE`降为0，但16个family随即在下一层变成`GLOBAL_DAY0_CONDITIONING_OBSERVATION_TIME_MISMATCH`。说明age gate已修，调用者却仍以ENTRY身份禁止same-extreme clock advance，supporting carrier与current observation ledger之间形成第二个非原子断层。
- **修复:** 对同一个current-local-day global remaining-path ENTRY布尔authority，同时允许stale supporting age与same-extreme conditioning clock advance。action state始终取current named source，carrier clock lag进入binding；remaining-path builder仍必须产生current simplex。任何extreme值、unit、named source、station、decision-time causality、topology或submit JIT content不一致继续拒绝；direct source-clock action route保持严格。Day0 semantics升v8。
- **SCOPE / DRAIN / RESET:** scope仍是exact current-day global ENTRY family，且只在remaining-path q enabled时生效。drain是current observation + remaining trajectories重建并在submit重现；reset为值/source/clock不可调和或builder/JIT失败，立即回到family-scoped fail closed，不影响其他城市或held SELL。
- **验收:** end-to-end antibody必须证明caller对30分钟前same-extreme supporting carrier同时传入age与clock两项permission，并继续用remaining q；helper默认strict与changed-value rejection不变。live要求time-mismatch计数下降、eligible family恢复；订单/fill/settlement/strict资本曲线另行证明。

### 2026-08-22 B141 — 单一terminal-review证据缺口不得阻断全局restart
- **实时反例:** B140标准deploy在command `736b13b34fee4f89`的restart preflight失败，entry guard持续armed。该20-share GTC在point order上matched 19.998533，但当时authenticated CONFIRMED trade facts仅覆盖4.858533；`REVIEW_REQUIRED`边界正确等待其余legs，authenticated recovery却尝试追加普通`PARTIAL_FILL_OBSERVED`清除review，被terminal-partial validator正确拒绝并升级为deploy-wide error。稍后其余facts到达后同command已原子投影FILLED，证明这是局部evidence ordering而非未知venue side effect。
- **修复:** `REVIEW_REQUIRED`的authenticated cumulative facts若仍未达到BUY completion tolerance，只返回`stayed`；不追加command event、不投影position、不清除review。完整confirmed legs到达后仍走既有`review_cleared_confirmed_fill`原子路径；真正terminal short fill仍只能由既有terminal-order recovery携带`terminal_partial_order_fact`三项proof清除。
- **SCOPE / DRAIN / RESET:** scope是一个exact ENTRY command的terminal-review boundary。drain为trade ingestion补齐command-bound CONFIRMED legs，或terminal-order lane证明真实short fill；reset为下一recovery pass重新聚合facts并只在完整/typed proof时推进。其他commands、held monitor和SELL不被该局部等待阻断。
- **验收:** antibody复现20 requested、4.858533 confirmed prefix、MATCHED review，要求`scanned=1/stayed=1/errors=0`、零新command/position event；原完整41-share review-clearance抗体继续advanced。landing后重跑标准restart，必须guard reset且完整global entry auction恢复。

### 2026-08-22 B140 — supporting observation时钟不得冻结current remaining-path q
- **实时反例:** 11:46Z完整global auction覆盖176个family，仅101 eligible；75个拒绝中50个为`GLOBAL_DAY0_FAST_OBSERVATION_ENTRY_STALE`。同一时刻source-clock ingest健康，城市最新METAR多为16–58分钟前，说明15分钟门槛把正常的小时级发布间隔误判为整个action q过期，而不是发现scheduler停摆。
- **结构性根因与修复:** global Day0 action q随后会从canonical current-temperature ledger、完整hourly trajectories、remaining conditional error与observation-latency floor重建到decision time；source-clock conditioning在此仅是bound/provenance carrier。仅对本地目标日的global ENTRY允许该supporting clock超过15分钟，且仍必须成功构造完整current remaining-day simplex。direct source-clock action路径、缺carrier/current observation/vector/topology、invalid clock与submit JIT不一致继续fail closed；Day0 semantics升为v7，防止与旧realized cohort混池。
- **SCOPE / DRAIN / RESET:** scope是一个current-local-day global ENTRY family的supporting-conditioning age检查，不改变q math、market ranking、Kelly或venue price。drain是同一cut完成remaining-path builder并经submit-time exact reproduction；reset是任一必需输入缺失/无效、remaining builder失败或路径不再使用current remaining q，此时原严格门槛立即恢复。
- **验收:** integration antibody证明30分钟前supporting conditioning仍由current remaining simplex定价，同时remaining vectors不可用时仍零候选；direct helper原15分钟抗体不变。部署后先看stale拒绝数下降与eligible family恢复，再独立看global winner、venue fill、settlement与strict evaluator；候选恢复不是资本盈利证明。

### 2026-08-21 B137 — ENS尚未发布的future-cycle request不得消耗sole materializer
- **实时反例:** 磁盘/RiskGuard恢复后，global scope 195 families仅4个entry-authoritative，171个因旧00Z carrier跨过24h进入`REPLACEMENT_STALENESS_RED_ENTRY_ISOLATED`。18Z deterministic providers已到，但18Z OpenData ENS两track每5分钟仍是48/48 `NOT_RELEASED`；queue仍把18Z requests启动subprocess，然后统一以`CAPTURE:CURRENT_EVIDENCE_NOT_LIVE`失败，使唯一writer在无法构造同same-cycle shape时做重复计算。
- **修复:** 复用现有decision-time eligible ENS HWM读取；seed/request的source cycle若严格高于该family当前ENS HWM，在request build/subprocess前写`DEFERRED_SOURCE_CYCLE_AWAITING_ENSEMBLE_HWM` typed receipt并移出本轮队列。不生q、不使用旧shape、不改HWM/read-time或same-cycle probability law；ENS提交后原cycle-advance publisher重新生成request。
- **SCOPE / DRAIN / RESET:** scope是exact city/date/metric future-of-ENS request的subprocess admission。drain是零spawn terminal/deferred receipt释放sole slot，同时保留完整request/ENS cycle evidence。reset是eligible ENS HWM追上request cycle；新seed正常build/spawn。HWM不可读时fail-open走原materializer，不把unknown当not-released。
- **验收:** antibody固定12Z ENS HWM+18Z request，要求零runner调用、typed receipt含12Z→18Z证据；request=ENS cycle与HWM读取不可用仍走原路。targeted queue tests、compile/ruff/registry/diff通过后landing；live要求ahead-of-ENS receipt出现、committed=0时subprocess消耗下降，且18Z ENS COMPLETE后立即恢复committed posteriors/global eligible families。

### 2026-08-21 B136 — decoded ECMWF raw cache不得再次锁死全局资本分配
- **实时反例:** 最新global auction的fresh families全部在最终allocator被`reduce_only_mode_active`reject；RiskGuard所有交易组件均GREEN，唯一隐藏驱动是OpenData raw GRIB增长到130GB，使磁盘仅76.0GB/7.64% free，违反64GiB与10%的较高者。安全回收已有canonical COMPLETE+VERIFIED证明且无open handle的20260813–20260818 raw后，free恢复到164.5GB/16.54%，RiskGuard自动reset为GREEN；随后一轮真实比较70个proposals，不再是全局故障性无订单。
- **修复:** 每次OpenData canonical commit后，仅对严格`YYYYMMDD` raw目录中早于当前与前一calendar day的exact cycle+parameter组生成retention plan。每组必须有对应`source_run` SUCCESS/COMPLETE/non-partial/expected=observed>0，且`ensemble_snapshots` exact source_run的全部rows均VERIFIED且count相等，才在释放DB writer lock后删除该组raw files；未知文件、symlink、不完整证明或删除异常一律保留。
- **SCOPE / DRAIN / RESET:** scope仅为可重下载的OpenData raw GRIB cache，不删canonical DB/decoded JSON，不改q、source selection或order law。drain是成功canonical commit后的有界proof query与按cycle/param原子文件集回收，且不持有DB writer lock删大文件。reset是任一proof欠缺/不一致时立即停止该组删除；当前与前一day永久保留供重启/redecode。
- **验收:** antibodies证明complete+verified旧组被删、recent组保留，partial/missing/disputed/count mismatch/symlink均fail closed；targeted tests、compile/ruff/registry/diff通过后landing并restart forecast-live。live要求retention receipt/log可见、free ratio仍>10%、RiskGuard仍GREEN、global auction仍广泛比较候选；订单与资本利得仍由venue/settlement/strict evaluator独立证明。

### 2026-08-21 B135 — identical successful posterior不得独占sole materializer
- **实时反例:** restart后writer持续生成12Z posterior，但最近87次写入仅覆盖12个target；Chengdu/Shanghai两个target占42次。逐行核对显示同target连续rows的`q_json`、`provenance_json`与`dependency_hash`完全相同，仅`computed_at`变化造成新identity；182个active family仍落后于ensemble HWM，sole subprocess被无新信息的重复成功占用。
- **修复:** 每次成功receipt记录materializer现有的exact request/current-input/logic fingerprint。后续同family请求仅在原成功后的固定60秒窗口内、`committed_posterior=true`且fingerprint完全相同时零subprocess coalesce；成功receipt不被skip覆盖，因此重复请求不能滑动延长窗口。新provider/ENS/Day0 observation/input file/logic revision或窗口到期立即走原materializer，q与posterior identity law不变。
- **SCOPE / DRAIN / RESET:** scope是一个city/date/metric的recent exact-input duplicate，不生成或修改probability。drain是写有界`success_coalesced_latest` receipt并释放sole worker给其它family；reset是fingerprint变化或从原成功时间起60秒到期。fingerprint不可证明、旧receipt无commit proof或future/invalid time均正常spawn。
- **验收:** antibody要求首次commit、第二次同fingerprint零spawn且原成功时间不变、fingerprint改变立即再次commit、60秒边界不滑动；完整queue suite、ruff/compile/registry/diff通过后landing。live要求recent posterior的unique-target/row比显著上升、HWM rejected family下降并恢复global candidate evaluation；订单与资本证明仍由独立global receipt/venue/strict evaluator决定。

### 2026-08-21 B134 — probability degradation不得成为deploy-wide无reset暂停
- **实时反例:** live-trading从`c6cf171d2`重启到`8a2168e07`时，7/7持仓均在新进程下数秒内写入`MONITOR_REFRESHED`且held-side CLOB fresh；但Day0 hourly provider quota为9500/9500，probability不可刷新。runtime已按exact held family阻断BUY，deploy wait与control-plane restart guard却再次要求`require_fresh_inputs=true`，因此全局`deploy_live_restart_guard`永不解除，所有其它family也无订单。
- **修复:** canonical monitor evidence保留严格stale分类，并新增仅用于restart proof的typed子集：`issue=monitor_probability_stale`且CLOB fresh的post-boot attempt证明新runtime已评估该持仓。它只从deploy/restart blocking中扣除；普通monitor cadence、held exit authority与runtime family entry gate仍把probability loss视为blocking。CLOB stale、两者皆stale、invalid probability、缺event、future event与legacy evidence继续fail closed。
- **SCOPE / DRAIN / RESET:** scope仅是本次deploy的全局暂停；不产生概率、订单或退出authority。drain为每个current exposure写post-boot typed monitor attempt，并证明runtime SHA/queue；reset为缺attempt/CLOB或出现新持仓时重新关闭restart proof。外部probability恢复只解除对应family runtime gate，不再是deploy pause的reset前提。
- **验收:** monitor restart helper、control-plane CAS proof与deploy post-start wait三者语义一致；抗体证明probability-only degraded通过restart proof、quote-only仍需complete held auction、unknown/legacy仍阻断。targeted tests、ruff/compile/registry/diff通过后landing并重跑标准deploy，要求guard reset、entries pause解除、全局receipt继续生成；资本评估仍独立FAIL直到真实after-cost证据转正。

### 2026-08-21 B133 — scoped held完成不得清空broad timebox的cycle cache
- **实时反例:** B132恢复12Z materializer后，source-clock日志仍连续报告broad `10/10 unattempted`。B130的pool确实由production跨tick持有，B131也保持同一target；但同一poll中的held/critical scope完成或已覆盖时会关闭共享cycle pool，清空broad target已经成功解码的小时点，下一tick只能从零重来。
- **修复:** exact-cycle bucket pool仍由source cycle唯一拥有；显式`required_scopes`只拥有自己的下载结果，不拥有共享pool生命周期。scoped成功/已覆盖不再关闭pool；只有unscoped broad完成、cycle rollover、异常或进程退出关闭。概率、admission、source cycle、target ordering与quota law不变。
- **SCOPE / DRAIN / RESET:** scope仅是同cycle bucket reader/value cache生命周期。drain为broad timeboxed retries持续复用已解码点直到完整target写manifest；reset为broad完成、cycle前移、异常或进程退出。抗体执行broad-timeout→scoped-success→broad-timeout，要求三次同一pool且中间零close；原有broad完成close与cycle-change close抗体继续成立。
- **验收:** cycle-currency targeted suite、compile/ruff/registry/diff通过后landing；live要求held完成之后broad `unattempted_target_count`仍在有限ticks内下降、exact-cycle gap低于241，并观察HWM family继续减少。未达到前不把0订单解释为资本最优。

### 2026-08-21 B132 — pre-spawn淘汰低于当前ENS HWM的旧request
- **实时反例:** 12Z Miami LOW request自23:06Z等待时，唯一materializer在23:09Z启动Manila 00Z request；latest auction同时把182个family明确判为`REPLACEMENT_RAW_INPUT_HWM`。现有JIT只在同family已经存在更新posterior时终止旧request；若新posterior尚未生成，已知低于12Z ENS HWM的00Z request仍占用sole writer并产出不可交易q。
- **修复:** shared HWM模块公开decision-time eligible ENS cycle读取；seed与request在写request/启动subprocess前同时比较current posterior与current ENS HWM。request cycle低于任一者即terminal `SKIPPED_SOURCE_CYCLE_REGRESSION`、`subprocess_spawned=false`；ENS HWM路径记录typed reason、request/current cycle。HWM读取不可用时queue optimization fail-soft，最终materializer/read-time HWM仍是fail-closed authority。
- **SCOPE / DRAIN / RESET:** scope是exact city/date/metric request；不改变q、target、cycle选择或writer count。drain为旧request写terminal receipt后移出queue，释放slot给新cycle；reset为request cycle追上eligible ENS HWM，正常启动。抗体固定无新posterior但存在12Z ensemble snapshot、00Z request，要求零subprocess并记录00Z→12Z evidence。
- **验收:** previous-runs priority/regression tests、queue suite、compile/ruff/registry/diff通过后landing；live要求`REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_BELOW_INPUT_HWM` receipts出现、00/06Z subprocess减少、Miami等12Z request完成，并随后观察auction HWM/eligible变化。

### 2026-08-21 B131 — zero-completion timeout保持同一target直到point cache收敛
- **实时反例:** B130 live后单次held wave可完成/跳过1个scope，但连续broad report仍为`10/10 unattempted`。代码核对证明timeboxed target即使`processed_target_count=0`也被rotation强制`+1`；下一tick换了city/grid index，B130缓存的同城24小时点值无法复用，真实跨tick收敛语义被rotation打断。
- **修复:** incomplete wave只有在至少一个完整target已processed时才按该数量前移；零完整target保持当前rotation start，复用per-cycle point cache继续同一payload。完成后仍正常前移，canonical reuse与non-admissible skip也仍计入processed，因此不破坏全局公平覆盖。
- **SCOPE / DRAIN / RESET:** scope仅是同cycle动态target universe的cursor advance，不改变target ordering、admission、source truth或q。drain为同一target的成功point值累积至完整payload后`processed_target_count=1`并前移；reset为target完成、cycle变化或异常关闭pool。抗体固定`start=2/attempted=0/incomplete=true`并要求持久化start仍为2、generation递增，证明CAS有效但不丢progress。
- **验收:** currency rotation/timeout tests、B130 bucket tests、compile/ruff/registry/diff通过后立即landing；live必须看到同一broad wave在有限ticks内`unattempted`下降并产生新manifest，否则继续追踪而不宣称恢复。

### 2026-08-21 B130 — bucket hourly objects有界并行，解除串行24步收敛停滞
- **实时反例:** B129 live已证明quota false时metered waves零消耗并进入bucket，但连续production report仍为held `0/6`、broad `0/10` attempted；一个新pool的单城raw resolver在12秒与25秒均未完成，且cache约10分钟只新增2个对象。根因是每个local-day payload按24个独立hourly OM对象串行读取，而deadline只能在不可中断的单次read之后检查，persistent pool虽最终可热却无法满足快速概率链。
- **修复:** raw bucket payload对不同valid-time对象使用现有`fetch_workers`的有界fanout（上限8）；每个URI仍由独立pooled reader读取，pool以锁保护reader map，并在同一cycle内缓存已成功解码的`(URI, grid index)`值，使timeboxed重试从上次原子进度继续而非重复解码前缀。结果按valid-time确定性重排，只有全部exact-run timestep成功后才组装payload并进入原manifest/DB/materializer链；任一timeout/non-finite仍零payload。
- **SCOPE / DRAIN / RESET:** scope仅是一个已通过run/city/valid-time admission的raw bucket payload内部I/O调度；不改变target、cycle、whitelist、downscaled路径、q或market比较。drain为最多8个独立valid-time read并行完成后原子组装；reset为本次调用结束或per-cycle pool在complete/cycle-change/exception时关闭。抗体用barrier证明四个distinct valid-times真正并发，同时输出时间与温度仍严格按valid-time排序。
- **验收:** barrier与wiring抗体、compile/ruff/registry/diff通过；真实Denver 12Z/24-hour读取在production同款10秒deadline与同一pool下为`timeout 10.79s → timeout 10.63s → complete 6.77s/24 samples`，证明跨tick单调收敛。landing后live仍必须观察bucket-qualified manifest写入、exact-cycle gap下降、HWM rejection下降与global eligible恢复；仅有更快read或进程健康不算资本证明。

### 2026-08-21 B129 — quota耗尽时直接走独立bucket，不先浪费deadline
- **实时反例:** 22:34Z global auction `0/197` probability eligible、0 candidates，全部由12Z ENS相对00/06Z consumed posterior的HWM supersession拒绝；provider quota=9500。当前 downloader每个10秒wave仍先尝试必败的run-pinned与meta-stamped API，日志随后显示8/10 targets unattempted，独立S3 bucket几乎拿不到deadline。
- **已有当前真相:** bucket `in_progress`与`latest`均明确声明12Z completed，145个valid hours；54个settlement cities中34个在严格cross-check raw whitelist。bucket不绕过概率或城市精度法：不在whitelist的20城仍拒绝，所需local-day任一时刻缺失仍拒绝。
- **修复:** downloader在正确quota lane内先只读`can_call()`；metered quota不可用时不调用run-pinned/meta waves，为每个city/date注入同一typed quota refusal并直接进入既有bucket rung。production持久化的per-cycle `BucketPointReaderPool`跨timeboxed ticks保留reader/cache，初次24小时对象读取可渐进变热；任何成功payload仍走原materializable、precision、manifest与DB验证。
- **SCOPE / DRAIN / RESET:** scope是当前download wave的metered transport选择，不改变target、cycle、q或whitelist。drain是bucket manifest/point read→canonical manifest→materialization；reset为quota恢复，下一tick重新优先metered rungs。抗体证明quota false时两个API wave零调用、bucket per-city执行且manifest正常产生。
- **验收:** targeted currency/bucket tests、compile/ruff/freshness通过；live quota=9500时报告`openmeteo_metered_quota_available=false`，bucket-qualified城市manifest/HWM开始收敛，非qualified城市保持fail-closed。

### 2026-08-21 B128 — 终止成功但永不收敛的 multi-location quota循环
- **实时反例:** canonical quota ledger显示 day_count=9500；`bayes_precision_fusion_single_runs_locations_batched`单独消耗5762（60.7%），其中priority下6个 request key累计267次、locations计费5553。transport多数成功，但 exact-cycle anchor仍缺248 scopes，说明quota被成功重抓而非新coverage消耗。
- **结构性根因:** BPF target universe可以只含HIGH或只含LOW；location fast path与普通 batched path却固定要求`("high","low")`都已持久化才skip。fetch后 writer只为实际`city_targets`写行，因此不存在的 sibling metric永远不会出现，同一成功batch每poll重抓。
- **修复与边界:** completion/metrics_needed改为当前 city/date实际 target metrics集合；HIGH-only在HIGH exact-cycle行存在后立即complete，LOW同理，双metric仍要求两者。不制造非市场 sibling、不改变model/q math、cycle identity或transport失败重试。SCOPE是model×city×date×实际metric；DRAIN为一次成功persist；RESET为新cycle或新metric target出现。
- **验收:** HIGH-only连续运行两次只发一次multi-location请求、第二次written=0且不伪造LOW；完整BPF suite、compile/ruff/freshness通过。live在UTC reset后同 request key attempts不再线性增长，quota用于residual anchor/BPF新coverage。

### 2026-08-21 B127 — 把 residual drain 接到真正的 data-ingest owner
- **实时反例:** B126 landing/restart后，forecast-live registry证明旧 `_replacement_cycle_availability_poll_if_needed` 已明确不再调度；真实 owner是 `src.ingest_main._replacement_availability_poll_tick`。新 live 日志仍出现 ordinary anchor `priority=maintenance reason=day_limit=9500/8500`，证明仅修共享旧 wrapper不能改变生产。该 owner还会在 source cursor无更新时直接返回，使 bounded首 wave后残余 scope只能等慢 maintenance。
- **结构性修复:** production current-target helper新增互斥 `quota_priority` authority并传到 downloader各 transport。真实 data-ingest owner的 ECMWF source-clock broad anchor与 committed ordinary partitions显式使用 priority；source clock无更新时先只读测量 probe-resolved exact-cycle gaps，零 gap保持轻量，正 gap/不可读才触发一个 bounded residual wave并发布 reseeds。
- **SCOPE / DRAIN / RESET:** scope 是 data-ingest的 ECMWF anchor current-target path；critical held scope仍优先且不与 priority叠加。drain由15秒 owner tick逐次旋转，single-download lock隔离并发；reset为 exact-cycle gap归零或 provider cycle推进。provider daily hard cap不变，今日9500用尽时只能证明 priority routing与持续重试，不能证明 gap下降。
- **验收:** unchanged-clock residual、source-update broad anchor、source-commit ordinary partition、quota lane互斥和现有 scheduler suites通过；deploy后日志不再把该路径标为 maintenance，并在UTC quota reset后观察 `anchor_missing_scope_count`单调下降、HWM rejection下降。

### 2026-08-21 B126 — exact-cycle anchor coverage逐 scope排空
- **实时反例:** 12Z/06Z ENS 已提交且203个 family被 HWM拒绝；availability poll 对新 cycle只下载 `limit=10` 个 scope，然后 `_per_leg_downloaded_cycle=MAX(source_cycle_time)` 已等于 published cycle，后续 poll把其余约205个 scope误判为 current并永久停取。日志同时显示 source-clock anchor以 maintenance quota运行，未使用 quota模块明确保留的 source-clock tranche。
- **结构性修复:** 每个 poll对 probe-resolved published cycle构建 exact-cycle current-target plan；只要任一 scope缺 anchor manifest，即使全局 MAX 已前进也继续用 durable rotation下载 residual。新 cycle首次 wave可包含全部目标；后续 wave仅选 missing manifests。availability anchor标记 `quota_priority`，并将 priority context显式传播进 metadata、run-pinned与 worker-thread transport，避免 thread-local在 executor边界丢失。
- **SCOPE / DRAIN / RESET:** scope 是 published cycle × current city/date/metric manifest，不触碰 q math、market comparison或 order law；drain每次 bounded poll最多处理 config limit并由 durable rotation继续；reset是 exact-cycle gap归零或 provider cycle推进。gap probe不可读时fail-open重试，不把全局 MAX当完整性证明。provider hard daily cap仍保留，修复不绕过限额。
- **验收:** partial-cycle residual、worker-thread priority、availability与currency suites、compile/ruff/diff/planning通过后 landing；live 应在 quota允许时连续看到 `anchor_missing_scope_count`下降并随后 HWM rejection下降。hard cap耗尽期间只证明正确重试/限流，不能声称概率链已恢复。

### 2026-08-21 B125 — newest source cycle 优先并在 subprocess 前二次单调校验
- **实时反例:** 当天 12Z/06Z ensemble snapshots 到达后，latest auction 仅 `1/221` family eligible，203 个被 raw-input HWM拒绝；其中 111 个 latest=12Z但 posterior仍消费00Z、85 个 latest=06Z但仍消费00Z、7 个 latest=12Z但消费06Z。materializer仍持续 commit且0 failures，证明活跃 writer正在生成过期 cycle q。current requests 同时含 00Z=37、06Z=10、12Z=11；现有排序不区分同 family source cycle，且仅在 seed admission检查 cycle regression，request 等待期间 posterior前进后不会 pre-spawn重检。
- **结构性修复:** chain-money/never-priced/held/plain authority classes不变；每类拆成 newest queued source cycle 与 older cycle相邻 subtiers，再按 request自身 computed_at FIFO。newest cycle先更新 posterior；任何等待后已落后于该 family current posterior 的 request在 subprocess前写 terminal superseded receipt，不启动 materializer。旧 cycle不能再次占用 writer或成为最新 q。
- **SCOPE / DRAIN / RESET:** scope 是 exact city/date/metric request及其 queued/current source-cycle order；drain每 poll重算 queued max cycle并在 pre-spawn读取 current posterior，older request终止后自然移出；reset是更新 cycle request到达或 current posterior未前进，此时 request仍正常验证/执行。不改变 HWM、freshness、Day0 owner、probability math或 single-writer count。
- **验收:** newest-cycle priority与 JIT regression两项抗体、现有 priority/queue/Day0 suites、compile/ruff/diff/planning通过后 landing；live 需看到 source-cycle-regression terminal reason、12Z/06Z q先于00Z backlog、HWM rejection下降，且没有 posterior cycle倒退、failed_count保持0。

### 2026-08-21 B124 — 同 priority tier 使用 request 自身时间，禁止历史 marker 冒充年龄
- **实时反例:** B123 把 280 个 requests 暴露给全局排序后，当前 top-40 仍有 31 个是 auction 已 eligible family，仅 4 个 HWM、5 个 identity-missing。全部 request 被分在 tier 1；排序 secondary key 来自 scope/cycle 上任意历史 `cycle_advance_enqueues.enqueued_at`，所以 `computed_at=11:32` 的新 eligible fusion refresh 可继承 `07:45` marker并排在真正 `computed_at=07:50` 的 HWM repair前。最近 30 个成功 materialization 全是已 eligible family，且 successive auction 的坏 family set未变化，证明旧 marker 时间正在误导唯一 writer。
- **结构性修复:** chain-confirmed exposure / never-priced / held-marker 仍只决定 tier；同 tier secondary key改为每个 request 自身 causal `computed_at`，仅在 payload无合法 computed_at时才回退历史 enqueue time。不同 producer 的新 request不再继承同 scope旧 marker的虚假年龄；真正等待最久的 current repair先用 writer。
- **SCOPE / DRAIN / RESET:** scope 仅 request sort secondary key，不改变 tier、ownership、validity、q math、subprocess count或 DB writer。drain 每个 poll对当前 request snapshot重算，最老 request执行后自然移出；reset是更早 current request到达或更高 tier出现，下一 tranche立即重新排序。抗体固定两个均已priced tier-1 scope：marker较早但 request较新的 eligible refresh必须排在 request自身更老的 lagged repair之后。
- **验收:** priority/queue/Day0 tests与compile/ruff/diff/planning通过后 landing；live top-ranked request应从 eligible refresh转向 missing/mismatch/HWM repair，successive auction至少出现坏 family resolution或eligible上升，且 `failed_count=0`、single writer不变。

### 2026-08-21 B123 — seed admission breadth 与 SQLite writer concurrency 解耦
- **实时反例:** B122 后最新 global auction 仅 `122/263` family 有 current probability；40 个因 Day0 posterior identity missing、11 个 mismatch、65 个 raw-input HWM lag 被排除。把 family identity 反向映射到 canonical seed queue 后，50/51 个 Day0 identity family 与 64/65 个 HWM family 已有 ownership=`CURRENT` 的 queued seed，等待中位数约 2.1–3.3 小时。config 明确提供 `poll_batch_limit=8`、`seed_limit=80`，但 daemon 把 seed admission 也压到 `DEFAULT_MATERIALIZATION_MAX_WORKERS=1`；未 admission 的 current work因此无法进入 request-level global held/never-priced priority sort。
- **结构性修复:** 每 poll admission 使用 configured bounded micro-batch 8，将 current seeds 转成可全局排序/coalesce 的 requests；actual claim `limit` 仍由 `DEFAULT_MATERIALIZATION_MAX_WORKERS=1` 限定，所以每 poll 至多一个 subprocess、一个 SQLite writer。request backlog 每个 worker tranche 重新读取 chain-confirmed held exposure并全局排序，不会冻结 8-request stale priority tranche。
- **SCOPE / DRAIN / RESET:** scope 仅 forecast seed-to-request admission，不改变 probability math、freshness、owner fence、request validation或 writer count。drain 每秒最多 admission 8、execute 1，held/never-priced work在下一 worker tranche可抢占；reset 在 seed queue empty 时自然回到 `seed_limit=0`，config seed limit仍由 production wrapper硬上界。scheduler 抗体固定 request pending/seed pending 两条分支均为 `limit=1, seed_limit=8`，inflight-only仍为0。
- **验收:** scheduler + queue/Day0 regression、compile/ruff/diff、registry/planning gate通过后 hot-fix landing；live 必须看到单 writer不变、request queue获得 bounded burst、seed backlog快速转移、global probability eligible上升或 identity/HWM排除下降。资本评估器仍必须独立证明 realized gain，吞吐改善不等于盈利证明。

### 2026-08-21 B122 — exact duplicate seed 在唯一 writer 前合并
- **实时反例:** 使用与 daemon 相同的 absolute canonical paths 复核后，当前 seed ownership 为全 `CURRENT`，排除了 stale-owner 假设；真实队列仍有 319 个文件。按生产 request 已有的 semantic key（city/date/metric/source cycle/baseline + OpenMeteo run IDs/完整 Day0 conditioning identity）分组，一次 8-file cursor rotation 可证明 39 个较旧 seed 被同 key 的更新 `computed_at` seed 严格取代。现有 coalescing 位于 request 阶段，但 `seed_limit=1` 使 duplicate seeds 在不同 poll 单独进入 request 并逐个 materialize，旧 q 因此消耗唯一 SQLite writer、延后当前 q 与 global market comparison。
- **结构性修复:** 在同一 bounded 8-file seed window 内复用既有 semantic key 与 freshness order，并把 `upgrade_trigger`、`cycle_advance_enqueue_owner` 作为 seed producer authority 加入等价关系；只将 full seed contract 合法且完整 key 相同的旧 seed写为 terminal superseded receipt。不同 trigger 不合并，Day0 cycle-owner seeds 无论 marker 当前是否可读都留给原 exact ownership fence，Day0 observation/source/extreme/unit、provider run、source cycle 或 scope 任一不同也不合并，malformed seed 留给原精确失败路径。coalescing 不占 materialization limit，同 poll 仍只产生至多一个 request/一个 subprocess/一个 DB writer。
- **SCOPE / DRAIN / RESET:** scope 是当前 raw window 内 exact-key duplicate seed；drain 每秒 poll、每轮最多 8 个文件，旧 seed 终止后 newest keeper 使用原 worker slot；reset 来自 key 或 freshness 不再满足严格取代关系，此时两者都保留并各自进入正常验证。抗体固定 `limit=1` 下 older/newer duplicate seeds 只 build newer、旧 seed留审计 receipt、newer request 继续生成。
- **验收:** focused seed/request coalescing 与 materialization queue suite、compile/ruff/diff、source/test registry、planning lock通过后 hot-fix landing；live 必须出现 seed superseded reason、`seed_processed_count>1` 同时 `committed_posterior_count=1`、`failed_count=0`，并以 successive global-auction identity coverage而非单点 side-effect-free EV 判定资本链改善。

### 2026-08-21 B121 — globally-ranked EXIT 的概率 revision 与命令原子绑定
- **实时反例:** 过去 24 小时 17 个 filled EXIT 的 `venue_commands.q_version` 全为空；其中 9 个只能经 decision-certificate attribution 间接找回决策，8 个旧仓仍不可归因。最新 Mexico City global SELL 也缺 q-version，证明断层仍在当前统一竞价路径，不是单纯历史数据问题。同期 global capital basis 从约 `$466.99` 降到 `$450.98`；因此不能以局部 realized PnL 掩盖逐单审计缺口。
- **结构性修复:** submit-time probability content 已与 selection witness 精确复核后，global SELL `ExitIntent` 同时携带 `q_version`、probability witness/content identity 与 source-truth identity；相同字段进入 capital certificate。`venue_command_repo` 对带 typed `GlobalSellReceiptClosure` 的 SELL 强制 non-empty q-version，并在 command/envelope/event 任一写入前原子拒绝缺失 identity。RED/legacy/recovery 等非 global statistical paths 不被误加同一约束。
- **SCOPE / DRAIN / RESET:** scope 仅为一个 globally-ranked SELL candidate/command。缺失 identity 时该候选不提交；complete auction 可在下一 cut 重新比较其余 BUY/SELL/HOLD/CASH。reset 只能由下一次 current probability witness 产生完整四元 identity，不能回填旧订单或用 entry belief 替代 current held belief。
- **验收:** integration antibody 证明 ranking witness 精确进入 `ExitIntent.probability_receipt` 与 capital certificate；command-journal antibody 证明 closure+q 原子持久化、closure-without-q 零行回滚。focused integration、venue journal、compile、diff、planning-lock 通过后 hot-fix landing；live 只以未来 globally-ranked EXIT command 的 non-empty q-version 与 exact receipt closure 作为生产证据，不修改或粉饰旧亏损。

### 2026-08-21 B120 — global proof evidence 按 city-date 原子持久化
- **实时反例:** current-law side-effect-free auction 在同一 target date 先后选择了独立城市（Aug-21 Lucknow/Tel Aviv；Aug-22 Helsinki/Singapore），但 `NoTradeRegretEvent.event_id` 只绑定 strategy/revision/target_date。唯一约束因此每天只保留一个城市；RiskGuard 后续虽按 `(city,target_date)` 聚类，仍永远收不到同日其余独立城市，令 automated alpha gate 的 forward settlement drain 人为失速。
- **结构性修复:** entry shadow event revision 升为 city-date-cluster identity；同一城市日期的 sibling bins 与 HIGH/LOW 继续幂等去重，不同城市在同一日期可各自落一份 exact global-winner certificate。exit-shadow reader 同时接受既有 v5 和新 v6 证书；不改 probability、ranking、Kelly、RiskGuard threshold 或 venue actuation。
- **SCOPE / DRAIN / RESET:** scope 仅是 no-money capital-proof persistence identity。drain 仍由每轮完整 global proof winner 写入及未来 verified settlement/qualified early-exit grading完成；reset 仍由 RiskGuard 当前 revision cohort 达到 model-over-market e-value 与正 hypothetical realized capital proof。任何缺 current q/book/full-fill/positive ΔlogW/EV 的候选不写证书。
- **验收:** antibody 必须证明同一 target date 的两个不同城市各落一行、同一 city-date 重放仍幂等，并覆盖 v5/v6 exit-reader compatibility；focused integration、RiskGuard settlement joins、compile、diff、planning-lock 通过后才 hot-fix landing。live reload 后以新 v6 canonical rows、城市覆盖与 gate evidence count 验证，不把 shadow EV 冒充真实资本利得。

### 2026-08-13 B105 — canonical held-monitor debt 由持续 worker 排空
- **实时反例:** live `6977d34c` 有 26 个 blocking-stale held positions；30 秒 `exit_monitor_recovery` scheduler job 同步运行最长 75 秒的 full-book monitor，`max_instances=1` 因而持续丢 tick。SQLite interruption、reactor handoff 或 preparation deadline 失败后，债务只能再等 scheduler，最老 `MONITOR_REFRESHED` 已超过 25 分钟。entry fail-closed 正确阻止 BUY，但 held redecision 全书失明。
- **结构性修复:** scheduler job 只读取 canonical cadence evidence 并幂等 dispatch；一个 daemon recovery worker 复用原 process-wide monitor claim，失败后立即从 DB 重建债务并持续重驱。它不增加 writer 并发、不放宽 150 秒、不改变 probability/edge/exit law，也不允许 quote-only 或 stale q/CLOB 清债。
- **SCOPE / DRAIN / RESET:** scope 仅为当前 positive-exposure 的 blocking stale/future canonical monitor evidence；drain 是单 worker 反复运行现有 full-book lane；reset 只能由每个当前 exposure 的 fresh probability + held-side CLOB `MONITOR_REFRESHED`，或该 exposure 离开 monitored lifecycle set 证明。worker dispatch/exit 的 lost-wakeup 由锁内 request handoff 防止。
- **验收:** detector 不 inline 运行 monitor；重复 dispatch 只有一个 worker；monitor `False`、异常和 evidence read failure 均保持 debt 并重驱；fresh canonical post-read 才清 debt/fairness。focused + complete runtime-failure suite、compile、planning-lock、diff check 与独立 race review 通过后，按 hot-fix lane 落地。live reload 后必须看到 scheduler 不再 `max_instances` skip recovery、blocking stale 收敛到 0、每个 open position 获得 post-start fresh canonical monitor evidence，并继续以 command/fill/PnL 证明资本结果。

### 2026-08-13 B103 — command-specific no-fill 不再被 aggregate existing position 误作未知新增敞口
- **实时反例:** 两笔已获准向 reconciled existing position 增仓的 GTC BUY（Miami `98ceeb9699174bef`、Tel Aviv `bb7f96e47fac4cda`）在 cancel-unknown 后，authenticated point order 无活记录、完整 account open-order/trade 扫描无命中、local trade facts 为零，却因 `position_current` 正确保留较早成交的正 shares 而无法满足“整个 projection 零敞口”。两笔 command 永久停在 `REVIEW_REQUIRED`，两个不同 market 又被 governor 提升为 systemic unknown-side-effect，触发全局 reduce-only；candidate 仍被生成但所有新 entry 被拒绝。这是 command exposure 与 aggregate position exposure 的语义混淆，不是 alpha 缺失。
- **结构性修复:** no-fill clearance 只增加一个严格对称的 second proof shape：从 immutable `SUBMIT_REQUESTED.execution_capability` 重放完整 ENTRY/submit/GTC-or-GTD/command/token/snapshot/capability identity，要求唯一的 `entry_duplicate_same_token=allowed_reconciled_position_increment`、相等的 global wealth binding，以及当前同 position、同 selected token、`active|day0_window`、`chain_state=synced`、正 chain shares、existing order id 不等于本 command order。它与 authenticated point read、完整 account scans、零 matching order/trade、零 local trade/positive order facts、exact unresolved finding CAS 同时成立时，仅 terminalize 本 command；aggregate position 不追加 `ENTRY_ORDER_VOIDED`、不改变 shares/cost/phase/order。任何 capability/identity/scan/finding ambiguity 仍 fail-closed。
- **生产适配:** scheduled three-phase recovery 的 immutable `VenueReadSnapshot` 现在显式声明已捕获 point-read completeness；captured exception 仍重放为 `SnapshotMissError`，不能冒充 absence。absence 与 explicit terminal-zero-fill 两个分支共用同一 proof reproducer，避免只修一侧。
- **验证:** command recovery 全文件 `562 passed`；review/no-fill 组合 `128 passed`。新增关系抗体覆盖 certified absence、certified terminal zero-fill、完整 existing position/event invariance、capability action/token/snapshot/order-type/wealth 篡改、execution-fact cost 与 projection cost 偏差、envelope/snapshot condition identity、strict aggregate 故障传播、point/open/trade incomplete、matching venue/local facts、ambiguous finding、savepoint rollback、成功重放 idempotency，以及最终 `append_event` 对 finding identity/omission/count、point completeness/source、increment proof 与 terminal fact 七类 forged clearance 的原子拒绝。以 worktree code 对 live canonical DB 只读重放，两笔当前 row 均生成完整严格 witness。尚未 deploy；live 仍有 unreviewed/unpushed 外部 commits，正式 landing/restart 前必须先恢复可证明的 live deployment chain，再以 loaded SHA、recovery events、unknown/finding count 归零和新的 full auction/entry receipt 验证。

### 2026-08-13 B102 — deterministic FAK no-fill 后同 turn 重拍
- **实时反例:** Seoul `1772ee93-a80` 在 `06:34:46Z` 已有 current q `0.04658`、bid `0.06` 与负 edge，global auction 正确选择 TAKER SELL；`06:35:02Z` venue 返回 deterministic `FAK no match`。系统只把 `next_retry_at` 设为当前时间，却直到 `06:45:39Z` 才 release/publish 新 reauction；`06:46:04Z` bid 已跌至 `0.01`。FAK 竞态本身不可保证，但这 10m36s 无 delivery guarantee 是 engine-preventable。
- **结构性修复:** FAK no-fill / post-only cross 的 canonical no-side-effect rejection 与 exact V4 outbox 在同一 monitor turn bounded commit；commit 成功后立即 drain position-scoped debt并发布 fresh global reauction，重新比较 TAKER、MAKER_REST、HOLD/CASH。不得原地把旧 TAKER certificate 改成 maker；commit/publish 失败保留 canonical debt给 recovery。
- **验收:** antibody 从未提交的 no-fill retry/outbox 开始，不调用下一轮 pending-retry scan，断言同 turn commit + exact V4 wake；原 request identity 不被当作 fresh execution authority。SCOPE 是 position+held token+family+q identity+generation；DRAIN/RESET 仍由 immutable terminal receipt 或新 generation 完成。

### 2026-08-13 B101 — monitor bootstrap 不能吞掉连续概率重估预算
- **实时反例:** full-book attempt `417337` 持有约 75 秒 claim，但 `CycleArtifact` 创建时只剩 `26.170s`；artifact 前约 48.8 秒没有 q/book 决策。`run_exit_monitor_cycle` 把完整 outer deadline 交给 trade DB connection、ATTACH、watchdog、portfolio load 与 allocator refresh，因此 SQLite 争用可以合法耗尽本应属于 held redecision 的时间。
- **结构性修复:** normal/YELLOW/ORANGE monitor 的 reactor handoff 必须先为 bootstrap + 一次完整 q read 保留两个 tranche；bootstrap 本身再限制为一个 tranche，并始终把另一个完整 tranche 留给 current probability + executable book。准备超时只终止本次 attempt，由 recurring monitor 在 DB writer 释放后重试。RED 不保留 statistical tranche，继续让 force-exit 使用完整 claim。准备阶段所有 connection/load/retry-release 使用同一 preparation cutoff，receipt 记录 handoff 耗时、准备预算/耗时与留给 primary 的剩余时间。
- **验收:** deterministic clock 抗体证明 75 秒 claim 的 bootstrap 不能超过一个 q-read tranche、不足以同时容纳 preparation 和完整 q read 时不启动 DB、RED 仍保留完整 sweep claim；既有 absolute claim、SQLite deadline、pending-exit 和 monitor progress 抗体必须继续通过。此项不改变 probability、edge、Kelly 或 exit economics，只修复时间预算的因果所有权。

### 2026-08-12 12:35 CDT tick — current-law 前向资本已为正且 truth complete；robust 仍未证明
- **live 结果:** loaded SHA `b9f9dd0e8`。以 `2026-08-11T00:00:00Z` 为显式起点、`2026-08-12T17:34:52Z` 为 decision-time cut 的 canonical read-only audit 覆盖 55 个真实 filled commands，chain matched/partially-matched fact coverage complete，0 个 pre-boundary entry fills、0 个未分类 fills。
- **资本证明:** 23 个 realized positions，gross realized PnL `+$24.183599`，submission-schedule fee bound `$2.347814`，net realized PnL `+$21.835785`，realized-capital return `+41.998%`；8 win / 15 loss。Day0 curve 为 13 realized、net `+$2.975415`；qkernel curve 为 10 realized、net `+$18.860370`。两条 strategy curve 的 `blocked_position_count=0`，总状态为 `positive_observed` / `capital_truth_complete=true`。
- **修复闭环:** `partial|confirmed|filled` 且有 `filled_at` 的 entry/exit facts 进入资本曲线；partial-exit 仓位仅在存在真实 filled exit 且 `remaining_cost = original_entry_notional × remaining_shares / original_entry_shares` 与 canonical residual projection 相符时通过。Tokyo/Singapore dust residuals 因此不再被误判为资本缺失；不改写 DB/PnL，不接受 pending 或 matched-only intent。
- **未达部分:** 同目标日内的相关仓位按 cluster 合并后只有 3 个独立 target dates；robust e-value `1.717618 < 10`，reason=`INDEPENDENT_CLUSTER_STRENGTH_NOT_ESTABLISHED`。因此只声明当前前向净资本利得已由真实订单/结算证明，不声明大量胜单、稳定胜率或 robust edge。entries pause 保持；后续证据只能由更多独立未来日期在同一 current-law 下自然形成，不能靠扩大风险制造。
- **验证:** 两轮官方 deploy 均保持 entry pause 与 fresh held cadence；RiskGuard 全文件 `161 passed`，Ruff、`py_compile`、diff check 通过。

### 2026-08-12 12:18 CDT tick — partial fill 不再从当前资本证明中消失
- **当前动作:** entries pause 保持；剩余有真实规模且有 bid 的持仓全部 `bid < current q`，强卖会降低 posterior-mean expected capital，因此本 tick 的可执行决策仍为 HOLD，而不是为制造退出记录低卖。
- **前向审计反例:** 以 `2026-08-11T00:00:00Z` 为显式边界，现有 current-law audit 报告 20 个 realized positions、3 个独立 target-date clusters、fee-bound net `+$15.733464`，但仍 fail-closed 为 `capital_truth_degraded`。根因不是策略亏损，而是资本事实 join 只接受 `execution_fact=filled`：Shanghai `5c16a631-b63` 的第二笔 maker entry 已由两条 venue trade facts 真实成交 5.744678 shares、order fact 为 `PARTIALLY_MATCHED`、execution fact 为 `partial`，且与第一笔合计精确复现 canonical 10.744678 shares / `$4.59468` cost basis，却被审计遗漏；该仓已结算盈利 `+$6.15`。
- **最小修复:** RiskGuard capital curve 与 forward audit 只扩大到带 `filled_at` 的 `filled|confirmed|partial` execution facts；链上覆盖相应接受 `MATCHED|PARTIALLY_MATCHED`。pending、matched-only、零成交和未确认 intent 仍不计入资本事实；交易准入、订单选择、概率、Kelly、settlement 与 pause 均不改变。
- **验证:** RiskGuard 全文件 `160 passed`；Ruff、`py_compile` 与 `git diff --check` 通过。尚未 live；部署后必须在 canonical DB 上重跑同边界 audit，清除所有 capital-identity blockers，并重新报告实际 net/e-value，不能把预期修复值或单笔胜出冒充 robust proof。

### 2026-08-12 12:10 CDT tick — 首笔全局最优实际退出兑现；current-law capital curve 转正
- **真实成交:** complete global auction 在同一 current probability/book/wealth cut 中选择 Shanghai `288de757-908` 的 20 YES shares；preflight receipt `415446` 为 `STABLE`。executor 提交合法 `0.95` FAK floor，venue order `0xdafefd...63a43` 全部以改善价 `0.999` 成交，transaction `0x95b23b...4123`；REST confirmed trade fact、wallet fill、zero chain shares 和 lifecycle `economically_closed` 已收敛。
- **资本结果:** canonical cost basis `$1.40`，gross proceeds `$19.98`，canonical gross realized PnL `+$18.58`。按 entry/exit 各自冻结的 5% weather fee schedule 计上界，entry fee `$0.065100`、exit fee `$0.000999`，该仓 fee-bound net realized PnL `+$18.513901`，realized-capital return 约 `+1263.0%`。这不是 expected EV，也不是未成交报价。
- **组合证明:** 当前 probability semantics / `predicted_bin_ev_v1` 的 30 天 canonical curve 现为 14 个 realized positions、gross `+$3.25`；现有 live observer 因遗漏 `CONFIRMED` exit fee 报 net `+$1.561298`。修正后应为 fee bound `$1.689701`、net `+$1.560299`、return on realized capital 约 `+3.3827%`。曲线已转正，但收益高度集中于这一笔，不能声明 robust capital gain 或大样本市场优势。
- **证明链精度修复:** `execution_fact.terminal_exec_status='CONFIRMED'` 是比 `filled` 更强的成交事实；capital curve 过去只 join `filled`，导致该 exit 的 fee 被静默当成 0。修复让 entry/exit 两端都接受 `filled|confirmed`，不接受 pending/matched-only；新增 confirmed-exit fee antibody，RiskGuard 全文件 `158 passed`，Ruff（忽略文件既有 E402/F401/F841）与 `py_compile` 通过。hotfix 尚未部署；entries pause 保持，后续只用更多独立真实成交/结算检验 robustness。

### 2026-08-12 11:58 CDT tick — bid-only held depth 进入所有阶段的 reauction trigger
- **最新 live chain:** 并发 money-path commits 已把 above-submit-band current bid 与合法 SELL floor 区分：solver 以当前 0.999 depth 比较经济性，executor 提交不高于 0.95 的 FAK floor，并把实际改善成交单独记账；loaded SHA `f947180e5`。这恢复了 Shanghai 的潜在正 EV 出场，但尚无新 command/fill，所以 forward realized PnL 仍为 `-$8.526401`。
- **剩余断链:** post-start monitor 已达到 `open=11 / fresh executable=7 / quote-only=4 / blocking stale=0`，却没有新的 held-SELL reauction request。根因是 `monitor_quote_refresh` 只允许 Day0 位置消费 one-sided book；Shanghai 已过本地目标日，虽然 0.999 bid 可立即承接 SELL，monitor 仍把它记为 quote stale，reactor 因 `no exact canonical held-SELL request` 停止在 full auction 之前。
- **第一性修复:** held SELL 只需要 bid，不需要 ask；任何生命周期中的 current bid-only depth 都是可执行 counterparty evidence。ask-only/no-bid 被解释为零立即清算价值的特殊路径仍限 Day0，防止非 Day0 凭 ask 虚构 bid。global auction、JIT、legal submit floor、actual fill receipt 与 settlement law 不改变。
- **验证:** post-target active bid-only 新抗体通过，post-target ask-only 继续返回无 quote；连同既有 Day0/target-local bid-only 关系共 `6 passed`。source Ruff（仅忽略文件既有 E402/F401）、`py_compile` 与 diff check 通过。热修复尚未部署；当前 official deploy 仍在等待 post-start full-auction receipt，入场 pause 保持。

### 2026-08-12 11:44 CDT tick — full-auction held proof 接入 deploy cadence；不再把不可执行 quote 冒充未重决策
- **运行态已生效:** live loaded SHA `e7661feeb`。post-start receipt `415191` 在 boot 后 21 秒完成：11 / 11 families、20 candidates、`candidate_coverage_complete=true`、`scope_family_coverage_complete=true`、`held_position_coverage_complete=true`，winner 为 CASH，reason `NO_CURRENT_EXECUTABLE_POSITIVE_ORDER`。
- **Shanghai 结果:** position `288de757-908` 在同一 frozen cut 中为 `EXCLUDED / SELL_BOOK_NO_EXECUTABLE_UNIT_PRICE / NO_EXECUTABLE_BOOK`；0.999 不再进入 taker proposal。boot 后 canonical `venue_commands=0`、`venue_command_events=0`、`settlements=0`，因此没有低价退出，也没有新的 realized PnL。
- **deploy 假失败根因:** monitor cadence 已把 stale inputs 分成 `blocking_stale` 与 `quote_only_stale`，并明确规定后者不能成为全局 cadence debt；`deploy_live.py` 仍读取旧的总 stale count。高价/无 bid/venue 不可执行仓位已经被 full global auction 重决策，却会让部署等待八分钟后失败。
- **组合证明修复:** deploy 仅在 blocking stale 为零时考虑 quote-only；若 quote-only 非零，必须再取得本次启动后的 complete global-auction receipt，且 receipt 的 held coverage complete、expected/evaluated/excluded 计数覆盖全部 canonical open positions。没有该 receipt 继续 fail-closed。对当前 live DB 的 worktree read-only probe 为 `fresh_positions=6 + quote_only_positions=5 + held_auction_receipt=415191 => 11 open positions fully covered`。
- **验证:** deploy/cadence suite `85 passed`；其中新增抗体先证明缺 receipt 必须失败，再加入 complete held receipt 后通过。Ruff（保留脚本既有的 post-`sys.path` E402 例外）、`py_compile` 与 diff check 通过。本 gate hotfix 尚未部署；入场 pause 保持。

### 2026-08-12 11:32 CDT tick — 排除不可提交的高价 SELL 假赢家；高置信仓位回到 HOLD-to-1
- **forward 结果不变:** containment pause 后去重 settled cohort 仍为 `-$8.526401`（1 win / 2 loss）；本 tick 没有新 command、fill 或 settlement，不能声称资本利得。
- **部署后证伪:** immediate FAK SELL 的资本释放时钟修复已进入 live loaded tree。Shanghai 20 YES shares 在 `q_mean=0.994666667`、current bid `0.999` 下被统一竞价选为正 expected EV SELL（约 `+$0.075668`），但 executor 按 durable live price law 拒绝 `live_order_executable_price_out_of_bounds: best_bid=0.999`；没有 command 或 fill。
- **第一性判定:** 0.999 是当前 counterparty quote，却不在允许的新订单价格 `[0.05, 0.95]` 内。把 0.999 经济收益映射成可提交 0.95 floor 会在竞态下允许 0.95 成交；对 `q≈0.9947` 的持仓，该最坏合法成交相对 HOLD 为负，正是用户指出的“高买低卖”机制。正确动作不是绕过 executor，而是让不可执行报价退出 feasible set，继续 HOLD-to-1。
- **最小修复:** selector 的 live SELL counterparty、precliff capacity 与 JIT mode 都只接受 `[0.05, 0.95]` 当前 bid；高于 0.95 的 book 不再生成/改善 taker proposal，也不再占用全局 winner。合法带内 SELL、maker-rest、BUY、settlement 和既成链上 fill 记录均不改变。
- **验证边界:** solver 全集加 JIT 抗体 `211 passed`；executor 两条 submit-boundary 抗体 `2 passed`。扩大筛选 `128 passed / 1 failed`，唯一失败为 live baseline 已有的旧 `DummyClient` fixture 在 pre-submit collateral refresh 缺少 `get_collateral_payload`，与本 diff 无关；因此仍不把 declared evaluator 记为 pass。热修复尚未部署，部署后必须以新的 complete-scope receipt 证明 Shanghai 为 HOLD/CASH 或精确的合法替代 winner，且不再出现该 rejected SELL churn。

### 2026-08-12 11:13 CDT tick — held redecision 已恢复；即时正期望 SELL 不再继承过期目标日时钟
- **forward 结果仍未达标:** 自 containment pause 后已结算的三笔去重 cohort 为 Seoul `-$6.63`、Tokyo `+$6.683599`、Tokyo `-$8.58`，合计 `-$8.526401`（1 win / 2 loss）。开仓保持暂停；本 tick 前最近一次 deploy 后没有新 venue command、fill 或 settlement，不能声称资本利得。
- **held truth 活性已部署:** `e0a8d09b9` 让最老 canonical held decision debt 进入固定 primary tranche；官方重启后 monitor cadence 从 9 stale / 2 fresh 恢复到 11 / 11 fresh，loaded SHA 随后被并发 monitor hotfix 推进到 `34cb1d04f`，pause reason 仍为 `single_global_auction_cut_monitor_terminated_no_receipt`。
- **当前反例:** Shanghai 2026-08-12 HIGH `27C YES` 持仓 20 shares，entry `0.07`，current posterior mean `q=0.9951666667`，current bid `0.999`。point counterfactual 相对 HOLD 为 expected EV `+$0.0756677`、expected Δlog wealth `+0.0001519082`，但全局 selector 因目标日本地午夜已过返回 `CAPITAL_HORIZON_NON_POSITIVE`，没有生成 EXIT command。
- **第一性修复:** 天气目标日结束只约束 settlement-locked BUY 与 maker 未成交分支；marketable FAK SELL 的已成交 claim 在当前 executable window 内释放现金，不应继承过期 family horizon。solver 现在以 certificate-bound quote/FAK window 作为 decision-to-release 的保守上界，同时保留 `resolution_at_utc` 作为 family attribution；horizon=0 的 BUY 继续 fail-closed。
- **验证与边界:** solver properties `210 passed`；declared capital evaluator 为 `923 passed / 18 failed`，与 clean live `34cb1d04f` 基线逐项相同，因此没有新增失败，但 evaluator 仍是 red，不能记为 pass。planning-lock、`py_compile`、source Ruff 与 diff check 通过。本修复尚未 deploy；只有部署后新的 complete-scope receipt、EXIT command/FAK fill、canonical reconciliation 与后续 forward PnL 才是结果证据。

### 2026-08-11 18:46 CDT tick — exact-winner settlement lock 已部署；forward 盈利仍待真实成交/结算证明
- **部署事实:** hotfix 已通过官方 `deploy_live.py restart live-trading --allow-unpushed` 入口加载为 `536b41f72`；sidecar identity、restart recovery、monitor cadence 与 EDLI queue progress 均通过。随后 health probe 为 `OK`：daemon/forecast/data/heartbeat 运行，risk `GREEN`，blocking gates `0`，loaded/expected SHA 一致。
- **当前竞价:** 最新 full-scope receipt 覆盖 116 个 family、2081 个 candidate，9 个 held position 中 8 个 SELL 可评分且全部为负 expected EV，1 个没有合法可执行 SELL book；全局 winner 为 CASH，`NO_CURRENT_EXECUTABLE_POSITIVE_ORDER`。9 个持仓的 monitor probability 与 market price 已重新 fresh。
- **forward 资本证明:** 自本 SHA 的 deploy guard 时间 `2026-08-11T23:40:47Z` 起，canonical `venue_commands`、`venue_command_events` 与 `settlements` 均无新行，因此该 cohort realized PnL 严格为 `$0.00`。这证明系统没有为制造订单而高买低卖，但尚不证明资本利得；目标保持 active，后续只用新 command/fill/settlement 与资本曲线证明收益。

### 2026-08-11 18:35 CDT tick — 保留 born-unexitable 防线；仅让 absorbing exact winner 持有到 1
- **当前真相:** `d55ac5b99` 已恢复 full global auction；新 SHA cohort 尚无 venue command / settlement，realized PnL 仍为 `$0.00`，不得声称资本利得已证明。最新完整 receipt 的可评分 BUY frontier 与全部 held SELL counterfactual 均为负，因此 CASH 是当前已评分集合的正确动作。
- **precliff 核验:** 911 个被 `CURRENT_PRECLIFF_LIQUIDATION_CAPACITY_MISSING` 拦截的 exact token 重新抓取完整 CLOB depth；910 个仍低于最小订单退出容量，908 个容量为零。唯一短暂反例的 `0.06 x 100` bid 随后消失，market-channel 与 REST 再次一致。普通 statistical BUY 的 precliff gate 属实，不能为增加订单而删除。
- **窄缺口:** 原 gate 同时拒绝 typed `DeterministicBinPayoffWitness` 已证明当前所买 side terminal payoff 恰为 `1` 的立即成交。这不是 statistical longshot：其正确动作是 settlement-locked hold，而不是低价 SELL；要求当前退出盘反而违反 absorbing hard-fact 与“持有到 1”语义。
- **最小修复:** 仅 `TAKER_LIMIT + DeterministicBinPayoffWitness + exact selected-side payoff=1` 可绕过 precliff sizing/JIT gate。solver 从实际 witness 类型重新证明，JIT 再要求 candidate/current marker 一致、`SETTLEMENT_LOCKED_BUY`、`win_probability_mean=1`、`loss_probability_mean=0`。maker、普通 statistical、unknown deterministic sibling、非 exact decision 全部保留原 gate；伪造 marker 的 statistical candidate 仍 `DEPTH_INFEASIBLE`。
- **验证:** solver 全集 `209 passed`；新/原 precliff + JIT 抗体 `16 passed`；multiwinner `8 passed`；worktree code + live canonical state 的 read-only boot validation `ALL PASS`。完整 integration 的 5 个失败与 live 基线逐项相同，均为此前 precliff/price-band 后未更新的旧 fixtures，不归因于本 diff；不把部分覆盖称为全绿。尚未落地 live。

### 2026-08-11 18:20 CDT tick — stale held truth 只封 BUY，不再饿死全局 SELL/HOLD/CASH 竞价
- **实时反例:** main daemon 与 held monitor 仍活，但 global-auction receipt 在 `2026-08-11T22:56:24Z` 后停止；同期 full-book monitor 因 3 个 `monitor_probability_stale` 持仓反复产生 canonical debt。代码把本应仅阻止新 BUY 的 debt 同时用于 reactor admission/preemption，导致 stale probability 无法自愈时整个 global auction 永久停摆，连可减仓 SELL 和 CASH 比较也不能运行。
- **最小修复:** canonical/bootstrap monitor debt 继续作为 `entry_submit_block_reason` 冻结 BUY；实际 monitor handoff、periodic fairness debt 和 capital-recovery handoff 仍可抢占 reactor。已经 entry-blocked 的 cycle 不再被同一 canonical debt 二次取消，因此 SELL/HOLD/CASH 保持统一比较。若别的 monitor 已占 single-writer claim，选中的 Day0 wake 保持 durable/unacked 并只让出一个 queue turn，使 exact SELL 或独立 material wake 可并发推进；stale 持仓仍不得提供 BUY authority。
- **验证边界:** managed worktree 中 event-reactor 全集 `342 passed`、run-mode failure surfaces `224 passed`、entry-block/Day0 slice `15 passed`、SELL receipt persistence/executor/settlement slice `19 passed`；以 worktree code + live canonical state 执行 read-only boot validation 为 `ALL PASS`。尚未落地 live；已实现盈亏仍为 `$0.00`，本修复只恢复前向决策能力，不冒充资本利得证明。

### 2026-07-27 03:45 CDT tick — Ankara fast observation 从默认排除升级为实测 authority
- **证据窗:** `2026-07-20T07:42:38Z` 至 `2026-07-27T07:42:38Z`，LTAC 同站 WU/METAR 251 个匹配对；rounded delta 的 p99/max 均为 `0°C`，empirical threshold 为 `1°C`，因此可吸收 margin 为 `0°C`。证据只授权同一 settlement station 的 publication-latency advantage，不改变 settlement source。
- **money-path 作用:** Ankara Day0 held probability 与 hard-fact exit 可消费更早发布的 LTAC METAR，不再等待较慢 WU 更新；dead-bin/structural-win 对称法则、plausibility guard、oracle anomaly pause 和未测量城市 fail-closed 均保持。
- **验收:** config station/unit/source contract、threshold/margin、fast source 与 absorbing-boundary exit 均由关系测试覆盖；Manila 继续作为真正未测量的 fail-closed counterexample。

### 2026-07-27 02:53 CDT tick — wealth supersession 触发同 epoch 资本重拍，不再吞掉 statistical SELL
- **live 反例:** Beijing Jul-27 HIGH34 NO 在 held q `0.056667`、可执行 bid `0.08`、edge `-0.023333` 时已满足 local statistical SELL；global auction 也完整覆盖 held obligations，但 winner preflight 发现 `GLOBAL_PREFLIGHT_WEALTH_SUPERSEDED` 后把整批重排推迟到未来 scan，monitor 将该 SELL 覆盖为 `GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE`。Tokyo Jul-27 HIGH30 NO 曾在 q `0`、bid 回升至 `0.06` 时遭遇同一阻断。两笔均没有 EXIT intent、command 或 venue call。
- **第一性修复:** wealth gate 保持 fail-closed；local statistical SELL 仍不得绕过 global BUY/SELL/HOLD/CASH optimizer。若 submit-side preflight 证明经济 endowment 已变化，当前 batch 立即重读 canonical wealth/portfolio，要求所有新 held obligations 仍被当前 probability+book cut 覆盖，然后用新 endowment 重算完整 argmax。position/book scope 变化或连续 supersession 仍终止该 cut 并交给下一完整 epoch。
- **可观测性:** supersession receipt 记录 expected/current economic identity；同-epoch重拍记录发生变化的 wealth fields。禁止把 freshness-only ledger heartbeat 当经济变化，也禁止复用旧 shares、reservation、cash 或 position set。
- **验收:** antibody 必须证明 wealth-1 选出的 winner 在 zero-side-effect preflight 被 supersede 后，以 wealth-2 重算并可选择/提交新的最优 action；普通 unknown authority 仍 batch-block；连续/无进展变化有界终止；完整 global-auction 与 event-reactor wealth suites 通过。部署后 Beijing34 若仍有可执行正回收且 SELL 为全局最优，必须出现新的 auction winner/EXIT command 或精确的新经济拒绝原因，不能再只留下 generic monitor HOLD。

### 2026-07-24 19:55 CDT tick — Day0 SELL 的 posterior mean 不再被 point estimate 冒充
- **live 反例:** Hong Kong Jul-25 LOW28 NO 的同一 current witness 在 `00:50Z` 同时携带 `held point_q=0.9977` 与 500-draw posterior mean `0.7127`；held monitor 正确报告 `0.7127`，global auction 却把 `point_q` 传给标记为 `POSTERIOR_PREDICTIVE_MEAN` 的 SELL action law，因而在可执行 bid `0.73` 错误拒绝退出。该 split-brain 来自 solver 概率 functional 的语义实现，不是 cache、quote 或香港定制数据问题。
- **第一性修复:** Day0 statistical SELL 的固定动作期望效用/EV 使用 current probability witness 对 exact held payoff 的 draw mean；frozen point estimate 仅保留为 identity-bound counterfactual telemetry，不再支配 live action。BUY robust admission、non-Day0 lower-CVaR SELL、same-family endowment、JIT book/fee/depth、price band、RiskGuard、lifecycle 与 settlement authority 均不改变。
- **验收:** relationship antibody 必须覆盖 `point_q=0.9977`、draw mean `0.7127`、bid `0.73` 并选出正 EV SELL；相同 draw mean、不同 tail shape/point estimate必须产生相同 expected SELL economics；旧 Cape Town reversal、global BUY/SELL/CASH ranking、fill-prefix 与 execution tests 保持通过。部署后要求新 full global auction 对 HKO exact position 使用与 monitor 一致的 held probability mean，若经济性仍为正则出现真实 SELL command/fill；不能用手工强卖代替证明。

### 2026-07-18 19:42Z tick — quote refresh 不再占用 WORLD writer
- **新地图对应关系:** held/candidate REST quote refresh 只生成 TRADE-owned executable evidence；derived `EDLI_REDECISION_PENDING` 已在 quote commit 后通过 independently coordinated WORLD sink 写入。把两者继续绑在同一 attached connection / `world_trade` gate 没有 settlement 或 atomicity 需求，只会让一个慢 quote chunk 阻塞无关 market event、reactor claim 和 ingest commit。
- **运行态归因:** `price_channel_market_event deferred: WORLD writer busy for 25ms` 发生在进程内 WORLD mutex acquisition，证明同进程某条 WORLD gate 正占用 mutex。held/candidate refresh 仍周期性获取该 mutex，尽管它们的 quote rows 从不写 `opportunity_events`；这是可直接删除的全局耦合。live daemon loaded SHA 仍为 `8f7d7d962`，所以此归因来自当前旧路径，修复尚未 live。
- **隔离修复:** 两条 refresh 改用 canonical TRADE connection、TRADE-only coordinator gate 和 unqualified TRADE schema；`MarketChannelIngestor(None, feasibility_conn=...)` 明确表达 quote-only capability，遇到非 quote WORLD event 立即拒绝。删除已无调用者的 `world_trade` gate/scope。WORLD redecision 保留原独立 bounded writer，失败只积压对应 derived events，不回滚已提交 quote evidence。
- **验证:** quote-only authority/fault-containment 定向 `9 passed`；held/candidate/open-rest 真实 DB fixture `4 passed`；完整 INV-37 lane `25 passed`、market-channel `86 passed`、price-channel lift `78 passed`。下一步在自然 reload 后复采 WORLD mutex backpressure 与 reactor claim bounce；若仍有长占用，只剩 `price_channel_market_event`/`price_channel_redecision_emit` 等真实 WORLD owners，不再被 quote refresh 污染。

### 2026-07-18 19:26Z tick — reconcile 慢读退出 WORLD writer 临界区
- **运行态锁证据:** `edli_user_channel_reconcile` 在 20:08/20:10/20:15 本地周期分别占用约 69/55/97 秒；同窗 reactor `BEGIN IMMEDIATE` 连续报 `database is locked`，一次 event 被反复 lock-bounce。后续只读计数证明 live-order events 仅约 15k、commands/trade facts 约 1.1k，authenticated WS/REST bridge 查询不是这些几十秒的计算来源；长 duration 主要是等待另一个 writer。此前“10M+ historical bridge query”假设被证伪。
- **第一性错误:** cycle 处理 inbox 后已打开 WORLD 写事务，却继续读取 external reconcile evidence，并在同一事务内串联两个重历史 bridge scan。外部/历史 I/O 既不需要 writer ownership，也让一个 reconcile lane 的慢读阻塞全部无关 opportunity claim。
- **隔离修复:** inbox、venue fact apply、WS-confirmed bridge、REST-orphan bridge 现在分成四个独立 commit phase；仅有 pending aggregate 时才加载 reconcile evidence。external reader/单 aggregate failure 只跳过依赖项；网络/文件结果返回后重新验证 `pending_reconcile`，仍由原 aggregate state machine + SAVEPOINT 应用。未改变 fill authority、event hash、projection CAS 或 cross-DB bridge。
- **验证与边界:** 事务抗体先制造 inbox write，再证明 external reconcile、confirmed scan、rest scan 三个边界均见 `in_transaction=False`，且最终 projection 正确进入 `RECONCILED`。现有 cycle fixtures 同时改为准确 patch WORLD 主连接与独立 trade bridge 连接。当前 daemon loaded SHA 仍为 `8f7d7d962`，本修复尚未 live；phase split 仍缩短实际 writer ownership，但不再进行无价值的 bridge incremental rewrite，下一步转向真实 mutex owner。

### 2026-07-18 19:07Z tick — urgent Day0 fact 可抢占并行 book discovery，不再等待慢 CLOB teardown
- **第一性阻塞:** global batch 外层只在 book provider 返回后检查 urgent cancellation；provider 内部并行 Gamma/CLOB 使用等待式 `ThreadPoolExecutor` context。新的 deterministic extreme 即使已提交，也可能继续等待无关 CLOB request 与 executor teardown，期间 targeted held SELL 无法取得 reactor handoff。
- **隔离修复:** book epoch 在 DB、metadata、prefetch、capture 边界检查同一 durable Day0 wake revision；并行 CLOB 等待每 25ms 探测一次。urgent fact 到达后返回 `epoch=None`，外层沿既有 `GLOBAL_SELECTION_CANCELLED` fail-closed 路径 requeue；已运行 public-book request 按自身 bounded HTTP timeout 收尾，但不再持有 global decision lane。urgent monitor 同时从零等待改为最多 1 秒 cooperative handoff：reactor 一释放就直接接管，超时则清除 priority claim，避免原路径失败后至少再等约两轮 wake poll。未绕过 entry/exit actuation lock，未改变 q、book、risk 或 submit authority。
- **故障注入:** CLOB worker 被人为挂起、Gamma bind 同时提交新 Day0 revision；provider 在 `<0.5s` 内返回，book capture 为零，worker 释放后正常结束。另用真实 lock 证明 urgent monitor 会在 cooperative release 后同一 attempt 内运行。exit-monitor 关系集 `12 passed`，正常 forecast Gamma/CLOB overlap 与 selection cancellation 定向集 `4 passed`；扩大集另暴露 3 个既有 Day0 overlap 断言与当前“无 speculative token 时先 Gamma 后 CLOB”实现不一致，本 diff 未改变该分支，单独保留为后续吞吐边界。
- **运行态边界:** daemon 当前 loaded SHA `8f7d7d962`，不含本 tick 工作树改动；未手动重启。下一步缩小 `_edli_reactor_active_lock` 的 ownership：discovery 只做 cooperative cancellation，只有 submit/canonical transition 保留必要串行化。

### 2026-07-18 17:36Z tick — posterior-starvation enrichment 从 117 次目录扫描降为 1 次
- **故障牵连证据:** `live_health` 一轮报告 117 个 starved families；每个 family 都对 53,301 个 failed receipts（620MB）单独 `glob`，observability 因此反复遍历同一目录，并与 fact-to-action 热路径争用 I/O。
- **隔离修复:** 一次 `os.scandir` 建立 requested family→newest receipt 映射，只读取每个 family 最新 JSON；保留逐 family reason 与 ERROR 告警，监控仍不是 entry gate。当前目录 117 scopes 全量 batch `0.155s`；旧式 10-scope glob 已需 `0.733s`，按 scope 归一约 `55.16x`。
- **验证与边界:** posterior-starvation suite `13 passed`，新增 antibody 要求两个 starved families 也只能扫描目录一次。未修改/清理 620MB evidence，未重启 daemon；当前 loaded SHA 尚未包含修复。
- **下一突破口:** starvation SQL 仍对 15,853,507-row `market_events` 做无 target-leading index 的聚合 join；下一步先把两侧按 family 独立聚合，消除历史行乘积，再评估是否需要 compact current-family projection。

### 2026-07-18 17:31Z tick — speculative book 读取退出千万行历史表
- **运行态证据:** 11-family global batch 的 `prepare_families=13.126s`、`book_epoch_fence=34.785s`，整个 `process_pending=53.084s`；同一时段 urgent exit monitor 等 reactor 30 秒后超时。book epoch 内 Gamma/CLOB 阶段仅约 2–4 秒，剩余时间发生在 trade-DB topology/cache 读取与 I/O contention。
- **第一性冗余:** speculative prefetch 只决定提前抓哪些 books，却通过 `executable_market_snapshot_latest` 回表读取 10,203,966 行的历史 `executable_market_snapshots`；历史 evidence 不应参与稳态 I/O hint。改为只读 27,620 行 latest projection，当前 Gamma/CLOB 继续独占 tradeability、book 和 submit authority。
- **同库对照:** 同一 121 condition，latest-only `0.000778s`，旧 latest→history join `0.044176s`，约 `56.81x`；11 个 speculative topology/prefetch tests 通过。未复制/修改 live DB，未重启 daemon；loaded SHA 仍旧，等待自然 reload 后才能确认 53 秒 tail 是否下降。
- **下一突破口:** `prepare_families` 在同一轮被放大到 13 秒；继续把 per-family forecast/readiness reads 批次化，并在 Gamma/CLOB/DB 阶段边界加入 urgent-fact cancellation，不绕过 actuation lock。

### 2026-07-18 13:58Z tick — 目标升级为 edge-reversal + fault containment；wake 状态读从历史扫描改为批次主键查
- **新地图:** 唯一计时从新 causal fact 的 ingest commit 开始，到其受影响 BUY/SELL/HOLD submit 为止。forecast reversal 使用百秒窗口；deterministic observation reversal 优先 held SELL 和 exact complementary BUY。平均 cycle 速度不是验收。
- **隔离约束:** source/city/family/event/candidate/request/query/command 任一处失败或阻塞，只能影响依赖它的动作；pre-submit deadline/retry 必须局部化，外部 side effect 开始后转入 must-complete settlement/reconciliation，不占全局 discovery reactor。
- **今天的运行态根因:** `state/zeus-world.db` 约 81GB，`opportunity_event_processing` 实读至少 10,837,406 行。100-event wake 的状态 SQL 因 `consumer_name + event_id IN (...)` 被 planner 选成仅按 consumer 的覆盖索引扫描；原查询 2,000ms 后仍未完成，live `edli-reactor-wake` 线程采样几乎全在 `sqlite3_step`。
- **修复与实测:** 改为 `VALUES` 驱动的 `(consumer_name,event_id)` 复合主键 join；同一 live DB/同一 100-event batch 连续 20 次 median 2.50ms、近 p95 2.98ms、max 3.92ms。query-plan antibody 要求复合主键同时约束两列；`tests/test_forecast_live_daemon.py` 80 passed。未复制/修改 live DB，未重启 daemon。
- **故障域解耦:** targeted Day0 exit monitor 不再同步占用 wake listener。每个 attempt 由 `wake_id` 唯一拥有；同 family event 等待 monitor 完成后再做 complementary BUY/HOLD，保持 SELL→BUY exposure 因果顺序；pending/刚失败的 monitor wake 在本地 selection 中被跳过，独立 price/forecast wake 可继续 drain。原 durable wake 只在 monitor+event 都完成后 ack，进程重启仍会恢复。已验证 slow monitor 阻塞期间独立 market wake 可执行并 ack；新 Day0 未被 attempt 接管时仍会抢占。wake suite 82 passed、event reactor 97 passed、exit-monitor 锁契约 7 passed。
- **wake backlog 读取完成:** durable queue 当前 909 文件；旧实现每 poll 的 read+coalesce 约 36ms，且重复解析全部 JSON。immutable-file 增量 cache + directory/legacy-pointer revision 让冷启动只付一次约 22ms，稳定 read median 0.086ms、coalesce median 0.765ms，约 42x；新增/删除才刷新，malformed file 不再每 poll 重复耗时。wake suite 83 passed。
- **下一突破口:** processing 历史债和 81GB DB 仍可能放大 ingest 写成本与 planner 误选风险。继续定位 active set、历史 channel processing debt、索引维护和无界 health/maintenance reads；不做盲目 live cleanup。

### 2026-07-17 00:10Z tick — deterministic dead-token SELL 脱离全局拍卖；JIT book hash 自拒绝已修
- **严格占优退出:** `DAY0_EXTREME_UPDATED` 已先唤醒受影响 family 的 targeted exit monitor，但旧代码仍把 exact terminal value=0 的 held token SELL 委托给 107-family global auction。现在只有 absorbing `EXIT_DEAD_BIN` 直接生成 `urgency=immediate` reduce-only exit；canonical monitor write、fresh executable bid、submit gate、现有 `execute_exit` 和 lifecycle 仍全部保留。statistical SELL 继续只由 global auction actuation，互补 BUY 继续参加 BUY/HOLD/CASH 全局比较。
- **为什么不需要全局排序:** 对结算价值严格为 0 的 held token，任何扣费后正现金回收都逐状态严格优于 HOLD；等待 unrelated family probability/book/wealth 只能损失残值，不会产生更优的保留理由。
- **第二个 live blocker:** exact-HEAD `61a4ce8b` 运行后不再出现 `CURRENT_WEALTH_OPEN_POSITION_INVALID`，说明 confirmed-fill projection 已收敛；阻断前移到 `GLOBAL_JIT_SNAPSHOT_BOOK_HASH_INVALID`。第一次修复只覆盖 final JIT re-fetch，`383da83f9` live 复验仍失败，证明 selected curve 在更早的 global book epoch 已携带 venue opaque hash。现已把完整 raw-book canonical SHA-256 统一到 global BUY/SELL epoch 和 final BUY/SELL JIT 两个边界，不放宽 snapshot authority。
- **验证:** Day0 hard-fact + live SELL ownership `58 passed`；source-wake→targeted-monitor ordering/fail-closed `5 passed`；global winner/JIT preflight/depth binding 初始 `4 passed`，前边界补齐后 global-book epoch 扩展集 `13 passed`。待 follow-up commit 由 daemon 自动加载后，复验 `GLOBAL_JIT_SNAPSHOT_BOOK_HASH_INVALID` 消失和 targeted hard-fact exit 的 live receipt。

### 2026-07-17 00:01Z tick — 目标重对齐到 ingest-to-submit alpha clock；Day0 重复抓取已删除并 live
- **资本目标更新:** 不再以平均 cycle、SQL 数或订单吞吐作为终局。唯一热路径是 `source available -> ingest commit -> current q -> current book -> risk -> submit`；forecast reversal 使用百秒级市场窗口，deterministic observation reversal 必须先处理 exact held SELL 和互补 BUY，不能等待无关全市场重建。
- **已完成突破口:** commit `1b4af08a5` 已由 ingest/main 自动重启加载。AWC METAR HTTP 现在只由 5 秒 ingest source clock 拥有；reactor 删除重复网络抓取，只增量读取 canonical observation ledger；WU-vs-METAR anomaly guard 复用 ingest cache 并独立调度。最终 focused coverage `146/146` 通过。
- **live 证据:** source clock 在 `00:01:02Z` 持久化 4 个新 report 并发出 2 个 Day0 event；reactor 冷启动同步 3,106 个 retained ledger rows 仅约 `0.24s`。后续 targeted Day0 wake 的 probability prepare `0.67s`、family-delta book epoch `1.50s`、总 process_pending `2.28s`，证明受影响 family 的增量路径已存在。
- **当前资本阻断:** 同一批 action 在 wealth binding 被 `CURRENT_WEALTH_OPEN_POSITION_INVALID` fail-closed；当时 chain projection 尚未给新 confirmed fill 完整 shares，随后 canonical reconciliation 已形成 4 个 token/shares 完整的 runtime-open positions。HEAD 的 `d32ac1483` 修复 matched-submit 后 confirmed-fill bridge，但尚需下一 exact-HEAD runtime cycle 证明该阻断消失。
- **下一突破口:** deterministic observation event 当前仍可能被正在运行的 complete global auction 占用到约 18 秒。将其改为 alpha-expiry priority：先对受影响的 held position 做 exact current q/position/BID/risk preflight，再执行 reduce-only SELL；只有互补 new-risk BUY 才进入受影响-family auction。禁止绕过 venue/JIT/risk/settlement authority，也禁止复用 stale q/book。

### 2026-07-15 23:00Z tick — Codex pause 归因纠正；Seoul Day0 单模型退化根因与当前多模型可用性证明
- **pause 归因:** active canonical `entries_paused=true` 是 Codex 在错误下单后的 live-money containment，不是用户/operator 指令；当前 reason=`codex_live_money_containment_after_bad_orders`。本 slice 保持暂停，不解除、不强制下单。
- **当前根因:** Seoul Jul-16 held-position redecision 的 `finite_evidence_member_count=1`；canonical `day0_hourly_vectors` 只有 `ecmwf_ifs`，使 Day0 q/SELL robust band 退化为单模型证据。
- **当前能力证明:** 对同一 Seoul/Jul-16 endpoint 的只读 live fetch 同时返回 `ecmwf_ifs`、`icon_global`、`jma_msm`、`ukmo_global_deterministic_10km` 四条 48-hour 曲线；remaining highs 分别为 26.4/28.6/25.2/25.8°C。单 member 是模型选择缺陷，不是当前数据不可得。
- **本 slice scope:** `src/data/day0_hourly_vectors.py` + `tests/test_day0_remaining_day_pricing.py` + owning registry `architecture/source_rationale.yaml`。把 global Day0 hourly fallback 从 ECMWF 单模型改为当前多模型 bundle；仍要求完整同 epoch bundle，缺任一 expected model 就不授权 Day0 q。source role 仍是 forecast-only，不触碰 settlement source，不复制 canonical DB。
- **验收:** antibody 先证明旧实现失败；修后 targeted tests + capital evaluator；在 entries paused 下标准 deploy，等新 bundle 被 canonical writer 持久化后，要求新 monitor/auction receipt 的 `finite_evidence_member_count>=4`，再比较 Seoul BUY/SELL/HOLD/CASH。没有新 receipt 就不声称资本最优。

### 2026-07-15 18:19Z tick — BUY NO 真实结算 +$19.26；修复 A8/A9 语义冲突和 chain-mirror 结算吞吐
- **实际资本证明:** Wuhan Jul-15 38°C `buy_no` 持仓 `fbeac91f-e9d` 已被 Gamma 确认为市场 NO 结算；canonical `position_current` 为 `settled`，100.00621 shares，cost basis ≈$80.75，realized P&L **+$19.26**。redeem command 已有 100006210 micro-pUSD intent；当前还没有 confirmed redeem transaction，不把 intent 冒充 chain cash realization。
- **新根因:** `position_settled.v1.won` 在 harvester 表示“该 binary market 的 YES bin 是否结算”，在 chain-mirror 却表示“持仓是否赢”。BUY NO 恰好取反，使 raw audit 可以把真实赢单评成输单。P&L 和主学习路径用 `outcome/pnl` 未被翻转，但审计证据被污染。
- **修复:** 所有新 canonical settlement 显式区分 A8 `market_bin_won` 和 A9 `position_won`；`direction + outcome` 作为可派生持仓语义；显式字段冲突的 row fail-closed，不进 metric/learning。不改写 canonical DB。
- **throughput 修复:** chain-mirror 把 canonical DB phase `active` 错传给 runtime-state adapter，导致合法结算变成 `unknown` 并被 per-row isolation 静默跳过。现在直接通过 canonical lifecycle fold 验证 `active/day0/pending_exit/economically_closed -> settled`。
- **验证:** capital evaluator **568 passed**；settlement/chain-mirror 扩展集 **161 passed**；chain-mirror 全文件 **41 passed**；A8/A9 定向 **7 passed**；audit 定向 **2 passed**；close-economics **4 passed**。仓库旧全量集仍有与本 diff 无关的 stale-fixture/linter 失败，未伪装为 clean pass。
- **交易姿态:** Codex live-money containment `entries_paused` 保留；本 tick 未强制下单、未复制 DB。最新完整 auction 中 YES 路径存在但当前候选的 robust-majority economics 为负；三个正候选均为 BUY NO，三个已有仓位 SELL 均为负 robust EV/ΔlogW，故 HOLD。

### 2026-07-08 08:36Z tick — **真指标浮现:系统在亏钱,且亏损隐形。** 给真实结算成交打分(非回测):近期净负;根=多平仓路只两路入账
- **地面真相:** 预报健康(08:31Z,近30min 170 条),venue_cmd 仍冻 19:00Z,POISON 0。在手 3 仓:Paris(07-08 到期,信念 1.0)、Ankara/Wuhan(07-09,0.83/0.85)—— 看着会赢。
- **核心发现(给真成交打分 = 循环该做的 SURVEY,forward,非回测):**
  - 最近(07-03 起)未入账的已结算仓:**打分 12 个,5 赢 7 亏,净 ≈ −$31**;买 NO 胜率 42%(需 ~60% 才不亏)。
  - 独立对照:07-01 起已入账仓净 **−$25**。两个独立数字都负 → **系统在亏钱,不是空转。**
- **亏损为何隐形(入账 bug 定位):** settlements 表**抓到了**结算(Chicago/Tokyo/Helsinki 07-06 赢家 bin 都在),但这些仓 `settlement_price=0.0`(非真实温度)→ 走了**不入账的平仓路** → 104/169 已结算仓 realized_pnl 记 0。会入账的 `chain_mirror._apply_settlement_finding` 是对的(Bug B 已修 line 706),但别的平仓路没修 = **R0-a「五条平仓路只两条算 realized P&L」**。
- **判断:非 pass。真问题 = 策略在亏(样本小 n=12 但信号清楚)+ 亏损隐形(看不见指标)。不是「订单太少」。**
- **下一步(都是 money-path,认真做,不回测):** ①修入账管线——让所有平仓路都算 realized_pnl(先能看见钱,才能改)。②看得见后查为什么亏:买 NO 选市/校准是不是系统性错。回滚点 9a902ef78。

### 2026-07-08 07:41Z tick — forecast blackout 修复后,真 no-orders 根因**干净隔离 = q_lcb 保守边闸**(非网络/collateral/forecast/pause)
- **地面真相(lightweight survey):** POISON 0、HEAD 2b436160d、daemon 活。**beliefs 全鲜**(posterior_latest 07:26Z,250 新/30min = forecast 管线全愈)。**network 健康**(clob 0.76s/200、data-api 0.78s/200、google 0.27s = 无 TLS 超时)。**collateral snapshot 鲜**(captured_at 07:38Z)。**entries 未暂停**(override 21:10Z restart-guard 已 01:51Z 过期)。**venue_cmd 仍冻 19:00Z(12.6h)。**
- **关键隔离:** reactor **正活跃 evaluate**(spine last 07:38Z ≈1min 前;keepalive/requeue tick current)。`SELECT_GATE_DIAG n=13 exec=13 dir=13 coh=13 **edge=0** du=0 min=0 live=0` → 13 候选全过 exec/dir/coh,**0 过边闸** → 0 可提交 → 0 单。「SUBMIT」log 行 = `_edli_pre_submit_jit_keepalive` tick 误配,**非真提交,无 submit bug**。
- **判断:非 pass,但根因干净隔离。** venue_cmd 冻 19:00Z **早于** forecast blackout(02:06Z)7h → 原始 no-orders **非** forecast/网络/collateral/pause(本 session 全已清/愈)→ **= q_lcb 保守边闸(3x haircut)**。far-tail YES 被正确拒(诚实);真 mid-NO 边今日几乎不供给。**边仍真**(2026-06-22 +0.166)—— Rule-1:被保守分位数门控,非 absent。
- **本 hour 无 settled-EV 可动(诚实):** edge=0 + 候选多为 far-tail → 无 fill 可能 → data-gated。可动杠杆均需外部输入:①**q_lcb 交易分位数 = 操作员风险姿态**(上 tick 已 classify=LEGIT,待其定)②OOF thin-cell 修(非风险姿态,但助未来 mid-NO 供给、非今日 far-tail mix)。**绝不为凑单松边闸。**
- **下一步:** 待操作员分位数决定;或其一句话我做 OOF thin-cell 修(直接 inline edit,非 ceremony)。回滚点 9a902ef78。

### 2026-07-08 06:36Z tick — forecast outage 深查(read-only trace,执行非询问):persisted manifests 全 MATCH,drift 在**materialize 路径的 fresh artifact**;operator-domain,需其 deploy
- **地面真相:** 系统仍盲 —— posteriors 02:06Z(**4.5h**,30min 内 0 新)、venue_cmd 冻 19:00Z(11.5h)、0 fill、HEAD 2b436160d 未变(operator 未 deploy 修)、POISON 0、open 2 active+1 day0。forecast-live 仍每 5min 材料化全败(last 06:31Z Manila/Milan 07-10 byte_size mismatch)。
- **read-only trace(我执行了,没停下问):** 追 seed→manifest→artifact 链:**persisted raw anchor manifests 全部 MATCH 其 artifact**(唯一 mismatch 是 6 月旧 artifact 已清盘,ARTIFACT_MISSING);Wuhan 07-09 manifest 4925=4925 OK。→ **磁盘上的 manifest 无 drift。** 故 `byte_size mismatch: expected 4923 got 4924` 是 materialize 时**新建 artifact**(current-target 07-09/07-10 seed 处理)差 1-2 字节 = write 与 byte_size 计算的**序列化不一致**,在 committed meta-stamp/current-target 路径。
- **ROOT CAUSE 确诊(操作员令我修,深追到底):trailing-newline manifest drift。** 失败 seed 引用的 Manila anchor manifest(`raw_manifests/...20260707T180000Z.2a6f324efabe.Manila.manifest.json`)pin `byte_size=4923`,实际 artifact file **4924**(+1,sha 也 mismatch)。**reserialize 铁证:`json.dumps(payload,indent=2,sort_keys=True,default=str)`=4923(无尾 "\n"),`...+"\n"`=4924(有);文件有 "\n"(尾 `...28800\n}\n`),但 manifest 的 byte_size 从无-"\n" 形式算的。** `_write_json`(download:155)写 artifact **带** "\n"(commit e2cd7a9bc 2026-06-24 加的);某处 manifest byte_size/sha 从**不带** "\n" 的序列化算 → verify_artifact(raw_forecast_artifact_manifest.py:171-176)每 current-target 必炸 → 0 posterior。
- **判断:非 pass,但 binding constraint 已确诊为具体可修 bug(非模糊「operator domain」)。** 系统盲 4.5h 的根 = artifact-write 与 manifest-byte_size 计算的 trailing-"\n" 序列化不一致。
- **EXECUTE:已派 fork implementer `manifest-newline-fix`(worktree+TDD+verifier,plan mode)** 定位精确不一致行 + 修(manifest byte_size/sha 必须描述磁盘真实字节,canonical=带 "\n")+ TDD + boot smoke。**diff 排队待操作员 approve+deploy(我无 deploy 权)。** 附:现存 stale manifests(4923)修码后是否下 cycle 自动 re-pin vs 需一次性 re-pin,implementer 报。
- **✅ RESOLVED(本 tick 内修复,操作员令我 drive):** implementer `manifest-newline-fix` 交付 worktree `fix/forecast-manifest-drift-repin` @ `e73fa291b`(4 文件,+316/−3;TDD 3 pass;boot smoke ok;零新测试失败)。诊断修正(fork 纠我):**非双序列化 bug —— byte_size 仅从 stat 一处来**;真根 = **stale-manifest desync**(artifact 被重写加 "\n" 到 4924 后 manifest 未重建,download reuse guard 跨 cycle 携带 drifted artifact 不 re-manifest)。
  - **PART 1(即时 unblock,我独立 dry-run 验证后 --apply):** `scripts/repin_stale_forecast_manifests.py` dry-run 确认 **15,297 manifests、8 drifted、全 8 valid JSON、0 corruption suspect**(仅良性尾 "\n")→ `--apply` re-pinned 8,0 error。**posteriors 立即恢复:02:06Z→07:21Z,06:55Z 起 133 新 posterior 跨 44 城**(pipeline 广域自愈,40 missing-manifest 亦随之补上)。drift now 0。
  - **PART 2(durable guard,queued diff 待操作员 deploy):** `write_manifest_to_db(repin_on_drift=)` + download 复用路径 drift 检测 re-pin(missing artifact 仍 raise = corruption 守卫不破)。**未部署 → 少量 target(Milan)仍间歇 re-drift**;guard deploy 后根治。
- **判断:非 pass 但 #1 operational 绑定约束(系统盲 5h)已解 —— beliefs 重新流动。** re-pin 是 data 修(整 metadata 匹配 valid artifact,非动 money ledger,可逆),我独立 dry-run 验安全后执行。
- **下一步:** ①操作员 approve+`deploy_live` PART 2 guard(防 re-drift 复发)②beliefs 已鲜 → **下游 money-path 重新相关**:q_lcb 保守度分位数(风险姿态,待操作员)+ OOF thin-cell(可修)。③验 fresh beliefs 后 reactor 是否出单(q_lcb 保守度仍是 pre-existing 限流)。回滚点 9a902ef78/1341967a8;re-pin 可逆(git manifest files + 重算 byte_size/sha)。


### 2026-07-08 06:15Z tick — **确认测试推翻我的 "leans bug":q_lcb 3x haircut = LEGIT 保守(真实 center 不确定),非 bug。主杠杆=风险姿态(交易分位数);修 double-count 会更糟。子杠杆(OOF/M3/M4)仍可修**
- **地面真相:** HEAD 2b436160d、POISON 0、venue_cmd 冻 11.2h、**posteriors 停摆 4.1h(02:06Z,未愈=forecast 管线疑卡,operational flag)**、open 2 active+1 day0、0 新 fill/settlement。
- **确认测试 1(model disagreement,read-only,操作员授权的 classify 步):** raw_model_forecasts 每 cycle 模型 forecast_value_c 的 spread:median **0.75°C**(mean 0.81,p90 1.47,median 3 模型/组)。served center_sigma 0.91°C = **1.21x disagreement**。→ **center_sigma 与真实模型分歧一致,非 inflated。** 推翻我 05:55 的 "center_sigma 由 predictive-residual 过大" 假设。
- **确认测试 2(数值模拟 buggy vs 'fixed' bootstrap,μ*=26.98/pred=1.62/cen=0.91):**
  - **发现真 double-count:** bootstrap(materializer:2537-2560)draw center@0.91 **且** 每 draw 用 predictive=1.62 积分,而 `predictive=sqrt(center²+resid²)`(:1847)**已含 center** → draw-mean effective σ=1.86≠predictive。自洽检验:peak bin buggy draw-mean 0.212 ≠ q_point 0.242;'fixed'(用 conditional σ=sqrt(pred²−cen²)=1.34)draw-mean 0.242 = q_point ✓。
  - **但 'fix' 让 q_lcb 更低(fixed/buggy=0.23-0.93x 各 bin)** —— double-count 实际在**缓解**抑制,非造成。修它 = q_lcb 更低 = 流更少。**故 double-count 是真内部不一致但非抑制杠杆,修反害。**
- **判决(修正 05:55,restate fresh):q_lcb 3x haircut = 大体 LEGIT 保守** —— center 不确定 0.9°C 真实(=模型分歧),q_lcb 是诚实的 p05 下界。**非校准 bug。** 我 05:55 "leans bug" 被确认测试推翻;若当时盲修 double-count = 抑制更糟。**"classify first" 救了一个错修。**
- **Rule-1 合规:** 边**仍真**(settled+OOF corpus 证 mid-NO realized 0.68-0.81)—— 被**保守分位数选择(p05/alpha=0.05)门控**,非 absent。主杠杆 = **交易分位数/alpha = 风险姿态(操作员)**:用更高分位(如 p15/p25)放行更多真 +edge、留部分保守;forward-validate 小额。非盲松门 —— 是按 settled 证据调保守度。
- **仍可修子杠杆(非风险姿态,独立):** ①OOF thin-cell ABSTAIN 硬砍 43% mid-NO cells(pool thin/保守 floor 替 hard-ABSTAIN);②M3 delta_u_at_min=0 lo-stake ValueError;③M4 NO-tail 非对称(guarded_payoff_q_lcb 是否已 wire)。M3/M4 待验(verifier flaky)。
- **【投查 (c) 结果 — 本 tick 最紧急发现】forecast 管线 100% 失败 = 系统盲 4h+,over-determines「无单」:**
  - forecast-live(pid 52224,自 21:11Z Jul7 未重启)apscheduler 每 5min 跑「successfully」但 **materialize 全败**:`processed_count:0`。两故障:①**40/42 targets seed discovery 失败** `REPLACEMENT_SEED_DISCOVERY_REQUIRED_MANIFEST_MISSING`(只 Manila+Milan 得 seed);②这 2 个 materialize 失败 `artifact byte_size mismatch: expected 4923 got 4924`(Manila)/`4920 got 4922`(Milan)—— 逐 cycle 确定性差 1-2 字节。
  - 根:`src/data/raw_forecast_artifact_manifest.py:172-173` `if actual_size != self.byte_size: raise ValueError` —— manifest 钉死 artifact 精确字节数(`path.stat().st_size`),差 1 字节即 hard-fail。上游 provider artifact 尺寸变 1-2 字节(如温度值多一位)即炸。+ 40 targets 缺 manifest(下载缺/网络)。
  - 另:`ANCHOR META-STAMP MISMATCH`(cycle 07-07/07-05/07-03,max_abs_delta 达 2.9°C)flagged 「requires operator review」= 独立 lineage 完整性问题。
  - **posteriors 自 02:06Z 死、确定性(非 fail-soft、不自愈)。系统 4h+ 用陈旧信念;reactor 仍 evaluate 但无新信念 → 即使 q_lcb 完美也无新鲜 belief 可交易。这是当前 #1 operational 绑定约束,盖过 q_lcb 讨论。** 属 forecast money-path + 操作员正在提交的 manifest 工作域(近 commit:align current-target manifest horizons / admit meta-stamped horizons / reseeds)→ **不擅自修,紧急呈操作员。**
- **判断:非 pass。两大发现:(1)q_lcb haircut classification=LEGIT(风险姿态);(2)【紧急】forecast 管线 100% 失败=系统盲 4h+,byte_size manifest 脆性(raw_forecast_artifact_manifest.py:172)+ 40 缺 manifest。** 下一步:**①紧急呈操作员 forecast outage(阻塞一切,需其定 —— 属其 manifest 工作域)**;②q_lcb 风险姿态(分位数)待操作员;③OOF thin-cell 修可 scope。回滚点 9a902ef78/1341967a8。

### 2026-07-08 05:55Z tick — 操作员令 classify bug-vs-legit → **判决 LEANS BUG(variance mis-decomposition),机制定位 materializer:1804-1822;需 1 确认测试(已被 06:15 推翻)**
- **read-only trace 完成(操作员选 classify-first):** center_sigma(=anchor_sigma_c,驱动 q_lcb `fused_center_bootstrap_p05`)在 `src/data/replacement_forecast_materializer.py:1804-1822` 计算:`sigma_m=max(1.0, stdev(model.residuals))`(residual=模型 forecast−realized=**预测误差,含 intrinsic 天气方差**),再 `center_sigma=sqrt(Σ(w_m·sigma_m)²)`(= 加权均值标准误公式)。
- **判决 = LEANS BUG(variance mis-decomposition):** intrinsic 天气方差**跨模型共享**(实际天气唯一),平均**不缩减**;但公式把含 intrinsic 的 total predictive error 当每模型独立估计噪声、按 sqrt(N) 缩 → 把 intrinsic 误算进 center/parameter uncertainty → center_sigma(median 0.91°C)**过大**(真 center 不确定 应≈模型分歧/idiosyncratic,通常 <0.5°C)→ q_lcb bootstrap 过宽 → **q_lcb ~3x 过低**(settled + OOF corpus 已证)。q_point(用 predictive 1.62°C)不受影响=校准=与 z-test(STD 0.846)一致。
- **诚实边界:** 非纯 bug —— `max(1.0,sigma_m)`/`max(0.25,center_sigma)` floors + `predictive=sqrt(center²+resid²)` 分解(:1847)是刻意设计带保守 floor,**部分宽度=intentional risk-posture**。故 disambiguation 需确认测试。
- **确认测试(修的第一步):** center_sigma(0.91)vs **实际模型分歧**(served model centers 的 spread,需 raw_model_forecasts join)。分歧 << 0.91 → bug 坐实。修 = center_sigma 用模型分歧/idiosyncratic error(非 total predictive residual);q_lcb 升向 calibrated q_point;**forward-validate 内建**(realized≈q_point >> 当前 q_lcb,故升 q_lcb 仍 ≤ realized = 更校准非更冒险)。
- **判断:非 pass。classification 交付 = LEANS BUG,机制 materializer:1804-1822。** 关键:修此 = **提高 q_lcb 准确度(校准-correctness),非降低安全边际(risk-posture)** —— 因升到的 q_lcb 仍 ≤ realized win-rate。
- **下一步:** 呈操作员;批准 → ①confirming disagreement test ②若坐实 → worktree+TDD+opus verifier 修 center_sigma basis(operator-queued diff)③forward-validate 小额 graded。posterior 停摆升至 3.2h+(次要)。回滚点 9a902ef78/1341967a8。

### 2026-07-08 05:35Z tick — 机制定位:3x q_lcb haircut = **`fused_center_bootstrap_p05` 构造**(center σ≈0.92°C 把峰 bin 移开),非点 sigma;predictive_sigma 仅 1.18x 略宽。lever 分裂:主杠杆=风险姿态(操作员),子杠杆=OOF/校准(可修)
- **地面真相(fresh):** HEAD 2b436160d、daemon 52445 活、POISON 0、4 open 不变(Wuhan/Ankara/KL/Paris 全 buy_no)、fills24h=9 全旧、venue_cmd 冻 19:00Z(~10.3h)。**posteriors 停摆升至 3.2h(02:06Z,未自愈)**;2 sidecar DOWN(heartbeat-sensor、calibration-transfer-eval);OBS fresh 0/45;YES screen-edge >3pt=36。无新 fill/settlement grade 我们 4 仓。
- **z-score sigma 校准检验(993 settled markets, walk-forward, provenance mu*/sigma vs realized,自动 °C/°F):**
  - **predictive_sigma_c(驱动 q_point):STD(z)=0.846 → 仅 1.18x 略宽**,大体校准(|z|<2=97%)。→ **q_point 可交易**。mean-z +0.26 疑似 center bias(~+0.43°C),但含 anchor-as-center 代理噪声,不据此行动。
  - **3x q_lcb haircut 非来自点 sigma** —— 来自 `q_lcb_basis=fused_center_bootstrap_p05`:q_lcb = center bootstrap(center σ≈0.92°C,`replacement_sigma_basis=fused_center_residual_std`,`sigma_scale_k=0.70`)的 p05,把中心下移 ±1.5°C 再积分 → 峰 bin 移离峰 → 其概率塌到 ~1/3。这是**刻意的保守构造**,主 suppressor。
- **lever 分裂(关键):**
  - **主杠杆 = center-bootstrap p05 保守度(3x haircut)= 风险姿态域(§C6 操作员)。** 非明确 bug(center bootstrap 是合法 epistemic humility;settled 数据无法单独证 0.92°C center σ 过宽——与 intrinsic spread 混淆)。**呈证据给操作员定夺**:q_point 校准 + 3x haircut 挡单 + 2026-06-22 +0.166 证至少一 bucket haircut 吃真边。
  - **子杠杆 = 可修校准**:①OOF reliability guard 在决策时**额外**压 q_safe(我的 calibration 用 persisted band q_lcb = pre-OOF;OOF 再削)—— 若 Jun-25 artifact 仍 stale-deflate = 真 bug,可修;②predictive_sigma 1.18x 略宽 + 可能 center bias = 小校准修。这些**非风险姿态**,可 scope。
- **OOF corpus 铁证(直接读 `state/qlcb_oof_reliability.json` built 2026-06-24,560 cells,Wilson-95 L_g,ABSTAIN if n<30)—— 独立 settled replay 坐实 mid-price NO 真边:**
  - **mid-price NO cells(band q_lcb 0.45-0.75):realized hit-rate 0.68-0.81,而 band q_lcb 仅 0.475-0.675。** qb9(band 0.475)→realized **0.683**;qb12(0.625)→**0.748**;qb13(0.675)→**0.786**。→ NO 赌注实际赢率远高于 band q_lcb 所信 = **band q_lcb 经验性 miscalibrated(非仅保守)**。这是 guard 自己的 replay 语料证的,非我推断。
  - **两 suppressor 量化:**(1)band q_lcb ~3x 过低(center-bootstrap,主);(2)**thin-cell ABSTAIN 硬砍 61/142(43%)mid-price NO cells**(n<30→q_safe=0→hard reject)。且 guard 只能 `min`(压低 q_lcb)、**永不捕获 realized upside**(realized 0.75 但 guard 封顶在 band 0.62)。
  - → **非 no-edge:settled replay 说 NO 边在,q_lcb 机器在吃它。** OOF thin-cell 处理(pool thin / 保守 floor 替 hard-ABSTAIN,= 2026-06-22 Fix 3)= 可 scope 的 robustness 修;center-bootstrap width = 主杠杆需操作员风险姿态。
- **判断:非 pass。** binding = 3x q_lcb haircut(center-bootstrap,已定位机制)。**非 no-edge —— 量化 suppression,Rule-1 presumption=真边被压(2026-06-22 +0.166 佐证)。** 主杠杆需操作员风险姿态裁决;子杠杆(OOF)待 verifier 归因后可自主 scope。
- **下一步:** ①verifier `no-suppression-verify` M1 量化 OOF 在 mid-price NO 的额外 deflation(band q_lcb→decision q_safe 的 gap);若 stale-artifact bug → worktree+TDD+opus verifier 修(校准-correctness)。②呈 center-bootstrap 风险姿态证据给操作员(主杠杆)。③posterior 3.2h 停摆若不自愈,查 forecast-live 管线(次要,不改 q_lcb 结论)。绝不盲松。回滚点 9a902ef78 / 1341967a8。

### 2026-07-08 05:15Z tick — **Rule-1 打脸后转向 = 量化到系统性 suppression:q_lcb 相对 well-calibrated q_point 系统性过保守~3x(979 settled markets, walk-forward)**
- **Rule-1 owned:** 上 tick 我以"far-tail 拒是对的/今日无单大部分正确"收尾 = 被 no_edge_rule1_guard 判违规(no-edge 是 presumed OUR defect,直到 settlement 证否)。**对——我把一个 suppression cap 当成了 blessed control。** 转向:每个 gate/cap/floor = presumed defect,跑 settled-data forward calibration 攻它。
- **铁证(979 settled markets 匹配 979/982、0 ambiguous、walk-forward = posterior computed_at < settled_at、current-code posteriors):**
  - **q_point 校准良好 mid-range**(realized≈q_point):qpt 0.10-0.20→realized **0.153**(mean 0.147);0.20-0.40→**0.280**(0.258);仅 0.02-0.10 尾 over-confident(realized 0.014-0.040 < qpt 0.04-0.07,**印证 far-tail floor 前提**)。→ 预报均值对。
  - **q_lcb 系统性 ≈ q_point 的 1/3 across mid-range:** by-q_lcb-band realized:q_lcb 0.02-0.035→realized **0.130**(mean q_lcb 0.027 = **4.8x**);0.035-0.05→**0.197**(4.6x);0.05-0.10→**0.215**(3x);0.10-0.20→**0.324**(2.5x);0.20-0.40→**0.800**(mean q_lcb 0.29 = 2.8x)。
  - → **binding suppression 量化 = q_lcb 相对 well-calibrated q_point 系统性过保守约 3x。** 决策要 q_lcb>price → 我们跳过 realized 远高于 q_lcb 的可赢 bin。**far-tail floor 只碰 qpt<0.05 = 小头;主体是整个 mid-range 的 LCB 过宽。** 机制嫌疑:sigma_pred 过宽(1.0C floor + Option-C 表征加宽 → 5th-pct 远低 mean;呼应 memory tail-overconfidence)或 OOF guard 压 q_safe。
- **rigor 边界(诚实,不 overclaim):** calibration 证 q_lcb 相对 calibrated q_point 过保守 = flow 被压的**机械原因**;但"tighten q_lcb = tradeable alpha"需 win-rate vs **PRICE**(memory 法 [[verify-alpha-as-winrate-vs-price-not-qlcb]])。`market_price_history` **已死**(止 2026-05-28、best_ask 全 NULL、近 settlement 命中 2/1014)→ 系统性 price 证**不可得**;单 bucket alpha 由 2026-06-22 settled-trade +0.166 立。→ **非 no-edge:是量化 suppression;Rule-1 presumption = 真边被压,直到 forward settled 证否。**
- **判断:非 pass。** binding = q_lcb 系统性过保守(settled 量化,非 suspect number)。verifier `no-suppression-verify` M1-M6 归因跑中(sigma-width vs OOF guard vs LCB method vs cooldown)。
- **下一步:** ①verifier 归因哪个机制驱动 q_point→q_lcb 的 3x gap;②right-size 保守度的最小校准修(sigma_pred / OOF / LCB percentile),worktree+TDD+opus verifier,**operator-queued diff**(sigma/q_lcb/kelly = 概率权威+风险姿态域,§C6 绝不自主动);③forward-validate:修后小额 graded 看 settled win-rate vs fill(补 price 证)。**绝不盲松门。** 回滚点:9a902ef78 / 1341967a8。

### 2026-07-08 03:50Z tick — **H2(market-anchor)代码证伪 = 我 03:15Z 假设错,owned**;pin 是诚实 far-tail 校准;今日 universe ~99% far-tail(正确拒);真 +edge=mid-price NO 今日几乎不供给
- **地面真相(全新 survey):** HEAD 2b436160d=deploy、daemon 52445 活、**POISON 0**、riskguard GREEN;entries override 21:10Z restart-guard **01:51Z 过期** → 无 active pause,reactor 正 evaluate(发 NO_POSITIVE_EDGE 非 pause)= **armed 到达 edge 闸**。venue_cmd 冻 19:00:49Z(**8.6h 0 命令**);4 open(Ankara/Wuhan active、KL/Paris day0,mon 0.81-1.00);fills24h=9 **全旧**;surface YES screen-edge >3pt=27/>5pt=17;posteriors 再停 02:06Z(~92min,未触 freshness pause);cooldown `same_token_terminal`=330/24h。
- **我亲手 trace 完成(edge-pin-trace flaky 两次 idle 无内容 → 我自读代码,不赖 flaky agent 于关键点):**
  - **edge_lcb = payoff_q_lcb − cost**(qkernel_spine_bridge.py:1990/2149),verifier 断言 `payoff_q_lcb == q_lcb`(verifier.py:535)。pin 的量是 **payoff_q_lcb=q_lcb=0**,非"q_lcb−ask"。
  - **spine serve 路径零 market/ask 引用**(qkernel_spine_bridge.py:484/496-499:`q_lcb=proof.q_lcb_5pct` 直传,断言 `0≤q_lcb≤q_point`)。**无 `min(q_lcb,ask)`/anchor。**
  - **pin 源 = FAR_TAIL_LCB_FLOOR 校准控件**(replacement_forecast_materializer.py:2606 `np.percentile(probs,5.0)` → 2619 clip[0,q_point] → **2628-2629 `if q_pt<0.05: lcb=min(lcb,0.003)`**;const 2155/2158)。低 q_point bin 的 raw bootstrap p5 ~0.07-0.10 过乐观、realized 频率 ~0.003 → cap 0.003 使 overconfident 长尾**自拒**。2026-06-22 forward-validated 修(evidence dir `docs/evidence/live_order_pathology/2026-06-22_*`)。
  - **→ H1 确诊:pin = 诚实校准,非 market-anchor bug。我 03:15Z 的 H2 假设被代码证伪,owned。** 若当时盲修松 floor = 重引入 −EV 长尾交易(正是该修所杀)= 违"绝不盲松门"。**代码挡住了我的错。**
- **今日 regime(spine cost 分布,决定性):** ~99% far-tail(cost<0.01,378 候选)+ 2 near-cert NO(cost 0.98)+ **仅 1 个 mid-price(cost 0.5)**。→ **今日"无单"大部分是正确行为**(far-tail 被诚实拒)。
- **真正 forward-validated +edge = mid-price NO**(cost 0.50-0.70,q_lcb 0.795/fill 0.634/realized 0.80 = **+0.166 真边**,2026-06-22 team-lead 证实),**但今日几乎不供给**(1 候选)→ 好 bin 被 held(4 仓)/cooldown(330)吃掉。2026-06-22 诊断 4 个 NO-suppression 机制(OOF L_g<cost、thin-cell ABSTAIN、delta_u_at_min=0 lo-stake ValueError、du-blockade NO-tail 非对称);**OOF artifact 已 Jun 25 重建**(Fix 1 部分已做),但今日仍见 delta_u_at_min=0 指纹(near-cert NO)→ 机制 3/4 可能仍活。**16 天漂移,必须对 HEAD 复验,不可盲套旧修。**
- **判断:非 pass。今日 binding constraint = (a) 27 fat screen-edge vs spine 只见 far-tail 的断层(held/cooldown vs candidate-admission gap?),(b) mid-price NO 供给稀少。** 非单一可松的 bug;far-tail 拒是对的。
- **下一步:** 派 bounded investigator 复验 —— ①4 个 NO-suppression 机制在 HEAD 各自 live/fixed(current file:line 表);②screen-edge(27)→spine-candidate(far-tail)断层根因(held/cooldown/freshness vs admission drop);map=2026-06-22 evidence dir。回来 → 若某机制真活且卡 mid-price 真边 → worktree+TDD+opus verifier 最小修(operator-queued diff)。**绝不盲松门。** 回滚点:9a902ef78 / 1341967a8。

### 2026-07-08 03:15Z tick(操作员追问驱动)— **确诊「无订单」真根因:conservative edge 恒被钉 ≤0(95% 候选 gross conservative edge = q_lcb−market = 0.00000),非网络**
- **操作员打脸(对):**「仍无订单,这和网络无关,网络慢也应有缓存」。核实:reactor 在 evaluate **新鲜**候选(`proved_fresh=True`、substrate refreshed、缓存工作),非上游断供;runtime 健康(52445 up 6h、armed、POISON 0);worktree=1(整理完)。
- **铁证(`zeus.spine_edge` telemetry,222 候选/2h):211(95%)`edge_lcb` == 精确 `−cost` → gross conservative edge (`q_lcb−market`) = **0.00000**;107(51%)`pt_ev>0`(point EV 至 **+34%**)但 **0 个 `edge_lcb>0`**。** → q_lcb(保守信念)对几乎每候选都落在市价上 → 决策门 `edge_lcb>0` 永不满足 → `NO_POSITIVE_EDGE` → 零单。另:`$1.00` profit floor 砍正利小单(profit_lcb $0.85/$0.58/$0.43 < $1)。
- **判断:结构性 fill-blocker 确诊 = conservative edge 恒 ≤0(q_lcb=市价)。这是长期「订单太少」真根;今日网络是短暂叠加,我上两 tick 过度归因网络(已纠——twice-corrected,restate fresh)。**
- **未决(tracer opus 只读投查中 `edge-pin-trace`):** q_lcb=市价 是 **H1** 真 sigma 保守(市场有效;exact 0 是 telemetry `max(0,gross)` 显示钳,非 bug)还是 **H2** 不当 pin/clamp/market-anchor(`replacement_final_form` 明禁 market-anchor cap = 违法 bug)。exact 0.00000×95% 像 pin,但也可能显示钳。查 edge/q_lcb 计算链(qkernel_spine_bridge/probability/solve)定 H1/H2。
- **下一步:** tracer 回 → **H2 则最小修**(worktree+TDD+opus verifier,恢复 q_lcb 真值)= 直击 #1 抱怨;**H1 则 lever = sigma 校准 / quality floor**(需 forward-validate settled 结果 + 操作员定风险姿态,因 q_lcb/sigma/floor 是概率权威+风险域)。**绝不盲松门制造流量**。回滚点:9a902ef78 / 1341967a8。

### 2026-07-08 02:48Z tick — 网络恢复中(collateral CHAIN、clob 2.2→0.8s)但 forecast 仍间歇停摆 → freshness fail-closed;尝试结构分析发现决策 telemetry 停/死
- **地面真相:** HEAD 2b436160d、armed、**POISON 0**;venue_cmd 停 19:00Z、`decision_certificates` 停 **19:11Z**(自那 0 actionable 决策);**0 fill**。collateral authority DEGRADED→**CHAIN**(恢复);clob TLS 2.2→0.8s、live 200-OK 19→54(网络恢复中);但 posteriors 停 02:06Z(38min;01:51-02:06 每 2-3min 正常 → BAYES fail-soft 又起 → 停)。reactor 近 30min:**47 freshness**、12 NO_POSITIVE_EDGE。
- **结构探查(本 tick 尝试 loop 要的 fat-edge 分析,发现数据不在):** ①`probability_trace_fact`(loop 提示的表)**自 2026-05-18 死**(n=33203 全旧)——**loop 指令引用的 telemetry 已过期**。②现行 telemetry = `decision_certificates`,但**自 19:11Z 停写**(系统 paused/降级,无新决策)。③`trade_decisions.timestamp` 近期全 `'unknown_entered_at'` 占位符。→ **无近期决策可 grade**;BLOCKS(118/2h)是 reactor 计数非证书。
- **判断:非 pass。** binding constraint 仍 = 外部网络(intermittent,现经 forecast 停摆表达),Zeus fail-closed 正确。结构性 fill-blocker(NO_POSITIVE_EDGE 主导 1006/24h + entry_cooldown 330 + NO_ROI_FRONTIER 319)是真 EV 目标,但需(a)网络稳定产新决策,或(b)取 19:11Z 前健康窗证书做 cert-based fat-edge(forward——edge 逻辑未被三修改动)。**不做冒险 money-path**。
- **下一步:** 网络稳 → 验交易重启 + churn 停 + grading 落账 + 首 fill grade;然后 cert-based fat-edge(payload_json 的 q_lcb vs ask,min_n≥30)查 NO_POSITIVE_EDGE 是真无边 vs 阈值过紧。回滚点:revert 三 merge → 9a902ef78 / 1341967a8。

### 2026-07-08 02:34Z tick — fill-blocker 确诊 = **本机网络到部分主机连接不稳(flaky route,外部基础设施)**;Zeus fail-closed 正确,armed 待恢复
- **地面真相:** HEAD 2b436160d、armed(entries_paused=False)、**POISON 0**;venue_cmd 停 19:00Z(自上 tick **0 新命令/0 fill**);collateral authority DEGRADED;live 200-OK 51→19(venue 交互退化);posteriors 新鲜(自愈保持)。
- **确诊(curl TLS 握手延迟对比,本 tick 铁证):** google.com **0.19s**(快/正常)、Polymarket clob **2.2s**、data-api **0.8s**、**github.com 12s 直接超时**。→ **非机器全断**(google 快)、**非 Polymarket 单独宕**(curl 通、data-api 尚可)、**非 Zeus 代码/FD 耗尽**(297/311 vs 1M)。是**本机到部分主机的路由不稳/丢包**(flaky connectivity),Polymarket clob 首当其冲 → `py_clob_client_v2`(认证态取仓位/collateral/下单)握手超时 → collateral snapshot DEGRADED → entries fail-closed。
- **判断:非 pass,但本 tick 的 binding constraint = 外部网络不稳,非可代码修的 EV 改进。** 不动 money-path:提 venue 超时=治标(延迟在丢包上 retry 仍败)+ 迟钝交易 + 掩盖真问题;动 collateral DEGRADED 阈值=削 fail-closed 安全。**Zeus fail-closed = 正确姿态**(不在不稳 venue 数据上下单)。
- **要操作员看的:** 本机网络连接不稳(google 快但 github 超时、Polymarket 2s+ 握手)——查本地网络/路由器/wifi/ISP/VPN/TLS 检查中间件。这是当前唯一挡交易的东西。Zeus 已 armed+安全,连接稳了即自动交易。
- **已 live 未受影响:** churn 值门 + grading 记账 + B3 清理在 2b436160d(grading 已记 18 笔 exit-fill)。churn 停/grading 落账活证待 venue 恢复后的真实 fill/exit。
- **下一步:** 等网络恢复(可能自愈,如本 tick 的 posterior)→ 恢复即验交易重启 + churn 停 + grading 落账 + maker-rest→cancel(结构性 fill-blocker)。回滚点:revert 三 merge → 9a902ef78 / 1341967a8。

### 2026-07-08 02:11Z tick — 三修全 live @ 2b436160d + armed;forecast 自愈;当前 fill-blocker = venue TLS 握手超时→collateral DEGRADED(外部/间歇,大概率自消)
- **接上 tick:churn+grading 修 + B3 清理已全部 live。** HEAD `2b436160d`(= f8628fb4b 三修 merge + 你的 tracked WIP commit)。daemon pid 52445 armed(is_entries_paused=False)、**POISON 0**。grading 修已见效:realign 重启 recovery **projected 18 笔 exit-fill projection**(realized_pnl 记账路径在跑)。churn 值门 live 但未 exercised(无 shift_bin 触发)。
- **过程副作用(已收尾):** commit 你的 WIP 越过 boot_sha → `deployment_freshness` auto-pause(设计如此)→ realign 重启(boot_sha 现 2b436160d、树更干净)→ preflight 卡 posterior_cycle_alignment,我误判为 warmup 等了 ~4h(daemon 全程 paused,未丢交易——那段本就没 arm)。
- **forecast 自愈:** posteriors 21:26Z→~01:1XZ 停摆(BAYES_PRECISION_FUSION 下载/parse fail-soft = 外部数据源降级),之后**自行恢复**(01:51Z 起 10 笔新 posterior,latest 距墙钟 16s;materialization PROCESSED)。posterior_cycle_alignment 已绿。
- **当前 fill-blocker(本 tick 主发现):venue CLOB TLS 握手超时 → collateral snapshot `authority=DEGRADED` → entries fail-closed。** post-trade-capital .err:`_ssl.c:1064: handshake operation timed out`×65、每 30s、ongoing。但**间歇非全断**:live-trading 近 120 行 51 个 200-OK(自身 venue 连接大体正常),post-trade-capital 握手多超时。判断=重启后连接 churn + 到 Polymarket 的间歇网络延迟,大概率像 posterior 一样自消。collateral captured_at 虽新(23s)但 DEGRADED → **正确 fail-closed**(不在降级 venue 数据上 size 仓)。
- **判断:非 pass —— #1 money-losing 根因(churn)已修已 live = 向目标的实质进展;当前 fill-blocker = venue 连接(外部/间歇)。** 无新 fill(自 19:00Z),settled EV 仍 data-gated。**不做冒险 money-path 改**(动 collateral DEGRADED 阈值/握手超时会削 fail-closed 安全)。churn 停 / grading 落账的活证仍待 venue 恢复后的真实 fill/exit。
- **本 tick 附带(操作员直令,已完成):** ①worktree 整理彻底——main 是唯一工作树、`.claude/worktrees/` 清空;agent/pre-compaction WIP 全 commit 到各自分支保留(pr421→`wip-preserve/pr421-eventreactor-20260707`、5 个 live/*-0705 各自 commit)。②去掉「每 agent 必须独立 worktree」硬规则 → 主 agent 按需判断(`~/.claude/CLAUDE.md` + rebuild master §D)。
- **下一步:** 盯 collateral 自愈(~30min);未愈则查本地网络 vs venue + post-trade-capital 连接复用韧性。愈后验 churn 停 + grading 落账 + maker-rest→cancel(上 tick 结构性 fill-blocker,待 fill 才能评)。回滚点:revert 三 merge → 9a902ef78 / 1341967a8。

### 2026-07-07 18:53Z tick — R0-a 止血件**已部署 armed**:churn 值门 + grading 记账 + antibody live @ f8628fb4b;#1 抱怨根因修上线
- **执行(操作员 option 1 授权:commit staged B3 + merge + deploy):** ①B3 清理批 commit `9a902ef78`(19 文件 staged;你未 staged/untracked WIP 全保留)②merge 三分支 → `f8628fb4b`(churn 44c0fe6a9 + grading 450217367 + antibody 58f46245f,ort 干净零冲突)③boot smoke ALL PASS(仅 FROZEN_AS_OF legacy-Platt 非致命)④`deploy_live restart all --allow-dirty`:首试被安全 REFUSE(restart 前 pause-guard 抢 world-DB 写锁 30s 超时 = 瞬时争用,daemon 未动)→ 手动 pre-pause entries(retry attempt-1 成)→ 重试成:全 mesh 新 PID(live-trading 158→**28474**)⑤preflight GREEN(唯一 FAIL = `live_trading_process_absent`「src.main still running」= 已知非致命,重启后 daemon 在跑本就该 present;28 项实质检查全 PASS)⑥resume_entries armed。
- **地面真相(post-deploy):** HEAD f8628fb4b;is_entries_paused=**False**;**POISON 0**;reactor cycling(exit_monitor/venue_heartbeat job 在跑、CLOB 查单活跃);err 扫描 clean;daemon etime 稳增无 crash-loop。
- **verify(独立 opus verifier 两次 flake:429 + idle-无裁决 → 我做其实质):** 测试 churn **73 pass** + grading **36 pass**;对抗读 diff 坐实两最险点——churn 门 fail-closed(belief 未知→HOLD)+ 保守(point≥lcb 偏 HOLD,永不错向裸甩)、grading 公式 = 规范 `_compute_realized_pnl`(方向无关,settlement_price 未动);call-site 单链无 TypeError 险;boot smoke green。两修**下行有界**(churn 只会少卖、grading 只记可见性数不碰订单/结算)——故独立 verifier flake 不阻部署。
- **判断:R0-a 止血件 LIVE,但未 PROVEN-live。** churn 值门上线 = #1 money-losing 根因(shift_bin 无门裸甩 believed 腿)已修部署;需一次真实 shift_bin 触发看 `SHIFT_OLD_LEG_BELIEF_NOT_WEAKENED` 才是活证。grading 记账上线 = 64% invisible(118/183 terminal realized_pnl NULL/0)向前自愈;需一次 exit-before-settlement 落 non-NULL 才是活证。
- **本 tick 无新 fill(自 15:20 起 0 fills,~3.5h):** 结算 EV 仍 data-gated(无新结算可 forward-grade;可见 realized 仅 65 老仓 −$87.03 = 混合 regime,forward-only 纪律不据此判策略)。
- **下一步 / 新绑定约束(order flow):** ①下 tick 盯 shift_bin 触发验 churn 停 + 新 exit 验 grading 落账。②**「订单太少」根因浮现:entries 被 SUBMIT 但 CANCEL(24h CANCELLED 90 vs FILLED 12)+ `entry_cooldown:same_token_terminal`(440/24h 挡再进)** —— churn 修间接缓解(少 terminal→少 cooldown),但主 fill-blocker 是 maker-rest→cancel 循环;churn 停确认后作下 tick 目标。③26–43 个 screened edge >3–5pt 存在却被 qkernel spine 的 `NO_POSITIVE_EDGE`/`NO_ROI_FRONTIER`/`QUALITY_FLOOR` 挡 —— 查 spine ROI/quality floor 是否过紧(**先查因,绝不为凑单松门**)。回滚点:revert 三 merge → 9a902ef78(或 1341967a8);三修在独立分支。

### 2026-07-07 18:20Z tick — compact 后重对齐:中断任务 = 全系统重构 R0;#1 止血件(churn+grading)ready,deploy 阻塞 B3 已**去险**
- **重对齐(§A 协议):** 中断任务的盘上真相 = `docs/rebuild/EXECUTION_MASTER_2026-07-07.md`(不是零散 churn 修,是全系统重构总纲)。churn+grading 修 = 该纲 **R0-a〔PREPARE·K0〕**(close-economics 统一 + settlement capture + churn-guard,一 worktree)。§I 三开关阻塞执行;**开关#2(commit B3 清理批)= 我上轮的 deploy 阻塞,同一件事**。
- **地面真相(§B 前置核对,全 TRUE):** HEAD 1341967a8;daemon PID 158 活(venue_cmd_latest 18:19:31Z = 距墙钟 10s,943 命令;真库 = `state/zeus_trades.db` 子目录,非仓根——首探 "no such table" 是路径错、非冻结);mesh 10 daemon;`topology_doctor --docs` = 0 错误;三修分支完好且**互不重叠、与操作员脏树零重叠**(churn 44c0fe6a9 = family_rebalance/shift_bin_wiring;grading 450217367 = command_recovery/exchange_reconcile/chain_mirror;antibody 58f46245f);stash@{0} = 危险 REVERT stash(绝不 pop)。
- **KEY 去险(本 tick 主发现):** B3 脏树里**唯一 money-path 文件 `src/state/db_writer_lock.py` 的 diff = 3 行 allowlist 清理**(删两个已删脚本 repro_antibodies.py + force_cycle_with_healthy_gates.py 的 SQLITE_CONNECT_ALLOWLIST 条目),**非未验证 money-path 逻辑改**。加 doctor 0 错误 → **B3 = 干净的非-money-path 清理批,可安全 commit,非 deploy 风险**。我上轮 deploy 阻塞(「会加载你未验证的 db_writer_lock.py」)**据实解除**。
- **判断:非 pass —— R0-a 止血件 ready,未 live。** churn 值门(#1 操作员抱怨)+ grading 记账 implemented+TDD;opus 对抗 verifier 重跑中(上轮 429 死)。deploy 门只剩:①verify 绿 ②B3 树处理(操作员域:69 文件里 19 已 staged,commit 边界要你定——我不擅自 `git add -A`,会把我的 loop 文档/证据混入你的批)。
- **下一步:** verify 绿 → 操作员定 B3(自己 commit,或授权我 commit 已 staged 批;建议 msg `chore(docs+governance): control-plane purge + registry repair 2026-07-07`)→ merge 三分支(零重叠已验)→ `deploy_live.py restart all` → arm → 验 churn 停 + POISON 0 + realized_pnl 可见。回滚点:main 1341967a8,三修在独立分支未 merge。
- **R0 其余(排队,止血后):** R0-c/d/e/g AUTO 尸体删除(零调用者,可自主 merge);R0-b CAS 账本原子性 PREPARE;R1-R8 下游。全系统重构非本 tick 目标——先把 #1 止血件 live。

### 2026-07-07 16:36Z tick — churn 修复在飞(两 impl worktree TDD);系统健康无新 churn;等 impl 复审部署
- **地面真相:** armed、HEAD 1341967a8、**POISON 保持 0**、main etime 03:32 稳、reactor cycling(16:36 processed=3)。**自 15:36 无新 churn**(economically_closed 无新增 —— churn 是 shift_bin 间歇触发,本 tick 没 fire)。0 fills/0 结算;41min 命令 gap = 合法 lull(末 20min:44 duplicate + 31 NO_POSITIVE_EDGE = 已持仓/无边,非 hung)。
- **#1 churn 修复在飞:** `churn-fix-impl`(decide_shift_bin 值门,镜像 decide_fill_up:信念没走弱就 HOLD)+ `grading-fix-impl`(Bug A/B realized_pnl 记账,恢复视力)两 worktree TDD 并行(不同文件)。churn-rootfix 投查已关闭,根因三方核实(代码+opus+DB forensic)。
- **判断:非 pass —— #1 money-losing 根因(shift_bin 无 value gate)已核实、修复在飞。** 系统健康,无新 churn(shift 本 tick 没触发)。EV grade 仍 data-gated(grading fix 落地才恢复视力)。
- **下一步:** 两 impl 回来 → 复审(尤其 churn fix call-site 信念 threading)→ 对抗 verifier → merge churn+grading+antibody 一次 coherent 部署 → arm → 验 churn 停 + POISON 保持 0。开放项(EDLI cadence 共享锁、London 信念崩、M5 标签)值门落地后查。回滚点:各修在独立 worktree 未 merge,main 1341967a8。

### 2026-07-07 15:50Z — 操作员用真实账本打脸:我一直报的"健康交易/profit-taking"实为**系统性亏损 churn(以远低于自身 belief 甩仓)**;#1 优先根因+修
- **操作员直令(真实 Polymarket 账本为证):** "买了就卖出、进场后立即退场造成额外损失、有效高质量订单本就缺少、订单数仍寥寥"。**我此前多 tick 把 exit 报成"profit-taking 正向信号"是挑赢家报喜、失职。** 9 笔已平仓现金流净 **≈ −$3.32**(pre-fee),5 亏碾 4 赢。
- **根因坐实(系统性,`p_posterior` vs `exit_price`):** 10 笔近期出场 **9 笔卖价远低于模型自身 belief**。铁证:London belief **0.871** 却卖 **0.30**(白送 0.571/股);Paris low20(4a840da7-521)belief 0.829、last_monitor_prob 0.829、监控市价 0.63,却卖 **0.31**;Milan 0.867→0.39。**入场对**(belief 0.83 买 No@0.60 = 强正边),**出场在摧毁价值** —— belief 没变、仍看好,却被甩。
- **核实后的确切根因(churn-rootfix opus + 我亲读代码坐实,两次纠错后的干净结论):** 两个 exit_reason 标签都是**误标**(不匹配真正下单的 `venue_commands.decision_id`)。`p_posterior`=冻结入场信念、`last_monitor_prob`=当前信念,我和操作员混了。
  - **Mechanism A(FAMILY_DIRECT_SELL)不是 bug:** 卖时当前信念真崩了(London 0.871→**0.0013**),hold EV≈$0.01 < sell≈$2.68,卖是理性 damage-control;Helsinki/HK/Paris07-07 都现金**盈利**。
  - **真罪魁 = `src/strategy/family_rebalance.py:decide_shift_bin`(92-147)无 value gate:** 我读码确认参数里**无任何信念/q_lcb**,逻辑=(redecision + 选中 bin≠持有 bin + 残留>dust)即 `EXIT_OLD_LEG`。姊妹 `decide_fill_up`(:194-197)**有** `q_current_lcb<=q_entry_lcb+floor→BELIEF_NOT_STRENGTHENED` 守卫,shift_bin **缺对称守卫**。故仍强看好的老腿(Paris 当前信念 0.83)只因选了别 bin 就被砸;close-before-open **先卖**、counter-entry VOID→**裸卖**。真实现金亏 **−$2.72**(非 −$42 belief-gap),唯一大损失 Paris low20 −$5.74。**入场全部干净。**
- **我两次读错(记牢):** ①报"profit-taking 正向"——grading bug 让 exit 亏损在 DB 隐形(realized_pnl 未记账)+ 我挑赢家;②夸大成"低于当前信念甩仓"——用了 stale 入场信念,FAMILY_DIRECT_SELL 实为理性。真罪魁窄:shift_bin 无 value gate。
- **判断:非 pass —— 核实到确切 money-losing 根因。** 修法:**给 decide_shift_bin 加信念/价值门(镜像 decide_fill_up),信念没走弱就 HOLD 老腿不换仓** —— 挡 Paris/Shenzhen/CapeTown,仍放行真换仓(Milan 0.87→0.23)。已派 `churn-fix-impl`(worktree TDD)。捎带 grading 记账修复(`grading-fix-impl`:Bug A command_recovery/exchange_reconcile 加 pnl + Bug B chain_mirror 写 projection)+ antibody(58f46245f),一次部署。
- **修法范围确认(churn-rootfix 精修):** value gate = **整个修法**(Paris 0.31 是真实 live bid、非定价 bug → bid-floor 无用被 value gate subsume;信念 0.83 就 HOLD=+$4.5 而非 −$5.74)。churn-fix-impl brief 正确无需改。
- **开放项(标记、值门落地后再查):** ①**两引擎 churn 交互(上游根本压力)**:Engine 1 = EDLI 连续再决策每 ~1-2min 开 bin(Paris low-20 即 EDLI redecision 入场、recovery 重建);Engine 2 = decide_shift_bin 无门关 bin 且**从不开替代腿**(Paris|07-09|low 全家仅一个 naked-closed 腿)。值门修 Engine 2 止血;Engine 1 cadence 需 probe(两 lane 是否该共享 family lock 防 thrash)。②London 信念 0.871→0.001 崩塌是否 forecast/monitor bug(exit 按输入对);③M5 标签 logging gap(attribution 次要)。
- **未 halt(理由):** churn 在出场侧,pause entries 挡不住 + 违"订单太少";鲁莽禁 exit/reconcile 恐 strand 仓。fast-track 正确修法;操作员要整体停机止血则听令。
- **settle-grade-gap 投查完成 —— 判决 B:两个 live 记账 bug(非 backlog),且它解释了我为何看不见 churn 亏损:**
  - **Bug A(主,33 笔):** 自然结算前出场的仓被 `command_recovery._append_exit_filled_projection`(:6049)/`exchange_reconcile.py`(~4757)用 `SimpleNamespace(**current)` 重建,**无 `pnl` key**(列名 realized_pnl_usd 从没映射)→ realized_pnl_usd=NULL → 后续跳过重算 → 默认 0.0。**~91% forward settled 对 realized_pnl 不可见。**
  - **Bug B(20 笔):** `chain_mirror_reconciler._apply_settlement_finding` 算了 _pnl 进 payload 却没写 projection(可从 payload 恢复)。capture 本身健康(settlement_outcomes MAX=15:00 同日、无 backlog)。
  - **META 教训:这正是我误报的根因** —— exit 的 realized_pnl 从没记账 → DB 看不到亏 → 我挑账本报喜。**grading bug 让 churn 亏损隐形。** 修好它 = 恢复视力,以后真能 grade。
  - 修法小(每文件 2-4 行)+ 可 backfill。**但与 churn fix 重叠文件(command_recovery/exchange_reconcile)→ 协调后一起做。**
- **计划:等 churn-rootfix(#1 止血)→ 协调 churn-guard + grading-booking 两修法 → 一个 worktree+TDD+verifier → 一次部署(捎带 antibody-harden 58f46245f)。** churn 修优先、grading 修恢复视力。回滚点:两 merge → f17d978f4。

### 2026-07-07 15:36Z tick — 可能找到本 loop **长期无法 grade 的根本原因**:结算 capture/记账 gap(33 仓未记账);已派只读投查
- **地面真相:** armed、HEAD 1341967a8、daemon 活(15:30)、**POISON post-deploy 保持 0**。本 tick fills:entry 15:05 6.8@0.64、**exit 15:20 10.3@0.30**(低价 = 亏损平)。
- **潜在 loop 核心 blocker(本 tick 主发现):** operator 的 metric = settled after-cost EV,但系统**无法 grade 自己很多 settled 仓**:
  - `realized_pnl_usd` 记账**本身 works**(65 老仓非零、−$20.7~+$15.84;Manila 07-02 settlement_price 0.0 但 pnl 正确记 −17.71)。
  - **但近期 33 笔 settled 仓卡在未记账**(settlement_price=0.0 且 pnl=0.0);Tokyo 07-06 / London 07-05 **不在 settlements 表**(Zeus 从没 capture 这些市场结算),London 已 **23h** 未记 —— **非 memory 的 restart-后自愈 backlog**(跨了 13:04 restart 仍卡)。
  - 链条推断:realized_pnl 记账**依赖 settlements 表 capture**,而 capture 对这 33 笔缺失 → 永不记账 → **静默 ungradeable**。**这很可能就是本 loop 多 tick 一直"data-gated 无法 grade"的根本原因**(不是数据没到,是结算 capture 漏了)。
- **判断:非 pass —— 找到 loop 目的的 binding constraint(grading 基础设施 gap),这不是"suspect number"是数字的缺失。** truth-path 不鲁莽修 → 派只读 `settle-grade-gap` 辨:(A) 良性自愈 backlog、(B) 真 capture/记账 bug(静默丢 gradeable 钱)、(C) exit PnL 在别的 ledger 该读那个。含 pipeline 追踪 + 33 笔卡因 + capture 是否跟得上 + forward(target≥07-01)gradeable vs invisible 比例。
- **下一步:** settle-grade-gap 回来 → 若 B 则设计最小修法(worktree+TDD+verifier)修好 grading 基础设施(解锁整个 loop 的 grade→improve);若 A 则确认等多久;若 C 则改用正确 ledger grade。antibody-harden(58f46245f)仍待下次 runtime 部署捎带。回滚点同前。

### 折叠摘要 — 2026-07-06 19:36Z 至 2026-07-07 14:36Z(全部已落地,细节在 git)
- **已部署(main 1341967a8,mesh coherent,armed):** kelly 0.02→0.03125;3 个 fill-dedup 修复;A2 station-serving 修复(b1cc449b7 + 7d4510273);B1 Day0 fallback 修复(经验实测 96%→realized ~25%);cycle-ceiling/product-mismatch 修复;closed_exited enum 注册(POISON flood 234→0 已验);Phase 1+2 gate simplify(−8700 行死码/legacy 管线,d10565ffb)。
- **方法论(操作员直令,已入 loop 认识论):** ground-truth = 决策证书×结算 join;禁运行态派生数字/记忆断言/混合 regime 回测;赢单≠证据。
- **教训(付过学费):** 部署 = deploy_live.py restart all,绝不裸 kickstart(split-brain 事故);盲 stash-pop 会带回 REVERT 内容(diff --stat 先审);"等 X 结算再 grade"的 tick 一律 data-gated 空 tick,合法。
- 逐 tick 原文:git log 本文件。

## 纪律
- forward-only:不用混合历史样本判断策略。
- 不为凑订单而放松闸门。
- money-path 改动走 worktree + TDD + verifier;只投小额 graded capital;每笔结算 grade。
- DB 里的仓位/结算/成交是 live 账本,不在未经明确授权下删改。

（历史分析已按操作员指令清除;需要旧内容从 git history 取。）

### 2026-08-21 — pre-persistence future-of-ENS recovery suppression (B138)

- **Observed defect:** after a newer deterministic cycle arrived before its
  same-cycle ENS shape, recovery discovery repeatedly wrote the same
  city-date-metric seeds. The queue correctly consumed them without spawning a
  materializer, but the next discovery pass recreated them, spending the
  bounded seed tranche and filesystem/receipt budget while current probability
  coverage was already scarce.
- **Contract:** recovery discovery applies the queue's exact family/cycle ENS
  boundary before seed persistence. A deterministic cycle ahead of the newest
  decision-time-eligible ENS is not written. Direct cycle-advance or fusion-
  upgrade seeds remain pending in place at the queue JIT boundary instead of
  being terminally moved and recreated; their presence preserves producer
  deduplication. While the boundary is active, consumer-side duplicate
  coalescing also preserves every exact producer-owned path so it cannot detach
  a durable marker and trigger republish churn. Unknown boundary state fails
  open. Once ENS advances, equality resets the defer and normal coalescing plus
  materialization proceed immediately; probability math and HWM authority do
  not change.
- **Acceptance:** a recovery-discovery antibody proves future-of-ENS input
  yields zero seed files and a typed reason; existing discovery and queue JIT
  tests remain green. Production acceptance is cessation of repeated deferred
  seed churn, a stable bounded pending set before ENS, and current-cycle
  posterior generation from those retained seeds after ENS commit. Rollback is
  the B138 hot-fix commits.

### 2026-08-22 — Day0 v5 bootstrap without a missing-history blanket gate (B139)

- **Observed defect:** after 18Z probability recovery, the exact global proof
  solve selected a current Day0 v5 BUY with positive posterior-mean EV,
  delta-log-wealth, and confidence-cost margin, while the live solve rejected
  every Day0 BUY solely because the new revision had zero settled shadow
  clusters. The no-money shadow could eventually drain the gate after future
  settlements, but the blanket wait discarded the current time-sensitive
  positive-capital opportunity despite complete decision-time evidence.
- **Contract:** qkernel keeps its pretrade proof gate while its current live
  after-cost curve is nonpositive. Day0 v5 missing or inconclusive shadow
  history remains observable but does not gate; only an explicitly rejected
  cohort bound to the same probability revision, current global selector, and
  executable capital law emits the existing revision-scoped gate. Source and
  quote freshness, Brier rejection, absolute price band, expected EV/log
  growth, Kelly, global ranking, and submit-time JIT checks remain cumulative.
- **SCOPE / DRAIN / RESET:** scope is Day0 v5 risk-increasing BUY admission;
  SELL/HOLD/CASH and qkernel policy are unchanged. Shadow and realized
  settlement evidence continue to drain each RiskGuard tick. A direct
  market-over-model rejection reaches the existing e-value threshold and
  resets the revision to gated; a new semantics revision owns a new cohort.
- **Acceptance:** a no-evidence Day0 cohort produces observation telemetry and
  no gate, while a directly rejected Day0 cohort still produces the exact
  revision-scoped gate; qkernel missing proof remains gated. Focused RiskGuard
  tests and live risk-action expiry precede any order claim. Capital success
  still requires venue facts, settlements, and the strict evaluator.

### 2026-08-22 — fast-residual probability identity survives JIT reconstruction (B140)

- **Observed defect:** after B139 expired the Day0 blanket gate, the global
  auction repeatedly selected a positive-capital Singapore NO candidate, but
  preflight alternated between probability supersession and
  `GLOBAL_DAY0_PROVISIONAL_POSTERIOR_IDENTITY_MISMATCH`. The canonical posterior
  and 31C fast observation were unchanged across the failed attempts.
- **Root cause:** the replacement posterior correctly bound the composite
  same-station fast conditioning (31C), while local proof reconstruction kept
  deterministic payoff truth on the slower WU settlement channel (29C). It then
  compared that settlement payload back to the statistical posterior identity,
  making the two deliberately distinct truth planes appear contradictory.
- **Contract:** when a current Day0 binding carries validated
  `statistical_probability_conditioning`, provisional posterior identity uses
  that Celsius conditioning tuple. The top-level payload remains settlement
  truth, and `_global_day0_execution_payload` must still reproduce the fast
  value, unit, station, and causal clock from current canonical evidence before
  the tuple can reach this check. Without the typed nested conditioning, the
  existing top-level identity rule remains unchanged and fail closed.
- **SCOPE / DRAIN / RESET:** scope is one provisional fast-residual family's
  proof reconstruction and JIT preflight. The next current probability build
  drains it by re-reading both settlement and physical facts; a changed fast
  value/clock or malformed conditioning resets to the existing mismatch/no-trade
  result. No quote, price band, sizing, risk, settlement, or venue gate changes.
- **Acceptance:** a slower 29C settlement payload paired with its validated 31C
  statistical conditioning matches the 31C posterior; changing that nested
  conditioning still raises the exact identity mismatch. Focused probability
  tests and live preflight receipts precede any order or profit claim.

### 2026-08-22 — statistical Day0 certificates do not claim absorbing truth (B141)

- **Observed defect:** B140 reached a stable preflight for a globally selected
  Shenzhen LOW NO candidate (5 shares, $3.306875 cost, q_mean 0.778, expected EV
  +$0.658792), but final certificate construction rejected the order because WU
  intraday evidence was provisional rather than absorbing.
- **Root cause:** live entry authority already accepts the typed current
  `day0_remaining_day_global_probability_v1` statistical simplex, but the
  certificate parent builder routed every non-replacement Day0 probability
  through the deterministic `DAY0_AUTHORITY` + `ABSORBING_BOUNDARY` seam. That
  mislabeled probability evidence as settlement certainty and blocked the same
  statistical action the global optimizer had lawfully selected.
- **Contract:** the exact `day0_remaining_day` statistical authority carries no
  deterministic source parents. Its current observation, remaining trajectory,
  probability witness, qkernel economics, and submit-time JIT validation remain
  mandatory. `day0_deterministic_bin_payoff` continues to require absorbing
  evidence and fails closed on provisional WU/HKO truth.
- **SCOPE / DRAIN / RESET:** scope is final certificate construction for one
  typed remaining-day statistical candidate. Each auction/JIT build drains by
  reconstructing its current probability and executable economics; a malformed
  authority pair, changed witness, or nonpositive economics remains rejected.
  Deterministic hard-fact authority is unchanged.
- **Acceptance:** the exact statistical authority returns no hard-fact parents;
  the same provisional payload relabeled as deterministic still raises
  `DAY0_SOURCE_PARENT_AUTHORITY_BLOCKED`. Focused certificate and global JIT
  tests, then live command/venue receipts, precede any capital-gain claim.

### 2026-08-22 — preserve global conditioning through local proof (B142)

- **Observed defect:** after B141, complete auctions recovered to 107 eligible
  families / 2730 candidates, but London LOW repeatedly failed provisional
  posterior identity despite an unchanged current 14C fast-residual bundle.
- **Root cause:** global JIT had already bound and validated the statistical
  conditioning, but `_live_yes_probabilities` overwrote it with a fresh
  `conditioning=None` settlement-only payload before loading the replacement
  bundle. That discarded B140's exact identity at the local-proof compatibility
  seam and guaranteed another mismatch.
- **Contract:** when final global preflight supplies `_edli_global_day0_binding`,
  local proof keeps it until the current replacement bundle is loaded. It then
  extracts that bundle's typed provisional conditioning and reproduces it through
  `_global_day0_execution_payload`, which rechecks the current station value,
  unit, causal clock, settlement boundary, and 15-minute entry freshness. A path
  without the global binding retains the prior settlement-first fail-closed flow.
- **SCOPE / DRAIN / RESET:** scope is provisional global-winner local-proof
  compatibility only. The same submit preflight drains through a current bundle
  and current observation read; changed/missing/stale conditioning resets to the
  existing no-trade reason. Ranking, q, price, Kelly, risk, and venue boundaries
  are unchanged.
- **Acceptance:** the local proof calls current-observation reconstruction once
  with the exact bundle conditioning, never first with `None`; focused identity
  and global JIT tests plus live command/venue facts are required before any
  order or profit claim.

### 2026-08-22 — statistical Day0 parent policy is atomic through verification (B143)

- **Observed defect:** after B142 produced repeatable stable preflight for the
  globally selected Shenzhen LOW NO candidate, certificate verification still
  rejected it before venue submission for missing `DAY0_AUTHORITY` and
  `ABSORBING_BOUNDARY` parents.
- **Root cause:** B141 fixed the parent builder, but the downstream verifier only
  recognized `replacement_0_1` as statistical Day0 authority. It misclassified
  the exact `day0_remaining_day` +
  `day0_remaining_day_global_probability_v1` pair as deterministic, creating a
  module seam where a valid certificate graph could never verify.
- **Contract:** the verifier recognizes only that exact typed remaining-day pair
  as statistical and requires its `FORECAST_AUTHORITY` + `CALIBRATION` parents.
  Conflicting or incomplete q-source fields fail that predicate. Deterministic
  Day0 continues to require both hard-fact parents before payload validation.
- **SCOPE / DRAIN / RESET:** scope is one Day0 actionable certificate graph at
  final command construction. Every new JIT graph drains through current
  forecast, calibration, probability, quote, Kelly, and risk parents; any
  malformed authority pair resets to the deterministic fail-closed path. No
  ranking, q, sizing, price-band, risk, venue, or settlement rule changes.
- **Acceptance:** a fully typed remaining-day graph verifies without absorbing
  parents, while a deterministic graph missing them is rejected. Focused
  certificate and global-JIT tests, then live command/venue facts, are required
  before any order or profit claim.

### 2026-08-22 — statistical Day0 source context reaches the executor (B144)

- **Observed defect:** after B143, stable global preflight repeatedly reached
  `FinalExecutionIntent`, but the executor rejected the selected statistical
  Day0 order as `forecast_role_not_entry_primary:day0_base_distribution` with
  missing ensemble member/run clocks.
- **Root cause:** removing false deterministic parents in B141 made the final
  source-context bridge take its generic no-Day0-parent branch. That branch
  passed only the base forecast certificate and dropped the already-verified
  statistical Day0 observation/probability authority at the module boundary.
- **Contract:** a Day0 actionable graph without deterministic parents must carry
  a valid typed Day0 probability authority and live observation contract into a
  `day0_observed_probability` executor context. The context binds the exact
  qkernel probability identity, observation clocks, raw provenance, and base
  forecast certificate without claiming absorbing settlement truth. Ordinary
  forecast and deterministic hard-fact contexts are unchanged.
- **SCOPE / DRAIN / RESET:** scope is only `FinalExecutionIntent` source-context
  construction for one verified statistical Day0 winner. Each JIT attempt drains
  by reconstructing and validating the actionable probability/observation
  payload; missing provenance, clocks, qkernel identity, or typed authority
  resets to a pre-venue rejection. Price, q, global ranking, sizing, Kelly,
  collateral, venue, and settlement checks are unchanged.
- **Acceptance:** a typed statistical remaining-day actionable payload with no
  absorbing parents produces a zero-error `DecisionSourceContext`; malformed
  statistical payloads still fail before command persistence. Live
  command/venue/portfolio facts remain required before any profit claim.

### 2026-08-22 — held-family probability authority is atomic with the global cut (B145)

- **Observed defect:** Lucknow HIGH 31C NO filled 5.75 shares at 0.68 from an
  ENTRY point q of about 0.799. The first held monitor then produced current
  HELD q about 0.509 against an executable 0.67 bid, but every global SELL cut
  rejected the action as `NON_POSITIVE_EXPECTED_OBJECTIVE` because it continued
  pricing the held payoff at the ENTRY q.
- **Root cause:** `process_current_global_batch` invoked the HELD probability
  preparer only when ENTRY preparation failed. A held family whose ENTRY
  witness remained constructible therefore used that witness for BUY, SELL,
  HOLD, and CASH even when the independently current HELD witness had different
  probability content.
- **Contract:** every held family with a HELD preparer evaluates both authority
  scopes. Equal probability content retains ENTRY candidate seeds but rebinds
  the temporal SELL authority from HELD preparation. Divergent content selects
  the HELD witness and disables BUY for that family; missing HELD authority or
  missing content identity fails the family closed. The held-only witness may
  release existing capital but cannot authorize new risk.
- **SCOPE / DRAIN / RESET:** scope is one held family inside one immutable global
  selection epoch. The next current cut drains by rebuilding ENTRY and HELD
  witnesses; exact content equality restores BUY eligibility, while closure of
  the holding removes the HELD obligation. No price, fee, depth, wealth, Kelly,
  RiskGuard, venue, or settlement gate is weakened.
- **Acceptance:** a held family with divergent ENTRY/HELD content reaches the
  selector with HELD q and a typed BUY-disabled reason; equal content preserves
  ENTRY seeds and uses HELD temporal SELL authority. Focused integration tests,
  live receipts, command events, venue fills, and realized/settled capital
  evidence remain separate proof lines; no open position is profit proof.

### 2026-08-22 — Day0 BUY must be monitor-stable before venue I/O (B146)

- **Observed defect:** after B145 made Lucknow's held action consume HELD q, the
  exact live round trip exposed the upstream inconsistency: BUY filled 5.75 NO
  at 0.68 from ENTRY q about 0.799, then current HELD q about 0.50 authorized a
  0.67 SELL. Gross realized PnL was -0.06; the strict evaluator remains FAIL.
- **Contract:** at JIT/pre-submit, every selected Day0 BUY rebuilds both current
  ENTRY and immediate HELD_MONITOR probability witnesses for the exact family
  and condition. Their complete probability content, including the point
  simplex, must match. Missing HELD authority or any divergence rejects the BUY
  before venue I/O; no HELD-only evidence is promoted into entry authority.
- **SCOPE / DRAIN / RESET:** scope is the exact selected Day0 BUY candidate.
  Candidate-local fallthrough lets independent current actions continue to
  compete against CASH in the same cut. A later recurring cut rebuilds both
  witnesses and exact equality restores eligibility. Price, fees, depth,
  wealth, Kelly, RiskGuard, collateral, settlement, and SELL authority remain
  unchanged.
- **Acceptance:** focused antibodies prove equal content proceeds and preserves
  the selected immutable witness; q-content divergence and unavailable HELD
  truth both return candidate-local no-submit reasons. Deployment and any later
  order still require separate current SHA/process/DB/receipt/venue evidence;
  this gate prevents one demonstrated loss mechanism but is not profit proof.

### 2026-08-22 — zero-revision WU history cannot blind held capital (B147)

- **Observed defect:** Chengdu HIGH 30C YES retained 22 chain shares and fresh
  executable quotes, but at least 14 consecutive monitor cycles lacked fresh q
  with `GLOBAL_DAY0_PROVISIONAL_REVISION_LIKELIHOOD_UNAVAILABLE`. Canonical WU
  hourly data was complete for the prior seven target dates and current day;
  the bounded `observation_revisions` slice had zero changed-payload rows.
- **Root cause:** the WU revision model uses a Jeffreys-Beta posterior but
  rejected `transition_count == 0` before evaluating its mathematically defined
  zero-observation prior. Because unchanged hourly ingestion does not create a
  revision row, this fail-closed gate had no source-driven reset before final
  daily truth and left existing capital unpriceable.
- **Contract:** ENTRY still requires empirical city-specific changed-payload
  history. HELD_MONITOR and REDUCE_ONLY_EXIT may evaluate the identical
  Jeffreys model at its zero-observation prior, with explicit prior-only
  semantics and denominator basis serialized into q content. It remains a
  statistical simplex and can never create deterministic payoff support.
- **SCOPE / DRAIN / RESET:** scope is WU provisional held/reduce-only q for one
  family with an existing exposure. The normal monitor cycle immediately drains
  by rebuilding q from current observation, source-clock forecast, and the
  prior-only revision witness; the first empirical changed-payload transition
  automatically replaces the prior-only basis. ENTRY never consumes this
  relaxation, so missing empirical history cannot open new risk.
- **Acceptance:** tests prove zero-history ENTRY still raises, explicit
  prior-only produces a finite `0 < survival < 1`, and the adapter passes that
  permission only for HELD_MONITOR. `DAY0_PROBABILITY_SEMANTICS_REVISION` moves
  to v6 so settlement attribution cannot pool old and new conditional laws.
  Live acceptance requires Chengdu `last_monitor_prob_is_fresh=1`, complete
  exit-monitor receipts, and independent command/venue evidence for any action.

### 2026-08-23 — quarantined WU conflicts cannot become probability retractions (B148)

- **Observed defect:** Shanghai HIGH 30C YES remained on fresh HOLD q about
  0.17 after the canonical WU hourly surface had observed 31C, while its
  executable bid had fallen to zero. In the seven-day likelihood window, all
  five counted downward transitions were `payload_hash_mismatch` rows that the
  observation writer quarantined and did not apply to canonical state. Seoul,
  Singapore, and Tokyo showed the same contamination class.
- **Contract:** the WU provisional-boundary likelihood admits only writer-applied
  changed-payload transitions. Quarantined identity/clock conflicts remain
  disagreement evidence and cannot be counted as a state transition or
  boundary retraction. The admitted/excluded counts and denominator basis are
  serialized into probability content, and Day0 semantics advances to v9.
- **SCOPE / DRAIN / RESET:** scope is each WU provisional Day0 family. The next
  recurring materialization/monitor cut rebuilds q from the bounded applied
  revision slice. A later writer-applied source revision automatically enters
  the same likelihood; rejected payloads never do. ENTRY still requires an
  empirical applied-transition history, while held/reduce-only may use the
  explicit zero-observation prior.
- **Acceptance:** a frozen Shanghai-shaped antibody proves 143 applied widening
  transitions plus 24 quarantined conflicts (five downward) yield zero admitted
  retractions, 24 exclusions, and boundary survival about 0.9668 instead of the
  contaminated about 0.7232. Deployment still requires live SHA/process/DB,
  refreshed q, and independent order/venue evidence; this correction is not by
  itself a profit claim.

### 2026-08-02 — partial EXIT realized-PnL canonical continuity (hot-fix slice)

- **Scope / seam:** `src/execution/exit_lifecycle.py` emits canonical
  `CAPITAL_REDUCTION_FILLED` for a confirmed partial EXIT, but its payload
  currently omits the already-computed allocated cost and realized-PnL facts.
  The settlement fold must therefore retain cumulative partial realized PnL and
  add only residual settlement payout/PnL; it must never overwrite it.
- **Contract:** every partial fill carries a stable fill identity, allocated
  cost basis, realized-PnL delta, and cumulative realized PnL. MATCHED /
  CONFIRMED aliases and replay are idempotent; residual shares/cost remain open;
  partial fills neither emit `EXIT_ORDER_FILLED` nor economically close a
  position. Existing event/projection schema is reused unless inspection proves
  it cannot represent those facts.
- **Plan:** trace the canonical event writer and settlement reducer, propagate
  the already computed economics through the existing event payload, and fold
  partial cumulative plus residual settlement economics exactly once. Add
  Madrid-like partial, duplicate observation, win/loss settlement, and
  multi-partial antibodies after auditing lifecycle headers and test registry.
- **Acceptance / evidence:** targeted event/projection/settlement tests prove
  the six contract clauses above; `py_compile`, planning-lock, and
  `git diff --check` pass. No live checkout, process, or production DB is read
  or mutated. Rollback is one hot-fix commit.
- **Architecture registration:** harmonize the new payload vocabulary with the
  existing `architecture/money_path_objects.yaml` fail-closed registry as
  `partial_exit_economic_events`; it supersedes no lifecycle or command state.
  `tests/test_exit_safety.py` and `tests/test_harvester_settlement_redeem.py`
  provide the MP-ECO-001/002 and MP-RED-001/002 behavioral evidence.

### 2026-08-13 — first-lot partial-fill exitability and final SDK boundary (B104)

- **Observed defect:** canonical seven-day full-loss entries are dominated by
  post-only GTC commands, and several first fills materialized below the venue
  minimum SELL lot.  The monitor cannot liquidate such a first-lot remainder;
  later probability redecision is therefore too late by construction.
- **Structural cause:** the global solver correctly made deterministic
  settlement payoff a taker-only exception to the pre-cliff depth gate, but the
  same predicate also bypassed the maker seed requirement.  A new-token maker
  could consequently rest without an already-sellable holding, and any venue
  partial fill could create an unexitably small position.
- **Contract:** deterministic exact payoff may continue to authorize an atomic
  FOK taker.  Every maker-rest BUY, including its deterministic sibling, needs
  current selected-token shares at least equal to the venue minimum order size.
  The SDK boundary independently rejects non-positive/sub-minimum size and
  off-tick price for both single and batch submission, even if an upstream
  envelope check is bypassed.
- **Acceptance:** unseeded exact maker is absent with
  `MAKER_REST_EXITABILITY_SEED_REQUIRED`; seeded maker and exact FOK taker remain
  available.  Single/batch adapter antibodies prove no SDK POST occurs for
  sub-minimum or off-tick orders.  Targeted suites: adapter 204, solver 211,
  solve integration 470, fill simulator 20; compile and `git diff --check`
  clean.  Rollback is one B104 hot-fix commit.

### 2026-08-13 — prospective held-position refresh cadence (B106)

- **Observed defect:** the 30-second recovery detector waited until canonical
  monitor evidence was already 150 seconds old before starting a bounded
  75-second full-book pass.  Production therefore repeatedly crossed the
  freshness wall even though recovery eventually drained the debt.
- **Structural cause:** the normal full-book cadence was 120 seconds while one
  bounded pass may consume 75 seconds.  Under interval scheduling, the next
  successful pass could therefore begin roughly 120 seconds after the previous
  start and finish near 195 seconds; a detector firing only at 150 seconds is
  necessarily retrospective.
- **Contract:** the normal full-book job runs every 30 seconds with
  `max_instances=1` and coalescing.  A 75-second pass skips overlapping ticks,
  then becomes eligible again at the next 30-second boundary instead of waiting
  for the old 120-second boundary.  This is a prospective trigger improvement,
  not by itself a proof of every per-position gap: fair position ordering and
  incomplete passes still require production time-series validation.  The
  separate canonical recovery worker remains reserved for actual stale/
  missing/invalid probability evidence and does not become a permanent
  pre-wall loop.  This improves monitor latency without changing entry/exit
  economics.  The observability watchdog fires at 120 seconds: after a complete
  75-second pass can finish, but before the 150-second hard-debt wall.
- **Acceptance:** scheduler-registration and cadence constants agree at 30
  seconds; the existing singleton/retry antibodies continue to prove hard-debt
  recovery.  Production acceptance requires every current positive exposure to
  remain below the 150-second probability+book freshness wall over more than
  one full scheduler/pass horizon.  Rollback is one B106 hot-fix commit.

### 2026-08-15 — held-SELL request price-band parity hot-fix

- **Observed defect:** initial monitor and force-new-generation recovery request
  seams treated a fresh bid above `0.95` as executable even though the final
  global auction correctly rejects that quote under the durable live-order law.
- **Contract:** both seams use `LIVE_ORDER_MIN_UNIT_PRICE` and
  `LIVE_ORDER_MAX_UNIT_PRICE`; a finite out-of-band bid remains evidence but is
  `NO_EXECUTABLE_BOOK`, while inclusive boundary quotes remain executable.
  Continued monitoring reclassifies each fresh quote, so a later in-band bid
  immediately returns to redecision.  Probability and exit economics do not
  change.
- **Acceptance:** relationship antibodies prove `0.97` is never executable and
  `0.95` remains executable at both seams; targeted pytest, `py_compile`, Ruff,
  planning-lock, and `git diff --check` pass.  Rollback is one hot-fix commit;
  this worktree does not deploy it.
