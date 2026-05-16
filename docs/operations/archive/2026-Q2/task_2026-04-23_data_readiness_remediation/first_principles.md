# P-0: First Principles of Zeus Quant Data Integrity

**Status**: **CLOSED (8 of 8 packets APPROVED by critic-opus, 2026-04-23)**. Originally drafted 2026-04-23; approved by critic-opus (MD5 `29715433...` initial baseline + §5 step 6 addendum at `e238cf80...`); used as the decision framework for all 8 packets in the workstream.

**Workstream outcome** (at closure):
- `state/zeus-world.db` settlements table: 1,561 rows (1,469 VERIFIED earned by SettlementSemantics gate + 92 QUARANTINED with 8 enumerable reasons)
- Every row carries the 4 INV-14 identity columns + `provenance_json` with `decision_time_snapshot_id`
- Writer signature on all 1,561 rows: `p_e_reconstruction_2026-04-23`
- Schema carries `settlements_authority_monotonic` trigger (P-B)
- Rollback chain: 4 binary snapshots + md5 sidecars on disk (pre-pg / pre-pb / pre-pf / pre-pe)
- DR-33-A follow-up (code-only harvester-live scaffold, flag OFF by default) committed at `docs/operations/task_2026-04-23_live_harvester_enablement_dr33/`

**One-sentence summary**: 1,562 provenance-hostile bulk-batch rows → 1,561 canonical-authority-grade rows whose every VERIFIED is earned via per-row evidence and every QUARANTINED carries a closed-set reason in machine-readable provenance_json.

Full audit trail in `work_log.md`. Closed R3-## traceability in Appendix C below. Per-packet evidence at `evidence/bulk_writer_rca_final.md` (P-A), `evidence/harvester_gamma_probe.md` (P-D), `evidence/settlement_observation_agreement_audit.md` (P-C), `evidence/pg_corrections_plan.md` + `evidence/pg_execution_log.md` (P-G), `evidence/pb_schema_plan.md` + `evidence/pb_execution_log.md` (P-B), `evidence/pf_quarantine_plan.md` + `evidence/pf_execution_log.md` (P-F), `evidence/pe_reconstruction_plan.md` + `evidence/pe_execution_log.md` (P-E).

---

**Created**: 2026-04-23
**Authority basis**: AGENTS.md + architecture/invariants.yaml + architecture/fatal_misreads.yaml + docs/authority/zeus_current_architecture.md + 3 rounds of review lessons (v3/v4/v5/v6)
**Purpose**: establish the DECISION FRAMEWORK that every packet P-A through P-H anchors to. Without this, each packet drifts into ad-hoc patching that introduces new bugs per-round.

**Critical directive from operator (2026-04-23)**: stop finding "one after another" problems. Ground every execution decision in first principles. No more 20-P0-per-round iteration cycles.

---

## Section 1 — What Zeus Is (economic reality)

### ⚠ BASELINE FACT: settlements table is 100% corrupted ⚠

SQL verified 2026-04-23:
```
SELECT (SELECT COUNT(*) FROM settlements) AS total,
       (SELECT COUNT(*) FROM settlements WHERE winning_bin IS NULL AND authority='VERIFIED') AS impossible,
       (SELECT COUNT(*) FROM settlements WHERE settled_at LIKE '2026-04-16T12:39:58%') AS bulk_rows,
       (SELECT COUNT(DISTINCT settled_at) FROM settlements) AS distinct_settled_at
→ 1562 | 1562 | 1562 | 1
```

**All 1,562 rows in the settlements table are the 2026-04-16 bulk-batch, all `authority='VERIFIED'`, all `winning_bin=NULL`. There is NO clean baseline row. No row predates this batch; no row escaped.**

Consequences for packet design:
- **P-E is RECONSTRUCTION, not patching**. `UPDATE` against existing rowids perpetuates the broken provenance chain under new column values. Correct approach: `DELETE` (or TRUNCATE via drop+create) then re-`INSERT` with complete identity from first principles.
- **Any packet that `SELECT`s from settlements BEFORE P-E is reading garbage.** Downstream consumers (calibration, replay, Platt) must be blocked from settlements reads until P-E completes.
- **The 22-90 hard-quarantine rows are not exceptions** — they are the set we choose NOT to reconstruct because observation evidence is missing. They will be re-inserted with `authority='QUARANTINED'` + reason, not left as existing-row downgrades.

---

### Economic reality

Zeus is a **discrete-contract weather probability trading system** on Polymarket. Every dollar flows through this path:

```
contract semantics
    → source truth (what will settle the market?)
    → forecast signal (ECMWF TIGGE ensemble)
    → calibration (raw probability → market-calibrated)
    → edge (calibrated P vs market implied P)
    → sizing (Kelly with execution-price distribution)
    → execution (Polymarket CLOB order)
    → monitoring (intraday observation vs position)
    → settlement (daily close, UMA resolution)
    → learning (settlement truth → calibration update)
```

**The most expensive failures are semantic, not syntactic**. Code that runs is necessary but not sufficient — correct code on wrong data produces wrong trades.

**The Zeus "money path" assumption chain** (failure at any link = silent loss):

1. Contract truth: `(city, target_date, bin_shape, unit, rounding_rule)` per market
2. Settlement truth: `(source_family, station, unit)` that Polymarket actually resolves against
3. Historical observation truth: past `(source, high_temp, authority)` that we can learn from
4. Forecast truth: ECMWF TIGGE ensemble members at `issue_time` for `target_date`
5. Calibration truth: `(forecast_bin, settlement_bin, win/lose)` pairs for Platt fitting
6. Decision truth: inputs at `decision_time` (no look-ahead)
7. Execution truth: `(order, fill_price, fill_time, cost)`
8. PnL truth: realized + mark-to-market, attributable per strategy_key

**Each link has a failure mode that 1M code reviews miss because the code is not where the bug is**.

---

## Section 2 — The 10 Invariants of Data Integrity

These 10 invariants are the lens through which every packet is evaluated. They are derived from `architecture/invariants.yaml` **INV-01..INV-10, INV-13..INV-22 (20 total; INV-11 and INV-12 are reserved and unassigned — verified via `grep -oE 'INV-[0-9]+' architecture/invariants.yaml | sort -u`)**, distilled for this remediation workstream.

