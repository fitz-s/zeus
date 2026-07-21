# 权威矩阵(5+1 类 × 全表)— design-B 交件 + team-lead 裁决

来源:子代理 design-B(sonnet),2026-07-21。team-lead 前置裁决两条(design-B 揪出的升级项):

## Team-lead 裁决(在读矩阵前先看)

**裁决 1 — 加第 6 类 `reconstructible-current-cache`。** design-B 正确指出 5 类方案对"可重建的当前服务缓存"(forecasts(world)、ensemble_snapshots 当前头、forecast_posteriors 复用件、deterministic_forecast_anchors)无干净归属,它勉强塞进 operational-work。但 REDESIGN_v2 §1 其实已在物理上把它们分到 `world-current.db`/`forecast-current.db`——**与 money-hot.db 同构但异文件、弱耐久(NORMAL vs FULL+fullfsync)**。这就是隐含的第 6 类。正式命名:
- **reconstructible-current-cache**:丢失可从 ledger+evidence 重建的当前服务态(当前 posterior/ensemble 头、活动 forecast 缓存、readiness/coverage 门)。弱耐久,不进 money-hot 的原子域。
- 与 operational-work 的界:后者是**有界可变工作态**(队列/租约/outbox/retry),丢了会破坏进行中的交付契约;前者丢了只是重算。二者都不是 money truth,但耐久与恢复语义不同。
矩阵中 design-B 标 operational-work 的 4 张"current-cache"表 → 归此第 6 类。

**裁决 2 — manifest rot 是 W2 的 BLOCKING 前置,升级为最高危。** design-B 实证:`execution_feasibility_evidence`(world)被 manifest 标 `legacy_archived`/"ghost",实为 **10.83 GiB / 15.77M cells 真实数据**,且 round-2 分类依赖它是 12.98M 行独立群体;`decision_certificates`/`edges`(trade)标 "drop after 2026-08-09",而 round-2 分类**依赖那 58K 行数**才能确立它是 selected-grain(区别于 world 1.35M candidate-grain)。**按 manifest 标签执行任何清理 = 删活数据 = 删结算证据。** 这是 FINDINGS F5/F14 的实锤扩大版。
→ **门**:任何 wave 执行前,先跑一致性扫描:`grep notes:.*[Gg]host` 交叉 `schema_class:(trade|world|forecast)_class`,枚举全部 stale copy-paste 误标(design-B 只经 census overlap 撞到 6 例,全集需脚本扫)。此扫描进 W2,且是 registry 可信化的前置。

**未决探针(design-B 列,进 W2 probe 队列)**:6 张 needs-probe(current-cache 归类、feasibility_latest/snapshot_latest 是否入 money-hot、edli_no_submit_receipts 是否被当权威读、hourly_observations 死否、market_events/selection_hypothesis_fact 谁读、deterministic_forecast_anchors 决策时是否读)。

---

## ⚠️ CRITIC-MATRIX 修正(BLOCKING,已应用 2026-07-21)— 覆盖下方原始交件的错误行

opus critic 对本矩阵做 money-data-loss 复核,判 **REJECT(as-is 不安全)**,并用**代码证据**抓出一个 authority 完全搞反——**design-B 与 GPT-5.6 round-2 consult 都错了这一处**。全文 `critic_matrix_review.md`。下列修正**覆盖**原始交件对应行:

**B1 BLOCKING — `decision_certificates` + `decision_certificate_edges` authority 反转(必须去反转)**
- 原始交件错:world 副本(1.35M 行)→ raw-evidence/learning-mart(可删);trade 副本(58K)→ ledger。
- **修正**:world = **immutable-ledger(活钱权威)**,整体保留;trade = **DEAD-drop**(pre-PR-S4b ghost,非 selected-grain)。
- 代码证据:两个证书写者都落 world/world-class 连接(reactor.py:1194-1197;event_reactor_adapter.py:7161,7270 `build_conn=live_cap_conn or trade_conn`);money-path 门读 world(check_edli_live_canary_gate.py:342/365、check_live_restart_preflight.py:1144-1166、live_profit_audit.py:594);`fact_revocations` world 实例在此表撤销 LIVE money-certificate(db_table_ownership.yaml:2812);manifest 说 world 是"authority table for proof-carrying decision certificates"(:1257);trade 副本 manifest = `legacy_archived`/"Drop after 2026-08-09"(:2649),**无 live 写者**。证书是密码学承诺,**不可重建** → learning-mart 断然错。
- 若按原矩阵执行:W5/W3 epoch 删除 world 活权威 → EDLI 赎回证明、live-canary promotion、restart-preflight 父链验证、结算 skill 评级全失源;money-ledger 反把 stale ghost 奉为正典。**永久钱记录丢失。**
- 若日后对 world 证书行拆分:必须带**祖先保留契约**(保留任何是 selected/settled/VERIFIED 证书的 Merkle 祖先的 cert,经 `decision_certificate_edges` 连线)——原矩阵给了 decision_log 这条,漏了证书本身。

