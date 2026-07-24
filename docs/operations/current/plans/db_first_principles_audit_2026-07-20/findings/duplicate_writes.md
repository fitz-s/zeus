# Lane W7 — Duplicate-Write Map

Scope per task: (a) near-duplicate table names across DBs from
`architecture/db_table_ownership.yaml` (3358 lines, read in full);
(b) `INSERT`/`UPSERT` call sites grouped by table, single-event fan-out;
(c) `position_events` vs `chronicle` vs `decision_log` overlap, and
`hourly_observations` in trades; (d) append-only-supersession classification
for the consult's epoch-rotation remediation.

**Evidence discipline**: every claim below is either an exact `census_raw.jsonl`
row (bytes/cells) or a `file:line` grep/read citation. Anything without both is
marked HYPOTHESIS. No query beyond the pre-computed census was run — SAFETY LAW
followed throughout (no dbstat, no live queries).

**Census coverage caveat**: `census_raw.jsonl` was captured mid-run by another
lane (grew from 288 → 332 rows between two reads in this session) and covers
only `forecasts` (26 tables) and `trades` (80 tables) — **`zeus-world.db` has
no byte data at all yet** (Lane W3 pending, `census_world.md` does not exist).
Every finding below that needs world.db bytes is marked UNMEASURED, not zero.

---

## TOP FINDING — the registry's "dead ghost, drop-safe" label is wrong for at least 3 tables that are live money/audit-path writers

The manifest's own author already caught **one** instance of this exact bug
class and left a flag in-place (`db_table_ownership.yaml:1888-1895`,
`opportunity_fact` entry): *"NOTE (flagged, not fixed by this packet):
opportunity_fact itself is registered db: world (canonical) but the runtime
writer (log_opportunity_fact) writes to trade.db post-INV-37 — a pre-existing
registry/runtime divergence."* Grepping actual write call sites turned up
**three more uncaught instances of the same class**, two of which carry a
"Drop after 2026-08-09" date in the registry:

### 1. `settlements` (db: forecasts) — registry says dead-since-2026-06-03, code writes it every settlement cycle

- Registry (`db_table_ownership.yaml:147-159`): *"B3cont 2026-05-28: bare
  world-class settlements shell dropped... W2 (2026-06-03): P&L resolver
  repointed to settlement_outcomes. **No live writes remain**; this table is
  a read-archive only."*
- Code, contradicting that: `src/ingest/harvester_truth_writer.py:1-15`
  module docstring — *"Writes ONLY to forecasts_conn (**settlements**,
  settlement_outcomes, market_events)"* — and the actual
  `INSERT OR REPLACE INTO settlements` at `src/ingest/harvester_truth_writer.py:747`
  and a second, independent writer at `src/execution/harvester.py:1789`
  (`_write_settlement_truth`, called from `run_harvester()`'s
  `forecasts_connection_with_trades_flocked(write_class="live")` block,
  `src/execution/harvester.py:1043`).
- **This is a true duplicate, not event+projection**: the same settlement
  fact (city, target_date, winning_bin, settlement_value, authority,
  provenance_json) is written to **both** `settlements` and
  `settlement_outcomes` in the same `_write_settlement_truth` pass — same
  source data, same call site, two tables.
- Bytes: `forecasts.settlements` = **11,694,080 bytes (11.7 MB), 12,276
  cells** (census_raw.jsonl). `settlement_outcomes` bytes: **UNMEASURED** —
  not yet in the growing census (last completed forecasts table at read time
  was `calibration_pairs`, 12.8 GB). Given `settlement_outcomes` is the
  documented sole P&L-resolver read target and presumably has ≥ as many rows,
  most of `settlements`' 11.7 MB is pure waste — an idle read-archive that
  is still being actively fed.
- Canonical per manifest: `settlement_outcomes`. Risk of dropping
  `settlements`: **LOW for reads** (P&L resolver already repointed per W2
  note) but **the write path must be patched first** — two live functions
  currently `INSERT OR REPLACE` into it every cycle; dropping the table
  without removing those call sites would turn a silent duplicate into a
  loud crash on the next settlement.

