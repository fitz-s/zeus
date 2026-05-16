# Zeus Deep Alignment Audit — 2026-05-16

**Run anchor**: cherry-picked skill `ff714a7507`; worktree HEAD aligned with main `556d55be23`.
**Worktree**: `.claude/worktrees/zeus-deep-alignment-audit-skill`
**Mode**: read-only (no production code edits); only this REPORT.md and Closeout updates to `LEARNINGS.md` + `AUDIT_HISTORY.md` are written.
**Scope authorized by operator**: C1 (K1 split parity), C4 (lineage gap under live orders), F3/F13/F18 (refreshed schema probes). F-class folded into C1/C4 where evidence overlapped.
**On-chain context**: position `c30f28a5-d4e` (Karachi 2026-05-17 HIGH ≥37°C, condition `0xc5fad…f44ae`, ~$0.59 cost basis, `phase=active`, `chain_state=synced`, `order_status=partial`) IS live during this audit. The SKILL stop rule fired (see Finding #2).

---

## Severity ladder used

- **SEV-1** = correctness break with active on-chain or money-at-risk exposure, OR systemic invariant unenforced in production
- **SEV-2** = correctness/visibility gap that would silently corrupt future analysis or recovery
- **SEV-3** = doctrine drift / dead-code / obsolete sentinel without immediate blast radius

Per SKILL.md, every SEV-1/2 has 3 independent evidences (code line + test/test-absence + data/log artifact) and a single design-only antibody.

---

## Finding #1 — Registry-vs-disk ownership drift is unenforced; 24 tables on wrong DB; 5 tables triple-/dual-DB-duplicated

**Severity**: SEV-1 (systemic invariant unenforced in production)
**Category**: F (cross-module invariants); secondary G (silent failures)

### Context

K1 split (PR #114, completed 2026-05-11 per `docs/operations/current_state.md`) was designed to move trade lifecycle and forecast-class tables off `state/zeus-world.db` (36 GB) into `state/zeus_trades.db` (394 MB) and `state/zeus-forecasts.db` (46 GB), with `architecture/db_table_ownership.yaml` + the public registry in `src/state/table_registry.py` as the canonical routing source. The registry exposes `assert_db_matches_registry(conn, db_identity)` as antibody A4 (per the file's docstring: "table-set + column-shape" check; raises `RegistryAssertionError` on mismatch).

### Evidence (3 independent)

**E1 — Code line: antibody is implemented but explicitly NOT wired at boot.**

[src/main.py#L857-L859](src/main.py#L857-L859):

```python
# world_schema_manifest.yaml + validate_world_schema_at_boot RETIRED in P2
# (2026-05-14 K1 followups plan §5.5 D5). assert_db_matches_registry() exists
# (src/state/table_registry.py) but boot wiring is deferred — not called here.
```

The function definition exists at [src/state/table_registry.py#L283](src/state/table_registry.py#L283). Grep confirms zero production call sites: only `tests/state/test_table_registry_coherence.py` invokes it.

**E2 — Test artifact: tests prove FATAL-on-mismatch semantics, but no integration test asserts that boot calls it.**

[tests/state/test_table_registry_coherence.py#L193-L245](tests/state/test_table_registry_coherence.py#L193-L245) — A4 unit test verifies `RegistryAssertionError` is raised on missing/extra tables. There is no end-to-end test asserting that `src/main.py` invokes the assertion during daemon startup. Antibody is dormant.

**E3 — Data artifact: 24 tables registry-vs-reality wrong-DB; 5 tables populated on 2-3 DBs simultaneously.**

Cross-checked `architecture/db_table_ownership.yaml` against live row counts on all three DBs (read-only `mode=ro` connections; today 2026-05-16):

| table | registry says | actually populated | rows |
|---|---|---|---|
| `position_current` | world | trade | 2 (incl. live Karachi) |
| `position_events` | world | trade | 7 |
| `opportunity_fact` | world | trade | 20,617 |
| `selection_family_fact` | world | trade | 46 |
| `selection_hypothesis_fact` | world | trade | 506 |
| `decision_log` | world | trade | 638 |
| `execution_fact` | world | trade | 5 |
| `venue_commands` | world | trade | 6 |
| `venue_command_events` | world | trade | 22 |
| `venue_order_facts` | world | trade | 3 |
| `venue_submission_envelopes` | world | trade | 8 |
| `venue_trade_facts` | world | trade | 1 |
| `market_price_history` | world | trade | 435,532 |
| `market_topology_state` | world | trade | 4,686 |
| `probability_trace_fact` | world | trade | 20,807 |
| `provenance_envelope_events` | world | trade | 26 |
| `shadow_signals` | world | trade | 19,647 |
| `token_price_log` | world | trade | 16,106 |
| `strategy_health` | world | trade | 1 |
| `rescue_events_v2` | world | trade | 1 |
| `collateral_ledger_snapshots` | world | trade | 9,099 |
| `collateral_reservations` | world | trade | 4 |
| `executable_market_snapshots` | world | trade | 14 |

Multi-DB duplicates (registry declares one home; data exists on multiple):

| table | registry | populated on (rows) |
|---|---|---|
| `market_events_v2` | forecasts | world 2,112 / trade 6,688 / forecasts 9,276 |
| `observations` | forecasts | world 145 / forecasts 43,903 |
| `availability_fact` | world | world 3,592 / trade 18,966 |
| `source_run_coverage` | forecasts | world 1,087 / forecasts 1,424 |
| `readiness_state` | forecasts | world 577 / trade 402 / forecasts 728 |

(`zeus_meta` triple-population is expected — per-DB meta table.)

The Platt-v2 (`platt_models_v2` world=1,406 ✅) and `historical_forecasts_v2` (world=22,644 ✅) routing IS consistent with the registry — the pre-summary "K1 split parity gap on platt" suspicion was a false alarm; the real gap is the trade-lifecycle bloc.

### Root cause (one sentence)

K1 was a code/data migration without a corresponding `db_table_ownership.yaml` rewrite for the trade-lifecycle bloc, and the A4 antibody was authored but deliberately left unwired at boot, so the registry has silently diverged from disk for 24 tables.

### Blast radius

- Any consumer that resolves a table via `owner(table_name)` for the 24 mismatched tables and opens the resolved DB will hit an empty / missing table (or, for the duplicates, the wrong copy). Today no consumer appears to do this for the trade-lifecycle tables (they hardcode `get_trade_connection`), which is why the system runs — but the registry is no longer a true map.
- `market_events_v2` triple-population is the highest-risk silent-failure: any cross-module read that picks the wrong connection will see a different event set.
- Boot will continue indefinitely to skip the FATAL check, so further drift accumulates undetected.

### Antibody (design only)

**A-F1** — Two-phase rewire of A4 at boot:

1. **Reconcile the registry to disk first** (not vice versa, since disk is the live truth): regenerate `architecture/db_table_ownership.yaml` ownership rows from the actual K1 layout. Mark the 5 duplicate-populated tables with explicit `legacy_archived` entries on the non-canonical DBs so they pass `tables_for` filtering but show in `_REGISTRY` for audit.
2. **Then wire `assert_db_matches_registry(world_conn, DBIdentity.WORLD)`, `(trade_conn, DBIdentity.TRADE)`, `(forecasts_conn, DBIdentity.FORECASTS)` into `src/main.py` startup** between `init_schema(trade_conn)` and `_startup_world_schema_ready_check()`. Fail-closed on mismatch (do NOT add an advisory bypass). Add an integration test that asserts the call is made by importing `src.main` and patching the assertion with a spy.

Sequence is critical: wiring before reconciliation would FATAL the daemon on the next boot.

---

## Finding #2 — `selection_hypothesis_fact.decision_id` 100% NULL ⇒ live edge lineage unauditable for active Karachi position (STOP-rule SEV-1)

**Severity**: SEV-1 — affects a position currently on-chain. Per SKILL stop rule, surfaced immediately rather than buried at end of report.
**Category**: A (data provenance holes); secondary G (silent failures)

### Context

The skill triplet for selection lineage is:

- `selection_family_fact` (per (city, target_date, strategy) family) — write site [src/state/db.py#L5253](src/state/db.py#L5253)
- `selection_hypothesis_fact` (per individual hypothesis after FDR) — write site [src/state/db.py#L5303](src/state/db.py#L5303)
- `opportunity_fact` (per candidate snapshot) — write site separate

Each is supposed to carry a `decision_id` (or `decision_snapshot_id`) join key so post-hoc audit can answer "which hypothesis cluster produced the family that the executor traded?"

### Evidence (3 independent)

**E1 — Code line: caller never threads `decision_id` to the hypothesis writer.**

[src/engine/evaluator.py#L1535-L1561](src/engine/evaluator.py#L1535-L1561): the loop that calls `log_selection_hypothesis_fact(...)` passes `hypothesis_id`, `family_id`, `candidate_id`, geography, p/q/edge, but **does not pass `decision_id=`**. The DB function defines `decision_id: str | None = None` (default) at [src/state/db.py#L5314](src/state/db.py#L5314). The sibling call to `log_selection_family_fact(...)` 18 lines above DOES pass `decision_snapshot_id=decision_snapshot_id` — proving the variable is in scope and the omission is a caller bug, not a missing input.

**E2 — Test absence: no test asserts `decision_id` is non-NULL after a real evaluator cycle.**

`grep -rn "selection_hypothesis_fact.*decision_id" tests/` returns zero hits. There is no integration test ensuring the lineage join key is populated, so the omission has been latent since the field was added.

**E3 — Data artifact: 100% NULL in production.**

Probe on `state/zeus_trades.db` 2026-05-16:

```
selection_hypothesis_fact decision_id NULL/total: (506, 506)
selection_hypothesis_fact distinct decision_id : (0,)
opportunity_fact          decision_id NULL/total: (0, 20617)   # works
selection_family_fact     decision_snapshot_id NULL/total: (0, 46)  # works
venue_commands            decision_id NULL/total: (0, 6)       # works
execution_fact            decision_id NULL/total: (0, 5)       # works
```

The break is isolated to the per-hypothesis layer. Upstream (`opportunity_fact`, `selection_family_fact`) and downstream (`venue_commands`, `execution_fact`) both populate the key correctly — so the hypothesis layer is the unique unauditable hop.

### On-chain consequence

Live Karachi position `c30f28a5-d4e` (`state/zeus_trades.db` `position_current` row, `phase=active`, `entry_price=0.37`, `shares=1.5873`, `condition_id=0xc5fad…f44ae`, fill confirmed 2026-05-16T06:40Z) cannot today be traced through `selection_hypothesis_fact` to the per-hypothesis statistical envelope (`p_value`, `q_value`, `ci_lower`, `ci_upper`, `selected_post_fdr`, `rejection_stage`) that justified its entry. The `family_id` chain works (`position_events.decision_id='9e960582-602'` → `selection_family_fact.decision_snapshot_id` join), but the `hypothesis_id → decision_id` join is empty for ALL 506 hypothesis rows. If a post-mortem is needed for this position (e.g. governance review or model re-validation), the hypothesis-level evidence is unrecoverable for it and every other position evaluated under this code path since the field was added.

### Root cause (one sentence)

`log_selection_hypothesis_fact(...)` defaults `decision_id=None`, and its sole caller (`evaluator.py:1535`) forgot to thread the in-scope `decision_snapshot_id` — a caller bug masked by the absence of any test asserting non-NULL.

### Antibody (design only)

**A-F2** — Two-layer fix; redundancy intentional because the call site is fragile:

1. **DB-layer hard contract**: in `log_selection_hypothesis_fact` change the parameter to required positional (`decision_id: str`) and raise `ValueError` if falsy. This forces every caller to thread the key and will FATAL any future regression at import / unit-test time rather than silently NULL'ing rows.
2. **Test-layer regression guard**: add `tests/state/test_lineage_join_keys.py` containing one parametrized test that for each of `(selection_hypothesis_fact, decision_id)`, `(selection_family_fact, decision_snapshot_id)`, `(opportunity_fact, decision_id)`, `(venue_commands, decision_id)`, `(execution_fact, decision_id)` asserts `SELECT COUNT(*) FROM <t> WHERE <k> IS NULL` returns 0 against a fresh evaluator-driven fixture DB. This makes the "100% NULL" failure mode shape-detectable, not just unit-coverable.
3. (no-op for retroactive rows: 506 existing NULLs are unrecoverable; document in the doctrine packet as a one-time historical hole.)

---

## Finding #3 — Doctrine files materially stale; multiple wrong facts in `current_state.md` and `current_data_state.md`

**Severity**: SEV-2 (operator-decision risk; no immediate money-at-risk)
**Category**: H (assumption drift)

### Evidence (3 independent)

**E1 — Stale HEAD claim**: [docs/operations/current_state.md](docs/operations/current_state.md) declares "Main HEAD = `8b3c3c2c59`". Actual main HEAD on 2026-05-16 = `556d55be23` (1 commit ahead — PR #120 followup landed). Doctrine is one commit behind reality.

**E2 — Wrong settlements baseline + harvester status**: [docs/operations/current_data_state.md](docs/operations/current_data_state.md) "Last audited 2026-04-28" (18 days ago, exceeds the file's own 14-day max), claims 1,609 settlements baseline and harvester DORMANT. Reality probe on `state/zeus-forecasts.db`: `settlements` legacy table = 5,570 rows, `settlements_v2` = 3,987 rows; harvester is active (3,605 rows with `authority='VERIFIED'` written by `harvester_truth_writer_dr33`). Three independent factual errors in one doctrine file.

**E3 — Schema drift not reflected**: prior audit (2026-05-08 PLAN.md F1-F25 matrix) used column names that no longer exist on disk: `availability_fact.city`, `availability_fact.payload_json`, `availability_fact.target_date`; `opportunity_fact.temperature_metric`; `venue_commands.status`; `venue_order_facts.status`; `position_events.action`; `execution_fact.occurred_at`; `selection_family_fact.decision_id`. All eight columns were renamed or removed since 2026-05-08; doctrine has not been refreshed.

### Antibody (design only)

**A-F3** — Tie doctrine freshness to a commit hook OR a daily cron:

- Add `scripts/doctrine_freshness_check.py` that asserts each of `current_state.md`, `current_data_state.md`, `current_source_validity.md` carries a `last_audited:` front-matter date within 14 days of `today()`, and that the `Main HEAD =` line matches `git rev-parse HEAD` from `main`. Fail-loud (exit 1) if either check fails; wire to the same launchd schedule as the existing data-daemon health checks. Doctrine that doesn't fail-loud rots silently.

---

## Finding #4 — `settlements_v2.settled_at` and `recorded_at` conflated; settlement writer silent for 5 days

**Severity**: SEV-2 (settlement integrity + potential SEV-1 if pipeline is broken vs simply quiet — needs operator confirmation to upgrade)
**Category**: E (settlement edges); secondary G (silent failures)

### Evidence (3 independent)

**E1 — Data artifact: 100% identity collapse between `settled_at` and `recorded_at`.**

Probe on `state/zeus-forecasts.db` 2026-05-16:

```
settlements_v2 settled_at == recorded_at rows / total: (3987, 3987)
```

Every single v2 settlement row has identical strings in the two fields. The schema-author's intent (two columns) cannot be satisfied if the writer pipes the same `now()` into both. Either the schema design is meaningless or the writer is wrong; both are bugs.

**E2 — Data artifact: writer cadence stopped 2026-05-11; no new rows for 5 days.**

```
settlements_v2 last settled_at: 2026-05-11T19:59:13+00:00
settlements   last settled_at: 2026-05-11T19:59:13+00:00
opportunity_fact recorded_at max: 2026-05-16T00:29:38+00:00
```

`opportunity_fact` is being written through today (last entry 2026-05-16T00:29Z), so the broader pipeline lives. But both `settlements` and `settlements_v2` froze at the exact same timestamp 5 days ago — settlement writer is silent. Target_dates 2026-05-12 through 2026-05-16 have not produced a single new settlement row in either table despite weather having occurred for those dates. With live position `c30f28a5-d4e` resolving 2026-05-17, the settlement writer must resume before that date or the position cannot exit cleanly via the canonical path.

**E3 — Legacy-vs-v2 migration not idempotent.**

```
legacy distinct (city,target_date): 4619
v2     distinct (city,target_date): 3862
overlap                           : 3805
legacy-only                       :  814  ← never migrated to v2
v2-only                           :   57  ← v2-original, no legacy twin
```

Per `docs/operations/current_state.md` the K1 split moved `settlements_v2` to forecasts.db as the canonical table. But the legacy `settlements` still has 814 (city, date) keys that v2 never received. Authority mix differs too: legacy has 5,263 VERIFIED + 307 QUARANTINED; v2 has 3,605 VERIFIED + 382 QUARANTINED. Consumers reading "the settlements table" get materially different answers depending on which they pick — and the registry says `settlements` is `forecasts/legacy_archived` while `settlements_v2` is canonical, so the 814 legacy-only keys are now dark data.

### Root cause (one sentence, hypothesis)

The v2 settlement writer treats `settled_at` and `recorded_at` as aliases (likely a single `dt.utcnow()` plugged into both columns); separately, the writer process has been silent since 2026-05-11T19:59Z, suggesting either a cron/daemon failure or an upstream input (UMA / weather harvester) that stopped producing settlement-ready candidates. Both bugs need direct daemon-log inspection to confirm/refute, but the data shape alone is sufficient evidence to flag.

### Antibody (design only)

**A-F4** — Two-part:

1. **Writer correctness**: rename / repurpose the columns so the bug surfaces. Either drop `recorded_at` (if it was supposed to be identity-with-settled_at) and adjust readers, or require the writer to set `settled_at = <observed-weather-cutoff-time>` (e.g., `target_date + interval '23:59:59' UTC` for daily-temperature markets) and `recorded_at = utcnow()`. Add a NOT NULL CHECK that `recorded_at >= settled_at` to fail-loud on regressions.
2. **Cadence health**: add `scripts/settlement_writer_freshness.py` that asserts `max(settled_at) > today() - 36h` and emits to the same source-health JSON as the existing data freshness gates. Wire as a degradation signal to `_startup_freshness_check()` so the boot gate notices a silent settlement writer.

---

## Findings summary

| # | Severity | Category | Title | On-chain impact |
|---|---|---|---|---|
| 1 | SEV-1 | F (cross-module invariants) | Registry-vs-disk drift unenforced; 24 tables wrong-DB, 5 multi-DB-duplicated | Latent (no current consumer mis-routes, but registry is no longer authoritative) |
| 2 | SEV-1 | A (data provenance) | `selection_hypothesis_fact.decision_id` 100% NULL — caller bug in `evaluator.py:1535` | Live Karachi position `c30f28a5-d4e` lineage unauditable at hypothesis layer |
| 3 | SEV-2 | H (assumption drift) | `current_state.md` 1 commit stale; `current_data_state.md` 18 days stale with wrong settlement counts + wrong harvester status; eight column-name drifts vs 2026-05-08 audit | Operator-decision risk |
| 4 | SEV-2 | E (settlement edges) | `settlements_v2.settled_at`==`recorded_at` for 100% of rows; both `settlements` and `settlements_v2` writers silent since 2026-05-11; legacy↔v2 migration leaves 814 dark rows | Could become SEV-1 if Karachi 2026-05-17 settlement is required and writer is broken |

K (root gap count) = 4, within the SKILL's ≤5 ceiling.

---

## B/C/D probes — null findings (recorded for yield-ladder accuracy)

These categories were probed but produced nothing worth promoting to a finding this run:

- **B (Math drift)** — sanity-checked `opportunity_fact.p_raw / p_cal / p_market` ranges (all in `[0,1]`, no out-of-range), `alpha` range `[0, 0.75]`, `best_edge` range `[0.0004, 0.73]`. `position_current.entry_price=0.0` for the voided position `7211b1c5-d3b` is a documented sentinel, not a math bug. No actionable finding; category remains `LOW` yield until next run accumulates more evidence.
- **C (Statistical pitfalls)** — `selection_hypothesis_fact` p/q values all in `[0,1]`; `selected_post_fdr` ↔ `rejection_stage` invariant holds (0 contradictions). FDR rejected only 3 of 212 prefilter-passing hypotheses (1.4%) — could indicate an over-permissive FDR alpha, but small sample (506 hypotheses total) makes the rate non-diagnostic. Worth re-probing in a future run with N≥2000 hypotheses.
- **D (Time/calendar)** — 6/7 `position_events.occurred_at` carry `+00:00`; the 1 naive entry is the documented `'unknown_entered_at'` sentinel. `target_date` is treated as a UTC calendar date for non-UTC cities (London, Paris, Karachi, etc.) — would be a SEV-2 if the settlement-window contract assumes city-local time, but no contract probe was done. Defer to next run with contract reads.



---

## What was NOT audited this run

Carry forward to next audit run (also recorded in LEARNINGS.md):

- `src/contracts/{settlement_semantics,execution_price,venue_submission_envelope,fx_classification}.py` — contract-vs-call-site shape audit (Boot step 5) was deferred.
- Category B (math drift) and C (statistical pitfalls) were sampled at surface level (range checks only); deeper math/stat probes (Kelly fraction → posted size derivation, FDR alpha calibration with bigger N) deferred.
- Category D (time/calendar) — naive-tz only the documented sentinel; the `target_date` UTC-vs-city-local question needs a contract read to escalate or close.
- Topology doctor (Boot step 6) and full git log review (Boot step 7) skipped.

These are not signals of low yield; they are signals of un-tested territory.