**M1 MATERIAL — `decision_log` 证据纠错 + manifest rot 门**
- 原始交件 §c#3 误读:说 trade `decision_log` 是 `schema_class: trade_class`——**实为两副本都 `legacy_archived`**(db_table_ownership.yaml:2626 trade、:789 world)。
- 但 decision_log **是活的 money-recovery 表**:写者 decision_chain.py:153,194;读者 command_recovery.py:3335(命令恢复路径)。
- 结论(保留为 ledger)不变,但证据换正,且**双 legacy_archived + 活表 = 独立 W2 门**:任何按 `legacy_archived` 触发的 drop 脚本会删活恢复表。

**M2 MATERIAL — needs-probe 的不安全删除默认(翻转)**
- `market_events`(forecasts,活 forecast_class,17,256 行,结算相关生命周期)与 `selection_hypothesis_fact`(world,活 world_class,fact_revocations 标记):原默认 **raw-evidence(可删)** → **翻转为保留**(ledger/current-cache)直到 reader 探针跑。
- **规则(矩阵须采纳)**:needs-probe 行默认取**最保留的合理类,永不默认删**。

**M3/缺漏 — 采纳 writers/readers 重导规则 + 正列遗漏表**
- **根因规则**:凡分类依赖 manifest 标签**或** round-2 断言,一律从**实际 writer/reader 重导**(对证书做了就不会有 B1)。→ 进 EXECUTION_MASTER 作 W2 强制门。
- `outcome_fact`(trade,结算真相,harvester log_settlement_event 写,18 行低于 top-40 截断)**未分类** → 显式 **ledger/money-hot**(结算真相不可留给推断)。
- money-hot 控制面(position_current/lots、venue_commands/_events、settlement_commands、collateral_reservations、trade_decisions、execution_fact)**正列 money-hot**,不靠"小即安全"推断;trade_decisions 尤是 W0 修复表。

**critic 确认 sound(无需改)**:collateral→money-hot、book_hash head→money-hot、position/venue facts→ledger、calibration_pairs→learning-mart、settlement_outcomes→ledger、opportunity_events 拆分、第 6 类——全对。矩阵约 80% 正确,B1 是唯一 catastrophic 反转。

**传播**:此修正也纠正 `REDESIGN_v2.md §2` 与 `consult_gpt56_answer.md §2` 的 decision_certificates grain-story(它们说 world=candidate-only-evidence,错;world 是含 selected 的权威 superset)。

---

（以下为 design-B 原始交件,未改动——上方 CRITIC 修正覆盖 decision_certificates/edges、decision_log、market_events、selection_hypothesis_fact 四处)

# 5-Class Authority Matrix — Zeus DB First-Principles Redesign

Scope: every table in `census_tables.md` (49 rows across trades/forecasts/world), classified against the 5-class scheme, cross-checked against `db_table_ownership.yaml` (251 table entries, verified via direct read + grep). `census "trades"` = manifest `db: trade` (`state/zeus_trades.db`); `census "forecasts"` = manifest `db: forecasts`; `census "world"` = manifest `db: world`.

**Tag legend**: `[RULED]` = direct application of a round-2/REDESIGN_v2 §2 verdict naming this table. `[INFERRED]` = my extension of round-2 principles to a table the verdict didn't name — check these.

**Critical scoping note before the matrix**: `census_tables.md` states "Objects: 799... Complete" but each DB section lists only ~40 rows (trades 40, forecasts 40, world 40 = 120 of 799). This document is a **top-N-by-size digest, not the full object list**. That changes how to read §(c) below — most manifest tables absent from this census are simply below the size cutoff shown here, not evidence of drift. I only flag genuine mismatches (content contradictions), not routine small-table omissions.

---

## trades (state/zeus_trades.db) — measured 93.9 GiB, 19 tables

