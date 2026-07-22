# capture_policy_spec — value-aware capture(design-E 交件 + team-lead 注)

来源:子代理 design-E(sonnet),2026-07-21。

## Team-lead 注(读 spec 前)

**最高价值发现 — 前提被推翻,杠杆解锁**:design-E grep 全 src 实证 **当前无任何"历史决策 vs 重建历史 book"回放路径**:`src/engine/replay.py` 头逐字 "REPLAY IS APPROXIMATE AUDIT ONLY... No market price linkage means hypothetical PnL is unavailable";`decision_kernel/modes.py` 的 `REPLAY_COUNTERFACTUAL` mode **零 producer**(guarded 门,非活功能)。→ "capture 削减会毁 replay"这条本会把 5-20x 打回 1x 的约束**今天不绑**。杠杆成立。这是本轮最有价值的单条产出——把最大杠杆从"理论上不敢动"变成"可落地"。

**内容去重死刑坐实**:`book_hash_transitions` 9.72M transitions / ~10.32M snapshots = **94% 捕获产出不同 book**,内容 dedup 只省 ~6%。杠杆必须是 value-based,不是 change-based(与 REDESIGN_v2 §4 dedupe 负结论一致)。

**change surface 比想象小**:trigger 复用**既有** priority 机制(`priority_condition_ids` 已从 marker/open-order/held-position 填充),compact 用**独立新表**(现有 ~13 个 money-path reader 零改动),唯一改动点是一个写调用的分支。低风险。

**回喂 W2 probe 队列**(design-E 无法在只读 trades-db 权限内测的):(a) `P` = priority-refresh 每 cycle outcome 数(最影响量级估计,加 `capture_trigger` 列后一句 GROUP BY 可测);(b) 历史 must-be-full 引用集大小(需 world-db `decision_certificates` 的被引 `executable_snapshot_id` distinct 集;design-E 指出这不是 4645 决策数,而是被评估 candidate 数、23.2x 更大 = 数万级)。→ 与 probe-A 数字合并前补这两条。

**未决呈操作员**:replay 充分性 framing(design-E 按"实际评估过的决策无条件保 + 其余靠 keyframe 概率性保"设计,非"任何时刻都可能决策所以全保")——若要保留极大解读,keyframe interval 是唯一旋钮,收紧而非弃用策略。

---

（以下 design-E 原始交件，未改动）

# capture_policy_spec — value-aware capture for executable_market_snapshots

Scope note up front: this lever is orthogonal to and composes with REDESIGN_v2.md §4's block-delta/zstd encoding. §4 decides *how many bytes* a FULL row costs; this spec decides *how many rows get to be FULL at all*. Apply this first (it's the 4000:1 lever), then §4 on whatever remains full.

## 1. Read/write load-bearing analysis

**Writers — three independent paths, not two:**

| Path | Cadence | Evidence |
|---|---|---|
| EDLI-warm broad sweep | 20s (`main.py:129 _EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS`) | breadth-first per-city, up to `max_outcomes` (default 4 = 2 bins × 2 directions) per city, ~30 cities/~90 outcomes captured per cycle in practice, budget-gated (`market_scanner.py:4835-4864` docstring) |
| Priority-refresh | 20s default, `ZEUS_SUBSTRATE_PRIORITY_REFRESH_INTERVAL_SECONDS` (`substrate_observer.py:211-224`), tighter ~18s budget | same writer function, narrower token/condition set |
| JIT synchronous submit recapture | event-driven, on every actual order submit | `events/reactor.py:8019-8043,8874-8917` (`PreSubmitAuthorityWitness.orderbook_depth_jsonb=json.dumps(raw_book,...)`), plus a global-auction JIT variant at `event_reactor_adapter.py:10590-10654` (`GLOBAL_JIT_SNAPSHOT_ID_COLLISION` dedup + `insert_snapshot`) |

All three ultimately call `insert_snapshot()` (`snapshot_repo.py:162-196`) against the same `executable_market_snapshots` table (append-only, `NC-NEW-B` triggers, `snapshot_repo.py:99-104`). `_snapshot_id` (`market_scanner.py:5989-6009`) folds `captured_at` + a `uuid4()` nonce into the ID hash — **every capture is a new row regardless of content**, i.e. capture is time-driven, not change-driven. Corroborating this: `book_hash_transitions` shows 9.72M hash transitions against ~10.32M snapshots (REDESIGN_v2.md §4) — **94% of captures produce a genuinely different book than the prior one**. This rules out content-based dedup ("skip if unchanged") as the lever — it would save ~6%. The lever has to be *value*, exactly as framed.

**Readers split cleanly into two classes:**

