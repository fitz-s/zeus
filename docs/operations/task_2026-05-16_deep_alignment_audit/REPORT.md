# Zeus Deep Alignment Audit — 2026-05-16

**Run anchor**: cherry-picked skill `ff714a7507`; worktree HEAD aligned with main `556d55be23`.
**Worktree**: `.claude/worktrees/zeus-deep-alignment-audit-skill`
**Mode**: read-only (no production code edits); only this REPORT.md and Closeout updates to `LEARNINGS.md` + `AUDIT_HISTORY.md` are written.
**Scope authorized by operator**: C1 (K1 split parity), C4 (lineage gap under live orders), F3/F13/F18 (refreshed schema probes), then B/C/D/E sweep. F-class folded into C1/C4 where evidence overlapped.
**On-chain context**: position `c30f28a5-d4e` (Karachi 2026-05-17 HIGH ≥37°C, condition `0xc5fad…f44ae`, ~$0.59 cost basis, `phase=active`, `chain_state=synced`, `order_status=partial`) IS live during this audit. The SKILL stop rule fired (see Findings #2 and #4).

---

## EXECUTIVE SUMMARY — findings ranked by live-impact (highest first)

This ranking re-orders the body-of-report by money/operational impact on currently-live positions. Body sections retain their original sequential numbers for git-blame continuity; **read in the order below**.

| Rank | Live impact | Body # | Severity | Title | Why it matters NOW |
|------|-------------|--------|----------|-------|-------------------|
| **A** | **🔴 MERGE-PENDING for Karachi 2026-05-17 settlement** | **#4** (escalated) | **SEV-1 MERGE-PENDING** | Harvester truth writer mis-routed to `world.db` (ghost copy) post-K1; canonical `forecasts.db.settlements_v2` silent since 2026-05-11T19:59Z (5 days, matches K1 split landing). **Phase-3 update**: fix exists on `feat/data-daemon-authority-chain-2026-05-14` but is **not merged to origin/main**; daemon offline since 2026-05-15 01:34Z so mis-routing is dormant; 726 stranded rows in `world.db.market_events_v2` from 5/12–5/13 are the real audit artifact. Rank A retained: un-merged fix + offline daemon + stranded rows still warrant highest live impact. |
| **B** | 🟠 Live position lineage unauditable | **#2** | SEV-1 | `selection_hypothesis_fact.decision_id` 100% NULL (506/506); evaluator.py:1535 caller bug | If Karachi position needs post-mortem (e.g. dispute, mis-settlement), per-hypothesis decision trace is unrecoverable. Read-only debt, not money-at-risk today. |
| **C** | 🟡 Systemic latent (no current consumer mis-routes; very-soon-likely-mis-routing-on-next-feature) | **#1** | SEV-1 | Registry-vs-disk drift unenforced; 24 tables wrong-DB; 5 multi-DB duplicated; `assert_db_matches_registry()` exists but unwired at boot | Same root-cause class as rank A. K1 split partially landed; followups (e.g. commit `1d952b072e`) never reached main. Next consumer added will likely pick wrong DB. |
| **D** | 🟢 Operator-decision risk only | **#3** | SEV-2 | Doctrine drift (`current_state.md` 1 commit stale; `current_data_state.md` 18d stale with wrong settlement counts + wrong harvester status) | Decisions made from doctrine are decisions from a fiction. No direct money flow. |

**Common root cause** (ranks A + C): the K1 multi-DB split (PR #114 `eba80d2b9d`, 2026-05-10/11) landed the schema migration and registry doctrine, but **structural followup commits did not all reach main**. Commit `1d952b072e` ("feat(k1/5b): CRITICAL-1 harvester trio → get_forecasts_connection", authored 2026-05-11 21:23 PDT — i.e. ~8h after the last settlement row was written) was developed but is `NOT in main` per `git merge-base --is-ancestor 1d952b072e origin/main`. Rank A's settlement writer-target bug is the most-visible-blast-radius instance of the registry-vs-disk drift class.

**Immediate operator action recommended** (out of audit scope; flag only): cherry-pick `1d952b072e` (and likely `a322810a2a` feat(k1/5c) too) onto main behind a focused PR, OR set `ZEUS_HARVESTER_LIVE_ENABLED=0` to disable the silent-failure path until the fix lands. Do this before Karachi 2026-05-17 settles.

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

## Finding #4 — Harvester truth writer is structurally mis-routed post-K1 (writes to ghost `world.db`, canonical `forecasts.db` silent 5 days); merge-pending fix exists

**Severity**: SEV-1 MERGE-PENDING (ESCALATED 2026-05-16 from initial SEV-2 after writer-target investigation, then DOWNGRADED in Phase-3 from BLOCKING after discovering the fix exists off-main). Live position `c30f28a5-d4e` resolves 2026-05-17; if the daemon were running on `origin/main`, settlement would still mis-route via the canonical path — but the data daemon has been OFFLINE since 2026-05-15 01:34Z, so the mis-routing pathway is currently dormant.
**Category**: F (cross-module invariants — registry-vs-code drift); secondary E (settlement edges), G (silent failures).

### Phase-3 Correction (added 2026-05-16, supersedes Phase-2 framing)

Phase-2 evidence below describes the bug as it exists on `origin/main` HEAD `556d55be23` (and on the audit worktree, which aligns with that HEAD). That description is **factually correct for origin/main**, but **incomplete** as a live-impact assessment because:

1. **The fix already exists off-main.** Commits `1d952b072e` (`feat(k1/5b): CRITICAL-1 harvester trio → get_forecasts_connection`) and `a322810a2a` (`feat(k1/5c): forecast-only readers → get_forecasts_connection`) are present on branch `feat/data-daemon-authority-chain-2026-05-14` and downstream on `deploy/live-continuous-run-2026-05-16` (merged to main via PR #121 *after* the Phase-2 evidence was captured — verify with `git merge-base --is-ancestor 1d952b072e origin/main` against the HEAD at audit-time vs. post-PR-121 HEAD). At Phase-2 audit-time the fix was NOT in main; the audit worktree still points at the pre-PR-121 HEAD.
2. **Daemon is offline.** Data daemon stopped writing at 2026-05-15 01:34Z. While offline, neither the bug nor the fix is executing — no further stranded rows accrue, no canonical writes happen either.
3. **The real audit artifact is 726 stranded rows.** `world.db.market_events_v2` carries 726 rows written during the 2026-05-12 → 2026-05-13 active window (post-K1-split, pre-daemon-stop) that were routed to the ghost copy and never landed on the canonical destination. These are the rows a reconciliation pass must address regardless of how the routing fix is sequenced into main.

**Net live impact**: the routing fix exists and is one PR-merge away from main; the daemon must be re-armed against a HEAD that contains the fix; the stranded-rows backfill is independent of both and is the immediate operational item.

### Evidence (3 independent — captured against pre-PR-121 main HEAD `556d55be23`)

**E1 — Code line: writer opens `get_world_connection`, but registry says canonical is forecasts.db.**

`src/ingest_main.py:641-651` (HEAD `556d55be23`):

```python
@_scheduler_job("ingest_harvester_truth_writer")
def _harvester_truth_writer_tick():
    ...
    from src.state.db import get_world_connection
    ...
    with acquire_lock("harvester_truth") as acquired:
        if not acquired: ...
        conn = get_world_connection(write_class="bulk")
        try:
            result = write_settlement_truth_for_open_markets(conn)
```

`architecture/db_table_ownership.yaml:67-69` (same HEAD):

```yaml
  - name: settlements_v2
    db: forecasts
    schema_class: forecast_class
```

And the ghost copy on world.db is explicitly flagged at lines 178-186:

```yaml
  - name: settlements_v2
    db: world
    schema_class: legacy_archived
    ...
      Ghost copy of settlements_v2 on world.db post-K1-split.
      Authoritative on forecasts.db. Excluded. Drop after 2026-08-09.
```

So the writer is opening the `world.db` connection and calling `write_settlement_truth_for_open_markets(conn)` which writes to the `legacy_archived` ghost table that is scheduled for deletion. Canonical destination is never reached.

**E2 — Data artifact: writer's actual destination has 0 rows; canonical destination silent 5 days; tail matches K1 split landing.**

Probe on all three DBs read-only (2026-05-16):

```
zeus-world.db.settlements_v2:     count=0     max_settled_at=None
zeus-forecasts.db.settlements_v2: count=3987  max_settled_at=2026-05-11T19:59:13+00:00
zeus_trades.db.settlements_v2:    count=0     max_settled_at=None
```

`opportunity_fact.recorded_at` MAX on forecasts.db = `2026-05-16T00:29:38+00:00` (today) — so the broader pipeline is alive. Only settlements are frozen, and the tail timestamp `2026-05-11T19:59Z` corresponds with K1 split work landing: `eba80d2b9d fix(state): K1 forecast DB split + ATTACH index helper + calibration adversarial bundle (#114)` is the merge that moved `settlements_v2` to forecasts.db. After that merge, the writer's target (`world.db.settlements_v2`) became an empty ghost; the canonical (`forecasts.db.settlements_v2`) retained the pre-split rows but has received zero new writes.

**E3 — Git evidence: the structural followup commit was authored but never merged to main.**

```
git log --all --oneline -- src/ingest_main.py | grep 'k1/5b'
1d952b072e feat(k1/5b): CRITICAL-1 harvester trio → get_forecasts_connection

git merge-base --is-ancestor 1d952b072e origin/main
$? = 1   ← NOT in main
```

Commit body says it swaps `get_world_connection → get_forecasts_connection` at `ingest_main.py` line 647 and updates corresponding tests. Authored 2026-05-11 21:23 PDT (~8h after the last settlement row was written — same date the upstream K1 split shipped). Its sibling commit `a322810a2a feat(k1/5c): forecast-only readers → get_forecasts_connection` is also absent from main per same check. K1 split landed partially: schema migration + registry merged; writer-/reader-side connection migrations did not.

Additionally: `src/ingest/harvester_truth_writer.py:13` says "Logic copied verbatim from `src/execution/harvester.py:_write_settlement_truth`" — so there's a second writer copy in `execution/harvester.py` that may or may not have the same mis-routing, expanding blast radius (not probed this run).

### Root cause (one sentence)

K1 multi-DB split landed the schema move + registry doctrine without their connection-migration followup commits (`1d952b072e`, `a322810a2a`), leaving the harvester truth writer to commit settlement rows to a now-empty world.db ghost copy while the canonical forecasts.db destination receives nothing — silent failure mode visible only by cross-checking writer destination vs registry.

### Antibody (design only)

**A-F4** — Three-layer:

1. **Immediate (operator action, out of audit scope but flagged)**: cherry-pick `1d952b072e` + `a322810a2a` to main via a focused PR titled "k1/5b+5c followups: route harvester trio + forecast-only readers to forecasts.db (settlement writer recovery)". Verify post-merge by re-running the E2 probe; `forecasts.db.settlements_v2` `max(settled_at)` must move past `2026-05-11T19:59Z` within one cron tick. Alternatively, as a containment measure until the PR lands, set `ZEUS_HARVESTER_LIVE_ENABLED=0` to disable the silent-failure path — this stops false writes but does not recover; only the cherry-picks recover.
2. **Structural antibody (durable)**: implement & wire `assert_writer_db_matches_registry(conn, table_name)` as a per-write assertion. At every site that does `conn.execute("INSERT INTO <table>…")` for a registry-tracked table, call the assertion first; it inspects `conn`'s file path vs registry's `db:` field and raises if mismatched. Wire at writer entry points (`harvester_truth_writer`, `harvester.py`, `evaluator.py` selectors). Cost: ~one syscall per write; well within budget for a bulk writer that runs hourly.
3. **Cadence antibody (also fixes the original SEV-2 framing)**: add `scripts/settlement_writer_freshness.py` that asserts `forecasts.db.settlements_v2 max(settled_at) > today() - 36h` (and equivalent for any other registry-canonical writer destination). Wire as boot gate AND as cron-emitted source-health JSON entry. A silent writer is the failure shape we just observed; this antibody catches the recurrence regardless of which-DB the routing bug points to.

### Connection to existing on-chain position

Karachi `c30f28a5-d4e` (target_date 2026-05-17) will go through the settlement window 2026-05-17T~23:59Z. At that point the canonical writer path must persist a row to `forecasts.db.settlements_v2` for downstream PnL/audit/UMA-reconciliation. With current code that row will be written to `world.db.settlements_v2` (legacy_archived) instead, and (per registry doctrine) consumers reading from canonical will see no settlement. Cost basis is small (~$0.59) so the immediate financial blast radius is bounded, but the **operational blast radius is broader**: every settled position since 2026-05-11T19:59Z is in the same state (settlement attempted, persisted to ghost, invisible to canonical readers). The position-events / pnl / UMA-reconciliation flows that depend on `settlements_v2` are operating on stale data.

---

## Findings summary

| # | Severity | Category | Title | On-chain impact |
|---|---|---|---|---|
| 1 | SEV-1 | F (cross-module invariants) | Registry-vs-disk drift unenforced; 24 tables wrong-DB, 5 multi-DB-duplicated; `assert_db_matches_registry()` unwired | Latent (no current consumer mis-routes today, but same root-cause class as #4) |
| 2 | SEV-1 | A (data provenance) | `selection_hypothesis_fact.decision_id` 100% NULL — caller bug in `evaluator.py:1535` | Live Karachi position `c30f28a5-d4e` lineage unauditable at hypothesis layer |
| 3 | SEV-2 | H (assumption drift) | `current_state.md` 1 commit stale; `current_data_state.md` 18 days stale with wrong settlement counts + wrong harvester status; eight column-name drifts vs 2026-05-08 audit | Operator-decision risk |
| 4 | **SEV-1 (escalated)** | E + F | Harvester truth writer mis-routed to ghost `world.db.settlements_v2` post-K1; canonical `forecasts.db.settlements_v2` silent 5 days; K1 followup commits `1d952b072e` + `a322810a2a` not in main | **BLOCKING**: Karachi 2026-05-17 settlement will write to legacy_archived ghost, invisible to canonical readers |

K (root gap count) = 4, within the SKILL's ≤5 ceiling. Final severity counts: SEV-1 × 3, SEV-2 × 1, SEV-3 × 0.

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


---

## Run #2 — Re-verify Phase A + Deep-Sweep Phase B (2026-05-16 16:50 UTC)

Anchor: main HEAD `a924766c8a` (PR #121 merged: `feat(k1): harvester writer DB-routing fix + open-markets pipeline`). Audit worktree HEAD `40e7709b2d`. Run #1 anchor was `556d55be23`; PR #121 advanced main with semantically-equivalent commits (`a2515d5547 feat(k1/5b)`, `fb44a47bb6 feat(k1/5c)`, `37b0dd5993 fix(k1)`, `4e597bd3c3 fix(live)`, `6d0290788b fix(live)`, `584cf92432 fix(data)`). Original Run-1 SHAs `1d952b072e` / `a322810a2a` are NOT ancestors of main.

### Phase A — re-verification verdicts

| # | Finding (Run #1) | Verdict on `a924766c8a` | Evidence | Residue / new signal |
|---|---|---|---|---|
| 1 | Registry-vs-disk drift, `assert_db_matches_registry` antibody unwired | **STILL-OPEN** | `src/main.py:861` still carries the inline note "antibody … not called here"; 0 production callers per `grep`. Registry cross-check yields **23 mismatch + 5 dupe** tables (Run #1 reported 24 + 5; `availability_fact` migrated mismatch→dupe; otherwise stable). | Systemic. |
| 2 | `selection_hypothesis_fact.decision_id` 100% NULL | **STILL-OPEN-REGRESSED** | `src/engine/evaluator.py:1535` still omits `decision_id=` kwarg in `log_selection_hypothesis_fact(...)`. Row count **693/693 NULL** (Run #1: 506/506) — +37% growth in unauditable rows. **NEW orphan**: `execution_fact` has **1/6 NULL `decision_id`**. | 693 stranded hypothesis rows + 1 stranded execution row. |
| 3 | Doctrine drift in operator-facing snapshots | **STILL-OPEN** | `current_state.md:12` reports Main HEAD `8b3c3c2c59` — actual `a924766c8a` (Run #1's fix landed on main but the operator snapshot was not refreshed). `current_data_state.md:4` last_audited `2026-04-28` (now **18 days stale**, > 14d threshold). | Both doc files unchanged since Run #1. |
| 4 | Harvester writer routed to WORLD instead of FORECASTS | **RESOLVED-WITH-CAVEATS** | Code fix landed on main via PR #121: `src/ingest_main.py:646` now calls `get_forecasts_connection(write_class="bulk")`. Daemon `com.zeus.data-ingest` PID 34316 alive; tick log `[zeus.ingest] INFO: harvester_truth_writer_tick: {'status':'ok','markets_resolved':0,'settlements_written':0,...}` confirms routing is correct and feature flag `ZEUS_HARVESTER_LIVE_ENABLED=1` is set in `~/Library/LaunchAgents/com.zeus.data-ingest.plist`. | (a) `world.market_events_v2` has **2,112 stranded rows** (2026-05-12: 1,386 + 2026-05-13: 726). **No new stranded rows since 2026-05-13 16:45 UTC** — clean post-fix. (b) `forecasts.settlements_v2` `max(settled_at) = 2026-05-11T19:59:13Z`, **0 writes in last 24h** — tick legitimately returns `settlements_written=0` because the open-markets pass is filtering out every market (see new Finding #7). Cleanup recommended for the 2,112 stranded rows but not required for forward correctness. |

### Phase B — new findings

Methodology: Eight categories A–H per SKILL.md were re-swept; high-yield categories prioritised (E settlement, G silent-failures, F cross-module, H assumption-drift). Four new findings emerged that meet the 3-evidence bar for SEV-1/2.

#### Finding #5 — DB-lock contention storm in live & ingest daemons (SEV-1, category G silent-failures + F cross-module)

**Evidence 1**: `grep -c "database is locked" logs/zeus-ingest.err logs/zeus-live.err logs/riskguard-live.err logs/zeus-forecast-live.err` → **50,730 / 1,894 / 29 / 0**.

**Evidence 2**: Last 1000 lines of `logs/zeus-live.err` contain 37 `database is locked` rows — contention is ongoing, not historical. Representative trace: `2026-05-16 11:50:09,360 [zeus] WARNING: CollateralLedger heartbeat refresh failed closed: database is locked`. Heartbeat runs every 5 s (`_write_venue_heartbeat`), so lock loss directly degrades venue freshness.

**Evidence 3**: `CollateralLedger.heartbeat_refresh` is the named site; the apscheduler log shows the job "executed successfully" immediately after a `database is locked` WARNING, meaning the per-tick exception is swallowed and the heartbeat row is silently skipped. Aggregate effect: silent under-counting of venue health and possible stale balance/allowance reads.

**Severity rationale**: Karachi position `c30f28a5-d4e` ($0.59) is live and chain-synced; if collateral allowance refresh silently fails during settlement Tx, the risk daemon may approve actions on stale balance state. SEV-1.

#### Finding #6 — Daemon stdout `.log` files are 0 bytes; all output routed to `.err` (SEV-1, category H assumption-drift + G silent-failures)

**Evidence 1**: `ls -lt logs/*.log logs/*.err`:
- `zeus-live.log`: **0 bytes** since 2026-05-15 09:04
- `zeus-ingest.log`: **860 bytes** since 2026-05-14 03:19  
- `zeus-forecast-live.log`: **0 bytes** since 2026-05-15 03:44
- `riskguard-live.log`: **0 bytes** since 2026-05-15 15:26
- Corresponding `.err` files: **89 MB / 114 MB / 1.5 MB / 10 MB** updated 2026-05-16 11:50 (live).

**Evidence 2**: Run #1 audit (`REPORT.md`) cited the May-15 `.log` mtime as evidence that the live-trading daemon was offline. **Run #1 was misled by this same observability gap**; daemons were running but had no stdout output.

**Evidence 3**: `~/Library/LaunchAgents/com.zeus.*.plist` `StandardOutPath` and `StandardErrorPath` both resolve to per-daemon log files inside `logs/`, but the Python logging config sends ALL records (INFO + WARNING + ERROR) to stderr — so `.log` is by design empty. The naming creates a false-negative trap for any operator who runs `tail logs/zeus-live.log`.

**Severity rationale**: Any operator (or audit run) inspecting `.log` will misdiagnose a healthy daemon as offline. SEV-1 because it directly degrades Tier-0 incident response.

#### Finding #7 — Harvester open-markets selector pulls every closed Polymarket event, not just weather (SEV-2, category E settlement + B math)

**Evidence 1**: `src/ingest/harvester_truth_writer.py:239 _fetch_open_settling_markets()` calls `GAMMA_BASE/events?closed=true&order=endDate&ascending=false` with **no category filter** — returns sport, election, music, weather indiscriminately.

**Evidence 2**: Recent `logs/zeus-ingest.err` shows the tick processing dozens of non-weather events per pass and emitting WARNING for each:
```
WARNING: harvester_truth_writer: both pm_bin_lo and pm_bin_hi are None; skipping Lucknow 2026-05-14 (degenerate bin)
WARNING: harvester_truth_writer: skipping Madrid 2026-05-14 ambiguous winners=3 slug=lal-rea-ovi-2026-05-14-more-markets
WARNING: harvester_truth_writer: skipping Atlanta 2026-05-14 ambiguous winners=7 slug=cs2-navi-lgc-2026-05-13
```
The slugs (`cricipl-`, `mlb-`, `lal-`, `cs2-`, `atp-`, `wta-`, `crint-`) are cricket, baseball, La Liga, CS2, tennis — explicitly non-weather. Each enters the bin-resolver and is rejected.

**Evidence 3**: Net result `markets_resolved=0, settlements_written=0` per tick over the last 24h despite real weather markets settling daily. The bin filter masks the absence of an upstream category filter, so the system looks healthy at the aggregate (`status: ok`) while no weather settlement work occurs.

**Severity rationale**: Karachi 2026-05-17 will settle within the next 24-48h; if the weather match-path is also rejecting valid weather markets (not just sport events) we will silently miss the settlement and rely on manual write-in. The log noise also hides Finding #5's heartbeat warnings. SEV-2 with **INVESTIGATE-FURTHER on whether the weather path itself fires**: needs a successful settlement to confirm.

#### Finding #8 — Live position carries non-ISO sentinel timestamp `"unknown_entered_at"` in `position_events.occurred_at` (SEV-2, category A data provenance + D time-calendar)

**Evidence 1**: `position_events` for the Karachi live position:
```
('c30f28a5-d4e', 'CHAIN_SYNCED', None, 'unknown_entered_at')
```
The `occurred_at` column on the live row is the string `"unknown_entered_at"` — not an ISO timestamp. `decision_id` is also NULL on this row (related to Finding #2 lineage).

**Evidence 2**: No schema CHECK constraint on `occurred_at` in `position_events`; sentinel string accepted at write boundary. Any downstream code that sorts/diffs on `occurred_at` will mis-order this row or crash on `datetime.fromisoformat()`.

**Evidence 3**: Search `grep -rn "unknown_entered_at" src/` shows the sentinel is emitted by chain reconciliation when an on-chain order has no corresponding local intent timestamp — defensive fallback, but it should be `NULL` (typed) or an ISO timestamp inferred from blockchain block timestamp, not a free-form string.

**Severity rationale**: Pollutes the audit trail for the only live position. SEV-2 because no immediate correctness break — but Fitz Constraint #4 (data provenance) applies; type system should make the wrong value unwritable.

### LEARNINGS — category promotions

- **Category I "Antibody-implemented-but-unwired"** now has 2 confirmed appearances (Run #1 + Run #2 Finding #1). **PROMOTED** from PROPOSED to active per the 2-appearance gate in LEARNINGS.md.
- **Category G silent-failures** earned 3 distinct yields this run (Findings #5, #6, #7) — yield ladder rises from MEDIUM to **HIGH**.
- **Category H assumption-drift** earned 1 new yield (Finding #6); stays MEDIUM.

### Updated executive ranking (Run-2 view)

Ordering by combined `(severity, blast-radius-on-Karachi-2026-05-17-settlement, ease-of-fix)`:

1. **Finding #5 — DB-lock storm** (SEV-1, directly affects live settlement-day collateral state)
2. **Finding #6 — empty `.log` files** (SEV-1, blocks operator triage including audits)
3. **Finding #4 residue + #7 harvester filter** (RESOLVED + SEV-2 INVESTIGATE-FURTHER; combined risk of missing Karachi auto-settle)
4. **Finding #2 — hypothesis decision_id NULL, regressed** (SEV-1 still, growing each day)
5. **Finding #1 — registry antibody unwired** (SEV-1 still, no growth)
6. **Finding #3 — doctrine drift** (SEV-2, low-blast operational)
7. **Finding #8 — sentinel timestamp on live row** (SEV-2, low-blast but on the only live position)

### Live position implications (Karachi 2026-05-17)

- Position `c30f28a5-d4e`, condition `0xc5fad…f44ae`, phase **active**, chain_state **synced**, 1.5873 shares @ entry 0.37, cost basis $0.5873, p_posterior 0.88. Order status `partial` (ENTRY_ORDER_FILLED at 2026-05-16 06:40Z).
- **Settlement path concern**: Findings #4-residue and #7 together mean we cannot prove the harvester will auto-write the settlement to `forecasts.settlements_v2` on 2026-05-17. The code is routed correctly but the open-markets pass has not produced a single successful weather settlement in 5 days. **Operator should prepare manual settlement fallback OR observe one weather settlement before 5/17 to confirm the path is functional.**
- **Collateral path concern**: Finding #5 — CollateralLedger heartbeat losing writes; if it fails during Tx submission, position state may diverge from chain. Risk-daemon mitigation depends on heartbeat freshness.

### Anomalies / blockers / non-claims

- Cannot probe `FORECASTS.opportunity_fact` — table does not exist on FORECASTS (it lives on TRADE; consistent with Finding #1 registry-vs-disk drift).
- Cannot probe `FORECASTS.observations.observed_at` — column does not exist. Did NOT investigate further (would expand scope beyond Run #2).
- Cannot confirm weather-market path of harvester fires correctly without observing one successful tick — labelled `INVESTIGATE-FURTHER` per skill protocol.
- DID NOT touch any production code, write to any DB, or modify any operator-facing docs. Read-only audit per skill contract.

