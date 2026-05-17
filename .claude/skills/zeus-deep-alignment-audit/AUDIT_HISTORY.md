# AUDIT_HISTORY — Zeus Deep Alignment Audit Runs

Append-only log of every completed run of the `zeus-deep-alignment-audit` skill. The skill's `Closeout` step appends one row + one retrospective paragraph per run.

**Never edit past entries.** If a past entry was wrong (back-filled discovery, antibody falsified, etc.), add a new dated note in the entry's retrospective section explaining the update — preserve the original.

---

## Run table

| # | Date | Commit | K root gaps | SEV-1 | SEV-2 | SEV-3 | Coverage | Report |
|---|------|--------|-------------|-------|-------|-------|----------|--------|
| 1 | 2026-05-16 | 556d55be23 (main) / ff714a7507 (skill) | 4 | 3 | 1 | 0 | A, E, F, G, H probed (findings); B, C, D probed (no findings) | docs/operations/task_2026-05-16_deep_alignment_audit/REPORT.md |
| 2 | 2026-05-16 | a924766c8a (main) / 40e7709b2d (skill) | — | 2 | 2 | 0 | Phase-A re-verify Run-1 + Phase-B new findings #5–#8 (E, F, G, H/D) | docs/operations/task_2026-05-16_deep_alignment_audit/REPORT.md §Run #2 |
| 3 | 2026-05-16 | a924766c8a (main) / 199a43cbbc (skill) | 5 | 2 | 2 | 1 | B, E (split → E2 daemon-supervision), F, G, **J (NEW)** probed (findings #9–#13); C, D, H skipped | docs/operations/task_2026-05-16_deep_alignment_audit/RUN_3_findings.md + deep-dives commits f65a6abe96 + Phase-3 40e7709b2d |
| 4 | 2026-05-16 | a924766c8a (main) / f0c4f48397 (skill) | 1 | 1 | 0 | 0 | **C+Cat-K NEW** (F14 submit_redeem zero callers — Karachi 5/17 blocker), **H** (F15 settlements/_v2 1583-row asymmetric drift) | docs/operations/task_2026-05-16_deep_alignment_audit/RUN_4_findings.md |
| 5 | 2026-05-16 | a924766c8a (main) / (this commit) (skill) | 1 (latent F16) | 2 | 2 | 0 | **Cat-K cascade-liveness exhaustion** (F16 wrap_unwrap_commands ZERO callers latent SEV-0; F17 calibration_transfers trapdoor SEV-1) + **H+F cross-DB shadow sweep** (F18 observation_instants_v2 asymmetric SEV-1; F19 market_events_v2 3-DB asymmetric SEV-2; F20 ensemble_snapshots 116 dead rows SEV-2); state-machine inventory (11 lifecycles: 8 ALIVE, 2 DEAD, 1 HALF-DEAD); F14 re-confirmed + DB-location corrected (zeus_trades.db not forecasts.db) | docs/operations/task_2026-05-16_deep_alignment_audit/RUN_5_findings.md |
| 6 | 2026-05-17 | a924766c8a (main) / (prior) (skill) | 1 | 1 | 2 | 0 | Cat-K decision_id NULL acceleration (F24: 693→1518 rows, 100% NULL); F21 hourly_instants legacy writer; F22 market_events_v2 raw connect; F23 migration runner bare | docs/operations/task_2026-05-16_deep_alignment_audit/RUN_6_findings.md |
| 7 | 2026-05-17 | 9259df3e9c (main, post-PR-126/130/132/133) / 5e8ceee1df (skill) | 3 | 2 | 1 | 0 | Post-PR-126 baseline; F25 SEV-0 triple-NULL (3 fact tables, 19175 rows joint); F26 two-truth allowlist; F27 SEV-1 PR-126 UNIQUE INDEX gap; new package task_2026-05-16_post_pr126_audit | docs/operations/task_2026-05-16_post_pr126_audit/RUN_7_findings.md |
| 8 | 2026-05-17 | 9259df3e9c (main) / (this commit) (skill) | 0 (resolution sweep) | 1 | 3 | 1 | **Resolution sweep**: all open v1.F1–F23 + v2.F1–F27 driven to definitive verdict or explicit INVESTIGATE-FURTHER. **F25 SEV-0 ROOT CAUSE PROVEN** (31 of 71 EdgeDecision ctors in evaluator.py omit decision_snapshot_id — fix plan PR-A.1). **F27 verdict** (PR-126 INDEX = INTENTIONAL design, not a bug; F29 sibling logged for REDEEM_REVIEW_REQUIRED). **Karachi 5/17 ship matrix** built — verdict GO. New findings: F28 META-numbering, F29 REVIEW_REQUIRED index gap, F30 migration-runner header drift, F31 market_events_v2 reader-trace deferred. | docs/operations/task_2026-05-16_post_pr126_audit/RUN_8_resolution_sweep.md + RUN_8_findings.md + KARACHI_5_17_SHIP_DECISION_MATRIX.md |

---

## Run retrospectives

Format per run:

```
### Run <N> — <YYYY-MM-DD> — commit <short-SHA>

**One-paragraph summary**: what was surprising, what pattern recurred from prior runs, what the audit MISSED that a later incident revealed (back-filled in subsequent runs).

**Categories that produced findings**: <list with SEV counts>

**Categories that produced nothing**: <list — track consecutive-empty count toward DEAD demotion>

**New patterns observed**: <bullets — if any didn't fit active categories, they should appear in LEARNINGS.md "Proposed">

**Methodology changes triggered**: <bullets — e.g. "Added probe X to LEARNINGS high-signal", "Demoted category Y to DEAD after 3rd empty run">

**Hand-edits to LEARNINGS.md beyond Closeout** (rare, should be justified): <bullets>
```

### Run 1 — 2026-05-16 — commit 556d55be23

**One-paragraph summary**: First real run after the skill landed (ff714a7507). The expected K1 split parity gap on Platt-v2 / historical_forecasts_v2 was a false alarm — both ARE in `world.db` as the registry declares. The real K1 gap is much larger: 24 trade-lifecycle tables declared `db: world` in `architecture/db_table_ownership.yaml` actually live on `zeus_trades.db`, and `assert_db_matches_registry()` was implemented but is explicitly left unwired at boot per a comment in `src/main.py:857`. Separately, the live Karachi position `c30f28a5-d4e` (active on-chain right now, $0.59 cost basis, condition `0xc5fad…f44ae`) has 100% NULL `decision_id` on `selection_hypothesis_fact` (506/506 rows) because `evaluator.py:1535` calls `log_selection_hypothesis_fact(...)` without threading the in-scope `decision_snapshot_id`. The sibling `log_selection_family_fact` call 18 lines above DOES pass it — clear caller bug. Doctrine files are also stale: `current_state.md` is one commit behind, `current_data_state.md` last audit was 18 days ago (exceeds its own 14-day max), claims wrong settlement baseline (1,609 vs reality 5,570 legacy + 3,987 v2) and wrong harvester status (DORMANT vs reality 3,605 VERIFIED).

**Categories that produced findings**: F (SEV-1, registry-vs-disk unenforced); A (SEV-1, hypothesis decision_id NULL); G (folded into F+A as silent-failure overlap); H (SEV-2, doctrine drift); E (SEV-2, settlements_v2 settled_at==recorded_at + 5d silent writer + 814 legacy-only keys).

**Categories that produced nothing**: B (math drift), C (statistical pitfalls), D (time/calendar) — probed at surface level (range checks, invariant counts, naive-tz scan) but no SEV-1/2 found. Treat as 1 LOW-yield run each on the ledger; not DEAD candidates.

**New patterns observed**:
- **Antibody-implemented-but-unwired** is a recurring failure shape: `assert_db_matches_registry` is the second instance (the comment at `src/main.py:857` cites a previous retired antibody `validate_world_schema_at_boot` in the same paragraph). This pattern deserves promotion as a candidate Proposed category.
- **Default-None on optional-but-required lineage keys** is the failure shape behind the hypothesis decision_id bug. Worth a high-signal probe: enumerate every `*_fact` table column ending in `_id` and assert sub-1% NULL rate.
- **Schema drift outpaces audit doctrine**: 8 column names from the 2026-05-08 PLAN matrix no longer exist. Audit probes must be re-validated against live `pragma table_info` at the START of every run, not reused verbatim from prior packets.

**Methodology changes triggered**:
- Added probe "registry-vs-disk cross-check across all three DBs counting rows per (table, db) pair, with multi-DB-populated rows flagged DUP" to LEARNINGS high-signal — exact phrasing preserved.
- Added probe "per-fact-table NULL-rate scan on all `_id` columns" to LEARNINGS high-signal.
- Proposed new category I (Antibody implemented but unwired) — needs 1 more appearance to promote.
- Bumped F (Cross-module invariants) yield to HIGH (1 SEV-1 in 1 run; verifies in next 2 runs to confirm).
- Bumped A (Data provenance holes) yield to HIGH (1 SEV-1 in 1 run; same rule).
- Bumped H (Assumption drift) yield to MEDIUM (1 SEV-2).
- Bumped E (Settlement edges) yield to HIGH (1 SEV-1 — mis-routed writer + missing K1 followup commits).
- Added probe "settlement-writer cadence + settled_at==recorded_at identity-rate cross-check" to LEARNINGS high-signal.
- Added probe "legacy↔v2 settlement-migration completeness (symmetric (city,target_date) key-set diff)" to LEARNINGS high-signal.
- Added probe "writer-destination-vs-registry cross-check across all canonical writers" to LEARNINGS high-signal (this is the probe that produced the run #1 SEV-1 escalation; should run first on every future Boot).

**Mid-run escalation (2026-05-16, post initial REPORT commit 490c902e77)**: Operator-requested follow-up probe of daemon logs / cron / writer code path elevated Finding #4 from SEV-2 to SEV-1 after discovery that the harvester truth writer opens `get_world_connection()` while the registry declares `settlements_v2` canonical on forecasts.db, and that the K1 followup commits intended to fix this (`1d952b072e`, `a322810a2a`) are not in main. Documented as a separate commit on top of 490c902e77 to preserve the discovery sequence. Lesson: a SEV-2 'silent writer' finding should automatically trigger writer-target-vs-registry investigation in-run, not rely on operator escalation request — codify this as a default expansion step in SKILL.md for any settlement/lineage anomaly.

**Hand-edits to LEARNINGS.md beyond Closeout**: none.

---

## Post-mortem index

When a Zeus incident later reveals an issue the audit missed, link it back here so the next run knows the gap.

Format: `<incident date> <one-line description> → audit run #<N> failed to catch because <reason> → category <ID> updated to catch in future`

(none yet)

---

## Operating notes for the orchestrator

When you (the opus orchestrator running this skill) read this file in Boot step 3:

1. **Identify repeat-offender categories**: any category appearing in retrospectives 2+ times → escalate its worker's probe depth on this run.
2. **Identify long-stale categories**: any category with no findings for ≥3 runs → it's a DEAD candidate this run (check Active table in LEARNINGS).
3. **Note any back-filled post-mortems**: they reveal the audit's blind spots. Read recent ones before designing this run's worker briefs.
4. **Track meta-audit cadence**: count entries in the Run table. If this would be run #3, #6, #9, … the Closeout MUST do a meta-audit step (see SKILL.md).


## Run 2 — 2026-05-16 16:50 UTC

| Anchor | Worktree HEAD | Findings | SEV-0 | SEV-1 | SEV-2 | INVESTIGATE-FURTHER |
|---|---|---|---|---|---|---|
| main `a924766c8a` | `40e7709b2d` | 4 re-verified + 4 new | 0 | 2 (#5 DB-lock storm, #6 empty `.log` files) | 2 (#7 harvester filter coarse, #8 sentinel timestamp) | 1 (Finding #7 weather-path liveness) |

### Run 2 retrospective

- **Phase A**: 3 of 4 Run-1 findings STILL-OPEN against current main (`#1` registry antibody, `#2` hypothesis decision_id, `#3` doctrine drift). Only `#4` (harvester writer mis-routing) RESOLVED via PR #121 — verified at `src/ingest_main.py:646` calling `get_forecasts_connection`. Residue: 2,112 stranded rows on `world.market_events_v2` (1,386 + 726) but no growth since 2026-05-13 16:45 UTC.
- **Finding #2 regression**: 506 → 693 NULL hypothesis rows (+37%) over ~10 days; bug actively accruing data debt. Plus new sister-instance: `execution_fact` 1/6 NULL `decision_id`.
- **Phase B yield**: Categories E/G/F dominated. **G silent-failures triple-yielded** (DB-lock storm, empty `.log` files, harvester noise) and rises HIGH.
- **Category I promoted**: Antibody-implemented-but-unwired (Run #1 + Run #2 Finding #1) is now an active LEARNINGS category.
- **Methodology surprise**: Run #1 was itself fooled by Finding #6 (empty `.log` files) — it cited `zeus-live.log` mtime as evidence the live-trading daemon was offline, but `.err` was 89 MB and growing. Audit-of-the-audit antibody: ALWAYS check `.err` alongside `.log` for python-logging daemons.
- **Tier-0 risk for Karachi 5/17**: Combined Findings #4-residue + #5 + #7 → cannot prove auto-settlement path works. Operator preparation required.


## Run 3 — 2026-05-16 ~17:10 UTC — commit 199a43cbbc (audit consolidation)

| Anchor | Worktree HEAD | Findings | SEV-1 | SEV-2 | SEV-3 | New cat |
|---|---|---|---|---|---|---|
| main `a924766c8a` | `199a43cbbc` (with deep-dives `f65a6abe96` + Phase-3 `40e7709b2d`) | 5 new (#9–#13) | 2 (#9 secret in crontab — **operator-marked FALSE POSITIVE post-run**; #10 severity-channel mismatch) | 2 (#11 plist no KeepAlive; #12 ghost trade-lifecycle tables) | 1 (#13 replay Kelly modulators neutralized) | **J. Secrets in plaintext** promoted from no-prior-coverage |

### Run 3 retrospective

- **Surprise #1 — operator-blind alarm channel (Finding #10)**: heartbeat-sensor.err had >49 consecutive RED ticks while `zeus-heartbeat-dispatch.log` reported `severity=degraded` every 30 min for 3.5+ hours. Neither Run #1 nor Run #2 probed the cron-dispatcher tier; the dispatcher was firing `ALERT: zeus degraded` during Run #2 itself and went unnoticed. Codified Run-3 probe #4 (severity-channel-disagreement check) to make this category permanently visible.
- **Surprise #2 — new Cat J first-probe SEV-1, then operator override**: WU_API_KEY plaintext in crontab (Finding #9) was a clean Cat-J hit. Operator subsequently classified it FALSE POSITIVE on 2026-05-16: the adjacent crontab comment documents the key's intentional placement. **Result**: J stays promoted (probe fired correctly), and a new anti-heuristic is recorded — the comment-adjacency gate: Cat-J probes must scan ±3 lines for an explanatory comment before flagging.
- **Surprise #3 — half-finished K1 cleanup left ghost schema (Finding #12)**: post-K1 trade-lifecycle tables exist as empty shells on world.db while data lives on zeus_trades.db. Sibling of Run-1 #1 / Run-2 #5, but on the read side. New probe #3 (schema-without-data ghost-table scan) added; distinct from Run-1 DUP probe (BOTH-sides populated) — this is the ASYMMETRIC case.
- **Taxonomy restructure**: split seed `E` into `E1` (settlement edges) + `E2` (daemon supervision). Finding #11 (heartbeat-sensor.plist missing KeepAlive) had to overload F/G because the original E description never covered launchd supervision.
- **Cat B first non-zero finding** in 3 runs (Finding #13 replay Kelly modulators hardcoded neutral): promotes LOW → MEDIUM. Confidence intentionally kept MEDIUM (downstream consumer chain not yet traced).
- **Methodology antibody**: VS Code terminal output buffering bug — multi-line heredoc-style sqlite3/python invocations occasionally returned stale output from a prior tool call. Antibody: prefix probes with `printf '==MARK==\n'` and grep the sentinel out before parsing.

### Meta-audit (this is run #3 — first meta-audit per SKILL.md)

- Recorded in LEARNINGS.md "Meta-audit log" section. Summary: 0 categories pruned, E split into E1+E2, J promoted (pending 2nd-run validation), recommend SKILL.md seeds rewrite v1 to include J + E1/E2 split.

### Hand-edits to LEARNINGS.md beyond Closeout

- Run #3 LEARNINGS append was applied as part of audit consolidation commit (this commit). All edits accounted for in this Run-3 row and retrospective.



## Run 4 — 2026-05-16 — keystone: Karachi 5/17 cascade-liveness verdict

| Anchor | Worktree HEAD | Findings | SEV-0 | SEV-1 | SEV-2 | Cascade |
|---|---|---|---|---|---|---|
| main `a924766c8a` | `8114dcd009` | 2 new (#14, #15) + 1 re-verified (#11) | 1 (#14 `submit_redeem` no production driver) | 1 (#15 `settlements`/`settlements_v2` 1583-row asymmetric drift) | 0 new (#11 re-verified, no state change) | **PARTIAL** — L1-L3 GREEN (truth-writer → pnl-resolver → `_settle_positions`); L4-L5 RED (`enqueue_redeem_command` writes `REDEEM_INTENT_CREATED` row, no driver advances it to `REDEEM_SUBMITTED`/on-chain) |

### Run 4 retrospective

- **Surprise #1 (keystone) — cascade-liveness gap (Finding #14)**: trace from `harvester_truth_writer` to `clob.redeem` showed all 5 links wired in the code but the 4th link (`submit_redeem`) has ZERO production callers — only test imports. Karachi 5/17 auto-redeem of the $0.59 LIVE position (c30f28a5-d4e, shares=1.5873) will NOT happen without manual intervention. Operator's `KARACHI_2026_05_17_MANUAL_FALLBACK.md` §1 description is wrong about the cascade reaching `clob.redeem` automatically. P&L books normally; on-chain claim does not.
- **Surprise #2 — asymmetric ghost (Finding #15)**: Cat-H (Run #3 probe #3 schema-without-data ghost-table scan) double-hit on `state/zeus-forecasts.db`: `settlements` has 5582 rows, `settlements_v2` has 3999 rows (1583-row gap). Production reader uses correct table; `_v2` is a silent shadow. Cat-H promoted MEDIUM → HIGH (now 2 hits).
- **Surprise #3 — anti-heuristic confirmed**: WU_API_KEY plaintext re-tripped Cat-J probe; was correctly NOT re-raised because Run #3 #9 was operator-overridden as FP. Confirms comment-adjacency gate value. Refinement added: `$(...)` substitution gate (literal `KEY=hex` fires; `KEY=$(keychain_resolver \u2026)` does not).
- **Methodology surprise — new Cat-K added**: "cascade liveness" as a first-class category. Definition-only is insufficient; every link in a documented cascade must have a verified production caller. Probe #9: for any function the runbook claims the system invokes, `grep -rn '<fn>(' src/ scripts/` and require ≥1 non-test hit. Pairs with Cat-I (antibody-implemented-but-unwired) — same root failure mode but at the cascade-link granularity.
- **Re-verification**: 6 prior STILL-OPEN findings (#1, #2, #5, #7, #10, #11, #12, #13) all confirmed STILL-OPEN; no regressions vs Run #3. Finding #4 (PR #121) remains RESOLVED on the read side.

### Hand-edits to LEARNINGS.md beyond Closeout

- Cat-H promoted MEDIUM → HIGH (2nd hit).
- New Cat-K added (cascade liveness).
- New probe #9 documented (production-caller-for-cascade-fn).
- Cat-J anti-heuristic refined ($(...) substitution gate).

---

### Run 5 — 2026-05-16 — commit (this commit)

**One-paragraph summary**: Exhaustive Cat-K cascade-liveness sweep + cross-DB shadow-table audit. Inventoried 11 production state machines and classified each as ALIVE / DEAD / HALF-DEAD by checking (a) does a scheduler/loop drive it, (b) does it have ≥1 non-test production caller, (c) is the table being written today. Two complete DEAD machines surfaced: `settlement_commands.submit_redeem` (F14 sibling, already known) and `wrap_unwrap_commands.*` (F16, 7 public functions, ZERO production callers anywhere, table empty — latent SEV-0 because `AGENTS.md` documents it as "no live chain side effects in Z4" so it is *intentionally* deferred but the skill must flag it so future runs don't re-discover). One HALF-DEAD machine: `validated_calibration_transfers` reader is wired and feature-flag-gated, writer is a one-shot CLI script with no scheduler (F17 trapdoor SEV-1 — if flag flips on without backfill, every transfer query falls into "no evidence" branch silently). Cross-DB shadow sweep produced 3 new findings: `observation_instants_v2` 929k row inverse-asymmetry on world.db (F18 SEV-1), `market_events_v2` triple-DB shadow with forecasts canonical 9914 / trades shadow 7326 / world stranded 2112 (F19 SEV-2), `ensemble_snapshots` 116 dead legacy rows on world.db (F20 SEV-2). F14 location corrected: state machine is on `zeus_trades.db` not `forecasts.db` (Run #4 wrong). No new Karachi 5/17 blockers — F14 remains sole direct blocker. State-machine inventory itself is a new artifact (§1 of RUN_5_findings.md) that should be re-run every audit as a regression check against AGENTS.md drift.

**Categories that produced findings**: K (SEV-0 latent F16, SEV-1 F17 — Cat-K now 2 runs consecutive HIGH yield); H (SEV-1 F18, SEV-2 F19 — 3rd run consecutive); F (SEV-2 F20 — orphan shadow, 3rd run consecutive).

**Categories that produced nothing**: A, B, C (single-callsite), D, E1, E2, G, I, J — probed at registry/inventory level via state-machine sweep but no new SEV-1/2. Cat-J specifically re-validated against new candidate findings, no false positives raised.

**New patterns observed**:
- **Writer-is-a-CLI-script pattern** (F17): when a reader is wired into production and a writer exists only as `scripts/*.py` with no scheduler/cron registration, the table risks silent emptiness. This is structurally distinct from Cat-K (which is "no callers"); the writer HAS a caller, just not an automated one. Worth its own probe.
- **AGENTS.md-as-cascade-map**: `src/execution/AGENTS.md` explicitly labels `wrap_unwrap_commands.py` as "HIGH — no live chain side effects in Z4". Grepping AGENTS.md files for "no live", "deferred", "future", "unwired", "Z5" is a near-free way to enumerate latent state machines without re-reading the code.
- **Cross-DB shadow tables form a graph**: F19 is the first 3-DB shadow finding. Single-DB row-counts miss it; must matrix-multiply across `state/*.db`.
- **State-machine inventory is the right base artifact for Cat-K**: enumerating every `*_commands.py` / `*_listener.py` / `*_runner.py` / `*_resolver.py` / `*_writer.py` module gives the universe; classifying each ALIVE/DEAD/HALF-DEAD turns Cat-K into a finite-coverage probe instead of an open-ended grep.

**Methodology changes triggered**:
- Added probe #10 to LEARNINGS high-signal: **AGENTS.md grep for deferred-cascade markers** (`grep -rni 'no live\|deferred\|future\|unwired\|TODO.*wire' src/**/AGENTS.md`).
- Added probe #11 to LEARNINGS high-signal: **cross-DB shadow-table row-count matrix** across `state/zeus_trades.db`, `state/zeus-forecasts.db`, `state/zeus-world.db` — for every duplicated table name, emit a (table, db, count) tuple and flag asymmetries.
- Added probe #12 to LEARNINGS high-signal: **writer-is-a-script gate** (`reader_calls > 0 AND scheduler_writer_calls == 0 AND scripts/*.py contains INSERT into table` → SEV-1).
- Promoted Cat-K from new→HIGH yield (2 consecutive runs producing SEV-0/SEV-1).
- Cat-J refinement: explicit comment-adjacency gate also covers `scripts/*.py` one-shot tools (no flag).
- **Methodology change for next runs**: every Run must produce or update the state-machine inventory matrix in §1 of its findings doc as a base artifact. Drift in the matrix (ALIVE→DEAD or vice versa) IS the finding.

**Hand-edits to LEARNINGS.md beyond Closeout**: none.

**F14 correction note (back-fill on Run #4 retrospective)**: Run #4 retrospective said the `settlement_commands` state machine writes to `forecasts.db`; Run #5 §1 inventory confirmed via `db_table_ownership.yaml` and live row-counts that it is on `zeus_trades.db`. Findings unaffected (the cascade is still broken at `submit_redeem`), but the manual fallback runbook DB query path needs the corrected location. Preserved per "Never edit past entries" rule; correction lives here + in RUN_5_findings.md §2 + §4.



---

### Run 7 — 2026-05-17 — commit (this commit)

**One-paragraph summary**: Post-PR-126 baseline audit. PR #126 (cascade-liveness fix, F14/F16 FIXED) + PR #130 (ref-authority docs) + PR #132/#133 (`src/state/db_writer_lock.py` 749 LOC, Phase 0/0.5/1 + Track A.6 daemon retrofit) shifted the baseline from `acaae2c242` → `9259df3e9c`. Bootstrapped a new package `docs/operations/task_2026-05-16_post_pr126_audit/` (README/STATUS/FINDINGS_REFERENCE_v2/RUN_7_findings) because Run #6's TODO PR landed and the master index needed a clean fork; old package retains run-narrative files but its master index gets a SUPERSEDED header. Per operator instruction "be skeptical of PR #126", uncovered F27 (SEV-1) — the new `REDEEM_OPERATOR_REQUIRED` state lacks a UNIQUE INDEX update on `settlement_commands`, so an operator-required row permanently blocks new INSERTs for the same `(condition_id, market_id, payout_asset)`. F25 (SEV-0) — broader F24: triple-NULL systemic snapshot-write failure across `selection_hypothesis_fact.decision_id` (100%/1518), `opportunity_fact.snapshot_id` (68.20%/19175), `probability_trace_fact.decision_snapshot_id` (67.74%/19175). The 19175 row-count match across two tables strongly implies one upstream call site silently dropping `snapshot_id`. F26 (SEV-2) — two-truth allowlist: `src/state/db_writer_lock.py:575:SQLITE_CONNECT_ALLOWLIST` (8 entries, declarative) diverges from `tests/conftest.py:177:_WLA_SQLITE_CONNECT_ALLOWLIST` (~40 entries, enforced by pytest gate). Production module misleads readers into thinking allowlist is shorter than CI actually accepts. Karachi 2026-05-17 position (`c30f28a5…`) confirmed in `day0_window` with 1.5873 shares; settlement_commands table empty; F4 fix held (no `settle_status` column). R2 sentinel held (`forecasts.db` user_version=3, `SCHEMA_FORECASTS_VERSION=3`).

**Categories that produced findings**: K (SEV-1 F27 — PR-126 schema-vs-state-machine co-evolution gap, 3rd consecutive run with Cat-K SEV); H (F25 — multi-table correlated NULL, qualifies as cross-DB-shadow-class via correlated fact-table integrity); L (new — two-truth registry/allowlist anti-pattern; F26).

**Categories that produced nothing**: A, B, C (no new single-callsite), D, E1, E2 (plist sweep N/A), F (no shadow-orphan delta), G, I, J (re-validated against F25/F26/F27 candidates, no FP).

**New patterns observed**:
- **Schema-vs-state-machine co-evolution gap** (F27): adding a new state-machine enum value (`REDEEM_OPERATOR_REQUIRED`) without auditing every index/constraint/trigger that conditions on the enum value is a structural review gap. PR-126 author + reviewers + CI tests all missed it. The cascade-liveness contract test was added but tests the transition semantics, not the lockout edge case.
- **Two-truth registry pattern** (F26): when a policy is declared in `src/` AND duplicated in `tests/conftest.py` (or `architecture/*.yaml`), the two copies drift. F26 is the first concrete instance for allowlists; the registry pattern is older (Run #5 db_table_ownership.yaml vs runtime declarations).
- **Correlated multi-table NULL** (F25): NULL rates on FK columns across multiple fact tables sharing one upstream writer is a stronger signal than single-table NULL. The 19175-match across opportunity_fact and probability_trace_fact is forensic-grade evidence of one buggy call path. Single-table NULL probes miss this — must compute pairwise correlations.

**Methodology changes triggered**:
- Added probe #13 to LEARNINGS high-signal: **PR-merge baseline-shift sweep** — when a referenced TODO PR lands, re-probe every finding the PR touched at INDEX/CONSTRAINT/TRIGGER level (not just function level). PR-126 touched `state` enum; should have automatically triggered re-audit of every constraint/index conditioning on that enum.
- Added probe #14 to LEARNINGS high-signal: **multi-table FK NULL correlation matrix** — for any fact-table sharing FK column names (`*_id`), compute pairwise row-count overlap of NULL counts. Equal NULL counts across tables = one upstream culprit; sum the SEV up one tier.
- Added probe #15 to LEARNINGS high-signal: **two-truth registry detector** — for every allowlist/registry/policy in `src/`, grep `tests/conftest.py` + `architecture/*.yaml` for a same-named parallel copy. If two copies exist and content differs, SEV-2 auto-flag.
- **Promoted Cat-K → permanent HIGH** (3 consecutive runs producing SEV-1/SEV-0: Run #4 F14, Run #5 F16/F17, Run #7 F27). Cat-K is now the highest-yield category in the audit's history.
- **NEW Cat-L (two-truth registry/allowlist)** added at MEDIUM, promote to HIGH if 2nd run produces a finding.
- **NEW Cat-M (schema/state-machine co-evolution gap)** added at MEDIUM (F27 first instance). Promote on 2nd hit.

**Hand-edits to LEARNINGS.md beyond Closeout**: applied Run #6 deferred deltas from RUN_6_findings.md §6 (not previously applied) PLUS Run #7 deltas above in one combined edit.

**Token-economy validation (Run #6 §7 prediction confirmed)**: First-pass context exhaustion came within ~30% of compaction before any probe started. Going forward: STATUS.md-only on first pass; load Run files only when narrative needed; lazy-load LEARNINGS only at closeout. Cat-J §7 antibody updated.

### Run 8 — 2026-05-17 — commit (this commit)

**One-paragraph summary**: Resolution-sweep run rather than discovery. Per operator instruction "continue investigating — every unresolved item needs to be fully clarified", drove EVERY open v1.F1–F23 + v2.F1–F27 to definitive verdict, 1-shot probe, or explicit accept-with-justification. F25 SEV-0 root cause PROVEN via AST scan of evaluator.py: 31 of 71 EdgeDecision construction sites (all early-rejection paths) omit `decision_snapshot_id=`, producing the 19,175 joint-NULL rows across opportunity_fact and probability_trace_fact. F27 PR-126 INDEX verdict reversed from "review gap SEV-1" to "intentional design artifact" after reading the in-code NOTE explaining REDEEM_OPERATOR_REQUIRED as designed-terminal-with-operator-action. F5 collateral_ledger reversed to RESOLVED (ledger owns its own DB, allowlisted by design). Built Karachi 5/17 ship-decision matrix: GO verdict; 5-link cascade L1-L5 GREEN/YELLOW status table; pre-event operator checklist; HARD-STOP triggers. 4 new findings (F28 meta-numbering, F29 REVIEW_REQUIRED sibling, F30 migration header drift, F31 reader-trace).

**Categories that produced findings**: Cat-N (audit-package-coherence): 2 (F28, F30 — SEV-2 + SEV-3). Cat-K (design-decision-incomplete): 1 (F29 SEV-2). Cat-J (silent-cross-DB-read): 1 (F31 SEV-2).

**Cat-K elevation note**: 4 consecutive runs producing SEV-1/SEV-0 (Run #4 F14, Run #5 F16/F17, Run #7 F27, Run #8 confirmed F27 intent + new F29 sibling). Cat-K remains the highest-yield category.

**Cat-N introduction**: this run formally introduces Cat-N (audit-package-coherence) after F28 caught the dual-index numbering drift that I myself replicated mid-run while drafting cards. The fact that the auditor's own deliverable manifested the same defect being audited is a strong signal the meta-process needs a structural fix, not a discipline reminder.

**Surprise**: I expected F27 to remain SEV-1. Reading the in-code NOTE flipped it. Lesson: when a finding looks like a bug, READ THE FULL COMMENT BLOCK around the suspect code before grading. PR-126 author left an explicit explanation; v2.F27 grader missed it.

**Hand-edits beyond Closeout**: appended Run #6 and Run #7 rows to the main table (they were previously only in retrospective sections — table-omission was itself an instance of F28/Cat-N). FINDINGS_REFERENCE_v2.md gained a "Numbering reconciliation" preamble + 4 new rows (F28-F31).

**Token-economy validation**: Resolution-sweep run completed within budget by FRONT-LOADING all probes into 4 large terminal commands (sentinel-delimited multi-section), reading each result file once, then synthesizing all deliverables in a single document-creation pass. No per-finding probe roundtripping. This pattern should be promoted to a Cat-J §7 best practice.

### Run 9 — 2026-05-17 — commit (this commit)

**One-paragraph summary**: Operator-triggered targeted investigation, not a sweep run. Operator observation (translated): "oracle rate may NOT be fully applied to runtime. I see Shenzhen orders pending fill." Two distinct questions, two distinct answers. Q1 (oracle): partial confirmation — the oracle PENALTY MULTIPLIER is correctly applied at `src/engine/evaluator.py:2801-2802` (Kelly haircut), but the oracle DATA itself never arrives — `data/oracle_error_rates.json` does not exist anywhere in the repo, `bridge_oracle_to_calibration.py` is documented as "the ONLY writer" yet has zero scheduler entries in `cron/jobs.json` or `crontab -l`, and `logs/zeus-live.log` shows `oracle_penalty reloaded: 0 records, 0 blacklisted` every 15 minutes for ≥640 consecutive cycles, fully ignored. Every city collapses to MISSING (0.5x Kelly) and every LOW track collapses to METRIC_UNSUPPORTED (0.0x block). F32 SEV-1. Q2 (Shenzhen pending-fill): falsified at every layer except pricing strategy — venue command 31dcda5c57ec4f8a is genuinely LIVE on Polymarket (REST + WS_USER both confirm), state-machine is healthy (ACK at 11:52:45Z, 5.3s post-submit), the order placed BUY 7.74 @ 0.26 = top_bid in a 0.26/0.31 book with only 16 shares at the bid and 14.36 at the ask. Root cause: `entry_price = self.p_market[i]` at `src/strategy/market_analysis.py:350` resolves to the bid, executor enforces passive limit at `executor.py:1328`, so zeus structurally joins-the-bid rather than lifting-the-ask. With 0.401/share edge unrealized, F34 SEV-3 raised (strategy-policy discussion, deferred to operator). Today's venue_commands shows 43 EXPIRED / 9 ACKED / 3 FILLED — 89% non-fill is the macroscopic signature. Karachi 5/17 blast radius: YES for F32 (Karachi/high also MISSING → 0.5x Kelly), NO behavioral change required pre-event since c30f28a5 GO position is already in day0_window with the haircut baked in.

**Categories that produced findings**: Cat-K (design-decision-incomplete): F32 (bridge writer shipped without scheduler), F34 (passive-only entry strategy never validated against book thickness). Cat-J (audit-blind-spot via log-only signal): F33 (no escalation on persistent MISSING).

**New patterns observed**:
- **Shipped-without-schedule anti-pattern** (F32): a module documented as "the ONLY writer to X" must, by definition, run on a recurring schedule, else the reader's fallback becomes the de facto behavior. PR #40 + A3 author shipped the loader/writer/test-suite/docs but no `0 10 * * *` cron line. Reader silently degraded; daemon kept running; nobody noticed for ≥10 days.
- **WARNING-tier observability theater** (F33): the daemon emits a WARNING on every reload cycle (15 min × 24 h × N days). The signal is technically logged but is observability *theater* — no operator reads 640 identical warnings/day. The correct level is ERROR + notification + debounce. WARNING is the "I made noise but did nothing" tier; reserve for genuinely intermittent conditions.
- **Two-question operator briefs require two-tracked investigation** (this run): operator stated two coupled observations (oracle + Shenzhen). Premature coupling — assuming both stem from one root cause — would have produced a wrong unified narrative. They are independent (F32 vs F34); coupling them yields a false story. Antibody: when an operator brief contains "AND" or "I see", split into N independent questions and falsify the coupling explicitly before allowing any unification.

**Methodology changes triggered**:
- Added probe #19 to LEARNINGS high-signal: **shipped-without-schedule detector** — for every module whose docstring contains the phrase "ONLY writer" / "single writer" / "canonical writer", grep `/Users/leofitz/.openclaw/cron/jobs.json` + `crontab -l` for the script basename. If missing AND the artifact file the module produces is also missing on disk, raise SEV-1 immediately.
- Added probe #20 to LEARNINGS high-signal: **WARNING-tier debouncer audit** — for every `logger.warning(...)` in a 15-min-cadence reload path, check whether a counterpart ERROR escalation exists after a temporal threshold. If WARNING repeats > 100 times with no escalation, raise SEV-2.
- **NEW Cat-O (operator-brief-coupling-falsification)** introduced at MEDIUM. First case: this run, where the operator stated oracle + Shenzhen as one observation; investigation falsified the coupling. Promote to HIGH if a 2nd run produces a false-coupling rescue.
- **Cat-K stays PERMANENT HIGH** (5 consecutive runs producing SEV-1/SEV-0: Run #4 F14, Run #5 F16/F17, Run #7 F27, Run #8 F29, Run #9 F32). The pattern is structural and operator-stable.

**Hand-edits beyond Closeout**: none.

**Token-economy validation**: targeted investigation with 5 sentinel-delimited terminal commands produced enough material for the full deliverable in well under context budget. Resolution-sweep pattern from Run #8 worked for a *forensic* run too; promoting to default shape for any non-discovery run.

---

## Run #10 — 2026-05-17 — Silent-gap archeology + F32-class sibling sweep

**Operator trigger (verbatim, translated)**: "Continue Run #10 — investigate other 'designed but never wired' silent gaps (F32-class). Also find where these missing data live — I CONFIRM they used to exist and were being generated correctly."

**Deliverable**: `docs/operations/task_2026-05-16_post_pr126_audit/RUN_10_silent_gap_archeology.md`

**Findings**: F35 (TIER-1, Cat-K), F36 (TIER-2 pre-flag, Cat-J), F37 (TIER-2, Cat-K), F38 (TIER-1, Cat-J INVESTIGATE-FURTHER), F39 (TIER-3, Cat-N).

**Operator-memory vindication**: confirmed correct. Settlement data DID exist and WAS generated correctly through 2026-05-07 (3987 VERIFIED rows preserved in `settlements_v2_archived_2026_05_11`). The 2-layer F32 (unscheduled bridge + empty live source) means even an out-of-band bridge run today produces `{}`.

**Methodology changes triggered**:
- **NEW probe #21 (LEARNINGS)**: argparse-absent-CLI antibody — never speculate-invoke a writer script with `--help`. Use `sed -n '1,50p'` to read docstring/argv parsing first. Triggered by accidental write in §2.5 of this run.
- **NEW probe #22 (LEARNINGS)**: stale-feed sweep — for every `state/*.json` with mtime > 168 h (1 week), check whether stale-by-design or F32-class. Triggered by the mtime audit table.
- **Cat-K extends streak**: 6 consecutive runs producing SEV-1/SEV-0 (Run #4 F14, #5 F16/F17, #7 F27, #8 F29, #9 F32, #10 F35/F38). Cat-K is now structurally permanent.
- **NEW Cat-N case-file**: F39 — loaded launchd plist contradicts its own header comment. Cat-N is "doc lies vs reality". First Run-10 case; promote to MEDIUM if a 2nd case appears.

**Hand-edits beyond Closeout**: accidental real-run of `bridge_oracle_to_calibration.py` (disclosed §2.5); cleaned with `rm -fv data/oracle_error_rates.json data/oracle_error_rates.heartbeat.json && rmdir data`. Repo verified clean post-cleanup.

**Token-economy**: 4 sentinel-terminal probes (self-claim grep + scheduler-universe enum + state-mtime + settlements-archive inspection). One read_file for tracker-tail format-matching. Full deliverable in well under context budget. Confirmed: forensic-archeology shape is well-suited to sentinel-terminal pattern.

---

## Run #11 — 2026-05-17 — F36 root cause + Q1/Q2/Q3 + F40/F41/F42

**Operator trigger (verbatim)**: "drive F36 to a definitive root cause + fix, answer Q1 (archive intent), Q2 (any VERIFIED rows since 5/7?), Q3 (plist intentional?); opportunistically catch any other F32-class silent gaps. READ-ONLY production."

**Deliverable**: `docs/operations/task_2026-05-16_post_pr126_audit/RUN_11_f36_rootcause_and_q1q2q3.md`

**Findings**: F36/F38 RETRACTED (DEFECT-INVALID-PROVENANCE); F40 (TIER-1, Cat-J+K), F41 (TIER-1, Cat-J), F42 (TIER-1 META, Cat-K+J).

**Run #10 error class**: Cat-J (data-provenance error). Run #10 queried `zeus-world.db` and `zeus_trades.db` for settlements without first tracing the post-PR-#114 K1 split (which moved live tables to `zeus-forecasts.db`). The empty-tables observation was correct for the DBs queried but the WRONG DBs were queried.

**Real regressions surfaced (F40/F41)**: PR #114 migrated WRITERS to `get_forecasts_connection()` but did NOT sweep ~30 reader callers using `get_world_connection()` that reference the 7 forecast-class tables. Bridge_oracle_to_calibration.py and evaluate_calibration_transfer_oos.py are the first two confirmed cases (the latter has a live log regression — "target domains: [...]" → "target domains: []" — aligned exactly with the 2026-05-11 migration timestamp).

**Methodology changes triggered**:
- **NEW probe #23 (LEARNINGS)**: post-DB-split provenance antibody — when any finding asserts "table X is empty / writer Y is dormant", FIRST `grep -n "get_.*_connection" <writer.py>` to identify the writer's actual target DB, then probe THAT DB. Symptomatic shortcut: `git log --since=30days -- scripts/migrate_*.py` to catch recent migrations.
- **NEW probe #24 (LEARNINGS)**: schema-first column verification — before writing `WHERE` clauses with assumed timestamp columns, `sqlite3 <db> ".schema <table>"` to enumerate actual columns. Run #10 used `verified_at` (does not exist; correct column is `authority`).
- **Cat-J upgrades to PERMANENT HIGH** after Run #11 exposed Run #10's data-provenance class error. 6 consecutive runs have produced Cat-J SEV-1 findings (#5 F17, #6 F22, #7 F25, #9 F32, #10 F36-mistake, #11 F40/F41).
- **NEW Cat-O case-file 2 (operator-brief-coupling-falsification reinforcement)**: this run *itself* falsifies Run #10's coupling between "harvester dormant" and "post-archive migration". Strengthens Run #9's Cat-O. Promote to HIGH.

**Hand-edits beyond Closeout**: none (read-only run preserved).

**Token-economy**: 3 sentinel-terminal probes (settlements-DB enumeration, calibration_pairs/bridge/plist probe, K1-callers + commit probe) + 1 read_file for finding/audit/learning format. Full deliverable in well under context budget. Pattern: forensic-correction runs respond well to "verify-then-broaden" — first probe re-checks the prior run's claim against the right DB, second probe enumerates downstream blast radius.

**LEARNING #19 (writer-claim grep) updated**: pair with **reader-claim grep** as Run #10 promised; also pair with **DB-target verification** — for any module whose docstring claims a target table, confirm the connection helper actually routes there post any recent migration. This run is the proof.