Money-path (need real bid/ask depth beyond top-of-book, to compute VWMP/sweep cost — top-K is *not* a drop-in substitute for these):
- `cycle_runtime.py:1136` — `orderbook = json.loads(...)` → `vwmp(...)` → `repriced_edge = p_posterior - snapshot_vwmp` (**the** repricing/edge computation)
- `strategy/live_inference/executable_cost.py:113-155` — `quote_book_from_depth_json`/`quote_book_from_executable_snapshot`, builds full `NativeQuoteBook`
- `execution/executor.py:5324` — pre-submit JIT-witness reuse check (bids/asks non-empty)
- `contracts/execution_intent.py:646-668` — `_orderbook_levels`, feeds `CLOB_SWEEP` depth proof / `marketable_limit_depth_bound` order policy
- `analysis/market_analysis_vnext.py:202-220` — `_queue_depth_ahead`, sums bid sizes above a quote price (needs ladder depth, not just top)
- `engine/qkernel_spine_bridge.py:2090-2170` — family-book builder reads `orderbook_depth_json` **directly off the proof's row**, explicitly documented as "no second capture and no snapshot reconstruction" (comment at 2102-2103) — i.e. one captured row already serves multiple candidate evaluations within a family. Capture cadence ≠ evaluation cadence.
- `engine/event_reactor_adapter.py:36380-36460` — native quote-book builder handling both nested and single-token CLOB formats
- `risk_allocator/governor.py:1598-1613` — depth-micro extraction, defensive fallback into full JSON only if explicit fields absent

Evidence/audit (hash or scalar only — **do not need the raw body to persist after write**):
- `event_reactor_adapter.py:20618-20685` — certificate evidence stores `_hash_jsonish(orderbook_depth_json)` (a hash, "orderbook_hash"/"quote_depth_hash") plus scalar `best_bid`/`best_ask`, never the raw body. Comment at 20667-20671 ("WALL #5 TYPE-LEVEL BINDING") explains this is a causal-binding anti-fraud check, not a data dependency.
- `decision_kernel/verifier.py:936` and surrounding `_require_equal` calls — compares ID/hash values **already embedded in the certificate's own payload sections against each other**. Does not re-open `executable_market_snapshots` or re-hash a live row. Certificate verification is self-contained at write time.
- `engine/global_auction_universe.py:2261-2302` — universe-state fingerprint reads only the **latest** row per (condition_id, token) (`ORDER BY captured_at DESC LIMIT 1`), folds raw bytes into a consistency hash over *current* state, not history.
- `execution/command_recovery.py:1000-1007` — `_command_snapshot`, exact-`snapshot_id` lookup for crash/repair recovery of an in-flight command's cited row (operational-window retention, not indefinite).
- `execution/harvester_pnl_resolver.py:120-160` — reads only `condition_id, event_slug`, never the JSON body.

**On the stated hard constraint ("replay needs a full book at any point a decision was or could have been made") — I need to correct the premise, not just accept it:**

