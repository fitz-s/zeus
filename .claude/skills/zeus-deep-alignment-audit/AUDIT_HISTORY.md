# AUDIT_HISTORY — Zeus Deep Alignment Audit Runs

Append-only log of every completed run of the `zeus-deep-alignment-audit` skill. The skill's `Closeout` step appends one row + one retrospective paragraph per run.

**Never edit past entries.** If a past entry was wrong (back-filled discovery, antibody falsified, etc.), add a new dated note in the entry's retrospective section explaining the update — preserve the original.

---

## Run table

| # | Date | Commit | K root gaps | SEV-1 | SEV-2 | SEV-3 | Coverage | Report |
|---|------|--------|-------------|-------|-------|-------|----------|--------|
| 1 | 2026-05-16 | 556d55be23 (main) / ff714a7507 (skill) | 4 | 3 | 1 | 0 | A, E, F, G, H probed (findings); B, C, D probed (no findings) | docs/operations/task_2026-05-16_deep_alignment_audit/REPORT.md |

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
