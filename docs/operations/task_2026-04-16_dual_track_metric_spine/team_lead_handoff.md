# Team-Lead Handoff (post-compact 2026-04-17)

**Written**: 2026-04-17 17:30+ local, post Phase 5A commit, pre-compact.
**Prior versions archived in git history.** This file supersedes all earlier handoffs.

## IMMEDIATE NEXT ACTIONS (post-compact, in order)

1. Read `~/.claude/agent-team-methodology.md` — your operating manual.
2. Read `~/.claude/CLAUDE.md` § "Code Provenance: Legacy Is Untrusted Until Audited".
3. Read THIS file IN FULL.
4. Read `docs/authority/zeus_dual_track_architecture.md` (§2/§5/§6/§8 minimum).
5. `git log --oneline -10` to confirm state.
6. If team `zeus-dual-track` still exists (check `~/.claude/teams/`), resume with SendMessage re-intros. Otherwise re-create per template in § "Team Bootstrap".

## Branch + commit state

Branch: `data-improve`
Last relevant commits:

```
977d9ae Phase 5A: truth-authority spine + MetricIdentity view layer  ← NEEDS PUSH
94cc1f9 fix(B063): rescue_events_v2 audit table with provenance authority
177ae8b fix(B091): forward decision_time to evaluator + explicit fabrication warnings
ef09dc3 docs(handoff): DT coordination handoff for 12 truly-RED bugs (Phase-5 split)
7732701 Merge remote-tracking branch 'origin/data-improve'  ← earlier merge
3b82dd5 Phase 4.5: GRIB→JSON extractor + R-L tightening + legacy-audit protocol
cf85ca6 Phase 4.6: R-AA cities cross-validate — anchor on config/cities.json
```

Remote (`origin/data-improve` at push time): `94cc1f9`. `977d9ae` is local-only; **push if user approves**.

## Gate status (updated post-5B)

- **Gate A, B, C** open (Phases 2-4 closed).
- **Phase 5A** committed at `977d9ae` — truth authority + MetricIdentity view layer seam.
- **Phase 5B** committed — low historical lane: extractor + ingest contract gate + rebuild/refit metric-aware + B078 absorbed. 41/41 R-AF..R-AO GREEN; full regression flat at 117 failed (baseline). critic-alice PASS verdict at `phase5_evidence/critic_alice_5B_verdict.md`.
- **Phase 5C** next — replay MetricIdentity half-1 + Gate D (low-purity parity) + B093 half-1.
- **Phase 6-9** future (Day0 split, shadow, limited activation).

## R-letter namespace update (5B lock)

- R-AF..R-AO locked at 5B commit:
  - R-AF (6): `validate_snapshot_contract` 3-law gating
  - R-AG (5): extractor 5-function public API
  - R-AH (2): Kelvin `members_unit` explicit
  - R-AI (3): `data_version` exact + cross-metric rejection
  - R-AJ (2): causality first-class
  - R-AK (6): `CalibrationMetricSpec` + `METRIC_SPECS` 2-tuple
  - R-AL (3): `iter_training_snapshots` metric isolation
  - R-AM (3): ingest unblock + R-AM.4 scanner-isolation antibody
  - R-AN (6): B078 low-lane truth metadata registry
  - R-AO (5): `refit_platt_v2` low-metric isolation
- R-AP reserved for 5B-follow-up (extractor behavioral coverage; see backlog).

## 5B-follow-up backlog (post-commit, fresh-team-owned)

