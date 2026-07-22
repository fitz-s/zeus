# atomicity_outbox_contract

**Author:** data-integrity engineer (design-A) · **Scope:** design + read-only DB analysis, no files written to repo/worktree/state
**Verdict headline:** The 16 missing `settlement_outcomes` rows are **NOT a cross-file partial commit.** They are a **systematic, by-design path divergence**: the `harvester_pnl_resolver` VENUE_RESOLVED settlement route settles positions in `zeus_trades.db` only and, by explicit module invariant, never writes forecast settlement truth. Every genuine cross-file money invariant in Zeus is already single-file (money lives in `zeus_trades.db`); the cross-DB helpers project money-state into *truth/evidence/coverage*, which is reconcilable — so the outbox is the right contract and the money-hot merge does not reintroduce partial commits.

---

## 1. Cross-file invariant inventory

Every place one logical operation must keep ≥2 DB files consistent. `F`=forecasts, `W`=world, `T`=trades. Each row cites the helper + the runtime caller I verified.

| # | Logical op | Tables (DB) | Helper / mechanism | Runtime caller (file:line) | Class |
|---|---|---|---|---|---|
| 1 | Obs write + coverage index | `observations`(F) **+** `data_coverage`, `daily_observation_revisions`(W) | `get_forecasts_connection_with_world` (F MAIN + W ATTACH, SAVEPOINT) `db.py:784` | `daily_obs_append._write_atom_with_coverage` `daily_obs_append.py:749`; `ingest_main.py:1248,1888`; `wu_scheduler.py:163` | **reconcilable-eventually** (coverage derivable from `observations`) |
| 2 | **Settlement (obs-VERIFIED)**: truth + position close | `settlements`,`settlement_outcomes`,`market_events`(F) **+** `position_current`,`position_events`,`decision_log`,`chronicle`,`settlement_commands`(T) | `forecasts_connection_with_trades_flocked` (F MAIN + T ATTACH, `BEGIN IMMEDIATE`, one SAVEPOINT) `db.py:845` | `run_harvester` `harvester.py:933,964,1043,1104,1136` | **money part atomic-with-itself (single file post-merge); truth part reconcilable** — today mis-modeled as one cross-file atomic unit |
| 3 | Settlement era provenance | `settlement_outcomes`(F) **+** `uma_resolution`(W, read) | `get_forecasts_connection_with_world` + `SAVEPOINT era_dispatch` `settlement_writers.py:225` | `write_settlement_with_era_provenance` `settlement_writers.py:139` | reconcilable / analytical (world side is a read) |
| 4 | Settlement-timing capture facts | settlement-capture result(F) [+W] | `get_forecasts_connection_with_world` `settlement_capture_verifier.py:231` | `SettlementCaptureVerifier.write_result` | **purely-analytical** (timing-quality audit) |
| 5 | Quote-evidence ingest | `opportunity_events`(W) **+** `execution_feasibility_evidence`(T) | `world_connection_with_trades_flocked` (W MAIN + T ATTACH) `db.py:966`; non-flocked long-loop sibling `get_world_connection_with_trades_required` `db.py:924` | `price_channel_ingest.py:1544`; `control_plane.py:562` | **reconcilable-eventually / analytical** (append-only decision witness) |
| 6 | Venue-sync / EDLI absence | trade-class **+** world-class | `trade_connection_with_world_flocked` (T MAIN + W ATTACH) `db.py:1153` | `venue_sync_contract.py:330`; `edli_absence_resolver.py:630` | mixed: order/venue truth is money (co-locate); world projection reconcilable |
| 7 | **Settlement (VENUE_RESOLVED)**: position close **only** | `position_current`,`position_events`,`decision_log`(T) — **and no forecast row** | `trade_conn` **single-file commit** `harvester_pnl_resolver.py:403` | `resolve_pnl_for_settled_markets` → `_settle_positions(trade_conn,…)` `harvester_pnl_resolver.py:359` | **reconcilable-eventually, but NO reconciliation exists → this is the bug (§2)** |
| 8 | Chain vs local reconciliation | `position_current`,`position_events`(T) vs on-chain | `append_many_and_project(conn,…)` + `conn.commit()` `chain_reconciliation.py:669,1267` | `chain_reconciliation` (Chain > Chronicler > Portfolio) | the existing **eventual-consistency reconciler** (trades ↔ chain) |

