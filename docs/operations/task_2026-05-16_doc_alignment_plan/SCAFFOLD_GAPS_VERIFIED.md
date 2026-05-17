# SCAFFOLD-to-Implementation Gaps — Verification (2026-05-16)

Audit-of-audit verification of a prior SCAFFOLD-gaps scout's findings. Per memory `feedback_audit_of_audit_antibody_recursive`, drift audits have ~50% self-error rate. This pass re-checks each CRITICAL and FABRICATION claim from the prior audit before any remediation is planned.

## Verdict Table

| # | Original claim | Verdict | Evidence | Why audit was wrong (if reclassified) |
|---|----------------|---------|----------|---------------------------------------|
| 1 | All 9 Task Handlers Missing | **CONFIRMED** | `maintenance_worker/core/engine.py:194, 318` — `_enumerate_candidates` and `_apply_decisions` are literal empty stubs returning `[]` / `ApplyResult(dry_run_only=True)`. Stub comment at engine.py:272 explicitly says "P5.3 task_registry.TaskRegistry.get_tasks_for_schedule() will implement this." | — |
| 2 | Archival Check #0 Missing | **CONFIRMED** | `grep -r "artifact_authority_status" maintenance_worker/` returns 0 hits. ARCHIVAL_RULES.md priority check #0 is entirely absent from the worker core. | — |
| 3 | Lore Extraction Engine Missing | **RECLASSIFIED_NOT_A_GAP** | `ls scripts/lore_promoter.py` confirms file exists; `head -100 scripts/lore_promoter.py` shows `_parse_frontmatter` and `REQUIRED_FIELDS` implemented. P7 commit `d3567238c7` created lore_promoter; `c9c506457f` registered in script_manifest. | Auditor failed to check the `scripts/` root path; components exist with full logic. |
| 4 | Companion Mechanism Missing | **RECLASSIFIED_NOT_A_GAP** | `scripts/topology_v_next/admission_engine.py:247` defines `_check_companion_required`; P2.1 commits `ef310157cb` + `3e39ee310d` added it with 8 probe regression tests. | Audit likely checked only `validator.py`; logic actually lives in admission_engine.py. |
| 5 | Hard Stop Kernel Hardcoded | **CONFIRMED** | `maintenance_worker/core/validator.py:117-190` defines `_FORBIDDEN_RULES` as hardcoded literal list of Zeus/general paths. `bindings/zeus/safety_overrides.yaml` (109 lines) has real rules but validator does NOT load them. Violates project-agnostic design promise from SCAFFOLD. | — |
| 6 | Lore Frontmatter Fabrication | **RECLASSIFIED_NOT_A_GAP** | `scripts/lore_promoter.py:86` defines `_parse_frontmatter`; `REQUIRED_FIELDS` constant validates. | Same as #3 — fabrication claim was false. |
| 7 | Companion Schema Fabrication | **RECLASSIFIED_NOT_A_GAP** | `scripts/topology_v_next/dataclasses.py:205` BindingLayer field includes `companion_required: dict[str, list[str]]`. | Same as #4 — schema exists in dataclasses.py. |
| 8 | Wave Family Logic Missing | **CONFIRMED** | `grep -r "wave" maintenance_worker/` returns 0 hits. ARCHIVAL_RULES §"Special Case: Wave Packets" specifies ATOMIC GROUP logic for `task_*_wave[0-9]+` families; entirely absent from code. | — |

## Final Tallies

- **CONFIRMED**: 4 (gaps #1, #2, #5, #8)
- **RECLASSIFIED_NOT_A_GAP**: 4 (gaps #3, #4, #6, #7 — all from auditor checking wrong file)
- **PARTIAL**: 0
- **Self-error rate of original audit**: 50% on CRITICAL/FABRICATION claims (4/8)

## Implication

The 4 CONFIRMED gaps drive WAVE 1 + WAVE 1.5 of the doc-alignment plan:
- WAVE 1.4: implement Check #0 (#2 confirmed)
- WAVE 1.5: refactor validator.py to load from bindings (#5 confirmed)
- WAVE 1.5: wire 9 stubbed handlers (#1 confirmed) — uses existing TaskRegistry per critic
- WAVE 1.6: implement wave_family.py (#8 confirmed)

The 4 RECLASSIFIED items are NOT_A_DEFECT and require no action; they remain documented here to prevent re-litigation in future audit cycles.

## Relevant Paths

- `maintenance_worker/core/engine.py:194-337` (confirmed stubs at 194, 318; relevant context 268-337)
- `maintenance_worker/core/validator.py:117-190` (confirmed hardcoding)
- `maintenance_worker/rules/task_registry.py` (EXISTS — TaskRegistry to wire into)
- `scripts/lore_promoter.py:86` (verified existence + logic)
- `scripts/topology_v_next/admission_engine.py:247` (verified companion logic)
- `scripts/topology_v_next/dataclasses.py:205` (verified companion schema)
- `bindings/zeus/safety_overrides.yaml` (109 lines, real rules ready for validator loader)

## Provenance

Verification scout dispatched 2026-05-16 by orchestrator-session 7f255122 during doc-alignment planning. Source agent ID: a9e5192501326e106 (verification scout) re-checking findings from prior agent ab425f7dd9134ec0c (SCAFFOLD-gaps original audit). Original audit BATCH_DONE return contained 36 GAPS + 3 FABRICATIONS; this verification re-classified per the table above.
