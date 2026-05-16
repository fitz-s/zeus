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

