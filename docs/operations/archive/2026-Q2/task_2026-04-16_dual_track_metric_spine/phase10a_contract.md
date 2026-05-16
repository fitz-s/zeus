# Phase 10A Contract v2 — Independent Hygiene Fix Pack

**Written**: 2026-04-19 post e2e audit (`630a1e6`), Golden Window explicitly confirmed by user.
**Revised**: 2026-04-19 post scout landing-zone scan + critic-dave cycle-2 precommit review — **v1 had 3 CRITICAL contract errors flagged; this v2 absorbs them**.
**Branch**: `data-improve`.
**Mode**: Gen-Verifier. critic-dave cycle 2 (continuation after P9C cycle-1 ITERATE → PASS).
**User ruling 2026-04-19**:
1. Proceed with Commit #1 (Bucket A) + Commit #2 (Bucket B).
2. No backfill, no training — TIGGE data not yet in v2; cloud VM (`tigge-runner`) in-flight; Golden Window explicitly refactor-safe.
3. Team shape: team-lead (main) + long-lived critic-dave + ephemeral scout/executor/testeng subagents.

## v1 → v2 delta (absorbed findings)

critic-dave cycle-2 precommit review + scout landing-zone scan produced 4 contract-level corrections:

| Finding | v1 state | v2 correction |
|---|---|---|
| S5 B009 ALREADY LANDED at `68cbacc` + `389247b` | Listed as open | Removed from code scope; moved to doc-flip |
| S6 B015 ALREADY LANDED at `dd59c88` | Listed as open | Removed from code scope; moved to doc-flip |
| S7 B079 ALREADY LANDED at `cf9c148` + `b4d140f` | Listed as open | Removed (log→raise upgrade is marginal; not a gap) |
| S3 line citation STALE (L3643-3722) | Said "token_suppression here" | Re-pointed to L3308 `record_token_suppression` — actual location |
| S4 line citation STALE (L1676-1750) | Said "decision emission" | Re-pointed to L1271-1286 (real fabrication site, under B091 comment block) |
| S4 parallel-vocab SD-G risk | Proposed new `time_field_status` enum | Reframed: **extend existing `decision_time_status` from P9C replay** — reuse vocab, don't create parallel |
| S1 L614 except narrowing (ops-behavior shift) | Included in scope | Removed — just rename; except narrowing = separate hygiene, deferred |
| S2 probe result | "Run probe" | **Scout confirmed PASS**: `extract_tigge_mn2t6_localday_min.py` already stamps `temperature_metric='low'` literal at L20/L101/L356 with validation at L411. S2 converts to antibody-only (lock the contract, no code change) |

**Net effect**: Bucket A shrinks from 8 items to 4 code items + 1 doc-flip. Doc-flip expands from 11 bugs → 14 bugs (adds B009/B015/B079/B100).

## Why this phase still exists

Two drivers (unchanged from v1):

1. **R1 CRITICAL production bug** — `monitor_refresh.py:355,405` NameError silently swallowed at L614 → every Day0-active position's refresh silently fails → stale probability enters exit decision. HIGH+LOW equally affected. Must fix regardless of Golden Window status.

2. **Audit tracker lag** — 14 bugs have closed in code without doc update. Single atomic flip restores doc authority.

Plus:
3. **B071 token_suppression still open** (sibling of B070) — mutable upsert at L3308; 3-state sequence `(auto→manual→auto)` collapses to 1 row, post-mortem impossible.
4. **B091 lower half still open** — evaluator fabricates `datetime.now()` + emits WARNING at L1271-1286, but no structured status field. P9C's replay already landed `decision_time_status` (`OK` / `SYNTHETIC_MIDDAY`). Extending that same vocabulary into evaluator closes SD-G debt from Phase 1.

## Scope — ONE commit delivers (atomic)

### S1 — R1 CRITICAL: monitor_refresh NameError rename

`src/engine/monitor_refresh.py`:
- L355: `remaining_member_maxes` → `extrema.maxes`
- L405: `remaining_member_maxes` → `extrema.maxes`
- **NO changes to L614** broad `except Exception`. Narrowing is a Phase 10B hygiene item; v1 overreached scope. The rename alone eliminates the NameError; the antibody catches the regression category.

**Dataclass invariant confirmed** (scout): `RemainingMemberExtrema.__post_init__` enforces exactly one of `maxes`/`mins` is populated (HIGH → `maxes=arr`, LOW → `mins=arr`). Both attributes are always safe to access post-construction. S1 fix is literally 2 lines.

### S2 — R2 antibody-only: ingest metric stamp lock

**Scout confirmed probe PASS** (2026-04-19):
- `scripts/extract_tigge_mn2t6_localday_min.py:20,101,356` all stamp `temperature_metric='low'` as literal or typed field
- L411 has explicit validation guard
- `scripts/ingest_grib_to_snapshots.py:132-138,204-206` ingest function takes `metric: MetricIdentity` param; every INSERT sources `temperature_metric` from `metric.temperature_metric` (not hardcoded)

