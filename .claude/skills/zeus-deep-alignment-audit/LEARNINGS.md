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
