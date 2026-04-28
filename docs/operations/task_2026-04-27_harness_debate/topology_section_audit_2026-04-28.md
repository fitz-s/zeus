# Topology section audit — 2026-04-28

Source: `architecture/topology.yaml`
Window: 90 days
Total sections: 17

## Stratified counts

| Verdict | Count |
|---|---|
| KEEP_STRONG | 2 |
| KEEP_MARGINAL | 6 |
| SUNSET_CANDIDATE | 0 |
| REPLACE_WITH_PYTHON | 9 |

## Per-section detail

| Section | Verdict | Body chars | Path cites | Git hits | File hits | Rationale |
|---|---|---|---|---|---|---|
| `coverage_roots` | **REPLACE_WITH_PYTHON** | 5382 | 22 | 0 | 3 | FS-walk-derivable; zones.py runtime introspection |
| `root_governed_files` | **KEEP_MARGINAL** | 1612 | 2 | 0 | 2 | file-mention only within last 90 days |
| `state_surfaces` | **KEEP_STRONG** | 8126 | 46 | 1 | 4 | bidirectional cite within last 90 days |
| `required_active_pointers` | **KEEP_MARGINAL** | 412 | 6 | 0 | 2 | file-mention only within last 90 days |
| `registry_directories` | **REPLACE_WITH_PYTHON** | 478 | 14 | 0 | 3 | FS-walk-derivable; package __init__.py registries |
| `docs_root_allowed_files` | **KEEP_MARGINAL** | 68 | 3 | 0 | 3 | file-mention only within last 90 days |
| `active_operations_registry` | **KEEP_MARGINAL** | 818 | 27 | 0 | 5 | file-mention only within last 90 days |
| `docs_registry` | **KEEP_STRONG** | 234 | 2 | 15 | 82 | bidirectional cite within last 90 days |
| `module_manifest` | **REPLACE_WITH_PYTHON** | 244 | 3 | 5 | 96 | Already a manifest; possible runtime_modes.py introspectable |
| `module_reference_layer` | **REPLACE_WITH_PYTHON** | 259 | 5 | 0 | 2 | Already a manifest; possible runtime_modes.py introspectable |
| `runtime_artifact_inventory` | **REPLACE_WITH_PYTHON** | 356 | 7 | 5 | 8 | FS-walk + scripts/* output classification |
| `docs_mode_excluded_roots` | **REPLACE_WITH_PYTHON** | 31 | 1 | 0 | 4 | FS-walk-derivable |
| `docs_subroots` | **REPLACE_WITH_PYTHON** | 1436 | 11 | 0 | 7 | FS-walk-derivable |
| `archive_interface` | **KEEP_MARGINAL** | 269 | 5 | 0 | 3 | file-mention only within last 90 days |
| `reference_fact_specs` | **KEEP_MARGINAL** | 373 | 2 | 0 | 1 | file-mention only within last 90 days |
| `core_map_profiles` | **REPLACE_WITH_PYTHON** | 3240 | 40 | 0 | 4 | Profile dispatch; could move to topology_navigator.py |
| `digest_profiles` | **REPLACE_WITH_PYTHON** | 142357 | 3244 | 0 | 3 | Profile dispatch; could move to topology_navigator.py |

## Recommendations (operator decides per-section)

- **KEEP_STRONG**: retain in YAML.
- **KEEP_MARGINAL**: investigate whether one-channel hits are real or coincidental name match; consider field-level pruning.
- **SUNSET_CANDIDATE**: archive (per round-2 verdict §2.1 D1 + Fitz Constraint #3 immune-system retention).
- **REPLACE_WITH_PYTHON**: defer to Phase 3+ when zones.py / runtime_modes.py / topology_navigator.py replacements land.