**Action**: NO code change. Add antibody that locks the contract:
- Mock sqlite connection, call extractor write path, assert every captured INSERT carries `temperature_metric='low'`
- Surgical-revert probe: if anyone changes ingest to stamp `='high'` or omit the field, test fails

### S3 — B071 token_suppression history+view

`src/state/db.py:3308-3388` (`record_token_suppression`):
- Current: `ON CONFLICT(token_id) DO UPDATE SET` — mutable upsert, prior reason/created_at lost
- Fix pattern: mirror B070 (`ebb4f41`):
  - Append-only `token_suppression_history` table (or `_v2` — align with B070 convention; check B070's table name first)
  - Derived `token_suppression_current` view (MAX created_at per token_id)
  - All writes → history INSERT; all reads → view
  - Callers not bypassed (scout to verify caller list)
- Bonus: `(auto-suppress → manual-override → auto-suppress again)` sequence remains reconstructible from history alone

### S4 — B091 lower half: decision_time_status in evaluator (extend P9C vocab)

`src/engine/evaluator.py:1271-1286` (fabrication site under `# B091:` comment):

Current:
```python
if decision_time is not None:
    _recorded_at = decision_time.isoformat()
else:
    _fabricated_now = datetime.now(timezone.utc)
    logger.warning("DECISION_TIME_FABRICATED_AT_SELECTION_FAMILY: ...")
    _recorded_at = _fabricated_now.isoformat()
```

Target — reuse existing `decision_time_status` vocab from P9C (`src/engine/replay.py:345,418`):
- `decision_time_status: Literal["OK", "FABRICATED_SELECTION_FAMILY", "UNAVAILABLE_UPSTREAM"]`
- Emit status alongside `_recorded_at`; downstream consumer (selection_family_facts row write) captures both
- Fabricated path stamps `"FABRICATED_SELECTION_FAMILY"` + keeps current WARNING log
- Degraded-upstream path stamps `"UNAVAILABLE_UPSTREAM"` (if `decision_time is None AND caller signaled upstream unavailable`)
- Normal path stamps `"OK"`

**SD-G consistency**: no new vocabulary; extends the 3-state enum P9C already set. If schema for `selection_family_facts` doesn't carry a status column yet, add it + migration guard (additive column, no DDL on v2).

### S5 — Doc flip: audit ground-truth refresh (expanded from v1 S8)

Status-only, NO code. Cover **14 bug rows**:

| Bug | Status | Commit / evidence |
|---|---|---|
| B009 | RESOLVED | `68cbacc` + `389247b` |
| B015 | RESOLVED | `dd59c88` |
| B050 | RESOLVED | `057979c` |
| B063 | RESOLVED | `94cc1f9` |
| B069 | RESOLVED | Phase 5A `977d9ae` |
| B070 | RESOLVED | `ebb4f41` |
| B071 | RESOLVED | **this commit** |
| B073 | RESOLVED | Phase 5A `977d9ae` |
| B077 | RESOLVED | Phase 5A `977d9ae` |
| B078 | RESOLVED | Phase 5B `c327872` |
| B079 | RESOLVED | `cf9c148` + `b4d140f` |
| B091 (both halves) | RESOLVED | upper `177ae8b` + lower **this commit** |
| B093 | RESOLVED | Phase 5C `821959e` + `59e271c` |
| B100 | RESOLVED | `src/state/db.py:889-1008` SAVEPOINT migration pattern (row-count check + IntegrityError on mismatch + DROP only on legacy rename) |
| B055 | OPEN_DT_BLOCKED | DT#6 architect packet |
| B099 | OPEN_DT_BLOCKED | DT#1 architect packet |

Files:
- `docs/to-do-list/zeus_bug100_reassessment_table.csv` — flip rows
- `docs/to-do-list/zeus_data_improve_bug_audit_100_resolved.md` — append Phase 5/9/10A resolutions block
- `docs/to-do-list/zeus_dt_coordination_handoff.md` — mark Section A fully closed (only C architect-gated remains)

## Hard constraints (preserve — unchanged from v1)

- **No TIGGE import** / **no v2 table writes** / **no SQL DDL on v2**
- **No changes to evaluator DT#7 gate** (P9C, locked)
- **No changes to `monitor_refresh` LOW plumbing** — S1 fix is pure rename
- **No `_TRUTH_AUTHORITY_MAP` changes** — B4/R13 deferred
- **No `kelly_size` signature changes** — R10 deferred to P10B
- **No `Position.temperature_metric` type upgrade** — R5 deferred to P10B
- **No `except Exception` narrowing anywhere** — separate hygiene, deferred
- Golden Window intact

## Acceptance

**Regression budget**: 144 failed / 1873 passed / 93 skipped baseline.
- Delta passed ≥ count of new antibodies added
- Delta failed = 0 (zero new failures)
- Skipped unchanged or decrease

**R-letter namespace**: R-CH onwards (R-CG.3 last used in P9C ITERATE).

**Antibodies required (minimum)**:

| ID | Target | Kind | Surgical-revert probe |
|---|---|---|---|
| R-CH.1 | Day0 refresh uses `extrema.maxes` post-L355 rename (positive) | Monkeypatch Day0 → assert `last_monitor_prob_is_fresh=True` | Revert L355 → test fails with NameError |
| R-CH.2 | Day0 refresh uses `extrema.maxes` post-L405 rename (positive) | Monkeypatch Day0 refresh → assert member_maxes dict key populated | Revert L405 → test fails |
| R-CI.1 | extract_mn2t6 ingest stamps `temperature_metric='low'` on every v2 INSERT | Mock conn captures INSERT params | Revert literal → test fails |
| R-CJ.1 | token_suppression: 3-state sequence `(auto→manual→auto)` all in history table | DB integration test | Revert to upsert → test fails |
| R-CJ.2 | token_suppression_current view returns latest row only | View read assertion | Drop view → test fails |
| R-CK.1 | evaluator emits `decision_time_status="FABRICATED_SELECTION_FAMILY"` on None path | Positive | Revert status emit → test fails |
| R-CK.2 | evaluator emits `decision_time_status="OK"` on normal path | Pair-negative | Revert status emit → test fails |

**All antibodies must**:
- Fail WITHOUT fix, pass WITH fix (surgical-revert tested by critic-dave in review)
- Live in new `tests/test_phase10a_hygiene.py` (convention per critic-carol L2)
- Use monkeypatch/mock over text-match (carol L9)

## Out-of-scope (explicit deferrals to P10B)

- R3 replay legacy fallback metric-aware WHERE
- R4 oracle_penalty (city, metric) keying
- R5 MetricIdentity at runtime seams (Position/get_calibrator/run_replay)
- R9 FDR family_id metric-aware
- R10 Kelly strict ExecutionPrice
- R11 v2 row-count observability sensor
- B067 hardcoded env=live
- B074 JSON fallback unknown_env
- `except Exception` narrowing (monitor_refresh + evaluator + 10+ sites)
- B079 log→raise upgrade (already landed as log; raise is a semantic strengthening, not a gap)

## Out-of-scope (explicit deferrals, no phase assigned)

- B055 (DT#6 architect packet)
- B099 (DT#1 architect packet)
- R6 Gate C resolution (user ruling — team lead recommends Option A doc-only under Golden Window)
- R7 Golden Window lift timing (user ruling)
- R8 title amendment
- R12 H7 144-failure triage
- R13 `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` semantic re-decision

## Critic-dave cycle-2 adversarial prompts (for wide review post-implementation)

1. **Two-seam probe**: for S3 (history+view), is ANY caller of `record_token_suppression` bypassed? Does the view read path satisfy every existing reader? Surgical-revert: drop the view → do reads fail or silently degrade?

2. **Silent-sink probe**: does S4 extension of `decision_time_status` actually get written somewhere readable? Just adding the emit without a schema column or payload key = checkbox antibody (carol L3). The status must land in `selection_family_facts` row OR equivalent persisted surface.

3. **Doc-flip evidence probe**: each of the 14 bug-flip rows in S5 cites a commit SHA. For 3 random rows, does the cited commit actually contain the claimed fix? Stale subagent memory risk.

4. **Phase-scope creep probe**: does any code in S1-S4 bleed into deferred items (e.g., S4 fix accidentally renames a LOW-related symbol that should wait for P10B)?

## Sequencing (revised)

1. team-lead rewrites contract v2 ← this file
2. **SendMessage critic-dave** v2 contract path + ack his 3 findings → await ack
3. team-lead implements S1 + S2 **direct** (2 LOC + ~40 LOC antibody — below subagent threshold)
4. Dispatch executor subagent for S3 + S4 (real code: ~80 + ~50 LOC)
5. team-lead implements S5 doc flip direct (markdown/CSV only)
6. Regression run + disk verify (`git diff --stat`)
7. SendMessage critic-dave → wide review
8. ITERATE fix or PASS → commit Phase 10A
9. Open Phase 10B contract

## Coordination notes (unchanged)

- `docs/README.md` + `docs/runbooks/AGENTS.md` + `docs/runbooks/task_2026-04-19_tigge_cloud_download_zeus_wiring.md` — orthogonal TIGGE runbook; don't touch, don't revert
- Pre-stage: `git diff --stat <file>` before every add (P5 antibody)