| Table | Size | → Class | Rationale | Move |
|---|---|---|---|---|
| executable_market_snapshots | 43.11 GiB | **raw-evidence** | Append-only book history; hot mirror already split out as `executable_market_snapshot_latest`. Same shape as `book_hash_transitions`. `[INFERRED]` | clean-move |
| execution_feasibility_evidence | 19.03 GiB | **raw-evidence** (+ledger slice) | Round-2: full diagnostics→evidence, small decision-used summary→ledger. Trades-side population (25.58M rows), distinct from world's. `[RULED]` | row-level-split |
| decision_log | 7.60 GiB | **ledger** (+evidence slice) | REDESIGN_v2 §2: diagnostic BLOB→evidence; envelope+preimage commitment bytes→ledger, never let a deletable epoch be the sole home of settled-cert verification bytes. `[RULED]` | row-level-split |
| book_hash_transitions | 2.13 GiB | **money-hot** (head) / evidence (history) | Round-2 explicit: head row per market/token→hot; transition history→evidence or delete if snapshot-derivable. `[RULED]` | row-level-split |
| position_events | 0.84 GiB | **ledger** | Round-2: immutable lifecycle facts→ledger; derive `position_current` into hot separately. `[RULED]` | clean-move |
| decision_certificates | 0.22 GiB | **ledger** | 58,021 rows ≈ round-2's "trades 58K" selected/graded-grain population. `[RULED]` — **manifest says `legacy_archived`/"residual drift"/drop-eligible; that's wrong, see §c.** | clean-move |
| collateral_ledger_snapshots | 0.20 GiB | **money-hot** (current) / ledger (deltas) / evidence (periodic snapshots) | Round-2 explicit 3-way split. `[RULED]` | row-level-split |
| edli_live_order_events | 0.16 GiB | **DEAD/evidence** | Manifest: this copy is a stale duplicate of world's authoritative ledger copy (16,743 rows, real data despite "ghost" label). `[INFERRED]` | needs-probe |
| market_price_history | 0.13 GiB | **raw-evidence** | 657,409 narrow-payload rows = append-only tick stream, not a money fact. `[INFERRED]` — manifest note contradicts schema_class, see §c. | clean-move |
| execution_feasibility_latest | 0.10 GiB | **money-hot** | Pre-submit book-evidence seam read synchronously by the live order runtime; same "hot mirror" family as `book_hash_transitions` head row (round-2 explicit for that sibling). `[INFERRED]` | clean-move |
| provenance_envelope_events | 0.05 GiB | **ledger** | Round-2 E7: may be the sole record of what was seen at decision time; verify same-authority before dedupe/evidence-demotion. `[INFERRED]` | needs-probe |
| token_price_log | 0.05 GiB | **raw-evidence** | 217,102-row append-only tick log. `[INFERRED]` — manifest note contradicts schema_class, see §c. | clean-move |
| probability_trace_fact | 0.05 GiB | **evidence/DEAD** | Manifest: 33K+ misplaced rows from INV-37 violation, writes now redirected to world's copy. Migrate-or-drop, not a canonical trade table. `[INFERRED]` | needs-probe |
| token_suppression_history | 0.04 GiB | **raw-evidence** | 94,342-row operational/diagnostic log. `[INFERRED]` — manifest note contradicts schema_class, see §c. | clean-move |
| executable_market_snapshot_latest | 0.03 GiB | **money-hot** | Manifest's own words: "hot live refresh-priority readers use this... first." Textbook hot mirror. `[INFERRED]` | clean-move |
| venue_order_facts | 0.03 GiB | **ledger** | Round-2: immutable lifecycle facts→ledger. `[RULED]` | clean-move |
| decision_certificate_edges | 0.02 GiB | **ledger** | Sibling of `decision_certificates`(trade); edges for the selected/graded cert population. `[INFERRED]` — manifest mislabel, see §c. | clean-move |
| availability_fact | 0.01 GiB | **evidence/DEAD** | Manifest: 24K+ misplaced rows (INV-37 violation), authoritative copy is world's. `[INFERRED]` | needs-probe |
| market_topology_state | 0.01 GiB | **evidence/DEAD** | Tiny; manifest ghost from pre-PR-S4b contamination, authoritative on world. `[INFERRED]` | clean-move |

---

## forecasts (state/zeus-forecasts.db) — measured 39.9 GiB, 14 tables

