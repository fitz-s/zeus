# Lane W11 — Completeness Critique (what the audit is MISSING)

Read-only meta-review of PLAN.md, all 9 findings files, census_raw.jsonl (799
objects, verified: trades 274 / forecasts 163 / world 362, 0 timeouts), and the
GPT-5.6 consult. No DB queries run — only static reads, the census file, and
`grep` over `src/`. Purpose: name every direction no lane closed, every consult
P0 not verified/refuted against the code, every cross-lane number contradiction,
and every PLAN §2 known-unknown left unanswered — each with the exact probe to
close it.

Severity: **BLOCKING-the-verdict** = FINDINGS.md cannot rank P0s correctly
without this · **MATERIAL** = a stated conclusion is wrong/incomplete or a named
question is unanswered · **MINOR** = worth a line, not a blocker.

---

## BLOCKING-the-verdict

### G1 — The production `sqlite_source_id()` was never captured (consult's own "single smallest fact that reorders the P0 list")

The consult closes (line 841): *"The single smallest fact that changes the first
operational priority is the exact production `sqlite_source_id()`. A vulnerable
build makes the runtime upgrade the immediate P0 action before additional
checkpoint or live-audit concurrency."* The WAL-reset corruption race affects
builds **through 3.51.2**, fixed 3.51.3 / backported 3.50.7 / 3.44.6.

No lane captured it. The only version evidence in the entire audit is W9's
incidental *"venv-3.53.2 read-only pattern"* and *"STAT4-compiled"* — that is the
**audit's** venv, not the daemon's linked library, and the consult explicitly
warns (line 45) *"the system sqlite3 CLI is not evidence about the library linked
into Python."* PLAN §1 records the CLI as 3.51.2 — **inside the vulnerable
window**. Whether `zeus-live` / `zeus-ingest` / `zeus-price-channel-ingest` /
`riskguard` / the deploy subprocesses link a fixed build is unverified, and
`source_id` (which the consult demanded precisely because a version number does
not reveal a backported fix on the 3.50.x / 3.44.x lines) was never read from any
process. Compounding the exposure: the census itself ran full `dbstat` aggregate
traversals against all three live WAL DBs under concurrent write/checkpoint — the
exact topology the corruption gate is meant to fence **before** such concurrency.

- **Probe:** run the consult's snippet (lines 49-59) through the *daemon's actual
  launch interpreter* — resolve the python each of `main.py`, `ingest_main.py`,
  `price_channel_ingest`, `riskguard`, `deploy_live.py` executes (check the
  LaunchAgent `ProgramArguments` / venv), and print `sqlite3.sqlite_version`,
  `SELECT sqlite_source_id()`, and `PRAGMA compile_options` from each. Compare the
  source_id commit date to the 3.51.3/3.50.7/3.44.6 fix commits. Until this is on
  record, the P0 ordering in FINDINGS.md is unfounded.

### G2 — The live `no such table: main.*` cross-schema defect has no root-cause owner (consult P0 #8, manifesting in production)

W5 counted the symptom and then explicitly punted it: 157 `no such table` in
`zeus-live.err` (dominant signature `no such table: main.ensemble_snapshots`,
latest 2026-07-20 23:00:06), 55 in `zeus-post-trade-capital.err`, plus
`main.settlement_outcomes`, `calibration_pairs`, `alpha_overrides`. W5's verdict:
*"a distinct cross-DB defect (out of this lane's remit to root-cause) … Escalate
to a provenance/cross-DB lane."* No such lane ran. This is the consult's **P0 #8**
(*"unqualified names resolve to the wrong attached schema"*) happening live and
silently degrading trade-lifecycle logging — `ensemble_snapshots` /
`settlement_outcomes` live on `zeus-forecasts.db`, so a bare reference resolving
against a `main` that is `zeus_trades.db` (no forecasts ATTACH) fails soft.

Nobody produced the ATTACH-caller inventory or the unqualified-DML scan the
consult prescribes, and — critically — **nobody determined the money-path
blast radius**: is every one of these reads a fail-soft *logging* path, or does
some decision/settlement read silently receive an empty result and act on it?
W5-8 offers a candidate mechanism (the fail-open `rwc` create producing an empty
wrong-path DB) but did not confirm it against these specific call sites.