### 2. `decision_log` (db: trade) — registered as "Ghost...Drop after 2026-08-09", actually the single biggest table in the census

- Registry (`db_table_ownership.yaml:2626-2632`): *"Ghost on zeus_trades.db
  from pre-PR-S4b init_schema(trade_conn). Drop after 2026-08-09."*
- Census: **8,159,510,528 bytes (8.16 GB), 190,032 cells, mx_payload
  1,146,549 bytes** (single-row payload over 1 MB) — the single largest
  table found in trades+forecasts and one of only two tables that took over
  a minute to scan (`elapsed_s: 40.1`).
- Confirmed live writer #1 (settlement path): `src/execution/harvester_pnl_resolver.py:392`
  and `src/execution/harvester.py:606` call
  `store_settlement_records(trade_conn, ...)` →
  `src/state/decision_chain.py:194` `INSERT INTO decision_log`. Module
  header of `harvester_pnl_resolver.py:7` states explicitly: *"Writes
  trade.decision_log via store_settlement_records()."*
- Confirmed live writer #2 (auction-receipt path, explains the huge
  payloads): `src/engine/global_batch_runtime.py:3632`
  `_store_global_auction_receipt(trade_conn, ...)` →
  `src/engine/global_batch_runtime.py:2016/2100/2269` `store_artifact(conn,
  ...)` → `src/state/decision_chain.py:153` `INSERT INTO decision_log`. This
  path stores full orderbook/auction comparison JSON per cycle — the source
  of the >1 MB single-row payloads.
- **Also confirmed why this landed on trade.db despite `decision_log` never
  being registered as forecast-class**: `src/execution/harvester.py:915-921`
  documents the pattern directly: *"a SINGLE connection with forecasts.db as
  MAIN and zeus_trades.db ATTACHed as 'trades'... trade-class tables
  (position_current, position_events, **decision_log, chronicle**,
  settlement_commands) → NOT in forecasts.db main → found in the attached
  'trades' schema."* This is INV-37-compliant, intentional routing — the
  registry's "pre-PR-S4b contamination ghost" story is simply **stale/wrong**
  for this table.
- A **third**, much smaller write path exists outside all three canonical
  DBs: `src/engine/cycle_runner.py:660` opens `get_connection()` with no
  path arg → resolves to `ZEUS_DB_PATH = state/zeus.db` (a **1 MB legacy
  file**, confirmed via `ls -la`, distinct from `zeus_trades.db`), and
  `cycle_runner.py:1138` calls `store_artifact(conn, artifact)` against it
  every cycle. This is architecturally stray (writes outside the 3-DB
  split entirely) but immaterial in bytes.
- Canonical per manifest: **none declared correctly** — `db:world` entry
  (`db_table_ownership.yaml:789-794`, legacy_archived) is plausibly the one
  genuinely-dead copy (no writer found targeting it). `db:trade` is the
  live 8.16 GB canonical home; registry should say so.
  Risk of dropping trade's `decision_log` per the stated 2026-08-09 date:
  **SEVERE** — would delete the live settlement-P&L audit trail and the
  auction-receipt evidence chain referenced by
  `payload_reference_decision_log_id` dedup logic
  (`src/engine/global_batch_runtime.py:1997`).

### 3. `chronicle` (db: trade) — same mislabel pattern, smaller stakes

- Registry (`db_table_ownership.yaml:2537-2543`): *"Ghost on zeus_trades.db
  from pre-PR-S4b init_schema(trade_conn). Drop after 2026-08-09."* Meanwhile
  `db:world` (`db_table_ownership.yaml:712-717`) is declared the canonical
  `world_class` copy.
