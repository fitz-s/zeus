# INV-47 registration: gate SCOPE/DRAIN/RESET declarations + enforcement

Plan-evidence file per AGENTS.md §3 (planning-lock applies: `architecture/**`
touched, >4 files changed). `topology_doctor.py --planning-lock` is a
compatibility no-op since commit `ac1f5a182` (unconditional `ok=True`) — this
file is the actual plan-evidence artifact `safety-gate` requires.

## Goal

Commit 7125dc633 added INV-47 to `AGENTS.md` (root law + Boot Digest clause)
but left it unregistered in `architecture/invariants.yaml` — the only
numerically-cited invariant with no registry entry. Close the gap with a real,
mechanically-checked antibody, not an aspirational stub.

## Decisions

- Registry entry follows the INV-45/INV-46 template exactly: `id`, `zones`,
  `statement`, `why`, `enforced_by.{tests,negative_constraints}`,
  `capability_tags`, `relationship_tests`, `basis`, `added`. Recent entries
  (INV-38 onward) use `basis`+`added` instead of `sunset_date` — matched that
  convention, not the older `sunset_date` one.
- Add `NC-24` to `architecture/negative_constraints.yaml` mirroring the
  INV-33/NC-20 through INV-36/NC-23 pattern (each recent INV has a paired NC).
- Enforcement mechanism: a new site-anchored test file,
  `tests/test_gate_scope_drain_reset.py`. For each of the 4 named gate sites,
  it locates a unique anchor string in the source file (asserting it occurs
  exactly once — 0 or 2+ occurrences is a loud failure, not silent pass), then
  asserts a window of surrounding source text contains `# SCOPE:`, `# DRAIN:`,
  `# RESET:` markers. A bare repo-wide grep for "SCOPE" was explicitly
  rejected per the task brief as worthless (not site-anchored).
- SCOPE/DRAIN/RESET comments added at each site are derived from reading the
  actual gate logic and its git history (not invented): `src/main.py`
  `_edli_live_entry_readiness_block`, `src/data/day0_oracle_anomaly.py`
  `is_day0_family_paused`, `src/engine/event_reactor_adapter.py`
  `_posterior_bound_multimodel_members`'s `model_identity_drift` check,
  `src/data/polymarket_request_governor.py`'s `POLYMARKET_ENDPOINT_EMBARGOED`
  circuit check.
- No runtime behavior, condition, threshold, or control flow changes anywhere
  — comments and tests only.

## Files touched

- `architecture/invariants.yaml` (INV-47 entry)
- `architecture/negative_constraints.yaml` (NC-24 entry)
- `architecture/test_topology.yaml` (register new test file: trusted_tests,
  categories.core_law_antibody, a new law_gate entry)
- `tests/test_gate_scope_drain_reset.py` (new)
- `src/main.py` (comment only)
- `src/data/day0_oracle_anomaly.py` (comment only)
- `src/engine/event_reactor_adapter.py` (comment only)
- `src/data/polymarket_request_governor.py` (comment only)
- `docs/operations/current/plans/INDEX.md` (index row for this plan)

## Tests

- `pytest tests/test_gate_scope_drain_reset.py -q`
- `pytest tests/test_invariant_citations.py -q`
- `python3 check_invariant_test_citations.py` if runnable
- Relevant architecture-contract tests (`tests/test_architecture_contracts.py`
  subset touching invariants.yaml schema, if any)

## Next action / rollback point

Land via worktree self-merge after tests pass. Rollback: revert the single
commit; no schema/runtime change means no migration or drain concern.
