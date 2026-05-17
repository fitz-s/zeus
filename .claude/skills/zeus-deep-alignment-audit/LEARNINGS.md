# LEARNINGS — Zeus Deep Alignment Audit

This file is the **evolving brain** of the `zeus-deep-alignment-audit` skill. `SKILL.md` is the protocol; this file is the accumulated wisdom of past audits and changes after every run.

**Update discipline**: only the skill's `Closeout` step rewrites this file. Do NOT hand-edit without recording the rationale and date in `AUDIT_HISTORY.md`'s retrospective section, otherwise the self-evolving loop loses provenance.

**Authority ordering**: when seed categories in `SKILL.md` disagree with active categories below, **this file wins**. Seeds are v0 priors; this is empirical reality.

---

## Active categories (current effective set)

The skill's Boot step 2 reads this table before dispatching workers. Each row = one parallel haiku worker.

| ID | Name | Yield | Last validated | Notes |
|----|------|-------|----------------|-------|
| A | Data provenance holes | HIGH | 2026-05-16 (run #1) | SEV-1: selection_hypothesis_fact.decision_id 100% NULL |
| B | Math drift | LOW | 2026-05-16 (run #1) | surface probe only; no finding; revisit with deeper Kelly/posted-size derivation |
| C | Statistical pitfalls | LOW | 2026-05-16 (run #1) | invariants hold; FDR rejected 3/212 prefilter passes (1.4%) needs larger-N retest |
| D | Time/calendar | LOW | 2026-05-16 (run #1) | only documented naive sentinel; target_date UTC-vs-local question deferred to contract read |
| E | Settlement edges | HIGH | 2026-05-16 (run #1) | SEV-1 (escalated): writer routes settlements_v2 to ghost world.db post-K1; canonical forecasts.db silent 5d; K1 followup commits 1d952b072e+a322810a2a not in main — blocks live Karachi settlement |
| F | Cross-module invariants | HIGH | 2026-05-16 (run #1) | SEV-1: registry-vs-disk drift unenforced; 24 tables wrong-DB |
| G | Silent failures | MEDIUM | 2026-05-16 (run #1) | folded into F+A this run (default-None param; comment-doc'd unwiring) |
| H | Assumption drift | MEDIUM | 2026-05-16 (run #1) | SEV-2: current_state.md + current_data_state.md stale + factually wrong |

**Yield ladder** (set by Closeout after each run):
- `UNPROVEN` — seed only, never validated
- `HIGH` — found SEV-1 or ≥2 SEV-2 in last 3 runs
- `MEDIUM` — found ≥1 SEV-2 or ≥3 SEV-3 in last 3 runs
- `LOW` — found ≤2 SEV-3 in last 3 runs
- `DEAD` — 3 consecutive runs with zero findings; demote (do not delete)
- `ARCHIVED` — all antibodies deployed, category permanently impossible

DEAD categories are skipped on the next 2 runs, then re-tested on every 3rd run as a regression check (assumption drift can resurrect a dead category).

---

## High-signal probes (worth keeping verbatim — reused next run)

Format: `[category] probe phrasing → what it caught → run date`

(none yet — first audit populates)

### Run #1 additions (2026-05-16)

- `[F] registry-vs-disk cross-check: load architecture/db_table_ownership.yaml, open all three DBs read-only, count rows per (table, db) pair, flag (a) registry-owner DB has zero rows while another DB has >0, (b) two or more DBs both have >0 rows for the same non-meta table` — caught the K1 24-table drift + 5 multi-DB duplicates in one pass
- `[A] per-fact-table NULL-rate scan: for every table whose name ends in _fact, enumerate columns ending in _id, run SELECT SUM(<col> IS NULL), COUNT(*) and flag any column with NULL-rate > 0% when sibling tables in the same write batch have 0% NULL` — caught the selection_hypothesis_fact.decision_id 100%-NULL bug via direct contrast against sibling selection_family_fact 0%
- `[H] doctrine-freshness check: parse current_state.md / current_data_state.md / current_source_validity.md for the Main HEAD anchor and last-audited date; cross-check vs git rev-parse HEAD origin/main and vs today() - 14d` — caught the 1-commit-stale HEAD and 18-day-stale data audit
- `[E] settlement-writer cadence + column-conflation probe: SELECT SUM(settled_at = recorded_at), COUNT(*), MAX(settled_at) FROM settlements_v2; cross-check MAX(recorded_at) of opportunity_fact same DB; if delta > 36h or identity-rate == 100%, flag` — caught the 5-day silent settlement writer plus the schema-design vs writer mismatch in run #1
- `[E+F] writer-destination-vs-registry cross-check: for each registry-canonical writer (harvester_truth_writer, evaluator selectors, etc.) grep the get_*_connection import and compare to architecture/db_table_ownership.yaml db: field for that table; if mismatch, also count rows on actual-target-db vs registry-target-db to confirm whether writes have been silently misrouting` — caught the K1/5b commit-not-in-main + ghost-vs-canonical settlements_v2 routing bug (run #1 SEV-1 escalation)
- `[E] legacy↔v2 settlement-migration completeness: SELECT count of (city, target_date) keys present in legacy but missing in v2, and vice versa; if either delta > 0 the migration is not idempotent and consumers see different answers` — caught the 814 legacy-only + 57 v2-only key drift
- `[B] math-range sanity: for every probability/score column (p_raw, p_cal, p_market, alpha, best_edge, p_value, q_value) run SELECT MIN, MAX, AVG and flag any value outside the documented domain` — null finding run #1 but kept as cheap regression probe
- `[C] FDR self-consistency: SELECT count where selected_post_fdr=1 AND rejection_stage IS NOT NULL` — invariant check; 0 contradictions run #1

---

## Anti-heuristics (probes proven low-signal — skip)

Format: `[category] probe phrasing → why it was noisy → run date`

(none yet)

---

## Proposed new categories (awaiting promotion)

Categories proposed by Closeout when a finding didn't fit any active row. Promoted to active after appearing in **2 separate runs** (avoid noise-driven proliferation).

Format:

```
### PROPOSED: <id> <name>
- Definition (3 bullets max):
  - ...
  - ...
  - ...
- First seen: <date> in run <N>
- Validation needed: appear again in 1 more run to promote
```

### PROPOSED: I Antibody-implemented-but-unwired
- Definition (3 bullets max):
  - A correctness antibody exists as a callable function (assert / validator / shape-check) but no production code path invokes it
  - Often justified by an inline comment citing "deferred" or "P2 wiring pending" — a smell of indefinite deferral
  - Detectable by static analysis: grep the antibody name, expect callers in `src/main.py` or `src/runtime/`, fail if only `tests/` references appear
- First seen: 2026-05-16 in run #1 (assert_db_matches_registry exists but not called at boot; main.py:857 inline comment cites prior retired antibody validate_world_schema_at_boot in the same paragraph, suggesting recurrence)
- Validation needed: appear again in 1 more run to promote

---

## Deployed antibodies (categories progressing toward ARCHIVED)

When an antibody recommendation from a past report gets shipped (commit lands, operator confirms), record it here. When all antibodies for a category are deployed, mark that category `ARCHIVED` in the active table above.

Format: `[category] antibody description → shipped commit SHA → audit-run-that-recommended → archived?`

(none yet)

---

## Meta-audit log (every 3rd audit, taxonomy restructure)

Records of structural changes to this file beyond per-run updates. Pruning, restructuring, renaming categories, updating SKILL.md seeds.

Format:

```
### Meta-audit <date> (after run #<N>)
- Categories pruned: ...
- Categories restructured: old <X> + old <Y> → new <Z> because ...
- SKILL.md seeds updated: yes/no, what changed
- Rationale: ...
```

(none yet — first meta-audit happens after run #3)

---

## Operating notes for the orchestrator

When you (the opus orchestrator running this skill) read this file in Boot step 2:

1. **Dispatch only ACTIVE categories** — skip DEAD (except every 3rd-run regression check) and ARCHIVED.
2. **Inject high-signal probes verbatim** into the relevant worker's brief. Don't paraphrase — exact phrasing matters because it's the empirically validated wording.
3. **Tell each worker their category's Yield level** so they calibrate effort. HIGH = scan deep. LOW = quick sweep.
4. **Cross-check Proposed categories**: if any finding this run fits a Proposed row, that's the 2nd appearance → promote in Closeout.
5. **Check deployed antibodies**: before flagging a SEV-1 in some category, verify the relevant antibody isn't already DEPLOYED (false alarm on archived issue would erode trust).

The whole point of this file is that future-you arrives smarter than past-you. If a run's `Closeout` doesn't update it, the skill silently devolves into a frozen template and loses its reason for existing.


## Run #2 update (2026-05-16) — yields & probe additions

### Category yield ladder after Run #2

| Category | Run #1 | Run #2 | New ladder |
|---|---|---|---|
| A data provenance | 1 (#2) | 1 (#8 sentinel timestamp on live row) | HIGH (sustained) |
| B math drift | 0 | 0 | LOW |
| C statistical pitfalls | 0 | 0 | LOW |
| D time-calendar | 0 | 0.5 (#8 cross-listed) | LOW |
| E settlement edges | 1 (#4) | 1.5 (#4 residue, #7 filter) | HIGH (sustained) |
| F cross-module invariants | 1 (#1) | 1 (#5 cross-process DB-lock) | HIGH (sustained) |
| G silent failures | 0 | 3 (#5 DB-lock storm, #6 empty .log, #7 harvester WARNING flood) | **HIGH** (promoted from MEDIUM) |
| H assumption drift | 1 (#3) | 1 (#6 .log naming false-negative) | MEDIUM (sustained) |
| **I antibody-implemented-but-unwired** | 1 (proposed) | 1 (#1 re-confirmed) | **ACTIVE (promoted)** — 2-appearance gate met |

### New high-signal probes added to the registry

1. **Empty stdout but full stderr** — `ls -lt logs/*.log logs/*.err | head -10`. If `.log` is 0-byte but `.err` is large and recently mtime'd, daemon is alive but operator-facing logs are misleading. Cost ≈ 0. (Finding #6.)
2. **DB-lock contention pulse** — `grep -c "database is locked" logs/*.err` and `tail -1000 logs/<daemon>.err | grep -c "database is locked"`. The historical vs recent pair tells you whether the storm is ongoing. Cost ≈ 0. (Finding #5.)
3. **Harvester tick fruitfulness** — `grep "harvester_truth_writer_tick.*markets_resolved" logs/zeus-ingest.err | tail -20`. If `settlements_written=0` over a multi-day window despite expected settlements, upstream filter is broken. Cost ≈ 0. (Finding #7.)
4. **Position-events timestamp sanity** — `python -c "import sqlite3; ... SELECT occurred_at FROM position_events WHERE occurred_at NOT GLOB '2*'"`. Catches non-ISO sentinels. Cost ≈ 0. (Finding #8.)

### Methodology antibody (audit-of-the-audit)

Run #1 itself was fooled by Finding #6 — it sampled `.log` files and concluded the live daemon was offline. **Audit protocol must always probe `.err` alongside `.log` for any python-logging daemon**, especially under macOS `launchd` where stdout/stderr split rigidly. Added to the Boot checklist for future runs.


## Run #3 update (2026-05-16) — yields, probes, taxonomy restructure

### Active categories — restructure

Per Run #3 meta-audit (3rd run is meta-audit per SKILL.md): split seed `E. Settlement edges` into two independent categories because Run #3 finding #11 (heartbeat-sensor.plist KeepAlive convention) had to overload F/G when its true home is "daemon supervision", a surface the original E description never covered.

**Updated active table** (replaces the row for E above, adds E2 + J):

| ID | Name | Yield | Last validated | Notes |
|----|------|-------|----------------|-------|
| E1 | Settlement edges (writer-route, migration symmetry, cadence) | HIGH | 2026-05-16 (run #3 — sustained) | Run-1 #4 escalation + Run-2 #7 harvester filter |
| E2 | Daemon supervision (launchd KeepAlive convention, plist↔cron coupling) | MEDIUM | 2026-05-16 (run #3 — promoted) | Run-3 #11 first standalone hit |
| J | Secrets in plaintext (cron, plist EnvironmentVariables, shell-rc, repo configs) | **ACTIVE — needs 1 more run** | 2026-05-16 (run #3 — promoted from proposed) | Run-3 #9 SEV-1 was a FALSE POSITIVE per operator override (adjacent crontab comment documented intentional placement); category still promoted because the **probe itself fired correctly** — see anti-heuristic below for the comment-adjacency gate |

### Yield ladder updates (Run #3)

| Category | Run #3 result | New ladder |
|---|---|---|
| A data provenance | 0 (re-test passive) | HIGH (sustained) |
| B math drift | 1 (SEV-3 #13 replay neutralized Kelly modulators) | **LOW → MEDIUM** (first non-zero in 3 runs) |
| C statistical pitfalls | 0 (skipped) | LOW (no change; skipped, not tested) |
| D time-calendar | 0 (crontab grep only) | LOW |
| E1 settlement edges | 0 | HIGH (sustained — watch for demotion next run) |
| E2 daemon supervision | 1 (SEV-2 #11) | **MEDIUM** (promoted, first standalone) |
| F cross-module invariants | 1 (SEV-2 #12 ghost trade-lifecycle tables) | HIGH (sustained) |
| G silent failures | 1 (SEV-1 #10 severity-channel disagreement) | HIGH (sustained) |
| H assumption drift | 0 | MEDIUM (no change) |
| I antibody-unwired | 0 direct (#12 is the sibling read-side instance) | HIGH (category active) |
| J secrets in plaintext | 1 (SEV-1 #9 — operator-overridden to FALSE POSITIVE) | **ACTIVE pending 2nd-run validation** |

### High-signal probes added in Run #3

1. **[J] cron + plist + shell-rc plaintext secret scan** — `crontab -l | grep -oE "[A-Z_]+_(KEY|TOKEN|SECRET|PASSWORD)=[^ ]+"` AND grep across `~/Library/LaunchAgents/*.plist` + `~/.zshrc` + `~/.bashrc` + `~/.profile`. **Comment-adjacency gate (mandatory)**: before flagging a hit, scan ±3 lines for an explanatory comment; if present, demote to INFO. Caught Finding #9 (then operator-classified as FP because of adjacent comment).
2. **[E2] launchd KeepAlive convention check** — `for p in ~/Library/LaunchAgents/com.zeus.*.plist; do plutil -extract KeepAlive raw "$p" || echo "$p MISSING KeepAlive"; done`. Caught Finding #11.
3. **[F] schema-without-data ghost-table scan** — for every table that exists on multiple DBs, count rows on each; flag any pair where exactly one side has 0 rows AND the other has > 0. **Distinct from Run-1 DUP probe** (which flagged when BOTH sides had rows); this catches the ASYMMETRIC ghost case. Caught Finding #12.
4. **[G] severity-channel-disagreement check** — parse `logs/heartbeat-sensor.err` for last severity, parse `logs/zeus-heartbeat-dispatch.log` for last dispatcher severity, flag if they differ for ≥30 min. Caught Finding #10 (RED-for-hours → dispatcher reports `degraded`).

### Anti-heuristics recorded (Run #3)

- **`grep -rEn "0x[a-f0-9]{64}" src/` as a secrets probe** is noisy — returns `market_id` and `condition_id` hex (true content, not secrets). Better: restrict to `*.py` files AND require `=` or `:` immediately before the hex, OR scan only crontab/plist/shell-rc, never `src/`. Recorded 2026-05-16.
- **Cat-J secrets-in-config-line probe without comment-adjacency gate** is prone to FP: Run-3 Finding #9 was classified SEV-1 then operator-overridden to FALSE POSITIVE because an adjacent crontab comment clearly documented the key's role and intentional placement. **Future Cat-J probes must check ±3 lines of context for an explanatory comment before flagging.**

### Methodology antibody (audit-of-the-audit, Run #3)

- **VS Code terminal output buffering bug** observed: multi-line heredoc-style `sqlite3` / `python` invocations occasionally returned stale output from a prior tool call (Karachi position query first returned `693|693` lines belonging to a different query). **Antibody**: prefix every probe output with a literal `printf '==MARK==\n'` sentinel and grep the sentinel out of the response before parsing. Already used successfully in the final Karachi/HB probe.

### Meta-audit (after Run #3 — first meta-audit cycle per SKILL.md)

- **Categories pruned**: none. Active set grew 8 (seed) → 9 (Run #1 +I proposed) → 10 (Run #2 I promoted) → 11 (Run #3 J proposed, E split into E1+E2). Still under the 12-prune threshold.
- **Categories restructured**: old `E. Settlement edges` → `E1. Settlement edges` (writer route, migration symmetry, cadence) + `E2. Daemon supervision` (launchd KeepAlive convention, plist↔cron coupling). Rationale: Run-3 #11 had to overload F/G because original E never covered launchd supervision; future runs need a dedicated worker.
- **SKILL.md seeds updated**: recommended add at v1 — new seed `J. Secrets in plaintext` (cron, plist `EnvironmentVariables`, shell-rc, repo `.env`/`config/*.json`), with mandatory comment-adjacency gate.
- **Category yield reality after 3 runs**: HIGH = A, E1, F, G, I. MEDIUM = B (just promoted), E2 (just promoted), H. LOW = C, D. None DEAD (no 3-consecutive-zero category).
- **Rationale**: meta-audit confirms the skill is converging on the real Zeus failure surface (lineage, schema, daemon/alarm coupling, secrets) rather than the original seed bias (pure math/stat probes). Seed list should be rewritten v1 in next release.



## Run #4 update (2026-05-16) — keystone Karachi 5/17 cascade verdict

### Yield ladder updates (Run #4)

- **Cat-H promoted MEDIUM → HIGH** (2nd consecutive hit: Run #3 #12 ghost trade-lifecycle tables + Run #4 #15 `settlements`/`settlements_v2` 1583-row asymmetric drift). Ghost-table-asymmetric is now a HIGH-yield surface.
- **NEW Cat-K cascade liveness** added at HIGH (caught keystone Finding #14 on first probe). Definition-only is insufficient; every link in a documented cascade must have ≥1 production caller.
- **Cat-C single-callsite scan**: re-promoted MEDIUM → HIGH on the cascade slice — F14 is a sister-instance (function defined but no production caller). Cat-C and Cat-K overlap on "wired-but-not-driven"; keep both, but Cat-K is cascade-scoped (multi-link) while Cat-C is single-callsite.
- **Final ladder after Run #4**: HIGH = A, C, E1, F, G, H, I, K. MEDIUM = B, E2. LOW = D. None DEAD.

### High-signal probes added in Run #4

9. **[K] cascade-liveness probe** — for any function the runbook/docs claim "the system invokes", run `grep -rn '<fn>(' src/ scripts/ | grep -v tests/` and require ≥1 hit. Apply to every link in any documented cascade (truth-writer → pnl-resolver → `_settle_positions` → `submit_redeem` → `clob.redeem`). Caught Finding #14 (submit_redeem zero production callers).
10. **[E2] plist-KeepAlive sweep** — `for p in ~/Library/LaunchAgents/com.zeus.*.plist; do printf '%s: ' "$p"; plutil -extract KeepAlive raw "$p" 2>/dev/null || echo 'MISSING'; done`. Whitelist one-shot eval plists. Caught Finding #11 re-verification.

### Anti-heuristics refined (Run #4)

- **Cat-J `$(...)`-substitution gate**: in addition to the comment-adjacency gate, exclude values whose RHS is a `$(...)` command substitution (e.g. `KEY=$(keychain_resolver \u2026)`). Only literal `KEY=hex` patterns should fire. Prevents re-raising operator-overridden Run #3 #9.

### Meta-audit-of-the-audit (Run #4)

- **Pattern**: Cat-C (single-callsite scan) and Cat-K (cascade liveness) both surfaced the same Finding #14 from different angles. This is healthy — overlapping probes converge on real bugs. Don't merge them; they apply at different granularities (call-site vs cascade-link).
- **Cascade-docs drift**: `KARACHI_2026_05_17_MANUAL_FALLBACK.md` §1 described an auto-cascade reaching `clob.redeem` that does not exist in production. Audit caught the gap before the operator relied on it. **Antibody**: every runbook describing a cascade must cite the production caller (file:line) for each link, not just the link function name.


## Run #5 update (2026-05-16) — Cat-K exhaustion + cross-DB shadow sweep

### Yield ladder updates (Run #5)

- **Cat-K confirmed HIGH** (2nd consecutive run producing SEV-0/SEV-1: Run #4 F14 + Run #5 F16/F17). Cascade-liveness is the dominant fault surface in late-2026 Zeus.
- **Cat-H confirmed HIGH** (3rd consecutive: Run #1 settlements_v2 + Run #3 #12 + Run #4 #15 + Run #5 F18 + F19). Cross-DB shadow tables now demand a dedicated sweep every run.
- **Cat-F confirmed HIGH** (3rd consecutive: Run #3 #12 + Run #4 #15 + Run #5 F20). Orphan shadow rows are a recurring debt.
- **Final ladder after Run #5**: HIGH = A, C, E1, F, G, H, I, K. MEDIUM = B, E2. LOW = D. None DEAD.

### High-signal probes added in Run #5

10. **[K] AGENTS.md-as-cascade-map probe** — `grep -rniE 'no live|deferred|future|unwired|TODO.*wire|Z5' src/**/AGENTS.md`. Latent state machines are usually documented (`HIGH — no live chain side effects in Z4` on `wrap_unwrap_commands` was the F16 signal). Near-free way to enumerate dead modules without re-reading code.
11. **[H+F] cross-DB shadow-table row-count matrix** — for every duplicated table name across `state/zeus_trades.db`, `state/zeus-forecasts.db`, `state/zeus-world.db`, emit `(table, db, count)` tuple. Asymmetric rows = finding. Caught F18 (`observation_instants_v2` inverse asymmetry), F19 (`market_events_v2` 3-DB shadow 9914/7326/2112), F20 (`ensemble_snapshots` 116 orphan rows). One-pass sqlite script suffices.
12. **[K] writer-is-a-script gate** — for every table T whose reader is wired into a daemon/scheduler: `grep -rn 'INSERT.*INTO ${T}' src/ scripts/` and classify writer. If writer lives ONLY in `scripts/*.py` (no APScheduler/cron registration), SEV-1 trapdoor. Caught F17 (`validated_calibration_transfers` reader on by feature flag, writer is `scripts/evaluate_calibration_transfer_oos.py:381` only, table empty).

### Mandatory base artifact (Run #5 onward)

- **State-machine inventory matrix** (§1 of every RUN_N_findings.md going forward). Columns: machine, driver/scheduler, public-fns, prod-caller-grep-count, table-row-count, AGENTS.md verdict, status (ALIVE/DEAD/HALF-DEAD), since-when. Enumerate every `src/**/*_commands.py`, `*_listener.py`, `*_runner.py`, `*_resolver.py`, `*_writer.py`. Drift (ALIVE→DEAD or vice versa across runs) IS the finding.

### Anti-heuristics refined (Run #5)

- **Cat-J extended**: `scripts/*.py` writers are NOT secrets-in-cron risks (Cat-J), they ARE writer-is-script trapdoors (Cat-K probe #12). Don't double-count.
- **Cat-K + AGENTS.md interaction**: if `AGENTS.md` explicitly labels a module as deferred/no-live, do NOT raise SEV-0; raise SEV-0-latent (skill brain category) so future runs don't re-discover. F16 demonstrates the shape. Tracker requirement: every SEV-0-latent must cite the AGENTS.md line proving the deferral is intentional.

### Meta-audit-of-the-audit (Run #5)

- **State-machine inventory turns Cat-K from open-ended grep into a finite-coverage probe.** Before Run #5, Cat-K was "grep for any function the runbook claims is invoked". After Run #5, it's "enumerate all production state machines; for each, run probes 9+10+12". This converts unbounded → bounded. Re-run the matrix every audit; drift = finding.
- **Cross-DB shadow surface forms a graph, not a list.** F19 was the first 3-DB shadow (forecasts/trades/world all carry `market_events_v2`). Probe #11's matrix form catches this; single-DB row-count diffs do not.
- **Runbook DB-location accuracy**: F14 was reported on `forecasts.db` in Run #4; actually on `zeus_trades.db`. Antibody: every state-machine finding must cite `db_table_ownership.yaml` line for the canonical DB, not infer from co-located code.



## Run #6 update (2026-05-16, applied retroactively in Run #7 closeout)

### Yield ladder updates (Run #6)

- **Cat-J ACTIVE** confirmed: dual-write detection (`market_events_v2` raw `sqlite3.connect` in `market_scanner.py:610`) surfaced as F22. Cat-J probe yielded SEV-2 directly.
- **Cat-A (decision_id NULL)** worsened: 693→1518 rows on `selection_hypothesis_fact.decision_id` = 100% NULL. Promote NULL-on-FK to its own probe-class.
- **Cat-K** still HIGH (Run #6 did not surface new Cat-K, but the TODO PR-126 was the planned fix for F14/F16; landed before Run #7).

### High-signal probes added (Run #6)

- (Run #6 had no new probe additions beyond Run #5 set; deferred to Run #7.)

## Run #7 update (2026-05-17)

### Yield ladder updates (Run #7)

- **Cat-K confirmed permanent HIGH** (3rd consecutive: Run #4 F14, Run #5 F16/F17, Run #7 F27). Most reliable yield surface in the audit's history.
- **Cat-H confirmed HIGH** (4th consecutive: F25 is a generalization of Cat-H to multi-table FK correlation, not just cross-DB row asymmetry).
- **NEW Cat-L (two-truth registry/allowlist anti-pattern)** added at MEDIUM. F26 first instance. Promote to HIGH if Run #8 produces another.
- **NEW Cat-M (schema/state-machine co-evolution gap)** added at MEDIUM. F27 first instance. Promote to HIGH if any future PR adds a state-value without index update.
- **Final ladder after Run #7**: HIGH = A, C, E1, F, G, H, I, K. MEDIUM = B, E2, L, M. LOW = D. J = active.

### High-signal probes added (Run #7)

13. **[K+M] PR-merge baseline-shift sweep** — when a referenced TODO PR lands, list `git diff PR^1..PR --name-only` and for every modified `src/state/db.py` or schema file, identify which state machines / enums / indexes are conditioned on. Re-audit each one against the post-merge schema. PR-126 added `REDEEM_OPERATOR_REQUIRED` and CHANGED the enum CHECK constraint; should have automatically triggered re-audit of every UNIQUE INDEX with a `WHERE state NOT IN (...)` predicate. Manual grep: `grep -rn "WHERE state NOT IN" src/state/`.
14. **[A+H] multi-table FK NULL correlation matrix** — enumerate `*_fact` tables on `state/zeus_trades.db`, scan every column ending in `_id` (not PK), compute (table, col, null_count, total, pct). For columns sharing a name across tables (e.g. `snapshot_id`), compute pairwise NULL-count overlap. Equal NULL counts → one upstream culprit, SEV bumped one tier. Caught F25 (3-table correlation: 19175 NULL on opportunity_fact.snapshot_id and probability_trace_fact.decision_snapshot_id is the same upstream writer).
15. **[L] two-truth registry/allowlist detector** — for every `frozenset` / `tuple` / `dict` of paths or table names in `src/`, grep `tests/conftest.py` and `architecture/*.yaml` for parallel copies. Diff the contents. Caught F26 (SQLITE_CONNECT_ALLOWLIST divergence).

### Anti-heuristics refined (Run #7)

- **Cat-J refinement**: when a "raw `sqlite3.connect`" finding is raised, FIRST check `tests/conftest.py:_WLA_SQLITE_CONNECT_ALLOWLIST` to see if the call site is allowlisted with a tag. If tagged `pending_track_a6` / `pending_track_a6_scripts` / `read_only_ro_uri` / `singleton_persistent_conn`, demote to OPEN-acknowledged rather than re-raising as new. F5 and F22 both downgraded in Run #7 status table to OPEN-acknowledged.
- **Token-economy antibody** (Run #6 §7 forecast): on first pass, read STATUS.md (or master index) only — DO NOT read individual RUN_N_findings.md files. Lazy-load Run files when a specific finding's narrative is required. Pre-probe context consumption should stay below 15%.

### Meta-audit-of-the-audit (Run #7)

- **PR-merge skepticism is a yielding stance.** Operator brief explicitly said "be skeptical of PR-126." F27 surfaced from that stance. Going forward, when ANY PR lands between runs, treat it as adversarial-review surface, not passive baseline.
- **Three-finding minimum for new-package bootstrap.** Run #7 produced exactly 3 new findings while bootstrapping a new task package. The 3:1 ratio (3 findings : 1 package) is healthy. If a baseline-shift run produces 0 new findings, question whether the package bootstrap was warranted.
- **Run #7 took ~50% more context than Run #6 due to baseline-shift sweep overhead.** The PR-merge probe (#13) is information-dense; budget for it explicitly.


---

## Run #8 deltas (2026-05-17) — resolution-sweep run, new Cat-N introduced

### Yield ladder updates (Run #8)

- **Cat-K promoted to PERMANENT HIGH** (4 consecutive runs producing SEV-1/SEV-0; Run #8 confirmed F27 verdict-flip and surfaced F29 sibling).
- **NEW Cat-N (audit-package-coherence anti-pattern)** introduced at MEDIUM. F28 first instance (v1↔v2 numbering divergence affecting the audit's own deliverables). F30 second instance (migration runner does not enforce `Last reused/audited:` header drift — same family: audit artifact hygiene without enforcement).
- **Cat-L stays MEDIUM** (no new two-truth finding this run).
- **Final ladder after Run #8**: HIGH = A, C, E1, F, G, H, I, K. MEDIUM = B, E2, L, M, N. LOW = D. J = active.

### High-signal probes added (Run #8)

16. **[K] in-code-comment-as-design-intent reader** — when a finding is being graded as SEV-1+ on a code site authored in a recent PR, MUST read the FULL comment block (5 lines above + 5 lines below) AND the PR description before final grading. F27 was reversed from SEV-1 to "intentional design artifact" only after reading the in-code NOTE the PR-126 author left explaining REDEEM_OPERATOR_REQUIRED. Pattern: `sed -n "$((LINE-10)),$((LINE+10))p" <file>` on every SEV-1+ code site before commit.
17. **[A] constructor-omission AST scan** — when a NULL-fact-column finding has equal-count overlap across multiple downstream tables (Run #7 probe #14), confirm the suspected upstream writer by AST-listing EVERY constructor call of the dataclass and counting which kwargs are present/absent. Caught F25 root cause (31/71 EdgeDecision ctors omit `decision_snapshot_id=`). Implementation: `python -c "import ast; …"` over the suspect module, group by kwarg presence.
18. **[N] auditor-package self-consistency check** — at run closeout, list ALL F-numbers cited in the run's deliverables + diff against the canonical reference file. If divergence > 1 finding, add a numbering-reconciliation block before declaring closeout complete. Caught F28 mid-run when v2.F1 ≠ v1.F1 collision surfaced.

### Anti-heuristics refined (Run #8)

- **Reverse-grading rule**: any finding graded SEV-1+ in a prior run that survives to the current run MUST be re-probed with probe #16 (in-code comment reader) before being re-promoted. Saves bogus carry-overs. F27 was the test case.
- **Resolution-sweep token-economy pattern**: when the run goal is "drive ALL open findings to verdict" rather than "discover new findings", front-load all probes into ≤5 large terminal commands using `printf '==MARK==\n'` sentinels between sections. Read each result file once. Synthesize all deliverables in a single doc-creation pass. Saves ~60% context vs per-finding probe roundtripping. Validated this run.

### Meta-audit-of-the-audit (Run #8)

- **The auditor's own artifacts can manifest the defects being audited.** F28 (dual-index numbering) caught the auditor (me) replicating the same dual-numbering inconsistency in the resolution sweep itself, half-way through drafting cards. Fix: introduce probe #18 as a closeout gate.
- **Resolution-sweep runs are higher-leverage than discovery runs for converting attention into decisions.** Run #8 produced 0 new SEV-0/SEV-1 findings but generated 22 actionable cards + 1 ship matrix. Discovery-mode runs MUST be interleaved with resolution-sweep runs; running discovery 4× in a row inflates the open-findings backlog without ever giving operator a decisive close.
- **Per-finding "next probe" annotation should be mandatory.** Every open finding card now carries either a definitive verdict or an explicit "1-shot probe to settle". Operator can ask "show me the 3 cheapest probes" and get a deterministic answer. Promote this to skill template.

---

## Run #9 deltas (2026-05-17) — operator-triggered forensic run, new Cat-O introduced

### Yield ladder updates (Run #9)

- **Cat-K reconfirmed PERMANENT HIGH** (5 consecutive runs with SEV-1/SEV-0; Run #9 = F32 shipped-without-schedule). The yield is structurally inexhaustible because shipped-but-incomplete is a category of operator-stable engineering process drift, not a one-off defect.
- **NEW Cat-O (operator-brief-coupling-falsification)** introduced at MEDIUM. First instance: Run #9 operator brief coupled two observations (oracle + Shenzhen) into one sentence; investigation falsified the coupling. Promote to HIGH on 2nd false-coupling rescue.
- **Final ladder after Run #9**: HIGH = A, C, E1, F, G, H, I, K. MEDIUM = B, E2, L, M, N, O. LOW = D. J = active.

### High-signal probes added (Run #9)

19. **[K] shipped-without-schedule detector** — for every module whose docstring contains the phrases "ONLY writer" / "single writer" / "the canonical X". For each: grep `/Users/leofitz/.openclaw/cron/jobs.json`, `crontab -l`, and `launchctl list` for the script basename. If none match AND the module's output artifact does not exist on disk, raise SEV-1 immediately. This is a 30-second probe that would have caught F32 on day-one of A3 shipping if it had existed.
20. **[J] WARNING-tier debouncer audit** — for every `logger.warning(...)` reachable on a reload/cycle path (search recurrence: grep `reload_oracle\|reload_calibration\|reload_policy` callsites), check the surrounding 20 lines for an ERROR escalation after a temporal threshold (`time.time() - _last_seen > 86400`). If no escalation exists AND the call site is in a path that runs more than once per hour, raise SEV-2. Catches F33-class observability theater.

### Anti-heuristics refined (Run #9)

- **Operator-brief decoupling**: when an operator observation contains "AND" / "I see X. Y is also Z." / two clauses joined by causal-sounding glue, *forbid* coupling them in the investigation until each is independently grounded. Falsify the coupling EXPLICITLY before allowing a unified narrative. F32 and F34 in this run are independent; an investigation that fused them would have produced a false unified "oracle is missing therefore sizing is small therefore order doesn't fill" story, when in reality the order's non-fill is independent of size and purely a pricing-policy artifact.
- **"NEVER ran in prod" vs "stopped running" distinction**: when an artifact-file is missing, distinguish (a) the writer ran successfully N days ago and stopped, vs (b) the writer was never wired into a recurring schedule. Probe: `git log <writer>` for recent edits + `grep -rn <writer-basename> cron/jobs.json crontab launchctl`. Case (b) is *higher* severity than (a) because (a) implies a daemon failure (recoverable) while (b) implies a process-shipping defect (recurs on every similar PR).
- **Daemon-WARNING tolerance ceiling**: a daemon emitting the same WARNING > 100 times in 24 h has crossed the line from "operational signal" to "broken thing the team has agreed to ignore". The right response is NOT to tune the threshold or filter the log — it is to either *resolve the underlying condition* or *promote the signal to ERROR with notification* so the broken-agreement is forced into the open.

### Meta-audit-of-the-audit (Run #9)

- **Forensic runs benefit from the same multi-section sentinel-terminal pattern Run #8 validated for sweeps.** 5 large terminal commands (oracle path probe, live status probe, log scan, bridge-callability check, expiry-sweep grep) produced enough material for the complete deliverable. Per-question round-tripping would have spent >2× the context for no information gain. Promote sentinel-terminal pattern to default shape for ALL non-trivial runs.
- **Operator's intuition is gold even when it's only partially right.** The operator said "oracle may NOT be fully applied to runtime" — technically false on the *application* side (the multiplier IS applied), and technically true on the *data* side (the data isn't arriving). The right framing for the response is to *honor the intuition by sharpening it*, not to either rubber-stamp it or dismiss it. F32 is the operator's intuition restated rigorously.
- **One run, two findings of different severity, different categories, different fix-paths — the deliverable must NOT collapse them.** F32 (SEV-1, oracle bridge schedule) and F34 (SEV-3, passive entry) are tempting to fuse because they were uncovered in the same investigation. Resisting the temptation is the lesson. Each gets its own card, its own owner-hint, its own verification probe.