- Code: `src/state/chronicler.py:1-5` module docstring says *"All writes go
  to the chronicle table in zeus.db"* — itself stale (the 1 MB legacy file
  again, not the actual target). The only two call sites,
  `src/execution/harvester.py:2291` and `src/execution/harvester.py:2808`,
  both fire `log_event(conn, ...)` using the same forecasts-MAIN +
  trades-ATTACHed `conn` from `run_harvester()`'s
  `forecasts_connection_with_trades_flocked` block — per the same
  `db.py`-comment-confirmed resolution rule as `decision_log` above,
  `chronicle` is not in forecasts.db main, so this lands in the
  trades-ATTACHed schema, i.e. **`zeus_trades.db`**.
- Census: `trades.chronicle` = 376,832 bytes, 452 cells — small, but
  non-zero and (per the two live call sites above, `SETTLEMENT` and
  `SETTLEMENT_SNAPSHOT_SOURCE` events fired on every harvester settlement
  pass) **actively growing**, not frozen pre-PR-S4b residue.
- No evidence was found of any code path still writing `world.db`'s
  `chronicle` — it is plausibly the true dead copy today (bytes
  UNMEASURED, world census pending). Three sources disagree on where
  chronicle canonically lives: the docstring (zeus.db), the registry
  (world.db), and the actual runtime resolution (trade.db, ATTACHed). None
  of the three has been corrected to match the other two.
- Canonical per manifest: `world` (world_class). Actual runtime home: `trade`
  (mislabeled ghost). Risk of dropping trade's `chronicle` per the stated
  2026-08-09 date: **MODERATE** — small in bytes but destroys the only
  audit trail of `SETTLEMENT`/`SETTLEMENT_SNAPSHOT_SOURCE` events (harvester
  learning-source attribution), and the write code would start raising
  `sqlite3.OperationalError: no such table: chronicle` on the next
  settlement cycle post-drop (see `chronicler.log_event`'s explicit
  "table missing" branch, `src/state/chronicler.py:56-61`).

**Common root cause across all three**: the registry's `legacy_archived` /
"Ghost... Drop after <date>" classification for the ~35 tables under the
"PR-S4b §4... known legacy_archived ghost entries on db: trade" block
(`db_table_ownership.yaml:2437-2452`) was a **one-time snapshot judgment**
made when `init_schema(trade_conn)` was first found creating 66 misplaced
world tables on `zeus_trades.db`. It was never re-verified against later
INV-37 remediation work (the `forecasts_connection_with_trades_flocked`
ATTACH pattern, `harvester.py:915-921`) that *intentionally* repointed
`decision_log`/`chronicle`/other trade-class writes through that exact same
physical table set. The registry conflates "this table was accidentally
created here once" with "no code writes here now" — the second claim was
not re-checked when the routing changed. **Every table in that block with
a non-zero cell count in the census is a candidate for the same
misclassification** — the two checked here (`decision_log`, `chronicle`)
were both wrong; the rest are unverified (see full list in §(a) below,
"UNVERIFIED — inherited misclassification risk" column).

---

## (a) Near-duplicate table names across DBs (from `db_table_ownership.yaml`)

Full set of table names that appear on 2+ physical DBs, grouped by table.
"Canonical" = the manifest's non-`legacy_archived` entry (or the entry
`domains.py`/notes explicitly correct to). Bytes are `census_raw.jsonl`
values where available; `trades`=`zeus_trades.db`, `forecasts`=`zeus-forecasts.db`,
`world`=UNMEASURED everywhere (no census yet).

### K1-split ghosts (forecast-class tables, canonical on forecasts.db, ghost on world.db)
`observations`, `settlements`\*, `settlement_outcomes`, `source_run`, `job_run`,
`source_run_coverage`, `readiness_state`, `ensemble_snapshots`,
`calibration_pairs_v2`(→renamed `calibration_pairs`), plus the
`*_archived_2026_05_11` one-time snapshot siblings (intentional archive
copies, not a duplicate-write bug — excluded from risk scoring).
World-side bytes UNMEASURED for all. Forecasts-side confirmed non-trivial:
`observations` 92.5 MB / 71,613 cells; `market_events` (separate family, see
below) 22.9 MB / 56,163 cells; `source_run_coverage` 52.3 MB / 101,479 cells.
\*`settlements` is the TOP FINDING #1 above — actively written, not dead.