1. **R-AP** (testeng-grace's replacement): behavioral tests for `classify_boundary_low` — 3 synthetic cases (cross-midnight steal, safe boundary, inner-None-only). Currently R-AG only asserts importability; polarity-swap footgun still live.
2. **`scripts/_tigge_common.py` extraction**: duplicated utilities (`compute_manifest_hash`, `_now_utc_iso`, `_city_slug`, `_overlap_seconds`) across mx2t6 + mn2t6 extractors. Drift-warning audit from critic.
3. **Dead-code audit for `_extract_causality_status`** in `ingest_grib_to_snapshots.py` — unreachable post-5B contract wiring; safe to delete.
4. **`scripts/scan_tigge_mn2t6_localday_coverage.py`** (deferred from 5B main scope): diagnostic scanner per remediation §8 (per-city quarantine rate alarm >20%). Not gating, diagnostic-only; scanner-isolation antibody R-AM.4 already installed.

## Team retirement protocol (post-5B, standing directive)

Per user directive (2026-04-17 pre-compact): team `zeus-dual-track` is **5B-only**. After commit:
1. Each teammate writes `phase5b_to_phase5c_<name>_learnings.md` (500-800 words).
2. Team-lead compact.
3. Fresh team spawn for Phase 5C (new names; briefs carry methodology §"Critic role" as background but not as corrective patches).

## Zero-Data Golden Window (STANDING until user lifts)

Still active:
- v2 tables zero rows (ensemble_snapshots_v2, calibration_pairs_v2, platt_models_v2).
- TIGGE GRIB archive still downloading.
- **No real ingest into any v2 table until user lifts**.
- **Smoke tests = unit first, then ≤1 GRIB file for structural validation**. Output to `/tmp/`, never committed.
- **No full-batch extraction runs**. 420+ file runs require user approval + download complete + critic PASS.
- **Structural fixes are free now**. Bias every decision toward "fix structurally now, ingest later".

## Key philosophical lesson from 2026-04-17 (encode, don't lose)

### Critic critiques the TASK and the TARGET, not the TEAMMATE

Tonight's cascading problems came from critic reaching for "teammate violation / discipline breach" as the default hypothesis whenever disk state disagreed with a report. The actual cause was almost always:
1. **Concurrent writes** flipping disk state between an agent's edit and critic's grep.
2. **Compound grep patterns** (`"A\|B"`) failing shell escape.
3. **Stale context** on the critic's side being presented as fresh.

All of these are technical/timing artifacts — not teammate dishonesty.

**Standing rule for next critic onboarding**:
- Critic's job is to critique the CODE, the TEST COVERAGE, the STRUCTURAL SEAMS — not the teammate.
- When disk disagrees with a teammate report, default hypothesis ordering:
  1. Concurrent dispatch timing (another teammate's write between your reads).
  2. Memory/report-state lag on the agent's side (pre-compact context, stale snapshot).
  3. Genuine mistake (benign).
  4. **Discipline breach — last resort, requires triple-verification**.
- "Grep reveals X at line Y" is a disk-verifiable claim. If you claim it, run a FRESH bash grep right before you write the claim. Paste the bash output in your verdict as evidence.
- Teammates are peers, not suspects. Language: "the diff shows", "the disk reveals", "the test needs" — NOT "exec-emma lied" or "exec-emma silently reverted".

**Standing rule for team-lead**:
- If critic files a "discipline breach" finding, team-lead independently disk-verifies BEFORE escalating to the executor. Probably caught me ~3 times tonight as I echoed critic's framing at exec-emma before verifying.
- When an instruction to an executor turns out to be scope creep by team-lead (e.g. tonight's `paper` mode addition), acknowledge AT ONCE. Don't let the executor carry the blame.

This rule should be encoded in the next critic's brief verbatim.

## Team state at Phase 5A close

| Name | Role | Model | Status | Action post-compact |
|---|---|---|---|---|
| critic-alice | opus critic | opus | active, 0 compacts | RETAIN. Add peer-not-suspect rule to her L0. |
| scout-finn | sonnet scout | sonnet | active, idle | RETAIN. Phase 5B inventory next. |
| testeng-grace | sonnet testeng | sonnet | active, idle | RETAIN. R-letters for 5B next. |
| exec-emma | sonnet executor, Phase 5 owner | sonnet | active, idle | **RETAIN** (probation withdrawn after recalibration). |
| exec-dan | sonnet executor, 5B lead | sonnet | active, idle (was standby) | RETAIN. Activate for 5B `extract_tigge_mn2t6_localday_min.py`. |

No replacements needed. No retires. Team stays.

## R-letter namespace ledger

- R-A..R-P: Phases 1-4 (locked).
- R-Q..R-U: Phase 4.5 (locked).
- R-AA: Phase 4.6 cities cross-validate (locked).
- R-AB/R-AC/R-AD/R-AE: Phase 5A (locked at `977d9ae`).
  - R-AB: PortfolioState.authority field + 3 exit paths.
  - R-AC: ModeMismatchError + mode threading.
  - R-AD: MetricIdentity view layer (row + top-level emission).
  - R-AE: canonical writer authority stamping (MAJOR-4 regression).
- R-AF onward: Phase 5B low extractor R-invariants (to be drafted).

Full ruling + namespace policy: `phase4_evidence/r_letter_namespace_ruling.md`.

## Phase 5B opening brief (for exec-dan)

### Scope
- New file: `scripts/extract_tigge_mn2t6_localday_min.py` (~500-700 LOC).
  - Mirror Phase 4.5 mx2t6 structural pattern, but with MIN aggregation semantics.
  - **NOT a polarity swap**: boundary is **semantically different for MIN** (boundary can steal the minimum via cross-midnight leakage, not just be "close to edge"). Write `classify_boundary_low` from Phase 0 spec.
  - `causality_status="N/A_CAUSAL_DAY_ALREADY_STARTED"` emitted first-class for positive-offset cities where day0 local-midnight < issue_utc.
  - 5 exported functions testeng-grace anchors R-letters against (signatures per exec-dan's pre-alignment sketch in his earlier a2a).
- Unblock `scripts/ingest_grib_to_snapshots.py:253` `NotImplementedError` for low track.
- `scripts/rebuild_calibration_pairs_v2.py` + `scripts/refit_platt_v2.py`: add `--track` arg.
- `LEGACY_STATE_FILES` + `build_truth_metadata` low-lane extension (B078 absorption).
- File-provenance headers on all new files (canonical format per `architecture/naming_conventions.yaml`).

### Do NOT
- Vendor from 51-source common module (critic's Phase 4.5 audit STALE_REWRITE verdict stands).
- Touch truth authority seam (5A work; don't re-edit).
- Add paper-mode anything (retired; antibody msg protects).
- Run any real batch extraction (zero-data window; smoke ≤1 GRIB).

### Section B absorption for 5B
- **B078** lands IN the 5B commit (LEGACY_STATE_FILES low-lane entries + `temperature_metric`/`data_version` metadata requirements).

### Critic posture for 5B onboarding (verbatim for her)
> "You are critic-alice. Your job is to critique CODE QUALITY, TEST COVERAGE, STRUCTURAL SEAMS. Teammates are peers. When disk disagrees with a report, default hypothesis is concurrent timing or memory-lag, not teammate dishonesty. Fresh bash grep is the evidence currency. Discipline findings require triple-verification + team-lead concurrence."

## Phase 5C brief (after 5B commits)

### Scope
- `src/engine/replay.py::_forecast_reference_for` — sentinel→typed-status fields (B093 half-1):
  - `decision_reference_source: Literal["historical_decision","forecasts_table_synthetic"]`
  - `decision_time_status: Literal["OK","SYNTHETIC_MIDDAY","UNAVAILABLE"]`
  - `agreement: Literal["AGREE","DISAGREE","UNKNOWN"]`
- Add code comment referencing Phase 7 for half-2 (table migration to `historical_forecasts_v2`).
- Gate D test: `tests/test_phase5_gate_d_low_purity.py` — asserts high and low Platt models do not share buckets; asserts no cross-metric leakage in `calibration_pairs_v2`.

### Do NOT (5C specifically)
- Migrate replay's query source to `historical_forecasts_v2` — that's Phase 7 (requires v2 populated after full batch extraction).

## Pending items / cleanups for later sub-phases

- **5B MINOR-1**: delete unused `_RUNTIME_MODES` constant in `src/config.py:42` (now that paper is retired, the constant is dead).
- **5B MINOR-2**: `src/state/db.py` view result `str(row["temperature_metric"] or "high")` — the `or "high"` is defensive dead code (column is NOT NULL via CHECK). Remove.

## Legacy-code-untrusted-until-audited rule (standing, global)

Per `~/.claude/CLAUDE.md § "Code Provenance: Legacy Is Untrusted Until Audited"` + Zeus `AGENTS.md § "Function Naming and Reuse Freshness"`. Enforcement:
- Every new/touched `scripts/*.py` + `tests/test_*.py` requires `# Lifecycle: created=YYYY-MM-DD; last_reviewed=YYYY-MM-DD; last_reused=YYYY-MM-DD|never` header + `Purpose:` + `Reuse:`.
- Machine check: `python scripts/topology_doctor.py --freshness-metadata --changed-files <files>`.
- Canonical source: `architecture/naming_conventions.yaml`.

## OMC session-end hook state

`~/.claude/plugins/marketplaces/omc/scripts/session-end.mjs` patched 2026-04-17 to early-return by default (preserves native teams across session boundaries). Env var `OMC_ENABLE_SESSION_END=1` restores original behavior. If `omc update` runs, re-apply the patch. Noted in `~/.claude/CLAUDE.md`.

## Worktree state

- Main tree (team-lead): `/Users/leofitz/.openclaw/workspace-venus/zeus` on `data-improve`.
- Debug agent tree (isolated): `/Users/leofitz/.openclaw/workspace-venus/zeus-debug` on `data-improve-debug`. Peer path, not nested.
- `.claude/worktrees/data-rebuild` nested worktree — user migrated debug agent out to peer path; nested one had activity during Phase 5A but per subagent inspection was safe to leave pending removal.

5 `/tmp/zeus_*` worktrees + 1 stale entry were cleaned in `docs/operations/.../phase4_evidence/` sub-cleanup run.

## Known forward risks

- **MINOR cleanups listed above** — land in 5B or 5C as convenience.
- **Replay table migration (B093 half-2)** deferred Phase 7. Phase 7 requires v2 populated by actual batch extraction (not yet authorized).
- **`temperature_metric` CHECK in position_current** currently allows `'high'` DEFAULT; once Phase 5B starts writing `'low'` rows from low-track runtime, verify no caller silently accepts the DEFAULT.

## Phase roadmap post-compact

```
Phase 5B — low extractor + ingest unblock + rebuild/refit --track (B078 absorbed)
Phase 5C — replay half-1 + Gate D (B093 half-1 absorbed)
Phase 6 — Day0HighSignal / Day0LowNowcastSignal split + DT#6 graceful-degradation (B055 absorbed)
Phase 7 — metric-aware rebuild full cutover; replay migrates to historical_forecasts_v2 (B093 half-2)
Phase 8 — low-lane shadow mode
Phase 9 — low-lane limited activation (Gate F); risk-critical DT#2/DT#5/DT#7 land here
```

## Do NOT (standing list for post-compact main-thread)

- Trust any critic "discipline breach" finding without independent disk-verify.
- Treat concurrent-write timing artifacts as scope-creep violations.
- Let paper mode back into `src/config.py` (retired; Zeus is live-only).
- Push `977d9ae` without user confirmation (still unpushed as of this handoff).
- Spawn new agents when existing ones are idle; resume the team.
- Skip `architecture/naming_conventions.yaml` header format on new scripts/tests.
- Do full-batch extraction without user approval + critic PASS.

## Status files on disk

- This file: `docs/operations/task_2026-04-16_dual_track_metric_spine/team_lead_handoff.md`.
- Phase 5 evidence: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase5_evidence/`.
- Coordination handoff: `docs/to-do-list/zeus_dt_coordination_handoff.md` (B069/B073/B077 flagged RESOLVED; B078/B093 open).
- Methodology (global): `~/.claude/agent-team-methodology.md`.
- Global rules: `~/.claude/CLAUDE.md` (Fitz methodology + Code Provenance + OMC session-end patch note).

Phase 5A is a clean milestone. Post-compact continues from here with team intact, paper mode retired, truth-authority seam installed. Harmonious teammate relations restored.