**Structural finding:** No row is "money in file A requires money in file B, atomically." All authoritative money state (orders, fills, positions, redeem commands, reservations, idempotency) already lives in **one file, `zeus_trades.db`**. Every cross-file write is *money-state ↔ (forecast-truth | world-evidence | coverage)* — derivable, a read, or reconcilable against the trades side + chain. This is what makes the outbox sufficient and the merge safe.

Every helper's own docstring already concedes the hazard: *"In WAL mode, ATTACHed DB files are not a cross-file host-crash-atomic contract; crash recovery must still prove or repair cross-file invariants"* (`db.py:871-873`, repeated `936`, `1004`). SQLite's multi-database atomic commit (super-journal) is a **rollback-mode** feature; in **WAL mode it does not hold** — each attached file has its own WAL, fsync'd separately. The SAVEPOINT gives in-process all-or-nothing during normal execution, **not** host-crash atomicity.

---

## 2. The 16 missing settlements — hypothesis with evidence

**Best-supported hypothesis: a systematic trades-only settlement route, not a partial commit.** Refuted: GPT-5.6 round-2's "likely partial-commit symptom."

### Evidence chain (read-only probes, EQP-verified, bounded)

**(a) Positions settled in trades, absent in forecasts.** For the two named-stale keys:
- `zeus_trades.db.position_current`: HK `2026-07-13` high (`384f1dd8-5c1`, `3983413f-a62`) and Paris `2026-07-02` low (`83ede0f8-d31`) are `phase='settled'`, `chain_state='synced'`, `exit_reason='SETTLEMENT'`, `settlement_price≈0`.
- `zeus-forecasts.db.settlement_outcomes` **and** `settlements`: **zero rows** for `(Hong Kong,2026-07-13)` and `(Paris,2026-07-02)` (both EQP-indexed lookups, empty result).

**(b) The settling writer stamped an authority that only one trades-only path produces.** The `phase_after='settled'` events in `position_events`:
```
384f1dd8-5c1  SETTLED  src.execution.harvester  caused_by=harvester_settlement  settlement_authority="VENUE_RESOLVED"
3983413f-a62  SETTLED  …                          settlement_authority="VENUE_RESOLVED"
83ede0f8-d31  SETTLED  …                          settlement_authority="VENUE_RESOLVED"
```
Plus Tokyo `20d1b043-254` and Ankara `32be639c-c22` (the other two W13 samples): both `VENUE_RESOLVED`. **5/5 sampled missing-settlement positions are VENUE_RESOLVED.**

**(c) `"VENUE_RESOLVED"` as a settlement authority is produced at exactly one site:** `harvester_pnl_resolver.py:238` (`_read_venue_resolved_settlement_rows`). That module's header states its design invariant verbatim: *"Does NOT write to settlements, settlement_outcomes, market_events, or any forecast table"* (`:12`), and *"Venue payout and physical-observation quality are separate facts… it cannot keep a position open after Gamma publishes an unambiguous binary payout"* (`:113-117`). Its terminal commit is `trade_conn.commit()` — **single file** (`:403`).

**(d) The class is systematic, not probabilistic.** Authority distribution across all `SETTLED` events (`zeus_trades.db.position_events`):

| authority | events | distinct positions |
|---|---|---|
| VERIFIED | 1393 | 229 |
| **VENUE_RESOLVED** | **49** | **49** |
| other | 37 | 37 |

The 16 W13-missing are a subset of the 49 VENUE_RESOLVED positions. VERIFIED settlements (229 positions) go through `run_harvester` (writes `settlement_outcomes` in the same SAVEPOINT, `harvester.py:1043→1104`) or the resolver's VERIFIED branch (which *reads* an existing `settlement_outcomes` row, so present by construction, `harvester_pnl_resolver.py:102`). **The missing-row population correlates perfectly with a code-path authority, which a crash cannot do.** A partial commit would hit VERIFIED settlements randomly and leave the trades side missing about as often as the forecasts side; neither is observed.

**(e) Batch-stamp corroborates a backlog sweep, not steady state.** All three primary samples settled at `2026-07-15T00:20:24` (differing only in sub-second: `.029 / .068 / .149`) despite target dates 13 and 2 days earlier — the resolver's explicit "backlog catch-up… a live position can sit far outside the recency window" behavior (`harvester_pnl_resolver.py:55-58`).