- **Probe:** `rg -n "ATTACH " src/` to inventory every attach caller and its
  qualified/unqualified DML; then trace the exact call sites emitting
  `ensemble_snapshots` / `settlement_outcomes` reads that resolve to `main` on a
  trade-rooted connection. For each, classify: (a) logging-only fail-soft, or (b)
  a money-path read that returns empty and changes a decision. (b) is a hard
  correctness bug; (a) is noise. The verdict cannot rank this finding without that
  split.

---

## MATERIAL

### G3 — macOS durability hole: `fullfsync`/`checkpoint_fullfsync` never checked; W5-1's "corruption-safe, no data-loss" is wrong for power loss on Darwin (consult P0 #9)

W5-1 concludes money-path writes run at default `synchronous=FULL` and that this
is *"corruption-safe; there is no data-loss law violation."* That reasoning is
incomplete on this host. On macOS, an ordinary `fsync()` does **not** flush the
drive's write cache — only `F_FULLFSYNC` (SQLite `PRAGMA fullfsync` /
`checkpoint_fullfsync`) does. `grep -rniE "fullfsync|f_fullfsync|checkpoint_fullfsync" src/`
→ **0 hits** (verified). So `synchronous=FULL` on this Darwin fleet fsyncs the WAL
on commit but the bytes can still sit in the disk controller cache; a **power
loss** (not just `kill -9`) can lose committed money-path transactions or, in the
worst case, corrupt — exactly the exposure the consult flags in P0 #9. W5's
durability conclusion needs revision from "safe" to "safe against process crash,
unproven against power loss because F_FULLFSYNC is off."

- **Probe:** confirm `fullfsync` is unset on every writer connection (it is, by
  grep); state the macOS power-loss consequence explicitly in FINDINGS.md;
  the remediation packet must decide `fullfsync=ON` for the authoritative trade
  DB and back it with a forced-power-loss recovery test (consult line 83).

### G4 — Census `cells` overcounts rows on overflow tables; W6/W7/W8 built row counts and per-row extrapolations on it — only W10 caught it

W10 alone flagged (line 158) that census `cells` *"exceeds `max(rowid)` … likely
double-counting overflow-chain structural cells"* and switched to `max(rowid)`.
Verified against the census: `executable_market_snapshots` table `cells =
17,989,157` but every one-per-row index on it carries `10,321,769-10,321,771`
cells → **true rows ≈ 10.32M, the table `cells` is inflated ~74%**.
`opportunity_events` table `cells = 24,737,537` vs its indexes' `17,896,630` →
true rows ≈ 17.9M, inflated ~38%. W6 cites *"cells=17,989,157 rows"* and derives
decode-volume-per-cycle from it; W7 repeats *"17,989,157 cells"* and *"24,737,537
cells"* as row counts; W8 reasons about row scale from `cells`. Every
per-row/per-decode figure resting on `cells` for a high-`mx_payload` table is
overstated.

- **Probe:** synthesis (W12) must adopt `max(rowid)` or the table's own
  one-per-row index cell count as the row basis, never census `cells`, for
  `executable_market_snapshots`, `opportunity_events`, `execution_feasibility_evidence`,
  `no_trade_regret_events`, `decision_certificates`, `decision_log` — and re-state
  W6's decode multipliers and W7/W8's row counts accordingly.

### G5 — world.db query-plan health is entirely unaudited, and world.db is the ONLY DB with statistics — contradicting the "planner blind everywhere" narrative

W9 (query efficiency) examined only a TRADES and a FORECASTS section — there is
**no WORLD DB section**, because W9 ran against a 304-row partial census before
world coverage existed. Yet world.db holds `opportunity_events` (32GB),
`no_trade_regret_events` (12GB), `opportunity_event_processing` (2.5GB) and the
**live EDLI reactor hot loop** (the process emitting 10,004 `database is locked`).
Its hot SELECT plans, index redundancy, and write-amplification are unexamined.