`src/engine/replay.py:1-16` header, verbatim: *"REPLAY IS APPROXIMATE AUDIT ONLY... Trading economics require a real market price vector at decision time or real trade history. No market price linkage means hypothetical PnL is unavailable."* No hits for `orderbook_depth_json`/`opportunity_book`/`raw_orderbook` anywhere in `replay.py`. `src/backtest/economics.py` reads a table called `lns`, not `executable_market_snapshots`. **There is no code path today that replays historical decisions against reconstructed historical books.** The infrastructure is deliberately reserved for it — `decision_kernel/modes.py:7-10` defines `mode: Literal["LIVE","NO_SUBMIT","REPLAY_COUNTERFACTUAL"]`, and `compiler.py:181-191` explicitly refuses to promote a `REPLAY_COUNTERFACTUAL` certificate to `NO_SUBMIT` — but grep finds **zero producers** setting that mode anywhere in `src/`. It's a guarded, open door, not an active feature. `tests/test_money_path_lifecycle_replay.py` exists and touches `orderbook_depth_json`, but it's *lifecycle* replay (crash/recovery, matching `command_recovery.py`'s usage pattern) — not counterfactual backtest replay.

This matters because the brief's framing ("any point a decision *could have* been made") is the maximal, unbounded reading — taken literally it justifies keeping everything, which defeats the entire lever. I'm treating this as a **named assumption**, not a silent resolution (see §5): the policy below satisfies *decisions actually made or actually evaluated* unconditionally, and satisfies the *hypothetical future counterfactual-replay* goal only probabilistically, via periodic keyframes (trigger 4) — since that capability is explicitly not implemented today and the codebase's own authority doc disclaims it as a non-goal for the existing replay concept.

## 2. Full-capture trigger taxonomy

A row goes to `executable_market_snapshots` (current schema, unchanged) when **any** of:

1. **Priority-tagged this cycle** — `condition_id`/`token_id` is in `priority_condition_ids`/`priority_token_ids` for this capture call. Not new machinery — `refresh_executable_market_substrate_snapshots` already accepts these params (`market_scanner.py:4846-4849`), already populated from three sources at `substrate_observer.py:2902-2936`: explicit markers, open resting orders, held positions. Collapses "decision evaluation," "order submit," "fill," "cancel," "repair boundary" into **one existing mechanism** — fills/cancels/repairs don't need a *new* capture, they need the row from *this* trigger retained through the position/order's operational lifetime (crash-recovery lookup at `command_recovery.py:1004` already assumes this row exists).
2. **JIT submit recapture** — the synchronous pre-submit path already force-recaptures full and bypasses the 20s cadence entirely. No change; already structurally full.
3. **Near-threshold candidate** — during EDLI-warm's breadth sweep, once top-of-book economics are computed (already happens unconditionally, `market_scanner.py:3256-3257,5930-5954`), if rough edge is within a configurable margin (`ZEUS_SUBSTRATE_CAPTURE_NEAR_THRESHOLD_MARGIN`) of actionable, capture full. This lets a market *graduate* into trigger-1's priority set before a certificate exists — closing the one real gap in relying on triggers 1-2 alone (a family that evaluates candidates but produces no certificate would otherwise lose its near-miss book).
4. **Periodic replay keyframe** — every `ZEUS_SUBSTRATE_CAPTURE_KEYFRAME_INTERVAL_CYCLES` cycles (default 20, ~1 full/market/~6.7 min), force full regardless of priority. The deliberate bounded-cost answer to the maximal "could have been made" framing — periodic full-fidelity coverage of the entire candidate universe, cheaply.

Everything else (EDLI-warm outcomes not priority, not near-threshold, not keyframe) → compact table (§3).

> **SHIPPED INCREMENT (2026-07-22, after GPT-5.6 PR review REQ-20260722-005247).**
> The first landed increment is deliberately narrower than §3–§4 below, which
> remain the design of record for later operator-fenced increments:
> - **Only** the nullable `capture_trigger TEXT` column on the existing
>   `executable_market_snapshots` + write-site stamping ships now, and the column
>   is **UNCONSTRAINED (no CHECK)**: a CHECK-constrained `ADD COLUMN` makes SQLite
>   (≥3.37) full-scan every existing row (measured ~0.9s / 3M rows; O(rows) with
>   heavy cold I/O on the ~43 GB live trade table at boot), whereas a plain
>   nullable `ADD COLUMN` is O(1) metadata-only. The §2 taxonomy is enforced by
>   the application at write, not by a boot-time full-table scan.
> - The **compact table (§3) is NOT created yet.** Creating it now — unused (no
>   writer routes to it, no reader queries it) and absent from
>   `architecture/db_table_ownership.yaml` — aborts the daemon at boot via the
>   fail-closed registry assertion (`assert_db_matches_registry`, extra_on_disk).
>   It ships in the later increment that adds real routing, registered in the
>   same commit.
> - The **Track-A hydration assertion (§4) is NOT on the hot path.** As written it
>   warned on every hydrated `DISCOVERY_SWEEP` row — a value the scanner
>   intentionally writes — i.e. log amplification on money-path reads. The same
>   taxonomy proof is obtained off the hot path by an audit query:
>   `SELECT capture_trigger, COUNT(*) FROM executable_market_snapshots GROUP BY 1`.

## 3. Compact-form schema

```sql
CREATE TABLE IF NOT EXISTS executable_market_snapshot_compact (
  compact_id TEXT PRIMARY KEY,               -- "emc2-" + sha256(...)[:40]
  condition_id TEXT NOT NULL,
  selected_outcome_token_id TEXT NOT NULL,   -- full table is already one-token-per-row (event_reactor_adapter.py:36446-36449)
  captured_at TEXT NOT NULL,
  raw_orderbook_hash TEXT NOT NULL,          -- sha256 of the full book fetched this cycle (body not stored); reuses _sha256_json(raw_orderbook), ties into book_hash_transitions lineage
  orderbook_top_bid TEXT,
  orderbook_top_ask TEXT,
  depth_at_best_ask INTEGER NOT NULL DEFAULT 0,
  spread_usd TEXT,
  top_k_bids_json TEXT NOT NULL DEFAULT '[]',  -- "[[price,size],...]" tuple-array, NOT objects (decode_serialization.md (d)1)
  top_k_asks_json TEXT NOT NULL DEFAULT '[]',
  prev_hash TEXT,                              -- absorbs book_hash_transitions' (prev_hash, delta_ms) role
  hash_delta_ms INTEGER,
  capture_trigger TEXT NOT NULL CHECK (capture_trigger IN ('DISCOVERY_SWEEP','NEAR_THRESHOLD_MISS_BELOW_FLOOR')),
  schema_version INTEGER NOT NULL
);
-- same NC-NEW-B append-only triggers; index (condition_id, captured_at DESC), (selected_outcome_token_id, captured_at DESC)
```

Also add `capture_trigger TEXT` to the **existing** `executable_market_snapshots` (idempotent ALTER, matching PR2 pattern at `snapshot_repo.py:142-159`), values `('PRIORITY_HELD_POSITION','PRIORITY_OPEN_ORDER','PRIORITY_MARKER','NEAR_THRESHOLD_MATCH','KEYFRAME','JIT_SUBMIT')`. Permanent self-documenting audit trail answering "why is this row full" — append-only, can never be reconstructed after the fact if omitted now; §4 validation and §5 tuning both depend on it.

**Why a separate table, not a lighter row:** `ExecutableMarketSnapshot` (`contracts/executable_market_snapshot.py:180-308`) is a frozen dataclass consumed by ~13 money-path sites that all assume `orderbook_depth_jsonb` parses to a dict with real bids/asks (several raise `ValueError` otherwise). None query the new table. **Zero changes to any existing reader** — only the one write call site changes. Materially smaller change surface than the taxonomy suggests.

Deferred (not first cut): true delta-vs-keyframe encoding — top-K-in-full already captures the dominant win; delta adds diff/insertion/rotation complexity for smaller marginal gain. Revisit after measuring compact-row volume in production.

## 4. Replay-sufficiency validation plan

**Track A — regression safety for what exists today (the one that matters for shipping):** _(SHIPPED increment: the hot-path assertion below is REPLACED by an off-hot-path audit query — see the SHIPPED note before §3. It fired on expected `DISCOVERY_SWEEP` rows on every money-path read.)_ every ~13 read site only ever touches a row triggers 1-4 would capture full — provable by construction: add a cheap runtime assertion in the hydration path asserting the fetched row's (condition_id, token_id) was in the cycle's priority set OR its capture_trigger is a full value; ship **log-only (never raising)** for one full week of live before any capture is routed away from full. If it never fires false, the taxonomy is empirically proven before the behavior change ships (mirrors REDESIGN_v2 E5's "先证后切"). Extend `tests/test_money_path_lifecycle_replay.py` to assert command_recovery exact-snapshot_id lookups still succeed post-change.

**Track B — the counterfactual goal (honest: no harness exists):** since there's no current bulk replay reader, the literal "replay reduced vs full, assert identical" can't run. Buildable from today's data: mine every historical certificate's cited `executable_snapshot_id` + decision-time edge, walk backward through preceding cycles' captured economics for that token (on disk today), check whether the §2 near-threshold margin would have flagged it early enough — measures the trigger's **false-negative rate against real history**, calibrating margin + keyframe-interval from real edges, not guesses. W2 action: `SELECT DISTINCT` referenced snapshot ids across `decision_certificates` (trades 58K + world 1.35M) → sizes the historical must-be-full set (NOT ~4645; bounded by candidates evaluated, 23.2x larger = tens of thousands). Needs world-db → flagged for probe-A.

## 5. Expected magnitude + assumption

Assumption the 5-20x rests on: FULL capture falls to ~**10-20% of capture events**: entire priority-refresh volume (small, bounded by concurrent trading) + near-threshold/keyframe slice of EDLI-warm.

Back-of-envelope from measured constants: EDLI-warm ≤ 4320 cycles/day × ~90 outcomes ≈ 388,800/day ceiling. Priority-refresh 4320 × **P**, where **P = priority-refresh per-cycle count — unpinnable from code (runtime-populated from held/open/marker counts)**; the single number most changing the answer → probe-A measure via capture_trigger GROUP BY. 4645 lifetime decisions ⇒ modest concurrency ⇒ P≪90 likely ⇒ EDLI-warm dominates. Within ~90/cycle: near-threshold matches ~10%, keyframe 1/20=5%, so compactable ≈ **85%**. Compact rows (~150-300B) vs full (1.5-3KB) ≈ **10-20x byte reduction** on compacted rows. Blended lands in the **5-20x band** independently of round-2's path — corroboration, not proof; P and near-threshold rate sharpen band→number, both measurable today.

## Named assumptions / open items
1. Replay framing (§1): designed against "evaluated decisions kept unconditionally + rest via keyframes," not "everything always." Maximal reading defeats the lever (5-20x→1x) and isn't what current replay/backtest does. If operator wants maximal, keyframe interval is the dial.
2. **P** unmeasured, materially changes magnitude, cheaply measurable once capture_trigger exists.
3. Near-threshold margin + top-K — configurable placeholders; calibrate from real historical edge/order-size distributions (Track B), don't ship as guesses.
4. Historical full-citation set size — tens of thousands not 4645; needs world-db decision_certificates → probe-A.