### Why this is a real integrity gap even though "by design"
`settlement_outcomes` is the registry-declared *"Canonical settlement truth table"* and its schema **already permits `authority='VENUE_RESOLVED'`** (CHECK at `db.py:5253`, `v2_schema.py:123`). The table is *designed* to hold these rows, but the only producer of venue resolutions never writes them. Result: the "canonical" settled-market ledger is silently incomplete for every obs-absent-but-venue-resolved market, and **nothing surfaces the gap** — invisible until the W13 cross-DB probe. Calibration is unaffected (it filters `authority='VERIFIED'`, `:102`), but P&L reconciliation, coverage metrics, and capital-gains attribution that read `settlement_outcomes` as the complete settled set silently miss these markets.

---

## 3. Durable outbox contract

Replaces false cross-file atomicity for invariant **#7** (and generalizes to #2/#1/#5). One authoritative transaction in **one file** commits the state change **+** an outbox row (truly atomic, same file); an idempotent consumer applies it to the other DB with a monotonic sequence, a destination-held watermark, and a reconciliation net independent of both.

### 3.1 Outbox row schema (in the authoritative/source DB — post-merge, `money-hot.db`)
```sql
CREATE TABLE cross_db_outbox (
  seq             INTEGER PRIMARY KEY AUTOINCREMENT,  -- monotonic watermark
  topic           TEXT NOT NULL,                      -- e.g. 'settlement_outcome'
  idempotency_key TEXT NOT NULL,                      -- deterministic natural identity
  payload_json    TEXT NOT NULL,                      -- full projection to apply
  producer        TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  UNIQUE(topic, idempotency_key)                      -- producer-side dedup
);
CREATE INDEX idx_outbox_topic_seq ON cross_db_outbox(topic, seq);
```
For the settlement topic: `idempotency_key = 'settlement:'||city||'|'||target_date||'|'||metric||'|'||authority`; `payload_json` carries `{city,target_date,temperature_metric,winning_bin,settlement_value,authority,settlement_source,provenance_json}` — everything `log_settlement` needs.

The producer write folds into the *existing* authoritative transaction. In `_settle_positions`' canonical write (`append_many_and_project`, `harvester.py:453`) and in `harvester_pnl_resolver`'s settle loop, add one `INSERT … INTO cross_db_outbox` in the same connection/commit. Same file ⇒ real WAL atomicity ⇒ the state change and the delivery obligation are inseparable.

### 3.2 Sequence / watermark
- **Source:** `seq` autoincrement is the monotonic cursor. (Autoincrement may skip on rollback; a skip never breaks a "> last_applied" scan.)
- **Destination (`forecasts`/evidence):** a cursor table lives with the data it guards, so apply + advance are one same-file transaction:
```sql
CREATE TABLE outbox_cursor (
  consumer TEXT NOT NULL, topic TEXT NOT NULL,
  last_applied_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
  PRIMARY KEY (consumer, topic)
);
```

### 3.3 Idempotency key
Destination apply is an **UPSERT on the natural settlement identity** (`INSERT … ON CONFLICT(city,target_date,temperature_metric) DO UPDATE` — the key `settlement_outcomes` already indexes, `idx_settlement_outcomes_city_date_metric`). Re-delivery is a no-op/refresh. Correctness therefore does **not** depend on the watermark being exact — even a reset cursor or a source restored from an older backup replays safely. The watermark is a performance cursor; the idempotency key is the correctness guarantee.

### 3.4 Consumer crash-safety
Per run, in a single **destination** transaction:
1. read `last = outbox_cursor(consumer,topic)`;
2. `SELECT * FROM source.cross_db_outbox WHERE topic=? AND seq>? ORDER BY seq` (source read is a snapshot; no cross-file write);
3. for each row: UPSERT into `settlement_outcomes`, then `last := seq`;
4. `UPDATE outbox_cursor SET last_applied_seq=last`; commit.

Crash before commit ⇒ destination txn rolls back, cursor unchanged, whole batch re-applied next run (idempotent). Crash of the *producer* before the consumer runs ⇒ outbox row is already durable (committed with the state change) ⇒ delivery still happens. The only unrecoverable loss is losing the source file itself — a backup concern (W13 F1), orthogonal to atomicity. This run reads source + writes destination on **two connections** — that is fine and does **not** violate INV-37, because it is *not* one write transaction spanning two files: the source is read-only here, and the destination write is self-contained and idempotent.

