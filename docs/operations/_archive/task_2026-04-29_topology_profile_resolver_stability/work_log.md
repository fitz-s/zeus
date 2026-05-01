# Work Log

Date: 2026-04-29
Branch: `topology-profile-resolver-stability-2026-04-29`
Task: Topology profile resolver stability.
Changed files: see `receipt.json`.
Summary: Implemented semantic-vs-companion profile selection and navigation input fixes.
Verification: focused topology gates pass; see Verification section.
Next: independent verifier review before marking the packet closed.

## 2026-04-29

- Opened a clean worktree from `plan-pre5` at `749a9f0`.
- Ran typed topology navigation for the topology graph agent-runtime profile;
  admission was `admitted`, direct blockers were empty, and risk tier was T3.
- Added regression tests proving:
  - shared registry/control-plane file sets do not select live-readiness by
    themselves
  - live-readiness-specific files still select the live-readiness profile
  - navigation `--changed-files` is not silently ignored
- Implemented profile-selection evidence classes:
  - semantic file hits can select a profile
  - companion/shared hits provide maintenance context but cannot select a
    semantic profile alone
  - typed intent still selects a profile without bypassing admission
- Added runtime output fields separating task blockers, admission blockers, and
  global health warnings.
- Added route-card fields for selection evidence class, typed-intent need, and
  companion files.

## Verification

- `python -m py_compile scripts/topology_doctor_digest.py scripts/topology_doctor.py scripts/topology_doctor_cli.py scripts/topology_doctor_registry_checks.py` -> passed.
- `python scripts/topology_doctor.py --schema --json` -> `ok: true`.
- `python scripts/digest_profiles_export.py --check` -> passed.
- `python -m pytest -q tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py` -> 41 passed.
- `python -m pytest -q tests/test_topology_doctor.py -k 'navigation or digest or context_pack or closeout or code_review_graph or digest_profile_selection or route_card or issue_schema'` -> 81 passed, 200 deselected.
- Typed topology navigation for this changed-file set -> `ok: true`, admission `admitted`, direct blockers empty, risk tier T3.
- Planning lock with packet plan evidence -> `ok: true`.
- Work record gate -> `ok: true`.
- Change receipt gate -> `ok: true`.
- Map maintenance closeout -> `ok: true`.
- Topology closeout -> `ok: true`, no blocking issues; Code Review Graph partial-coverage warnings remain for changed tool/test files.
- `git diff --check` -> clean.

## Critic Notes

- The fix changes selection semantics, not admission authority: forbidden files,
  generic fallback, and typed-intent admission checks remain intact.
- The schema guard is intentionally narrow: it rejects only missing shared
  companion config, semantic/companion overlap, and profiles selectable solely
  from shared files.
- Full `tests/test_topology_doctor.py` currently has an unrelated repository
  health failure: `reference_replacement_missing_entry` for
  `docs/reference/zeus_calibration_weighting_authority.md`.

## Subagent Review Follow-up

- Subagent review verdict: `REQUEST CHANGES`.
- Finding addressed: exact receipt `changed_files` without typed intent still
  routed to `r3 live readiness gates implementation` because
  `tests/test_digest_profile_matching.py` was treated as a legacy semantic file
  hit.
- Fix applied: added cross-profile resolver/topology tests to
  `digest_profile_selection.shared_companion_patterns` and kept true
  live-readiness files explicit under `semantic_file_patterns`.
- Regression added: exact profile-resolver-stability changed-file set now
  returns generic `advisory_only` with `shared_file_only` evidence and
  `needs_typed_intent: true`, not live-readiness.
- Finding addressed: schema guard now reads `match_policy.strong_phrases`
  instead of only top-level `strong_phrases`.
- Re-verified closeout after follow-up -> `ok: true`, no blocking issues.
