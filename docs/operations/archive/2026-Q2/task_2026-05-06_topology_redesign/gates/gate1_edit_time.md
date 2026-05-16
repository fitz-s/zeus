---
gate_id: gate1_edit_time
name: Gate 1 — Edit-time Write-tool capability hook
phase: 4.B
mandatory: false
sunset_date: 2026-08-04
evidence:
  - phase3_h_decision.md §Phase4MandatoryConditions
  - ULTIMATE_DESIGN §5 Gate 1
  - IMPLEMENTATION_PLAN §6 days 51-55
feature_flag: ZEUS_ROUTE_GATE_EDIT=off
implementation: src/architecture/gate_edit_time.py
hook_wiring: .claude/settings.json PreToolUse Edit|Write|MultiEdit|NotebookEdit
ritual_signal: logs/ritual_signal/YYYY-MM.jsonl
schema_version: 1
---

# Gate 1: Edit-time Write-tool capability hook

Consults `route()` from `route_function.py` to identify capability hits for
paths about to be written. Reads `reversibility.yaml` to map each capability's
`reversibility_class` to its `enforcement_default`. Refuses writes to
`blocking`-class capabilities without `ARCH_PLAN_EVIDENCE` env set to an
existing plan file.

Emits one `ritual_signal` JSON line per evaluation to
`logs/ritual_signal/YYYY-MM.jsonl` per ANTI_DRIFT_CHARTER §3 M1.

Feature flag `ZEUS_ROUTE_GATE_EDIT=off` short-circuits all checks.
Sunset: 2026-08-04 (90 days from authoring per CHARTER §5).