### 3.5 Reconciliation query — the net that would have caught the 16
Watermark-independent; run by a monitor with both files opened **read-only** (two connections, app-side join — never a live ATTACH write):
```sql
-- money-hot side: every terminally-settled position with a realized settlement
SELECT city, target_date,
       COALESCE(temperature_metric,'high') AS metric,
       trade_id, settled_at, settlement_authority
FROM position_current
WHERE phase='settled' AND settlement_price IS NOT NULL;
-- anti-join against forecasts.settlement_outcomes on (city,target_date,metric);
-- rows with NO match are the gap.
```
Exactly the W13 F2 probe, promoted to a standing check. Emit `settled_without_outcome_total`; alert when it exceeds a bounded same-cycle lag. Against today's DBs it returns the 16.

---

## 4. Why `money-hot.db` stays safe (no reintroduced partial-commit class)

The merge is safe **because the partial-commit class only bites when one logical money operation spans two files, and after the merge no money operation does.**

**Co-located in `money-hot.db` (one WAL ⇒ real host-crash atomicity):**
`position_current`, `position_events`, `venue_commands(+_events)`, `venue_order_facts`, `venue_trade_facts`, `venue_submission_envelopes`, `settlement_commands(+_events)`, reservations/caps (`edli_live_cap_*`), idempotency keys, hot read-heads (`executable_market_snapshot_latest`, `execution_feasibility_latest`, `wallet_balance_head`, `fill_sync_watermarks`), **and `cross_db_outbox`**.
Invariants that become *genuinely* atomic (today only in-process-atomic via ATTACH): position-settle ↔ redeem-enqueue (`_settle_positions` `harvester.py:2741,2764`), order ↔ fill ↔ position, reservation ↔ command, and **state-change ↔ outbox-row**. These are the entire must-be-atomic-on-host-crash set from §1 — all money, now all one file.

**Cross to `money-ledger.db` via outbox (authoritative append-only journal, reconcilable):**
realized-P&L / capital-gains entries. Consistent with the standing rule that capital gains are measured from chain payouts + fills, not local `realized_pnl_usd` — the ledger is a reconcilable projection, and the outbox makes its delivery reliable rather than a second synchronous cross-file write.

**Cross to forecast/world evidence epochs via outbox (reconcilable-eventually):**
`settlement_outcomes` (both VERIFIED and **VENUE_RESOLVED** — this closes the 16-gap: the settle in money-hot emits the outbox row, the consumer writes the truth row, calibration still filters VERIFIED), `data_coverage` (from `observations`), and the decision-witness logs (`opportunity_events`, `execution_feasibility_evidence`) where a money action must project evidence.

**Purely analytical (best-effort, rebuildable, no atomicity contract):**
`decision_log` auction receipts, `chronicle`, `probability_trace_fact`, settlement-capture timing facts.

**The argument in one line:** today invariant #2 pretends `settlement_outcomes`(F) + `position_current`(T) are one atomic unit via ATTACH+SAVEPOINT (which WAL does not honor across files), and invariant #7 doesn't even try — it just drops the forecast side. After the merge, the money half of #2 is single-file-atomic, the truth half is an idempotent outbox delivery with a reconciliation net, and #7 stops being a silent drop because the same outbox carries the VENUE_RESOLVED row. No cross-file money atomicity is ever required, so none can be violated.

---

## Evidence index (file:line + query)
- Helpers / atomicity concessions: `src/state/db.py:784, 845, 871-873, 924, 966, 1153, 5253`
- Settlement paths: `src/execution/harvester.py:933-1147` (VERIFIED, ATTACH+SAVEPOINT), `1652-1871` (`_write_settlement_truth`), `2548-2836` (`_settle_positions`); `src/execution/harvester_pnl_resolver.py:12, 55-58, 112-243, 238, 359-403` (VENUE_RESOLVED, trades-only)
- Consolidated truth writer: `src/state/settlement_writers.py:139-233`
- Probes: `position_current` / `position_events` in `state/zeus_trades.db`; empty `settlement_outcomes` / `settlements` in `state/zeus-forecasts.db`; SETTLED-authority distribution 49 VENUE_RESOLVED / 229 VERIFIED-positions / 1393 VERIFIED-events
- INV-37 + canonical DBs: root `AGENTS.md §2` (`:148`)

**Worktree status:** read-only design task, no files written to repo/worktree/state, no commits; nothing of mine to merge. Untracked `EXECUTION_MASTER.md` / `W0_SPEC.md` in the worktree belong to concurrent agents and were left untouched.