| Table | Size | → Class | Rationale | Move |
|---|---|---|---|---|
| calibration_pairs | 11.96 GiB | **learning-mart** | Round-2 explicit: not spine, rebuildable; canonical truth is a narrow `graded_predictions` fact carved out. `[RULED]` | row-level-split |
| ensemble_snapshots | 3.35 GiB | **operational-work** (current head) / evidence (historical) / ledger (decision-committed digest) | Round-2 explicit 3-way split, paired with `forecast_posteriors`. "Hot/cache" language, not order-book state — REDESIGN_v2 §1 places this in `forecast-current.db`, a separate file from `money-hot.db` with weaker durability (NORMAL vs FULL+fullfsync); operational-work is the nearest of the 5 classes. `[RULED, class-mapping INFERRED]` | row-level-split |
| forecast_posteriors | 3.30 GiB | **operational-work** (reusable artifact) / ledger (decision input) / evidence (diagnostic array) | Round-2 explicit: same E1-style byte dissection, 3-way. `[RULED, class-mapping INFERRED]` | row-level-split |
| raw_model_forecasts | 0.25 GiB | **raw-evidence** | Manifest: `training_allowed=0`, immutable product-identity-tagged capture — textbook raw-evidence. `[INFERRED]` | clean-move |
| observations | 0.09 GiB | **ledger** | Extends round-2's "canonical observation facts→ledger" (settlement-input observation table, compound key city/date/source). `[INFERRED]` | clean-move |
| raw_forecast_artifacts | 0.09 GiB | **raw-evidence** | Manifest: "immutable raw... downloaded artifacts," name says what it is. `[INFERRED]` | clean-move |
| deterministic_forecast_anchors | 0.06 GiB | **learning-mart** | Deterministically regenerable from `raw_forecast_artifacts`; not itself order/settlement truth. `[INFERRED]` | needs-probe |
| source_run_coverage | 0.05 GiB | **operational-work** | Readiness/coverage gate consumed by executable forecast readers — bounded, not money-truth. `[INFERRED]` | clean-move |
| market_events | 0.02 GiB | **raw-evidence** | Small market-lifecycle event log; not clearly money-adjacent. `[INFERRED]` | needs-probe |
| day0_hourly_vectors | 0.02 GiB | **operational-work** | Name suggests Day0 nowcast hourly buckets; **UNREGISTERED — not in manifest at all** (verified via grep). `[INFERRED]` | needs-probe |
| readiness_state | 0.02 GiB | **operational-work** | Manifest: "producer readiness verdicts." | clean-move |
| settlement_outcomes | 0.02 GiB | **ledger** | Manifest: "canonical settlement truth table" — matches class-3 definition verbatim ("settlements"). `[RULED — direct class-definition match]` | clean-move |
| settlements | 0.01 GiB | **ledger (dead)** | Manifest: `DELETED_PENDING`, superseded by `settlement_outcomes`, OK to drop once verified sole-truth. Retire, don't migrate. `[INFERRED]` | clean-move |
| readiness_state_legacy_no_ready_20260607T131810Z | 0.00 GiB | **DEAD** | Timestamped migration-rename artifact; **UNREGISTERED** (verified via grep). Essentially empty. `[INFERRED]` | clean-move |

---

## world (state/zeus-world.db) — measured 83.8 GiB, 16 tables

