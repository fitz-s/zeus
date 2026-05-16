# critic-alice — Phase 5 Onboarding

**Written**: 2026-04-17. Role: persistent adversarial critic for team `zeus-dual-track`, Phase 5 onward (low historical lane birth + truth authority flag + SD-A mode propagation). Reports to `team-lead`.

## Authority chain re-loaded

- `~/.claude/agent-team-methodology.md` (operating manual: phantom-work, compact, wide-critic L0-L5, legacy-audit verdicts).
- Root `AGENTS.md` incl. §"Function Naming and Reuse Freshness" + `architecture/naming_conventions.yaml`.
- `docs/authority/zeus_dual_track_architecture.md` §2/§5/§6/§8 + `zeus_current_architecture.md` §13-§22.
- `team_lead_handoff.md` incl. Zero-Data Golden Window standing rules.
- `zeus_dt_coordination_handoff.md` (Section A/B/C bug split; Section B rides Phase 5 commits).
- `TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md` (low-lane law).
- Prior phase products: `phase4_5_wide_review.md`, `legacy_code_audit_phase4_5.md`, `legacy_code_audit_phase4_5_common.md`.

## L0-L5 checklist (standing) + Phase 5 additions

- **L0** self-verify authority loaded post-compact (re-read handoff + methodology + this file).
- **L1** INV-## + FM-## in touched scoped AGENTS respected (`src/state/AGENTS.md`, `src/engine/AGENTS.md`, `scripts/AGENTS.md` if present).
- **L2** Forbidden Moves: bare Kelvin defaults, fixture bypass, DROP TABLE in migration, silent sentinel strings, string-typed `mode`/`authority`, orphan helpers under `scripts/`.
- **L3** Silent fallback / default / unit assumption violating NC-## (naming_conventions.yaml canonical).
- **L4** Source authority preserved at every seam (provenance: `source`, `authority`, `data_version`, `temperature_metric`, `manifest_hash`, `mode`).
- **L5** Phase boundary: Phase 6 Day0 split / Phase 7 cutover / Phase 9 risk deferred, not leaked; Phase 1-4 contracts not regressed.
- **WIDE** "what else did you see that wasn't on my checklist?" — non-negotiable.
- **Phase 5 additions**:
  - **Section-B absorption check**: verify each 5A/5B/5C diff exposes a clean seam where B069, B073, B077, B078, B093 become writable (test-first), not re-writable. Red-test present before fix.
  - **Low-lane MetricIdentity propagation**: `temperature_metric="low"` threaded through truth metadata, replay filter, ingest unblock, rebuild/refit `--track` flag. Grep every seam; a bare column access without track filter is a MAJOR finding.
  - **Boundary-quarantine for MIN aggregation**: mn2t6 aggregator must preserve `boundary_ambiguous` + `training_allowed=false` flow; NOT auto-inherit from mx2t6 shape.
  - **Golden-window enforcement**: zero real DB writes in 5A/5B/5C. Unit tests first; ≤1 GRIB smoke max, `/tmp/` output, not committed.
  - **Legacy-audit protocol**: any exec-emma reuse from 51-source or existing Zeus → verdict `{CURRENT_REUSABLE | STALE_REWRITE | DEAD_DELETE | QUARANTINED}` written to `legacy_code_audit_phase5.md`.
  - **Citation-prefix**: exec-dan/exec-emma status reports must carry `[AUTHORIZED by: <path>]` prefix; flag if missing.
  - **Disk-first**: every status attaches fresh `git diff --stat` + `grep` evidence. Universal — extends to me.

## Top-3 antipatterns to hunt in 5A/5B/5C

1. **Orphan helper / parallel source** (Phase 4.5 precedent): low extractor recreates utilities already living in `scripts/extract_tigge_mx2t6_localday_max.py` instead of importing a shared module. Result: two drift-prone copies of boundary classification / causality framework / manifest hashing. Gate: grep for duplicate function bodies across the two extractors; demand import-or-justify.
2. **Fixture bypass** (4B MAJOR-1 + 4C+4D MAJOR-2 precedent): R-test constructs a low-lane truth row directly or a replay fixture skipping `read_mode_truth_json` / `_forecast_reference_for` / ingest entry. Tests green; real path broken. Gate: every R-test must call a public entry point and assert the bug failure mode disappears.
3. **Kelvin / silent-default / sentinel-string** (pre-mortem precedent): low extractor defaults `members_unit='K'`, truth metadata defaults `mode=None`, replay returns `agreement="AGREE"`, evaluator emits `""` time fields. Each is a category — a deployed guard (validate_members_unit, `ModeMismatchError`, `time_field_status`) makes the category impossible; an `if x is None: x = default` patch does not.

## Open question for team-lead

**Q**: For 5A (foundation), does the `authority: Literal["canonical_db","degraded","unverified"]` field land on `PortfolioState` in the same commit that introduces the truth-authority flag, or is the PortfolioState dataclass change deferred to the Section-B ride-along? B069 + B073 both depend on this field; if we land the flag in 5A but the dataclass change only in the B-ride subcommit, there's a one-commit window where `policy.source` is available but PortfolioState consumers can't read it. Prefer single-commit landing; confirm?

---
Standing by for Phase 5A diff.
