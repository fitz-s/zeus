# critic-alice — Phase 5B Onboarding

**Written**: 2026-04-17, post Phase 5A commit `977d9ae`. Role: persistent adversarial critic, `zeus-dual-track` team. Reports to team-lead.

## Authority chain re-loaded

- `~/.claude/agent-team-methodology.md` — full, incl. new §"Critic role — critique the TASK, not the TEAMMATE" (2026-04-17 addition).
- Root `AGENTS.md` incl. §"Function Naming and Reuse Freshness" + `architecture/naming_conventions.yaml`.
- `docs/authority/zeus_dual_track_architecture.md` §2/§5/§6/§8.
- `team_lead_handoff.md` post-5A (Zero-Data Golden Window still active; paper mode retired system-wide).
- `zeus_dt_coordination_handoff.md` — B069/B073/B077 CLOSED at 5A; B078 rides 5B commit; B093 bifurcated.
- DT v2 package @ `zeus_dual_track_refactor_package_v2_2026-04-16/`, especially `04_CODE_SNIPPETS/ingest_snapshot_contract.py:22-66` (3 quarantine laws) + `08_TIGGE_DUAL_TRACK_INTEGRATION_zh.md §3/§5` (JSON shape + `training_allowed` formula) + `04_CODE_SNIPPETS/rebuild_calibration_pairs_v2.py` (METRIC_SPECS pattern, NOT `--track` flag).
- Phase 4.5/5A critic products for precedent (STALE_REWRITE on 51-source common, fixture-bypass trap, Kelvin silent-default, authority inversion at sidecar write).

## L0-L5 checklist + Phase 5B additions

- **L0.0 — Peer, not suspect (NEW).** Critique the CODE/TEST/SEAM/INVARIANT. Default hypothesis when disk contradicts a report: concurrent-write → memory-lag → shell artifact → benign mistake → discipline breach (LAST, triple-verified + team-lead concurrence). Language: "the diff shows", "the disk reveals". Fresh bash grep before any "grep reveals X" claim; paste raw output as evidence.
- **L0** authority re-loaded post-session boundary.
- **L1** INV-## / FM-## per scoped AGENTS in `scripts/`, `src/state/`, `src/engine/`.
- **L2** Forbidden Moves: Kelvin silent-default, fixture-bypass, orphan helper, paper-mode anything (retired), polarity swap on MIN boundary.
- **L3** NC-## / silent fallback / unit assumption (mn2t6 is Kelvin at GRIB level — converter is load-bearing).
- **L4** Source authority at seams: extractor emits `temperature_metric='low'`, `members_unit` explicit, `causality_status` first-class, `training_allowed` computed not defaulted.
- **L5** Phase boundary: no Phase 6/7/9 leak; Phase 5A truth-authority seam not regressed (exec-emma's 5B B078 edit is narrow addition, not re-wire).
- **Phase 5B additions**:
  - **3-quarantine-law gating**: R-AF must fail when any of `boundary_ambiguous`, non-`OK` causality, or missing `members_unit` lands on training path.
  - **MIN boundary semantics** — NOT a polarity swap of MAX. Boundary can steal the minimum via cross-midnight leakage. `classify_boundary_low` must be written to spec, not copy-pasted from `classify_boundary_high` with `>` flipped to `<`.
  - **METRIC_SPECS pattern** for rebuild/refit — no `--track` CLI flag (package authority). Check 5 hardcoded `'high'` sites are genuinely replaced, not shadowed.
  - **B078 absorption** — `LEGACY_STATE_FILES` low-lane entries land IN the 5B commit with `temperature_metric` + `data_version` metadata requirements fail-closed on absence.
  - **Golden-window enforcement** — no v2 DB writes; smoke ≤1 GRIB → `/tmp/`; file-provenance headers on all new files.
  - **Legacy audit on any 51-source reuse** — Phase 4.5 `tigge_local_calendar_day_common.py` STALE_REWRITE verdict stands; any vendored helper must be audited fresh.
- **WIDE** prompt intact: "what's off-checklist?"

## Top-3 antipatterns I'll hunt in 5B

1. **MIN polarity swap without rethinking boundary semantics** — MAX boundary logic inverted with `min()` and sign flips, missing the cross-midnight leakage case. The new file is ~700 LOC; easy place for copy-paste drift. I'll diff boundary classification against Phase 0 spec line-by-line.
2. **Fixture-bypass on R-AF..R-AM** — testeng-grace's R-tests must exercise the REAL `validate_snapshot_contract` / `extractor.main` / `ingest_grib_to_snapshots.main` entry points, not raw dict construction that skips the quarantine laws. Phase 4 precedent: 4B MAJOR-1 + 4C+4D MAJOR-2.
3. **Orphan helper / parallel source** — a helper vendored from 51-source `tigge_local_calendar_day_common.py` would be STALE_REWRITE per Phase 4.5 audit. Grep for duplicate function bodies across `extract_tigge_mx2t6_localday_max.py` + `extract_tigge_mn2t6_localday_min.py`; demand shared module or justification.

## Scope question for team-lead

Scanner (`scripts/scan_tigge_mn2t6_localday_coverage.py`) deferral to 5B-follow-up: concur, but one follow-up — does the 5B commit include a test that validates "no coverage scanner was silently used to bypass quarantine during ingest smoke"? If exec-dan's ingest unblock at `ingest_grib_to_snapshots.py:253` relies on the scanner at any seam, the deferral creates a runtime NotImplementedError re-trip risk. Otherwise deferral is clean.

---
Standing by for 5B RED tests from testeng-grace.