### Trade-cutover ghosts (trade-class tables, canonical on trade.db, ghost on world.db)
`trade_decisions`, `execution_fact`, `position_events`, `position_current`,
`position_lots`, `venue_commands`, `venue_command_events`,
`venue_order_facts`, `venue_trade_facts`, `venue_submission_envelopes`,
`settlement_commands`, `settlement_command_events`. Registry states these
were **verified 0 rows on world.db at the 2026-05-17 audit**
(`db_table_ownership.yaml:562-565`) — the one family in this document with
an explicit historical zero-row citation, not just an assumption. No
contradicting writer found in `src/` (grepped `position_events` INSERT sites
— all target `conn` params fed by trade connections per manifest
`cutover_note`s). Treated as **correctly classified**.

### PR-S4b-era "world-schema-on-trade" ghosts (db.py:5738-era `init_schema(trade_conn)` contamination, ~35 tables)
All declared `legacy_archived`/`trade_class` "Ghost...Drop after
2026-08-09" on `db: trade`, canonical said to be `world`. Two verified
**wrong** (decision_log, chronicle — see TOP FINDING). Remaining members
with non-zero trade-db census bytes — **UNVERIFIED, inherit the same risk**:

| table | trades bytes | trades cells | verified? |
|---|---:|---:|---|
| `decision_certificates` | 238,821,376 | 58,021 | mislabel risk UNVERIFIED (registry says legacy_archived ghost; 58k rows is not "empty ghost" scale) |
| `probability_trace_fact` | 50,089,984 | 45,401 | registry cites "33k misplaced rows... redirected to world in PR-S4b §3" (`db_table_ownership.yaml:3111-3120`) — **current count (45,401) exceeds the cited historical snapshot (33k)**. HYPOTHESIS: either the note is stale or the redirect did not fully stop trade-side writes. Not independently re-traced (time-boxed) — flag for follow-up. |
| `availability_fact` | 14,209,024 | 27,849 | registry cites "24k misplaced rows... Writes redirected to zeus-world.db in PR-S4b §3" (`db_table_ownership.yaml:2505-2514`) — current count (27,849) likewise exceeds the cited 24k. Same HYPOTHESIS as above. |
| `opportunity_fact` | 10,711,040 | 41,162 | **registry itself flags this as a live divergence** (see TOP FINDING preamble) — writer `log_opportunity_fact` confirmed writing trade.db despite `db:world` canonical declaration. |
| `provenance_envelope_events` | 56,279,040 | 57,249 | not independently re-traced; 57k rows on a table registered "Ghost...Drop" is inconsistent with its own `world` sibling being labeled `legacy_archived` too (**both copies of `provenance_envelope_events` are legacy_archived in the registry — no declared canonical at all**, a gap distinct from the mislabel pattern). |
| `market_price_history` | 142,741,504 | 657,409 | world copy also `legacy_archived`; trade copy `trade_class` (not "Ghost" text, inconsistent labeling within the same PR-S4b block — some entries in this block use `trade_class`, others `legacy_archived`, with no visible rule distinguishing them). |
| `token_suppression_history` | 43,069,440 | 94,342 | same pattern as `market_price_history`. |
| `venue_order_facts` (dup name, different from the trade-cutover-family entry) | — | — | N/A, this is the single canonical trade entry, not a PR-S4b dup. |

Every row in this table represents bytes that are either (a) genuinely
dead residue safe to drop, or (b) actively written and mislabeled — and the
manifest text alone cannot distinguish which without a `file:line` grep per
table, which this lane only had time to do for the two headline cases
above. **Recommend**: before any drop of a "PR-S4b ghost" table, re-run the
same grep-for-writers check done here for `decision_log`/`chronicle`.

### `fact_revocations` / `schema_epoch` — NOT duplicates (correctly triplicated by design)
Both are intentionally instantiated once per owning DB (trade/world/forecasts)
per `db_table_ownership.yaml:2776-2869` — owner-local tables tagging only
that DB's own rows, explicitly documented as the DIQ packet's replacement for
a single cross-DB-ATTACH design. **Legitimate**, excluded from risk scoring.

