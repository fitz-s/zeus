# P8 Authority Drift Remediation — 3 BLOCKING reference_replacement_missing_entry

Created: 2026-05-15
Authority basis: P8 of runtime-improvement engineering package;
  input: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/
         03_authority_drift_remediation/REMEDIATION_PLAN.md

## Companion-update notice (per AGENTS.md rule)

This POSTMORTEM is the required companion-update entry for each edit to
`architecture/reference_replacement.yaml` below. All three edits are Hypothesis B
remediations: the docs are current; the yaml entries were simply absent.

---

## Root Cause

Three reference docs were added to `docs/reference/` after the initial creation of
`architecture/reference_replacement.yaml` (2026-04-14), without companion entries:

| Doc | Added | By |
|-----|-------|----|
| `zeus_calibration_weighting_authority.md` | ~2026-04-29 | calibration-weight authority packet |
| `zeus_kelly_asymmetric_loss_handoff.md` | 2026-05-03 | Kelly asymmetric loss handoff packet |
| `zeus_vendor_change_response_registry.md` | ~2026-05-03 | vendor-response registry packet |

The topology checker `scripts/topology_doctor_reference_checks.py:run_reference_replacement()`
(lines 228-229) emits `reference_replacement_missing_entry` for any `docs/reference/*.md`
(except AGENTS.md) that has no corresponding entry in the matrix. All three were blocked.

This is a process gap: no gate existed at the time of these doc additions to require a
companion yaml entry. P2 of the same engineering package will add that gate structurally.

---

## Per-Doc Investigation

### 1. zeus_calibration_weighting_authority.md

**Verdict: Hypothesis B** — yaml entry missing. Doc is current.

**Evidence:** Grep of src/, scripts/, tests/, architecture/ confirms doc content matches
live code:
- `src/contracts/snapshot_ingest_contract.py` — snapshot ingestion gate
- `rebuild_calibration_pairs_v2.py` — calibration pair builder
- `refit_platt_v2.py` — Platt refitter
- `config/cities.json` — per-city eligibility

Binary→continuous `training_allowed`→`precision_weight` transition is live code.
Per-city opt-outs for coastal/monsoon physics match cities.json.
ΔT-magnitude weighting forbiddance is enforced in rebuild_calibration_pairs_v2.py.

**AGENTS.md status:** Already present in both conditional reads (line 22-26) and file
registry table (line 74). No AGENTS.md edit needed.

**Fix:** Add yaml entry with `allowed_action: keep_conditional`, `default_read: false`.

---

### 2. zeus_kelly_asymmetric_loss_handoff.md

**Verdict: Hypothesis B** — yaml entry missing. Doc is current.

**Evidence:** Grep confirms landed code:
- `src/strategy/kelly.py` — `DEFAULT_CITY_KELLY_MULTIPLIERS`, `city_kelly_multiplier()`,
  `dynamic_kelly_mult()` with `city` parameter. Landed 2026-05-03.
- `tests/test_city_kelly_multiplier.py` — 14 cases passing.
- Open wiring at `src/engine/evaluator.py:2755` and `src/engine/replay.py:1599` is
  deliberate two-stage rollout per doc §Implementation Status. Not drift.

**AGENTS.md status:** NOT present in conditional reads or file registry. Two sub-issues:
1. Missing from conditional reads block → would trigger `reference_replacement_default_read_mismatch`
   if `keep_conditional` is used without it.
2. Missing from file registry table.

**Fix sequence:**
1. Edit `docs/reference/AGENTS.md` to add kelly handoff to conditional reads + file registry.
2. Add yaml entry with `allowed_action: keep_conditional`, `default_read: false`.

**Rationale for `keep_conditional`:** The doc is reference-only for Kelly sizing
asymmetric-loss work. It is not a default-load doc; agents working on non-Kelly tasks
should not pay the load cost.

**Self-check (new barrier?):** Adding to conditional reads WIDENS access (from zero to
conditional). Does not narrow scope or add gate. No new barrier introduced.

---

### 3. zeus_vendor_change_response_registry.md

**Verdict: Hypothesis B** — yaml entry missing. Doc is current.

**Evidence:** Doc cites current files (tier_resolver.py, station_migration_probe.py,
source_health_probe.py, calibration_weighting_authority for Layer 6). All exist.
T1-T5 playbooks are structural protocol — no code drift.

**AGENTS.md status:** Already present in both conditional reads (lines 32-38) and
confirmed in file registry (not in registry table shown but present in conditional reads
block — will add to registry table alongside kelly fix if needed).

Check: The file registry table (lines 64-88) lists `zeus_calibration_weighting_authority.md`
but NOT `zeus_vendor_change_response_registry.md` or `zeus_kelly_asymmetric_loss_handoff.md`.
Both need to be added to the table.

**Fix:** Add yaml entry with `allowed_action: keep_conditional`, `default_read: false`.

---

## Fixes Applied

1. `docs/reference/AGENTS.md` — add `zeus_kelly_asymmetric_loss_handoff.md` to conditional
   reads block and both missing docs to file registry table.

2. `architecture/reference_replacement.yaml` — add 3 entries (one per blocking doc).

3. `docs/lore/topology/` — 3 lore cards recording the "no companion-update gate" pattern.

---

## Cross-Doc Root Cause Pattern

All 3 cases follow the same pattern:
> Reference doc added without companion `architecture/reference_replacement.yaml` entry.

This is a single structural gap (missing enforcement gate), not 3 independent doc-drift
events. The antibody is a pre-commit or topology check that refuses new `docs/reference/*.md`
files without a companion yaml stanza. P2 of the engineering package will implement this
structurally.

The lore card `20260515-ref-replacement-companion-gate-missing` records this pattern for
future sessions.

---

## Commit Plan

Three atomic commits, one per doc:
- `fix(authority): zeus_calibration_weighting_authority — Hypothesis B remediation`
- `fix(authority): zeus_kelly_asymmetric_loss_handoff — Hypothesis B remediation`
- `fix(authority): zeus_vendor_change_response_registry — Hypothesis B remediation`

Plus one commit for this POSTMORTEM and lore cards.
