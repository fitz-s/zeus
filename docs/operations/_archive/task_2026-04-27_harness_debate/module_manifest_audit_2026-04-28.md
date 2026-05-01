# Module manifest audit — 2026-04-28

Source: `architecture/module_manifest.yaml`
Total modules: 25

## Stratified counts

| Verdict | Count |
|---|---|
| KEEP_AS_YAML | 21 |
| REPLACE_WITH_INIT_PY | 0 |
| HYBRID | 4 |

## Per-module detail

| Module | Verdict | Path | Hand-curated fields | Auto-derivable fields | Runtime registry | Missing cites | Rationale |
|---|---|---|---|---|---|---|---|
| `analysis` | **KEEP_AS_YAML** | `src/analysis` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `calibration` | **KEEP_AS_YAML** | `src/calibration` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `closeout_and_receipts_system` | **KEEP_AS_YAML** | `docs/operations` | 11 | 3 | ✗ (no __init__.py) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `code_review_graph` | **KEEP_AS_YAML** | `.code-review-graph` | 11 | 3 | ✗ (no __init__.py) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `contracts` | **HYBRID** | `src/contracts` | 11 | 3 | ✓ (__all__ declared) | 0 (-) | 11 hand-curated fields + __all__ declared; auto-derive path/scoped_agents/module_book; retain curated in YAML appendix |
| `control` | **KEEP_AS_YAML** | `src/control` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `data` | **KEEP_AS_YAML** | `src/data` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `docs_system` | **KEEP_AS_YAML** | `docs` | 11 | 3 | ✗ (no __init__.py) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `engine` | **KEEP_AS_YAML** | `src/engine` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `execution` | **KEEP_AS_YAML** | `src/execution` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `ingest` | **KEEP_AS_YAML** | `src/ingest` | 11 | 3 | ✗ (no __init__.py) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `manifests_system` | **KEEP_AS_YAML** | `architecture` | 11 | 3 | ✗ (no __init__.py) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `observability` | **KEEP_AS_YAML** | `src/observability` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `risk_allocator` | **HYBRID** | `src/risk_allocator` | 11 | 3 | ✓ (__all__ declared) | 0 (-) | 11 hand-curated fields + __all__ declared; auto-derive path/scoped_agents/module_book; retain curated in YAML appendix |
| `riskguard` | **KEEP_AS_YAML** | `src/riskguard` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `scripts` | **KEEP_AS_YAML** | `scripts` | 11 | 3 | ✗ (no __init__.py) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `signal` | **KEEP_AS_YAML** | `src/signal` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `state` | **KEEP_AS_YAML** | `src/state` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `strategy` | **HYBRID** | `src/strategy` | 11 | 3 | ✓ (__all__ declared) | 0 (-) | 11 hand-curated fields + __all__ declared; auto-derive path/scoped_agents/module_book; retain curated in YAML appendix |
| `supervisor_api` | **KEEP_AS_YAML** | `src/supervisor_api` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `tests` | **KEEP_AS_YAML** | `tests` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `topology_doctor_system` | **KEEP_AS_YAML** | `scripts` | 11 | 3 | ✗ (no __init__.py) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `topology_system` | **KEEP_AS_YAML** | `architecture` | 11 | 3 | ✗ (no __init__.py) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |
| `types` | **HYBRID** | `src/types` | 11 | 3 | ✓ (__all__ declared) | 0 (-) | 11 hand-curated fields + __all__ declared; auto-derive path/scoped_agents/module_book; retain curated in YAML appendix |
| `venue` | **KEEP_AS_YAML** | `src/venue` | 11 | 3 | ✗ (no runtime registry symbol) | 0 (-) | 11 hand-curated fields (no runtime registry to absorb them) |

## Recommendations (operator decides per-module)

- **KEEP_AS_YAML**: hand-curated metadata is load-bearing; YAML is the right surface.
- **HYBRID**: auto-derive path/scoped_agents/module_book via filesystem walk; retain hand-curated fields (priority, maturity, zone, authority_role, law/current/test dependencies) in YAML appendix.
- **REPLACE_WITH_INIT_PY**: package `__init__.py` already has runtime registry (__all__ / PUBLIC_ENTRY_POINTS / MODULE_REGISTRY); migrate path-level metadata there.

Per round-2 verdict §4.2 #11 + Phase 2 lesson: apparent gap ≠ drift. Verify before replacing.