### `executable_market_snapshots` / `book_hash_transitions` — legitimate ownership migration (2026-05-20)
Both moved canonical ownership from world→trade on 2026-05-20 ("live
substrate repair"). Trade-side confirmed massive and growing:
`executable_market_snapshots` **46,285,611,008 bytes (46.3 GB!),
17,989,157 cells** — the single largest table in the whole census, larger
even than `calibration_pairs`' partial read at time of check.
`book_hash_transitions` **2,287,394,816 bytes (2.29 GB), 10,272,682 cells**.
World-side ghost bytes UNMEASURED (no census). Single writer confirmed for
`book_hash_transitions` (`src/state/book_hash_transitions.py:76`, no other
INSERT site) — no evidence of continued world-side writes. Treated as
correctly classified, **but flagged for §(d) below as unbounded-growth risk
independent of the duplicate-write question** — this is the biggest single
byte consumer found in this lane and has zero retention/compaction script.

---

## (b) Single-event fan-out: legitimate projections vs true duplicates

Per task instruction, "event + derived projection is LEGITIMATE — flag only
true duplicates written from the same source data."

**Legitimate (latest-state mirror over an append log, different read
pattern, not flagged)**:
- `executable_market_snapshots` (append, 46.3 GB) → `executable_market_snapshot_latest`
  (compact per-`(condition_id, token_id)` mirror, 34.4 MB) — explicit
  "hot live readers use this bounded... state first" design
  (`db_table_ownership.yaml:2722-2741`).
- `execution_feasibility_evidence` (append, **20,429,262,848 bytes / 20.4 GB**,
  30,534,122 cells) → `execution_feasibility_latest` (mirror, 108.9 MB) — same
  pattern (`db_table_ownership.yaml:1158-1176`).
- `edli_live_order_events` (append) → `edli_live_order_projection`
  (rebuildable current-state projection) — explicit "append-only event log
  remains source of truth; this table is rebuildable current state"
  (`db_table_ownership.yaml:1401-1422`).
- `edli_live_profit_audit` (mutable current) → `edli_live_profit_audit_supersessions`
  (append-only pre-image archive) and `settlement_attribution` →
  `settlement_attribution_supersessions` — see §(d), this is the
  archive-before-overwrite pattern, legitimate by explicit design.
- `trade_decisions` bridge-row synthesis
  (`src/state/trade_decisions_synthesizer.py`): reconstructs a **structural**
  bridge row from `position_current ⋈ venue_commands ⋈ position_events`;
  analytical fields (edge, kelly_fraction, p_raw) are explicitly zero-filled,
  not duplicated from any source — legitimate join-derived stub, not a
  duplicate.
- `observation_prints` WU triple-write: the registry itself calls this
  **DELIBERATE** — WU hourly extrema are written to `observation_prints`
  (append-only ledger) *and* `observation_instants` + `observation_revisions`
  (`db_table_ownership.yaml:1036-1044`: *"The WU double-write... is
  DELIBERATE: the absorbing-direction reduction is idempotent under
  duplicates... retirement direction is ledger-alone once full coverage +
  retired parity."*) This is a **documented, intentional, currently-still-live
  double-write** with a stated retirement plan — not a bug, but worth noting
  as a currently-active 2x-write cost on every WU hourly tick until the
  stated retired-parity retirement happens.

**True duplicate (flagged)**:
- `settlements` / `settlement_outcomes` (forecasts) — see TOP FINDING #1.
  Same source data (city, target_date, winning_bin, settlement_value,
  provenance_json), written from the same function call, into two tables
  with overlapping columns, where one is documented dead but isn't.

---

## (c) `position_events` vs `chronicle` vs `decision_log` — trades-DB overlap

All three are triggered from the same settlement/exit lifecycle events but
serve genuinely different shapes, so they are **not** a naive duplicate of
each other's *content* (position_events = structured phase-transition rows
consumed by riskguard/P&L; chronicle = short human-readable audit line;
decision_log = full JSON artifact/receipt blob) — this is legitimate
event-fan-out into differently-shaped sinks, not the same fact repeated
verbatim. The actual bug found in this lane is not that these three overlap
with each other, but that **two of the three (`chronicle`, `decision_log`)
are mislabeled dead in the registry while still being written** — see TOP
FINDING #2 and #3. `position_events` itself is correctly classified
(world-side ghost verified empty at 2026-05-17 audit, no contradicting
writer found).

**`hourly_observations`**: registered on both `world`
(`legacy_archived`, "No INSERT matches in src/ as of PR-S4b audit
2026-05-18") and `trade` (`legacy_archived`, "Ghost... Drop after
2026-08-09"). Independently verified in this lane:
`grep -rn "INSERT.*INTO hourly_observations"` across `src/` → **zero hits**,
and census confirms `trades.hourly_observations` = 4,096 bytes, **0 cells**
— genuinely empty, genuinely dead. This is the one table in the whole "PR-S4b
ghost" family that the registry got right — **not** a forecasts-class table
duplicated into the wrong DB (no forecast-class registration exists for this
name at all; it is legacy pre-K1 nomenclature, superseded by
`observation_instants` which is correctly forecast-adjacent world-class).
No finding here beyond confirming the registry label is accurate for this
one case (in contrast to `decision_log`/`chronicle` above).

---

## (d) Append-only supersession classification (for epoch-rotation remediation)

`src/state/append_only_supersession.py` (78 lines, read in full) implements
exactly **one** shared helper, `archive_row_before_overwrite`, used by
exactly **two** callers found via
`rg -n "archive_row_before_overwrite\(" src/`:

- `src/analysis/settlement_skill_attribution.py:1291` → archives
  `settlement_attribution` rows into `settlement_attribution_supersessions`
  before every re-grade `ON CONFLICT DO UPDATE`.
- `src/events/live_profit_audit.py:244` → archives `edli_live_profit_audit`
  rows into `edli_live_profit_audit_supersessions` before every re-audit
  `ON CONFLICT DO UPDATE`.

Both `_supersessions` siblings store a **full whole-row JSON snapshot**
(`prior_row_json`) per superseded version — i.e. every mutation of the
"current" row costs 1 UPDATE + 1 full-row-JSON INSERT. This is the smallest
possible schema footprint per the design note, but it is structurally
**append-only-unbounded**: no compaction, no TTL, no row limit found
anywhere in `src/` (`rg -n "supersessions" src/ scripts/` shows only the two
writers above and no reader/pruner).

### Full append-only-unbounded / bounded / mutable classification

Built from every table whose registry `notes` field says "append-only" or
"append/upsert-only", cross-checked against retention scripts
(`find scripts -iname "*prune*" -o -iname "*retention*" -o -iname "*purge*"`
→ only `scripts/prune_terminal_opportunity_events.py` and
`scripts/purge_partial_fsr_events.py` exist in the whole repo):

| classification | tables | retention script? | census bytes (largest first) |
|---|---|---|---|
| **append-only-unbounded, NO compaction script** | `executable_market_snapshots`, `execution_feasibility_evidence`, `decision_log`, `book_hash_transitions`, `ensemble_snapshots`(forecasts), `calibration_pairs`(forecasts), `decision_certificates`, `market_price_history`, `position_events`, `venue_order_facts`, `venue_trade_facts`, `venue_submission_envelopes`, `position_lots`, `provenance_envelope_events`, `edli_live_order_events`, `chronicle`, `token_price_log`, `token_suppression_history`, `probability_trace_fact`, `availability_fact`, `wallet_fill_observations`, `payout_observations`, `settlement_attribution_supersessions`, `edli_live_profit_audit_supersessions`, `executable_market_snapshot_invalidations`, `decision_certificate_edges`, `decision_compile_failures`, `edli_live_cap_usage`, `edli_live_cap_day_slots`, `edli_live_cap_rate_window`, `db_chunk_boundary_events`, `market_channel_connectivity_events`, `shoulder_exposure_ledger`, `regret_decompositions` | none found | `executable_market_snapshots` 46.3 GB, `execution_feasibility_evidence` 20.4 GB, `calibration_pairs` ≥12.8 GB (partial read, still growing at census time), `ensemble_snapshots` 3.59 GB, `decision_log` 8.16 GB, `book_hash_transitions` 2.29 GB — **these six alone account for ≥93 GB of the ~100 GB `zeus_trades.db` + ~43 GB `zeus-forecasts.db` files**, entirely without a compaction path. This IS the epoch-rotation remediation's target list. |
| **append-only, bounded by `opportunity_events` retention job** | `opportunity_events` (has `scripts/prune_terminal_opportunity_events.py`, per `PLAN.md:36` "唯一 retention 脚本") | yes — the **only** table in the entire audit with an active pruner | `opportunity_events` bytes not yet in census |
| **partial-purge only (FSR events)** | (FSR-related events, name not confirmed byte-level — `purge_partial_fsr_events.py` exists but this lane did not trace its target table) | partial, script exists | UNVERIFIED |
| **mutable, single-row-per-key (paired with an unbounded supersessions sibling)** | `edli_live_profit_audit`, `settlement_attribution` | N/A — mutable by design, cost is pushed to the unbounded sibling above | `edli_live_profit_audit` 5.67 MB / 4,142 cells; `settlement_attribution` not yet in census |
| **mutable, latest-state mirror (paired with an unbounded append log)** | `executable_market_snapshot_latest`, `execution_feasibility_latest`, `edli_live_order_projection`, `wallet_balance_head`, `position_current`, `readiness_state`, `fill_sync_watermarks` | N/A — legitimate, bounded by construction (one row per key, no history kept here) | small, bounded (`position_current` 917 KB / 1,311 cells) |

**Two tables named identically appear in both the unbounded list and would
be double-counted with §(a)'s PR-S4b ghost family if that family turns out
to be genuinely dead**: `decision_certificates`, `provenance_envelope_events`,
`probability_trace_fact`, `availability_fact` are append-only-unbounded on
their canonical (world, unmeasured) home **and** carry non-trivial byte
counts on their supposedly-dead trade-db ghost twin (see §(a) table) — until
each is individually re-verified for an active writer the same way
`decision_log`/`chronicle` were, **do not assume the trade-side copy is
inert for epoch-rotation sizing purposes.**

---

## Bytes-wasted summary (measured only; world.db entirely excluded, unmeasured)

| finding | bytes confirmed wasted/duplicated | confidence |
|---|---:|---|
| `settlements`(forecasts) duplicate of `settlement_outcomes` | 11.7 MB (settlements side only; settlement_outcomes bytes unmeasured but presumably larger — this is the READ-DEAD side) | CONFIRMED (code) |
| `decision_log`(trade) mislabeled — NOT wasted, is live; risk is deletion, not waste | 0 (all 8.16 GB is live data) | CONFIRMED — reclassify risk, not "duplicate" |
| `chronicle`(trade) mislabeled — same | 0 (376 KB is live) | CONFIRMED — reclassify risk, not "duplicate" |
| `observation_prints` WU triple-write (deliberate, retirement pending) | ongoing 2x-3x write cost per WU tick, not yet quantified in bytes this lane | CONFIRMED deliberate, bytes UNVERIFIED |
| PR-S4b ghost family possible-live subset (`probability_trace_fact`, `availability_fact`, `opportunity_fact`, `decision_certificates`, `provenance_envelope_events`, `market_price_history`, `token_suppression_history`) | up to ~536 MB combined trade-side census bytes, unknown fraction actually duplicate vs. genuinely-idle-but-mislabeled | HYPOTHESIS, needs per-table writer grep |

**The dominant finding of this lane is not bytes-wasted from true content
duplication (measured duplication is small, ~12 MB) — it is that the
registry's drop-date labels are unreliable for at least 2 confirmed and
~7 more plausible tables, several of which are multi-GB and would be
catastrophic to drop on the stated 2026-08-09/08-15/08-30 dates without
re-verifying against actual runtime writers first.**