| Table | Size | → Class | Rationale | Move |
|---|---|---|---|---|
| opportunity_events | 30.00 GiB | **raw-evidence** (envelope) / operational-work (projections) | Round-2 explicit vertical split: immutable envelope→evidence; availability/lease/retry/pending-order projections→work. `[RULED]` | row-level-split |
| no_trade_regret_events | 11.23 GiB | **ledger** (rejection fact) / learning-mart (hindsight columns) | Manifest: "hindsight columns exist for post-settlement analysis, excluded from live readers" — implies two different lifecycles inside one table. `[INFERRED]` | row-level-split |
| execution_feasibility_evidence | 10.83 GiB | **raw-evidence** (+ledger slice) | Round-2 explicit: world population is 12.98M rows, real and distinct from trades' 25.58M — not a mirror. `[RULED]` — **manifest labels this `legacy_archived`/"ghost from pre-trade-repoint drift"; that's dangerously wrong given 10.83GiB of real, round-2-verified data. See §c.** | row-level-split |
| decision_certificates | 3.13 GiB | **raw-evidence** (or learning-mart) | Round-2 explicit: 1.35M-row candidate-grain population, not a duplicate of trades' selected-grain copy; rejected full candidate material→evidence/mart. `[RULED]` | clean-move |
| opportunity_event_processing | 2.34 GiB | **operational-work** | Round-2/REDESIGN_v2 §2 explicit: 11M historical rows are "queue corpses"; keep only pending+leased+short-horizon (~5GiB recovery). Named as the class-2 canonical example in the classification brief itself. `[RULED]` | row-level-split |
| selection_hypothesis_fact | 1.91 GiB | **raw-evidence** | Candidate/hypothesis evaluation record, same family as decision_certificates' candidate population; tagged by `fact_revocations` as a "forecast-snapshot-linkage" table alongside `probability_trace_fact`. `[INFERRED]` | needs-probe |
| observation_instants | 1.57 GiB | **ledger** (authority-gated rows) / evidence (UNVERIFIED filler) | Round-2 base: canonical observation facts→ledger. Manifest's own authority column (A1/A2/A6-gated native vs UNVERIFIED OpenMeteo filler) suggests a finer split than the base ruling states. `[RULED base, split-refinement INFERRED]` | row-level-split |
| decision_certificate_edges | 0.67 GiB | **raw-evidence** | Sibling of `decision_certificates`(world); edges for the candidate-grain population. `[INFERRED]` | clean-move |
| observation_revisions | 0.58 GiB | **ledger** | Round-2 explicit: canonical observation revisions→ledger. `[RULED]` | clean-move |
| edli_no_submit_receipts | 0.26 GiB | **ledger** | Durable, append-only, proof-carrying decision-outcome receipt — matches class-3's "certificate identities, order facts" despite manifest calling it a "report convenience" relative to `decision_certificates`. `[INFERRED]` — genuinely uncertain, see §b. | clean-move |
| edli_live_order_events | 0.17 GiB | **ledger** | Round-2 explicit: immutable lifecycle facts→ledger. This is the manifest-authoritative copy. `[RULED]` | clean-move |
| decision_compile_failures | 0.16 GiB | **ledger** | Manifest: "durable compiler failure denominator... so no-submit reports cannot hide rejected looks" — permanent decision-adjacent fact. `[INFERRED]` | clean-move |
| forecasts | 0.10 GiB | **operational-work** | Manifest: "active forecast cache." REDESIGN_v2 §1 explicitly separates `world-current.db` from `money-hot.db` (different durability tier); operational-work nearest fit among the 5. `[INFERRED]` | clean-move |
| probability_trace_fact | 0.09 GiB | **raw-evidence** | Diagnostic trace of probability computation, not itself a truth fact; this is the manifest-authoritative copy (trade's copy is contamination). `[INFERRED]` | clean-move |
| hourly_observations | 0.09 GiB | **raw-evidence/DEAD** | Manifest: `legacy_archived`, **"no INSERT matches in src/ as of 2026-05-18"** — no active writer. Round-2's "hourly→ledger" pattern-match does not apply to this specific dead copy. `[INFERRED, contradicts naive pattern-match]` | needs-probe |
| data_coverage | 0.08 GiB | **operational-work** | Manifest: per-city/date coverage-status tracking — same flavor as `source_run_coverage`/`readiness_state`. `[INFERRED]` | clean-move |

---

## (a) Tables needing a row-level split (the hard ones)

1. **execution_feasibility_evidence** (both DBs) — decision-used summary rows/fields → ledger; bulk diagnostics → evidence. Populations don't overlap 1:1 (25.58M trades vs 12.98M world), so this is two separate split jobs, not one.
2. **decision_log** (trade) — diagnostic BLOB columns → evidence; envelope+preimage commitment-hash columns → ledger. High stakes: round-2 explicitly warns a deletable evidence epoch must never become the sole home of bytes a settled certificate's hash depends on.
3. **book_hash_transitions** (trade) — head row per (market_slug, token) → money-hot; transition history → evidence (or delete if snapshot-derivable — needs the snapshot-sufficiency proof from REDESIGN_v2 §4).
4. **collateral_ledger_snapshots** (trade) — 3-way: current row(s) → money-hot; deltas → ledger; periodic full snapshots → derived checkpoint/evidence.
5. **calibration_pairs** (forecasts) — narrow `graded_predictions` fact (prediction_id, cert_id, city/date/metric/lead, model version, p, y, weight, timestamp) → new ledger-adjacent fact; everything else (bucket/grouping columns, the 8 B-trees) → learning-mart, rebuilt not migrated.
6. **ensemble_snapshots** + **forecast_posteriors** (forecasts) — 3-way each: current head/reusable artifact → operational-work cache; decision-committed exact digest → ledger; diagnostic/historical → evidence.
7. **opportunity_events** (world) — immutable envelope columns → evidence; availability/lease/retry/pending-order projection columns → operational-work. This is a column split, not a row split — same physical event needs both halves.
8. **opportunity_event_processing** (world) — pending + leased + short-horizon-complete rows → operational-work (kept); the ~11M-row historical tail → evidence or delete (~5GiB recovery target per REDESIGN_v2).
9. **observation_instants** (world) — split by the existing `authority` column: A1/A2/A6-gated native-source rows → ledger; UNVERIFIED OpenMeteo filler/backfill rows → evidence. `[INFERRED refinement — not explicit in round-2]`.
10. **no_trade_regret_events** (world) — core rejection fact (rejection_stage/reason, decision-time) → ledger; hindsight/post-settlement-analysis columns → learning-mart. `[INFERRED]`.

## (b) Genuinely uncertain classifications — specific probe needed

1. **The "current-cache" class-mapping problem** → RESOLVED by team-lead 裁决 1: 6th class `reconstructible-current-cache`. (forecasts(world), ensemble_snapshots-current-head, forecast_posteriors-reusable-artifact, deterministic_forecast_anchors move there.)
2. **execution_feasibility_latest / executable_market_snapshot_latest** (trade) — money-hot by analogy to book_hash_transitions head, but these hold market/book *evidence* read inside the order-decision txn, not position/collateral truth. **Probe: does money-hot extend to "evidence read synchronously inside the order-decision transaction"? Apply uniformly to all three (book_hash head, feasibility-latest, snapshot-latest).**
3. **edli_no_submit_receipts** (world) — manifest calls it "report convenience" subordinate to decision_certificates, yet durable/append-only/proof-carrying. **Probe: read as authority by any live path, or audit-only?** Flips ledger vs evidence.
4. **hourly_observations** (world) — manifest says dead (no writer since 2026-05-18). **Probe: confirm zero live writer/reader before evidence/DEAD** — don't let name-pattern override the manifest dead-writer finding.
5. **market_events** (forecasts) / **selection_hypothesis_fact** (world) — round-2 silent. **Probe: who reads them, decision/settlement path (→ledger) or retrospective (→evidence)?**
6. **deterministic_forecast_anchors** (forecasts) — regenerable but "input to replacement posterior." **Probe: read live at decision time (→current-cache) or only calibration/backtest (→learning-mart)?**

## (c) Manifest ↔ census mismatches

**Scope caveat (structural, not a finding)**: census shows only top ~40 objects per DB (120 of 799). Manifest tables absent — incl. the entire money-hot control surface (`position_current`, `position_lots`, `venue_commands`, `venue_command_events`, `settlement_commands`, `collateral_reservations`, `trade_decisions`, `execution_fact`) — are not drift; they're too small to crack a top-40-by-size list. That's a POSITIVE signal: the tables that need to be small under the target architecture already are.

**Unregistered — in census, not in manifest** (grep-verified, zero hits): `day0_hourly_vectors` (forecasts, 0.02 GiB); `readiness_state_legacy_no_ready_20260607T131810Z` (forecasts, ~0, timestamped rename artifact).

**Manifest rot — label contradicts measured reality (highest severity first)**:
1. **execution_feasibility_evidence (world)** — manifest `legacy_archived` + "Ghost... pre-trade-repoint drift"; census **10.83 GiB, 15.77M cells**, round-2 treats as real distinct 12.98M-row population. `legacy_archived` reads as "safe to drop" — it is NOT. Most dangerous manifest/reality gap in the set.
2. **decision_certificates + decision_certificate_edges (trade)** — manifest `legacy_archived`, "Drop after 2026-08-09"; census 58,021 certs / 105,275 edges, and round-2's classification DEPENDS on the 58K count to establish selected-grain vs world's 1.35M candidate-grain. Drop-date framing is stale and contradicts the verdict built on it.
3. **decision_log (trade)** — `schema_class: trade_class` correct, but note text "Ghost... Drop after 2026-08-09" is a copy-paste artifact from neighboring genuine ghosts. 7.60 GiB, 190,032 rows, the diagnostic-BLOB shape E1 targets. Note wrong, class right.
4. **market_price_history, token_price_log, token_suppression_history (trade)** — same pattern: class correct, copy-pasted "Ghost... Drop" note despite real rows (657K/217K/94K). Low-severity cleanup.

**Recommendation (→ team-lead 裁决 2, now a W2 BLOCKING gate)**: before this matrix drives any wave, run a scripted consistency check — grep `notes:.*[Gg]host` against `schema_class:(trade|world|forecast)_class` (non-legacy_archived) to enumerate the full stale-copy-paste set rather than the 6 this pass hit via census overlap.