**Scope-excluded INVs** (operationally live in Zeus, but outside this remediation workstream's direct scope):
- INV-01 (exit ≠ close), INV-02 (settlement ≠ exit) — K2 runtime lifecycle, not data ingest
- INV-04 (strategy_key sole governance), INV-05 (risk must act) — K1 governance, not data
- INV-07 (lifecycle grammar finite) — K0 lifecycle, not settlement write
- INV-13 (Kelly provenance registry) — K2/K3 sizing, not settlement truth
- INV-16 (Day0 low Platt routing) — K2 runtime signal, not settlement
- INV-18 (chain 3-state), INV-19 (RED sweeps), INV-20 (authority-loss degradation) — K1/K2 risk/exit, not data
- INV-21 (Kelly distribution) — K2 execution

**In-scope INVs mapped to the 10 INV-FP-* below**: INV-03, INV-06, INV-08, INV-09, INV-10, INV-14, INV-15, INV-17, INV-22.

### INV-FP-1: Provenance chain is unbroken

**Claim**: every value in a canonical table carries `(source, timestamp, authority, transform_history)`. Any break = QUARANTINE, not silent default.

**Anti-pattern caught**: **all 1,562 settlement rows** have `authority='VERIFIED'` but `winning_bin=NULL` — someone wrote them without the semantic contract. The `VERIFIED` stamp is a lie about what was verified. 100% of the table is provenance-hostile (see §1 Baseline Fact).

**Packet test**: before writing ANY row, name the source + writer + decision_time. If unknown, the row is QUARANTINED, never VERIFIED.

**Mapped to architecture**: **INV-03** (canonical authority is append-first and projection-backed — the bulk writer bypassed this); INV-06 (point-in-time truth); INV-09 (missing data first-class); INV-15 (forecast provenance).

### INV-FP-2: Identity preservation across JOINs (INV-14)

**Claim**: every temperature-market row carries 4 identity fields: `temperature_metric ∈ {high,low}`, `physical_quantity`, `observation_field ∈ {high_temp,low_temp}`, `data_version`. JOINs on `(city, target_date)` without these fields silently collapse high+low tracks.

**Anti-pattern caught**: v6 DR-47 tried to use `data_version='tigge_mx2t6_local_calendar_day_max_v1'` but `forecasts` table has NO `data_version` column. The axis v6 claimed to align with doesn't exist. JOIN returns 0.

**Packet test**: before joining any 2 tables, verify both have the same identity columns. If not, either (a) add them explicitly (schema_packet), or (b) document that the join CANNOT preserve track identity and is provisional only.

**Mapped to architecture**: INV-14 directly; zeus_current_architecture.md §7.1 Metric Identity Spine.

### INV-FP-3: Temporal causality (no hindsight)

**Claim**: for any row used in training/calibration: `observation.fetched_at ≤ decision_time ≤ settlement.target_date + settlement_delay`. Re-deriving a settlement TODAY (2026-04-23) for a target_date 4 months past uses observations that may have been corrected post-hoc.

**Anti-pattern caught**: NYC 2025-12-30 obs was `fetched_at=2026-04-14` — 145 days after the market settled. If we use this obs to re-derive the settlement, and a trader was looking at different (earlier) data when they decided to trade, we're learning on corrected truth, not decision-time truth.

**Packet test**: every derived settlement row MUST carry `provenance_json.decision_time_snapshot_id = obs.fetched_at` (UTC ISO8601). Calibration queries MUST filter `decision_time_snapshot_id ≤ training_cutoff` where `training_cutoff` is a named config value, not inferred.

**Mapped to architecture**: INV-06 (point-in-time truth beats hindsight).

### INV-FP-4: Semantic integrity at system boundaries

**Claim**: values entering a canonical table must pass the type's assertion gate. SettlementSemantics.assert_settlement_value() is MANDATORY for every settlement_value write. canonical_bin_label() is MANDATORY for every winning_bin string.

**Anti-pattern caught**: `_format_range` at harvester.py:506-514 returned strings like `"-999-40"` (sentinel format, not canonical). Any row it produced would be unreadable by bin-parsers.

**Packet test**: before writing any settlement_value or winning_bin, trace its construction back to the assertion gate. No "just round with numpy and move on".

**Mapped to architecture**: `src/contracts/settlement_semantics.py:101` ("MANDATORY gate for all settlement DB writes"); `src/contracts/calibration_bins.py` canonical format.

### INV-FP-5: Authority is monotonic and earned

**Claim**: `authority='VERIFIED'` means "this row passed the full semantic contract". It does NOT mean "someone stamped it". Authority cannot silently regress; QUARANTINED → VERIFIED only via explicit re-activation packet; writing VERIFIED without going through the gate is forbidden.

**Anti-pattern caught**: 1,562 rows with authority='VERIFIED' but winning_bin=NULL. The stamp was applied, the gate was not.

**Packet test**: trigger `settlements_authority_monotonic` enforces `QUARANTINED → VERIFIED` requires explicit marker. But the trigger only catches UPDATEs; INSERTs that go straight to VERIFIED without the gate are still dangerous. Every INSERT path that stamps VERIFIED must be audited.

**Mapped to architecture**: **INV-03** (canonical authority is append-first and projection-backed — a row stamped VERIFIED without going through the canonical append is by definition not canonical authority); **NC-02** (no JSON→authority promotion — the same principle: authority is earned by process, not stamp); `src/state/db.py:167` CHECK constraint. INV-10 (LLM output is never authority) is related but about code-origin, not data-integrity.

### INV-FP-6: Write routes are registered (no ghost writers)

**Claim**: every writer to a canonical table is named in `architecture/source_rationale.yaml::write_routes`. Any write that bypasses a registered route is a ghost.

**Anti-pattern caught**: 1,562-row bulk insert on 2026-04-16T12:39:58.026729Z has NO registered writer. We don't know where it came from. No code in current repo produces this signature. Possibly an interactive REPL session; possibly a deleted script. Either way, the rows are provenance-hostile.

**Packet test**: `grep -rn 'INSERT INTO settlements' src/ scripts/` must match exactly the registered writers in source_rationale.yaml. Any mismatch is a ghost.

**Mapped to architecture**: **INV-03** (append-first projection — a registered writer IS a writer that uses the canonical append path); **INV-08** (one transaction boundary — a registered writer holds ONE boundary per canonical write); `architecture/source_rationale.yaml::write_routes::settlement_write::owner`; AGENTS.md §Mesh maintenance.

### INV-FP-7: Source role boundaries are strict

**Claim**: settlement_daily_source (what settles the market) ≠ day0_live_monitor_source (what we watch during trading) ≠ historical_hourly_source (training features) ≠ forecast_skill_source (forecast accuracy assessment). Each has its own validity contract, station, date_range, and evidence. **Additionally**: hourly-to-daily aggregation must preserve required extrema (max/min per settlement contract). First/last/average downsampling is wrong whenever the target metric needs extremes.

**Anti-pattern caught**: using `observations.wu_icao_history` (WU private API v1 hourly-aggregate) as a proxy for the WU website daily summary that Polymarket actually resolves against. They are DIFFERENT products. May divergence for some city-months.

**Packet test**: for every re-derivation, state which source_role applies and cite `architecture/city_truth_contract.yaml` + `docs/operations/current_source_validity.md` for the per-city binding. No "obs.wu_icao_history is close enough". If hourly→daily aggregation is in the path, name the extrema preservation rule.

**Mapped to architecture**: `architecture/city_truth_contract.yaml::source_roles`; fatal_misreads.yaml `daily_day0_hourly_forecast_sources_are_not_interchangeable`; fatal_misreads.yaml `wu_website_daily_summary_not_wu_api_hourly_max`; fatal_misreads.yaml `hourly_downsample_preserves_extrema`; fatal_misreads.yaml `airport_station_not_city_settlement_station`; fatal_misreads.yaml `hong_kong_hko_explicit_caution_path`.

### INV-FP-8: Transaction atomicity — DB commits before JSON (INV-17)

**Claim**: canonical write = `(event_append, projection_update, settlement_table_update)` in ONE transaction. Derived JSON exports (status, positions, strategy_tracker) are written AFTER the DB commits. Crash recovery = DB wins, JSON rebuilds from projection.

**Anti-pattern caught**: v3 proposed "JSON is canonical, DB is projection". Inverts INV-17. Enables silent overwrites on crash.

**Packet test**: for every script, trace: DB COMMIT → JSON WRITE. If JSON writes happen before/during the DB transaction, the ordering is broken.

**Mapped to architecture**: INV-17 ("DB authority writes must COMMIT before any derived JSON export"); INV-08 (one transaction boundary).

### INV-FP-9: Missing data is first-class truth (INV-09)

**Claim**: `NULL` does not mean "default" or "unknown — use fallback". `NULL` means "we know we don't know". QUARANTINE with reason is the correct handling; silent default is wrong.

**Anti-pattern caught**: v5 DR-41 (NULL pm_bin reconcile) originally had no shape-validity guards — if JSON had `pm_bin_lo > pm_bin_hi` (sentinel paradox), it would silently write bad data.

**Packet test**: every script that fills NULL values must have an explicit "unrecoverable" branch that QUARANTINEs with reason. Reason strings must be enumerable (fixed set); not freeform.

**Mapped to architecture**: INV-09; zeus_current_architecture.md §3 item 7 "without hindsight leakage".

### INV-FP-10: Re-derivation is suspect — mark it explicitly

**Claim**: re-deriving a settlement that was originally derived in the past introduces hindsight risk (INV-06) AND may use different observation snapshots than the live harvester did. Re-derivation output must carry explicit markers.

**Anti-pattern caught**: bulk 1,562 rows were all `settled_at=2026-04-16T12:39:58`, but target_dates span 2025-12-30 to 2026-04-15. 5-month re-derivation window. Without decision_time_snapshot_id, calibration cannot distinguish fresh vs hindsight rows.

**Packet test**: every re-derivation row's `provenance_json` includes `{source, obs_id, rounding_rule, data_version, decision_time_snapshot_id, derivation_method ∈ {live_harvester, bulk_redrive, cross_source_fallback, json_fallback}}`. Calibration queries respect these markers.

**Mapped to architecture**: **INV-06** (point-in-time truth beats hindsight truth); NC-05 (no decision→latest snapshot fallback).

---

## Section 3 — Anti-patterns catalog (accumulated across 3 review rounds)

These are concrete failure modes observed in plans v3/v4/v5/v6. Every future packet MUST NOT reproduce any of them.

| # | Anti-pattern | Rounds seen | What to do instead |
|---|---|---|---|
| AP-1 | **Ghost bulk writer** — 1,562 rows with no registered source | 1,2,3 | Identify real writer (P-A); if unidentifiable, QUARANTINE not VERIFY |
| AP-2 | **Authority stamp without integrity** — authority='VERIFIED' + winning_bin=NULL | 1,2,3 | authority IS a claim about completeness; never stamped independently of the gate |
| AP-3 | **Midpoint fabrication** — pm_high treated as observation, but it's the bin midpoint (fabricated) | 2 | settlement_value comes from observations + SettlementSemantics, never from JSON pm_high for F-city range bins |
| AP-4 | **Source role collapse** — wu_icao_history treated as WU-website settlement truth | 3 | Settlement-source-correct observation; cross-source only with audit evidence |
| AP-5 | **Axis mirage** — data_version chosen to "align" with a non-existent column in forecasts | 3 | Enumerate the actual join partners BEFORE choosing axis |
| AP-6 | **Caller count confusion** — import lines counted as call sites | 3 | Use AST, not grep; distinguish `from X import Y` from `Y(...)` invocations |
| AP-7 | **Deprecated API proliferation** — tests mandate use of deprecated helpers | 3 | Every new use of an API must check its deprecation status; tests lock in current, not legacy |
| AP-8 | **SAVEPOINT atomicity loss** — `with conn:` inside SAVEPOINT commits+releases (MEMORY L30) | 3 | Audit every `with conn:` in call chain; replace with explicit BEGIN/COMMIT where SAVEPOINT nesting applies |
| AP-9 | **Self-contradicting triggers** — trigger blocks the very operation the plan requires | 2 | Trigger creation phase MUST NOT block the phase using that operation |
| AP-10 | **Vacuous AC** — awk range collapses to single line; always-passes check | 3 | ACs must be tested with BOTH success and failure inputs before adoption |
| AP-11 | **Reinstated removed architecture** — DR-43 revived outcomePrices fallback that was deliberately removed with documented rationale | 3 | Before reviving a pattern, read the commit that removed it; explain why this time is different or pick a different pattern |
| AP-12 | **Partition sum ≠ total** — §2.B admitted 1,580 / 1,569 / 1,562 inconsistency across rounds | 1,2,3 | Every category schema must be mutually-exclusive SQL; sum verified inline |
| AP-13 | **Missing category** — Taipei WU 11 rows orphaned from all categories | 3 | Every row must fall in exactly one category; AC-R3-CATEGORIES checks via per-row matching |
| AP-14 | **Type-incompatible fabrication** — DST pm_high applied to both finite-range AND open-shoulder bins | 3 | Different bin types (finite/point/shoulder) get different handling with distinct provenance labels |
| AP-15 | **Deferred Task-Zero work** — "verify later" moves unverified claims into execution phase | 2,3 | Every number/name in the plan must be SQL-verifiable at plan-writing time, not "at Task Zero" |
| AP-16 | **Zone laundering** — inventing a new zone or relocating semantic atoms to non-K0 locations to bypass K0 packet evidence (e.g., DR-45 moving `src/contracts/bin_labels.py` → `src/shared_helpers/`) | 3 | NC-01 (no broad K0 patch without packet). Any change to `src/contracts/`, `src/types/`, `src/state/{ledger,projection,lifecycle_manager}.py` requires a K0 schema_packet with full evidence. Relocation out of K0 is itself a K0 change. |
| AP-17 | **Graph-as-truth** — using code-review-graph output as semantic authority (what settles / what validates) rather than as blast-radius / impact context (where to inspect) | ongoing | fatal_misreads.yaml `code_review_graph_answers_where_not_what_settles`. Graph is Stage-2 derived context per `architecture/task_boot_profiles.yaml`; never substitutes for invariants / source_rationale / current_*_validity |

### AP-4 refinement (P-G 2026-04-23, post-critic endorsement)

AP-4 (source role collapse) has TWO distinct shapes. Diagnosis MUST distinguish before prescribing a fix:

- **Shape A — label error**: the row's `settlement_source_type` doesn't match what Polymarket actually used to resolve. Relabel (after audit evidence proving the current label is wrong) is the correct fix. Hypothesis typically surfaces when obs exists in a cross-family source AND there is no authority-trail for why the label would differ from that obs family.
- **Shape B — collector gap**: the row's `settlement_source_type` IS historically correct (Polymarket switched settlement source over time for that city; labels correctly encode that switch), but we lack source-family-correct obs for those dates. Backfill-or-QUARANTINE per row is the correct fix. **Relabeling would INTRODUCE AP-4 in reverse** (substituting cross-family obs for a correctly-labeled row).

**Detection rule**: per-city SQL on target-date ranges grouped by settlement_source_type:
- If date ranges are **cleanly disjoint** per source_type → Shape B (source-handoff history)
- If date ranges **overlap** between labels → Shape A (muddled) or needs deeper audit
- Example Shape B observed in P-G: Taipei (CWA Mar 16-22 / NOAA Mar 23-Apr 4 / WU Apr 5-15), Tel Aviv (WU Mar 10-22 / NOAA Mar 23-Apr 15), HK (WU Mar 13-14 / HKO Mar 16-Apr 15 with Mar 20 gap). All 3 cleanly disjoint → Shape B.

**Packet test**: before prescribing "relabel", check date-range SQL. If disjoint, the label is historically correct; the problem is obs-collector coverage. Reaching for relabel to obsolete non-live runtime over a collector gap is AP-4 in reverse.

---

## Section 4 — Packet Evaluation Framework (8 questions)

Every packet (P-A through P-H) MUST answer these 8 questions BEFORE execution begins:

**Q1. What invariant does this enforce/preserve?**
Cite INV-FP-## and/or architecture/invariants.yaml INV-##. If none applies, question whether the packet is well-scoped.

**Q2. What fatal_misread does this NOT collapse?**
List each fatal_misread from fatal_misreads.yaml that the packet touches. Prove each is preserved (not re-introduced by the fix).

**Q3. What's the single-source-of-truth for every number and name?**
Every count, every city list, every source_type value in the packet must cite: (a) SQL query against state/zeus-world.db, OR (b) file:line in architecture/*.yaml, OR (c) explicit constant justification. No "about 40 cities" or "roughly 1,450 rows".

**Q4. What happens at first failure?**
Rollback, QUARANTINE, resume, or halt? Specify per error type. If the packet has "on error, log and continue" without explicit decision, it WILL corrupt silently.

**Q5. Who owns the commit boundary?**
Which function, which line, which transaction? If there are 2+ commits in the path, name each and justify why they're not atomic (per INV-08).

**Q6. How do we verify CORRECTLY?**
Every AC must be tested with (a) a scenario where it SHOULD pass and (b) a scenario where it SHOULD fail. Verified on obsolete non-live runtime or in sandbox before adoption. No awk-range checks; no trivial-pass greps.

**Q7. What new hazard does this introduce?**
Every fix creates the possibility of a new bug. Name it. Flag it. Mitigate it.

**Q8. What's the critic-opus closure requirement?**
State what the packet will deliver (files, scripts, evidence, DB changes) and what critic-opus will verify to approve closure. If the packet can't be closure-tested, it's not well-defined.

**Q9. What decision is this packet reversing (if any), and why now?**
If the packet revives/reinstates a pattern that was previously removed (e.g., DR-43 reviving the `outcomePrices >= 0.95` fallback that `harvester.py:490-491` explicitly notes was removed), name the removing commit + its documented rationale + justify why this time is different. If not reviving anything, answer "N/A". This question exists because AP-11 (reinstated removed architecture) went undetected for 2 rounds.

**Q10. What rolls back if the packet commits but downstream packet fails?**
Multi-packet rollback is invisible without explicit design. State: (a) which DB rows/files does this packet write; (b) are they idempotent if the next packet re-runs; (c) if this packet commits and a later packet fails, does this packet's output need to be undone, or is it forward-compatible with retry? No packet should commit state that can't be undone without a named script.

---

## Section 5 — Closure Protocol (critic-opus integration)

Every packet follows this lifecycle:

```
1. PRE-PACKET (team-lead)
   - Answer the 8 framework questions above
   - Post to work_log.md Section <P-X>-pre
   - Send to critic-opus for pre-review (optional, for high-risk packets)

2. EXECUTE (team-lead)
   - Run scripts, collect evidence, update DB
   - Log every SQL result, every script exit code, every file change
   - No silent skips

3. SELF-VERIFY (team-lead)
   - Re-run every AC independently
   - Confirm invariants not broken (sample queries)
   - Prepare closure request

4. CLOSURE REQUEST (team-lead → critic-opus via SendMessage)
   - List deliverables with absolute paths
   - List evidence files with absolute paths
   - List DB changes (SELECT … counts before/after)
   - Request: APPROVE | APPROVE_WITH_CONDITIONS | REJECT

5. CRITIC REVIEW (critic-opus)
   - Independent SQL / grep / topology_doctor verification
   - Invariant coverage check
   - Fatal_misread check
   - New hazard scan
   - Response with verdict

6. CLOSURE (team-lead)
   - APPROVE:
     a. `TaskUpdate` packet task status='completed'
     b. **Update Appendix C** `Status` column for each R3-## item the packet resolved: `PENDING` → `CLOSED-BY-P-X`
     c. Update `work_log.md` closing packet's section with `Closure action` timestamp + list of R3-## items closed
     d. Move to next packet per §6 dependency graph
   - APPROVE_WITH_CONDITIONS: fix conditions; re-submit (step 4); App-C Status stays PENDING until final APPROVE
   - REJECT: revise approach; may split into sub-packets; re-submit; App-C unchanged

**Per critic-opus §5 step 6 addendum (2026-04-23)**: App-C is the single source of truth for R3-## status tracking (NOT TaskList, per §App-B "One System"). Packet closure MUST atomically update App-C or closure is incomplete. Closure without App-C update = silent regression of traceability.
```

**No packet is closed without critic-opus APPROVE.** This is the structural enforcement that prevents drift.

---

## Section 6 — Packet Dependency Graph

One canonical topology (no contradictions):

```
P-0 (this doc — gates everything)
  │
  ├─► P-A (Bulk Writer RCA)            ┐
  ├─► P-D (Harvester Gamma probe)      │  parallel investigations
  └─► P-C (WU product identity audit)  ┘  (no mutual dependency)
       │
       ▼  (all 3 complete)
     P-G (v2 corrections + Denver + 2026-04-15 wrong-entry)
       │
       ▼
     P-B (Schema migration: INV-14 + provenance_json + CHECK + trigger)
       │
       ▼
     P-F (Hard quarantines: HK + Taipei known-bad rows)
       │
       ▼
     P-E (Reconstruction: DELETE + re-INSERT from observation+contract)  ┐
     P-H (Atomicity refactor — feature-flagged)                          │  parallel
                                                                          ┘
       │
       ▼
  Workstream closure (execution_log.md finalized; all success criteria §7 met)
  [Training unblock = DR-03/DR-04 forecasts packet — separate downstream packet]
```

**Ordering rules**:
- **P-0 blocks every other packet** (gate).
- **P-A, P-D, P-C run in parallel** — they are independent evidence-gathering tasks. No packet depends on the outcome of another among these three.
  - P-A outcome may affect later packets (if writer IS identified as e.g. a deleted script, we can audit git for provenance). But it does not affect P-D or P-C scope.
  - P-D outcome may affect DR-33 scope in later packets. Does not affect P-A or P-C.
  - P-C outcome (per-city go/no-go) affects P-E ONLY.
- **P-G runs after all three complete** — P-G's Denver decision depends on P-D probe output (does Gamma API have Denver 2026-04-15?); P-G's 2026-04-15 wrong-entry fix depends on P-D's market-identity discovery (scientist R3-D4: both JSON entries may be legitimate different markets).
- **P-B after P-G** — schema migration needs P-C outcome (per-city data_version choice could diverge if some cities fail audit) and P-G cleanup (no stale rows during migration).
- **P-F after P-B** — hard quarantines require the new provenance_json column from P-B.
- **P-E and P-H can run in parallel** after P-F — both touch harvester but P-H is feature-flagged; P-E is the main reconstruction. P-H must NOT be enabled live until P-E completes and is verified.
- **P-E is DELETE + re-INSERT**, NOT UPDATE (per §1 Baseline Fact). The 1,562 current rows are all corrupted; reconstruction is from scratch via observation+contract, not patching of existing rowids.

---

## Section 7 — Success Criteria for This Workstream

The workstream is complete when all of the following are true:

1. **Ghost writer is either identified OR its rows are fully quarantined with explicit provenance** (P-A closed)
2. **Harvester write path is proven functional** OR **alternative pipeline is documented** (P-D closed)
3. **WU product identity is audited per city; per-city go/no-go is a concrete list** (P-C closed)
4. **All pre-existing data corruption is addressed or explicitly tombstoned** (P-G closed)
5. **settlements schema carries INV-14 identity + provenance_json; CHECK + trigger active** (P-B closed)
6. **30+ known-bad rows are QUARANTINED with enumerable reasons** (P-F closed)
7. **Settlements table rebuilt FROM SCRATCH (delete-and-reinsert) with rows carrying full provenance chain, decision_time_snapshot_id, source-correct observation**. Final count (P-E executed 2026-04-23): **1,561 rows** — revised from initial 1,562 target because P-G's Denver 2026-04-15 DELETE (synthetic orphan per P-D Gamma probe) stays deleted. Split: **1,469 VERIFIED** (observation-derived, audit-passed via SettlementSemantics containment) + **92 QUARANTINED** (one of 8 enumerable reasons in provenance_json). Zero rows carried over as UPDATEs from the existing corrupted rowids; all 1,561 rows are DELETE+INSERT products with writer='p_e_reconstruction_2026-04-23'.
8. **Harvester atomicity refactor passes all test scenarios with feature flag** (P-H). **DEFERRED to future DR-33 live-harvester enablement packet per critic-opus P-E closure analysis**: P-D proved the harvester `_find_winning_bin` write path is STRUCTURALLY UNREACHABLE (winningOutcome absent in 412/412 Gamma closed markets); atomicity guards for a non-firing code path are work-for-its-own-sake. When DR-33 lands (applying P-D §6.1 diff to enable writes), P-H's atomicity refactor naturally ships with it as a coupled change. Feature-flagged per original plan; not a workstream blocker.
9. **critic-opus has APPROVED every packet** (workstream closure gate)
10. **execution_log.md documents every decision + every evidence artifact** (audit trail)

Only AFTER these 10 criteria are met is the workstream considered complete. Training unblock (DR-03/DR-04 forecasts packet) is a separate downstream effort.

---

## Section 8 — What NOT to Do (accumulated lessons)

1. **Do not write another plan_v7.md.** Plans accumulate defects per round; executions accumulate evidence.
2. **Do not skip critic-opus review** even if the packet feels small. AP-1 (ghost writer) was invisible for 2 rounds.
3. **Do not trust v6 (or any plan) unverified**. If a number in v6 doesn't have SQL evidence in this workstream's artifacts, it's provisional.
4. **Do not revive removed architecture** without reading the removing commit + justifying why now is different.
5. **Do not let "grep didn't find any" stand as proof**. grep has false negatives from Unicode, aliasing, dynamic imports. AST or semgrep for correctness.
6. **Do not assume fatal_misreads is complete**. Round-3 candidate 8th misread `db_authority_verified_implies_pipeline_integrity` is still unwritten. If we encounter a new systemic assumption, flag it here.
7. **Do not commit any packet's output without re-reading AGENTS.md planning-lock.** Cross-zone edits require packet justification.
8. **Do not defer verification to Task Zero**. Verify at plan-writing time.
9. **Do not invent zones or relocate K0 atoms to sidestep K0 packet evidence.** Any change to `src/contracts/`, `src/types/`, `src/state/{ledger,projection,lifecycle_manager}.py` is a K0 change requiring schema_packet + full evidence. Stateless utility helpers (time, geo) are K3 or K_utils (if that zone is formally added via a separate K0-charter-amendment packet). Semantic atoms (bin_labels, settlement_semantics) STAY in K0.
10. **Do not UPDATE the corrupted 1,562 rows in P-E**. They are 100% bulk-batch; UPDATE perpetuates broken provenance under new column values. DELETE then INSERT fresh from observation+contract.
11. **Do not use code-review-graph output as semantic authority.** Graph tells WHERE to look and WHAT the blast radius is; it does NOT tell what settles, what is source-correct, or what is currently valid (fatal_misreads.yaml `code_review_graph_answers_where_not_what_settles`).
12. **Do not trust Gamma API pagination tail silently.** `NH-G-future` (critic-opus P-G post-review): direct paginated fetches (`offset=0..N` @ `limit=100`) may silently miss events beyond offset ~1900, even though they ARE tagged correctly (observed empirically in P-G: paginated scan missed 4 of 5 LOW-temp 2026-04-15 events; direct slug queries returned all 5). For DR-33 implementation or any future Gamma-backed settlement detection, prefer slug-based direct fetch OR loop-until-empty-with-smaller-page-size. Paginated fetch is a `convenience, not an authority`; verify every "empty tail" claim with at least one slug probe for a known-expected market.
13. **SQL `RAISE(...)` takes exactly one string argument; never rely on Python adjacent-string-literal concatenation inside triple-quoted SQL heredocs.** `NH-B3` (P-B first-run failure): the multiline SQL `SELECT RAISE(ABORT, 'msg line 1; ' 'msg line 2');` is valid Python (adjacent literals concatenate) but two separate argument tokens in SQL — SQLite rejects it with `near "'...'" : syntax error`. Use a single quoted string, or SQL `||` concatenation: `SELECT RAISE(ABORT, 'msg line 1; ' || 'msg line 2');`. This pitfall appears only when DDL is executed via `sqlite3` CLI OR via `conn.executescript()`; single-statement `conn.execute()` that parses the full string also trips it. Standardize on single-string messages for RAISE.
14. **Schema-migration snapshot integrity check must be asymmetric between first-run and re-run.** `NH-B4` (P-B first-run recovery): on first run, the pre-migration snapshot must match the main DB (`md5(main) == md5(snapshot)`). On re-run after any partial mutation, main has diverged by design — the check becomes `md5(snapshot) == recorded_hash_file`. Record the snapshot hash atomically with the `cp` and verify `snap == recorded` on re-run to detect snapshot corruption without false-failing on legitimate re-run recovery.
15. **When introducing a new string format that will be read by existing parsers, empirically verify round-trip BEFORE integration.** `NH-E1 promoted to rule` (P-E C1 lesson): the `≥X°C` / `≤X°C` unicode shoulder form LOOKED correct (human-readable, semantically clear) but silently misparsed through `_parse_temp_range` as POINT bins because the existing regex uses `re.search` instead of `re.fullmatch`. Text form `X°C or higher` / `X°C or below` round-trips correctly. Broader pattern: any new format (bin labels, provenance keys, JSON field names) consumed by parsers/codepaths not owned by the new packet MUST be validated against an actual parse + round-trip + assertion BEFORE the format ships. Don't trust that "looks right" = "parses right". Test with pathological inputs too (adjacent substrings, prefix/suffix chars, case variants).

---

## Section 9 — Critic-Opus Special Instructions

critic-opus, when reviewing any packet, your PRIMARY verification targets are:

1. **SQL numbers**: reproduce every count claim. Tolerances: **±0 for partition sums and row-count claims**; **statistical claims must include sample size + estimator method + explicit CI source** — bare percentage claims earn ±0 tolerance (no implied CI).
2. **File:line citations**: verify with grep. Any drift = P0.
3. **Invariant violations**: for INV-FP-1 through INV-FP-10, name any weakening. Also check INV-## from architecture/invariants.yaml directly if the packet touches an in-scope INV not captured here.
4. **AP-1 through AP-17**: scan the packet for any re-occurrence.
5. **Q1-Q10 framework answers**: demand explicit, evidence-backed answers.

**Your authority boundary**:
- You **may** run: `SELECT` queries, `grep`, `sed`, `awk`, `cat`, `wc`, Python AST inspections, `topology_doctor` (any read-only flag), git log / git show / git blame.
- You **may NOT** run: `INSERT`, `UPDATE`, `DELETE` against any DB; file writes / Edits / renames to repo files; `launchctl`, `gh` / `git push`, anything that mutates external state. Your role is to verify, not to execute.
- If a verification requires mutation (e.g., "does this INSERT succeed?"), request a test fixture DB or ask team-lead to run it.

Your verdict strings:

- `APPROVE`: everything checks; packet closes.
- `APPROVE_WITH_CONDITIONS`: minor fixes needed; list each as `[condition_id: action]`.
- `REJECT`: substantive defect(s); list as `[finding_id: severity — evidence — correction]`.

**Never approve with "looks good". Always cite.**

**Escalation path if team-lead disputes REJECT**:
1. Team-lead may NOT silently override. Must either: (a) address every finding in the REJECT, or (b) request external arbitration.
2. External arbitration = operator (human) reviews the finding + team-lead's counter-argument. Operator decides: finding stands OR finding is overridden with documented rationale.
3. Documented rationale lands in `docs/operations/task_2026-04-23_data_readiness_remediation/critic_overrides.md` with operator signature line. This becomes part of the permanent record.
4. Default path: team-lead addresses findings. Override is exceptional.

---

## Appendix A — Cross-reference to round-1/2/3 findings

Every finding from rounds 1/2/3 maps to an anti-pattern here:

- Round 1 P0s (architect+critic on v4): AP-1, AP-2, AP-3, AP-6, AP-12, AP-13
- Round 2 P0s (on v5): AP-4, AP-5, AP-8, AP-9, AP-10, AP-15
- Round 3 P0s (on v6): AP-5 (again), AP-6 (again), AP-7, AP-11, AP-13 (Taipei WU), AP-14, **AP-16** (DR-45 zone laundering)

Detailed per-finding → AP → packet traceability is in **Appendix C**.

**Plan-file history footnote**: the sequence is `plan.md` (round-1 draft, not `plan_v1`) → `plan_v2.md` → `plan_v3.md` → `plan_v4.md` → `plan_v5.md` → `plan_v6.md`. `ls plan_v*.md | wc -l` = 5 (v2..v6); `ls plan*.md | wc -l` = 6 (includes original `plan.md`). First principles is NOT a plan; it is the decision framework that supersedes the plan-revision approach per §8 rule 1.

---

## Appendix B — The "One System" Principle

A quant trading system has ONE ledger, ONE truth, ONE projection. v3 proposed 2 ledgers (JSON + DB). v5 proposed source-family data_version (incompatible with forecasts physical-quantity axis). Both are "two sources of truth" anti-patterns.

Every packet must ask: **am I creating a second source of truth?** If yes, why?

---

## Appendix C — R3 TODO → AP → Packet Traceability

Every round-3 review finding with its anti-pattern classification and target packet. Source: `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/operations/task_2026-04-23_data_readiness_remediation/plan_v6.md` §0 correction log + round-3 reviewer reports. This is the gate — every packet must cite which R3 findings it addresses.

**Note on counts**: plan_v6 §0 lists 20 P0 + 7 P1 = 27 correction entries mapping v5→v6 deltas. Of these, the 24 "R3" findings are the subset flagged by round-3 reviewers (architect + critic + scientist independent reports); the remaining 3 P1 entries in plan_v6 §0 are v5→v6 rephrasings of earlier-round findings, not R3-originated.

| TODO ID | Source reviewer | Anti-pattern | Target packet | Status |
|---|---|---|---|---|
| R3-01 | architect P0-1 | AP-5 (axis mirage) | P-B schema | **CLOSED-BY-P-B** (2026-04-23; critic-opus POST-EXECUTION APPROVE; `data_version` column added as concrete axis; P-E populates on re-insert) |
| R3-02 | architect P0-2 + critic | AP-8, AP-9 (savepoint collision + self-contradiction) | P-H atomicity | PENDING |
| R3-03 | architect P0-8 | AP-1, AP-2 (ghost writer, auth stamp without integrity) | **P-A Bulk Writer RCA** | **CLOSED-BY-P-A** (2026-04-23; critic-opus APPROVE; evidence/bulk_writer_rca_final.md) |
| R3-04 | architect P0-3, critic P1-4 | AP-8 (savepoint atomicity loss) | P-H atomicity | PENDING |
| R3-05 | architect P0-4, critic P1-7 | AP-6 (caller-count confusion) | P-H atomicity (P-A did NOT close per critic-opus: "AST-level caller evidence is P-H territory") | PENDING |
| R3-06 | architect P0-5 | cross-packet coordination (dual-track) | P-B schema | **CLOSED-BY-P-B** (2026-04-23; critic-opus APPROVE; `temperature_metric` + `observation_field` columns implement dual-track separation; P-E writes preserve HIGH/LOW identity) |
| R3-07 | architect P0-6, P0-7 | AP-16 (zone laundering) | P-B schema / separate K0 schema_packet | PENDING |
| R3-08 | architect P0-9, critic P0-1 | AP-10 (vacuous AC) | P-H atomicity | PENDING |
| R3-09 | critic P0-2 | AP-11 (reinstated removed architecture) | P-D harvester probe | **CLOSED-BY-P-D** (2026-04-23; AP-11 review-gate concern resolved: P-D §5.3+§8 proves semantic distinction between removed pre-resolution price fallback and proposed post-UMA-resolution oracle-vote read; critic-opus verified `_build_pm_truth.py:137-139` uses the same pattern WITHOUT gate, so fix is stricter than existing production. DR-33 code implementation is a separate downstream implementation task — not blocking this review closure.) |
| R3-10 | critic P0-3 | AP-7 (deprecated API proliferation) | P-B schema | PENDING |
| R3-11 | critic P0-4, scientist D2 | AP-12 (count unstable) | P-E reconstruction | **CLOSED-BY-P-E** (2026-04-23; canonical containment script + relationship tests; partition 1469 V + 92 Q = 1561 exact) |
| R3-12 | critic P0-5, scientist D3 | AP-14 (type-incompatible fabrication) | P-E / P-G DST handling | **CLOSED-BY-P-E** (2026-04-23; 7 DST rows correctly QUARANTINED with `pc_audit_dst_spring_forward_bin_mismatch`; no VERIFIED-by-lie across DST day) |
| R3-13 | critic P0-6, scientist D1 | AP-12, AP-13 (partition fails to sum, missing category) | P-E reconstruction | **CLOSED-BY-P-E** (2026-04-23; relationship test T14 validates aggregate arithmetic; every row in exactly one authority∪reason bucket) |
| R3-14 | architect P1-1, critic P1-6 | AP-15 (deferred verification) | P-E / P-F | **CLOSED-BY-P-E** (2026-04-23; `data_version` column binds source family to unit semantics per row; 1469 VERIFIED rows carry data_version ∈ {wu_icao_history_v1, ogimet_metar_v1, hko_daily_api_v1}; CWA rows correctly quarantined) |
| R3-15 | architect P1-2, scientist D6 | AP-15 + INV-FP-3/INV-06 | P-B schema → P-E | **CLOSED-BY-P-E** (2026-04-23; 1469/1469 VERIFIED rows carry `provenance_json.decision_time_snapshot_id` from obs.fetched_at; INV-FP-3 enforced row-level) |
| R3-16 | architect P1-3, critic P1-5 | AP-15 (statistical claims weak) | P-C WU audit | **CLOSED-BY-P-C** (2026-04-23; critic-opus APPROVE; operational-equivalence 97.78% over 1,444 WU rows; fatal_misread `wu_website_daily_summary_not_wu_api_hourly_max` stays active as invariant anchor per NH-C5) |
| R3-17 | architect P1-4, critic P1-2 | cross-packet coordination (gate_f_data_backfill step 8) | P-F hard quarantine | **CLOSED-BY-P-F** (2026-04-23; 74 rows hard-quarantined with enumerable reasons; critic-opus POST-EXECUTION APPROVE) |
| R3-18 | architect P1-6 | AP-15 (trigger body unspecified) | P-B schema | **CLOSED-BY-P-B** (2026-04-23; `settlements_authority_monotonic` trigger specified, deployed, 4+1 case validated including critic-opus false-positive probe that proved `json_extract` robustness over `LIKE`) |
| R3-19 | architect P1-7 | AP-15 (NC-13 enforcement deferred) | P-B schema / P-E | PENDING |
| R3-20 | critic P1-1 | AP-4 (source role collapse — Shape B per §3) | P-C → P-G → P-F → P-E | **CLOSED-BY-P-E** (2026-04-23; full trail P-C (detected) → P-G (reframed as source-handoff history) → P-F (quarantined 27 AP-4 rows) → P-E (reconstructed with enumerable `pc_audit_source_role_collapse_no_source_correct_obs_available` reason in provenance_json); all 27 rows now canonical-authority-grade QUARANTINED) |
| R3-21 | scientist D4 | AP-15 (market identity lost) | **P-G** | **RESOLVED-BY-P-G** (2026-04-23; critic-opus POST-EXECUTION APPROVE; Gamma probe confirmed 5 "duplicates" are HIGH/LOW metric-identity collisions, NOT JSON duplicates; DELETE 5 wrong-metric rows; P-E re-inserts HIGH-market winners using JSON EARLY entries 1513/1520/1517/1530/1532) |
| R3-22 | scientist D5 | AP-1 (obs_v2 corrupt rows) | P-G pre-existing corrections | PENDING |
| R3-23 | scientist D7 | AP-1 (Denver orphan) | P-D extension §9.1 (Gamma probe: 250-event scan, 0 Denver matches → synthetic orphan) → P-G DELETE | **CLOSED-BY-P-D** (2026-04-23; Denver not in Gamma; P-G to DELETE in execution phase) |
| R3-24 | architect cumulative | meta: candidate 8th and 9th fatal_misreads | Separate guidance_kernel packet (out of v6 scope) | NOTED |

**Packet coverage summary**:
- P-A: R3-03, R3-05 (partial)
- P-D: R3-09, R3-23 (closed), R3-21 (routed to P-G)
- P-C: R3-16 (CLOSED-BY-P-C), R3-20 (addressed; reframed in P-G; quarantined in P-F; closes at P-E)
- P-G: R3-21 (RESOLVED-BY-P-G), R3-20 (REFRAMED; closes at P-E)
- P-B: R3-01 (CLOSED-BY-P-B), R3-06 (CLOSED-BY-P-B), R3-18 (CLOSED-BY-P-B)
- P-F: R3-17 (CLOSED-BY-P-F)
- P-E: R3-11, R3-12, R3-13, R3-14, R3-15, R3-20 (all CLOSED-BY-P-E)

**Workstream outstanding**: R3-02, R3-04, R3-05 (partial), R3-08, R3-10, R3-19 (PARTIAL), R3-22 (partial) → deferred to future DR-33 / P-H-equivalent live-harvester enablement packet. Not workstream blockers per §7 criterion 8 reassessment: P-D proved harvester write path structurally unreachable, so atomicity guards for a non-firing path are not load-bearing for this workstream's closure.
- P-G: R3-22, R3-23 (partial)
- P-B: R3-01, R3-06, R3-07, R3-10, R3-15, R3-18, R3-19
- P-F: R3-14, R3-17
- P-E: R3-11, R3-12, R3-13, R3-14 (ownership split with P-B), R3-20 (partial)
- P-H: R3-02, R3-04, R3-05 (partial), R3-08
- Out of scope (separate packet): R3-24

**Orphan check**: every R3-## is named in at least one packet. R3-24 (cumulative fatal_misreads) is explicitly noted as out-of-scope for this workstream; it goes to the guidance_kernel packet owner.

---

## Appendix D — Work Log Skeleton

Per §5 step 2, `work_log.md` is the ongoing record. Skeleton created at `docs/operations/task_2026-04-23_data_readiness_remediation/work_log.md` at P-0 closure. Every packet appends a section with:

```
## P-X [packet name] — [date started]

### Pre-packet (Q1-Q10 answers)
Q1. What invariant does this enforce?
    → …
… (Q2-Q10)

### Execution log
- [timestamp] [command] → [result]
- [timestamp] [SQL] → [output]
…

### Self-verify
- AC-X: [command] → [expected] | [actual]
…

### Closure request → critic-opus
- deliverables: [list]
- evidence: [list]

### critic-opus verdict
- [APPROVE | CONDITIONS | REJECT]
- [findings if any]

### Closure action
- [completed | re-submitted | revised]
- [date closed]
```

`critic_overrides.md` (per §9 escalation path) is created only if an override occurs; it does not exist at P-0 closure.

---

**This document is the reference for every subsequent packet. When a decision feels ambiguous, return here. When critic-opus rejects a packet, check which principle or anti-pattern was violated. When the operator asks "why are we doing this?", the answer is in this file.**

---

**Awaiting critic-opus review.**