Worse, the census contradicts the audit's headline planner story. PLAN §1 / W9
assert the fleet *"never ran ANALYZE"* and the planner is *"blind almost
everywhere on both DBs."* The census shows **world.db carries `sqlite_stat1` (2
cells) AND `sqlite_stat4` (50 cells)** — world.db *has* been ANALYZE'd (STAT4 no
less); trades has none; forecasts has `sqlite_stat1` (1 cell). No lane noted
world's stats or asked why only world has them, whether they are stale/partial
(2 stat1 rows is near-empty — an aborted or `analysis_limit`-bounded run?), or
whether they actually help the EDLI queries.

- **Probe:** run the W9 EQP method against world.db's hot EDLI reads
  (`opportunity_events`, `opportunity_event_processing`, `no_trade_regret_events`
  writers/readers in `src/events/`), and characterize the world `sqlite_stat1`/
  `sqlite_stat4` contents (which objects have stats, how old). Correct the
  fleet-wide "never analyzed" claim to be per-DB.

### G6 — 32GB mmap on append-heavy 90GB+ DBs never analyzed (PLAN Q1.8, consult P1 #16) — it maps the COLD end

PLAN §Q1.8 named this: *"mmap 32GB < single DB volume: does the K3 cold-cache scar
recur at 94GB?"* No lane addressed it. The consult (P1 #16) supplies the
mechanism: SQLite maps the **first N bytes** of each file, i.e. the lowest
rowids = the **oldest** history, while the hot append tail (today's snapshots)
falls back to ordinary pager I/O. On the 94GB trades DB the 32GB cap maps roughly
rowid 0-36% (cold) and leaves the hot recent rows unmapped — plausibly the
opposite of the intended benefit, and the setting has a prior incident (the K3
cold-cache antibody the comment cites).

- **Probe:** no live benchmark needed to state the structural point — confirm
  from `db.py` that `mmap_size` maps from file offset 0 and that the top tables
  are rowid-append-ordered (they are, per W6/W10), then flag that the 32GB window
  covers cold history and misses the hot tail. The A/B (`mmap_size=0` vs 32GB on
  hot-query p99, page faults, RSS) belongs to an external-clone bench in the
  remediation packet, not this read-only audit.

### G7 — Early lanes (W6/W7/W8) ran on a partial, world-less census; their UNMEASURED world findings were never reconciled with the now-complete census

W7 and W8 both carry an explicit blocking caveat that `zeus-world.db` had **zero
byte coverage** ("BLOCKING CAVEAT … no size claim … can be corroborated"; "world
has no byte data at all yet"). The final `census_raw.jsonl` **does** carry all 362
world objects, and W10/W13 used it. So the world-side sizing that W7/W8 left
open — the byte magnitude of the `decision_log`/`chronicle`/`settlements`
canonical-vs-ghost **inversion**, the 12 legacy position ghost shells, the 13-table
"world-schema-on-forecasts" ghost class — is now answerable but was never closed.
W10 partially did (position ghosts confirmed 0 rows; `decision_log` world-copy = 0
rows while trades-copy = 8.16GB live), but W7/W8's conclusions were not updated
against it, leaving the synthesis to inherit stale "UNMEASURED" labels on facts
that are now measured.

- **Probe:** W12 must re-run every W7/W8 world-dependent claim against the
  complete census: confirm the world-side `settlements`/`chronicle` byte sizes
  (to finish TOP FINDING #1/#3's "which copy is canonical" call), and verify the
  13-table forecasts ghost class byte weight now that forecasts coverage is
  complete. Do not ship a verdict that still says "world unmeasured."

### G8 — Cross-file crash-atomicity invariant inventory missing (consult P0 #2/#6); W13-F2's 16 missing settlements may be its live symptom, unconnected

W5 confirmed `WriteCoordinator` *refuses* cross-file atomicity
(`CrossDatabaseTransactionUnsupported`) — the mechanism is honest. But no lane did
what the consult's P0 #2/#6 actually asks: **enumerate which money-path invariants
span DBs** and are therefore exposed to a partial commit after host failure. A
settlement logically touches `forecasts.settlement_outcomes` +
`trades.position_current` + `trades.decision_log`/`chronicle` in one business
event; a crash between their independent WAL durability points leaves them
inconsistent. W13-F2 found exactly this shape live: **16 chain-confirmed-settled
positions (`chain_state='synced'`, `phase='settled'`) with NO matching
`settlement_outcomes` row**, some 8-18 days stale. F2 filed it as a possible
"settlement path that writes position_current without a settlement_outcomes
insert" — but nobody tested whether it is instead a cross-file partial-commit /
missing-outbox symptom (consult P0 #2/#6), which would make it a systemic
recoverability gap, not a one-off writer bug.

- **Probe:** enumerate the multi-DB write operations on the settle/exit path
  (`harvester.py`, `harvester_truth_writer.py`, `harvester_pnl_resolver.py`);
  for each, classify must-be-crash-atomic vs reconcilable; then test whether the
  16 F2 gaps correlate with a specific exit route that commits the trades side
  but not the forecasts side (partial commit) vs a route that never issues the
  forecasts insert at all (writer bug). This decides whether F2 is P1-local or a
  P0 cross-file-atomicity finding.

---

## MINOR

### G9 — Overflow-page ratio per table is unmeasured (PLAN Q3, consult §22); census is aggregate-only
The census carries no `pagetype` breakdown, so the consult's page-amplification
metric (`overflow_bytes / total_btree_bytes`, overflow-chain p95) is unknown
fleet-wide. W6 correctly used `mx_payload` as a proxy and flagged it can't compute
the ratio. **Probe:** if a detailed pass is authorized, run the consult's
`pagetype`-aggregation query (lines 240-253) on the shortlist
(`decision_log` mx_payload 1.15MB, `opportunity_events` 167KB, `no_trade_regret_events`
68KB, `decision_certificates` 118KB) — but only on an external clone, since a
full detailed `dbstat` on the live 94GB file is exactly what the consult §B and
Safety-Law rule 1 prohibit. Also fold in the two smaller items: no lane
inventoried WITHOUT ROWID tables (consult §23) or read `application_id`/`user_version`
for path identity (consult P0 #7 — `grep application_id src/state/db.py` → 0 hits).

### G10 — Census live-impact (cache displacement / IO saturation / hot-query p99) during the multi-GB dbstat traversal was not measured
Every lane guarded WAL size (<512MiB) but none recorded the consult's broader stop
signals — oldest-reader age, writer/critical-query p99 vs baseline, storage
latency — during the census scan (consult lines 255-304). The census "was safe"
by WAL size, but its effect on live trading latency (OS-file-cache eviction of the
hot working set, shared-IO-queue saturation) is unknown. **Probe:** if G9's
detailed pass runs, capture hot-query p99 and IO latency around it, per the
consult's budget/stop-conditions — not just WAL bytes.

### G11 — Lock-wait distribution / connects-per-cycle / busy_timeout-exhaustion rate not quantified (PLAN Q2)
W5 gave only a HYPOTHESIS bound on connects/cycle and did not quantify how often
the **30,000ms write `busy_timeout` is fully exhausted** — yet the 13,749 +
10,004 + 3,906 `database is locked` errors ARE the 30s ceiling being blown. The
frequency/duration of those 30s stalls on the live decision path is unmeasured.
**Probe:** parse timestamps of consecutive `database is locked` bursts in
`zeus-price-channel-ingest.err` / `zeus-ingest.err` to bound how long real
writers block; runtime latency instrumentation is remediation, but the log-derived
stall frequency is extractable read-only.

### G12 — Trade-WAL never-truncating: PASSIVE-vs-TRUNCATE (W5-3) and reader-pinning (consult P1 #13) not disambiguated
W5-3 attributes the trade WAL floating at 95-373MB to the intended TRUNCATE
shipping as PASSIVE. But a long-lived **unfinalized read cursor** (the bulk
calibration scans `ens_bias_repo.py`, `replay.py`, or any partially-consumed
generator) would *also* pin the WAL floor and block truncation — consult P1 #13.
Both causes were not separated, so a remediation that only swaps PASSIVE→TRUNCATE
may still not truncate if a reader is pinning. **Probe:** `rg` for long-lived /
generator-style read cursors over the trade DB held across cycle boundaries;
correlate their lifetimes with the WAL floor. (`sqlite3_txn_state`/oldest-reader
instrumentation is remediation; the cursor-lifetime code audit is doable now.)

### G13 — macOS in-process flock thread-exclusion (consult P0 #3) not resolved from code
W5 established `db_writer_lock` is `flock`-only (cross-process) and that `world`
uses an in-process `threading.Lock`, but did not resolve whether two threads in
**one** process both taking `db_writer_lock(TRADE, LIVE)` actually exclude — the
consult's P0 #3 (Apple `flock` is file-scoped, so a fresh `open()` per thread may
not block). **Probe (code, not live test):** read `db_writer_lock.py:89-140` to
confirm whether it opens a fresh fd per acquisition (no exclusion between two
same-process threads) or reuses one; if fresh-fd-per-call, same-process
concurrent LIVE writers of the trade DB are unserialized at that layer and rely
on `busy_timeout` — a code-provable extension of the W5 fragmentation finding.

### G14 — Two money-path anomalies surfaced by the audit are deferred with no owner assigned
W13-F2 (16 chain-settled positions missing from the canonical `settlement_outcomes`
truth table) and W10 (`forecasts.calibration_pairs` frozen 51 days while
settlements continue today — the calibration/Platt loop may be training on data
that stopped 2026-05-31) are both money-path-adjacent, both explicitly punted
("out of scope for a read-only lane," "flag for the calibration-boot-profile
owner"). A disk-audit verdict that drops them loses them. **Probe:** FINDINGS.md
must route F2 to a settlement-semantics owner and the calibration freeze to the
calibration-boot owner as tracked follow-ups, distinct from the disk/retention
remediation packets — they are correctness findings the census happened to
expose, not storage findings.

---

## Coverage scorecard (consult P0/P1 → lane)

| Consult item | Addressed by | Status |
|---|---|---|
| P0 #1 SQLite build / source_id | — | **G1 OPEN (blocking)** |
| P0 #2 cross-file partial commit | W5 (mechanism only) | **G8 partial** |
| P0 #3 macOS flock thread exclusion | W5 (cross-proc only) | **G13 partial** |
| P0 #4 split/unenrolled writers | W5 (headline) | CLOSED |
| P0 #5 env write-class race | W5 CLAIM 2 | CLOSED |
| P0 #6 interrupt_main | W5 CLAIM 3 | CLOSED |
| P0 #7 path-identity split brain | W4, W5-8 | mostly; app_id/inode markers **G9** |
| P0 #8 unqualified→wrong schema | W5 (punted) | **G2 OPEN (blocking)** |
| P0 #9 durability pragmas | W5-1 (synchronous only) | **G3 fullfsync missing** |
| P0 #10 backups unrestorable | W13-F1 | CLOSED |
| P1 #11 checkpoint false-green | W5-2 | CLOSED |
| P1 #12 physical WAL misleads | W5 | CLOSED |
| P1 #13 unfinalized cursors | — | **G12 OPEN** |
| P1 #14 autocheckpoint spikes | W5 (structural) | partial |
| P1 #15 cache×N handles | W5-4 (count only) | partial (RSS unmeasured) |
| P1 #16 32GB mmap append-heavy | — | **G6 OPEN** |
| P1 #17-18 temp-store / dirty-txn | W5 (BulkChunker) | partial |
| P1 #19 APFS snapshot free-space | W13-F9 | CLOSED |
| §21-25 physical/planner amp | W6, W9, W10 | CLOSED (trades/forecasts); **G5 world** |
| §22 overflow ratio | W6 (mx_payload proxy) | **G9 ratio unmeasured** |
| §26 rotation late-write/uniqueness | W7(d) classification | partial (rotation-safety unassessed) |

PLAN §2 known-unknowns unanswered: Q1.8 mmap (**G6**), Q2 lock-wait distribution
(**G11**), Q3 overflow-page ratio (**G9**) and CLI-vs-driver version (**G1**).
Everything else in §2 has a lane.
