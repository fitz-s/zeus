# Created: 2026-07-20
# Authority basis: AIFS banned-source deletion order (operator: ECMWF OpenData mx2t3/mn2t3 and
#   AIFS -- COMPLETELY DELETED, never to be used again, reaffirmed 2026-07-20), executed per
#   docs/evidence/capital_efficiency_2026_07_19/banned_source_deletion_audit.md checklist.
"""ANTIBODY: the deleted ecmwf_aifs GRIB-ingest namespace never reappears.

2026-07-20 deletion batch removed the AIFS GRIB/HTTP retrieval cluster that has zero generic
reuse anywhere else in the tree: src/data/ecmwf_aifs_grib_identity.py,
src/data/ecmwf_aifs_grib_samples.py, src/data/ecmwf_aifs_ens_request.py, and
scripts/measure_fusion_aifs_drop_performance.py. This mirrors the existing not-live-wired style
of antibody (e.g. tests/test_replacement_member_vote_smoothing_not_live_wired.py): a static scan,
not a runtime behavior check, so it fails RED even if nothing currently imports the resurrected
module.

Additional retired modules are included in this antibody because they encoded
an unwired alternate calibration/fine-tune path whose names and activation
semantics could otherwise seed a second probability authority.

Still outside this antibody's module-path boundary:
  - src/data/ecmwf_aifs_sampled_2t_localday.py (AifsSampledLocalDayExtraction/AifsInstantSample)
    and src/strategy/ecmwf_aifs_sampled_2t_probabilities.py (AifsTemperatureBin) turned out to be
    de facto GENERIC value types reused by ~8 test files across the whole replacement-forecast /
    bayes-precision-fusion test suite (test_hk_settlement_preimage_contract.py,
    test_replacement_sigma_scale_k_c.py, test_bayes_precision_fusion_history_provider_
    materializer_wiring.py, etc.) that have nothing to do with AIFS data ingestion --
    `replacement_forecast_materializer.py`'s own `bins` field is `Sequence[object]` (fully
    duck-typed, zero AIFS coupling). Relocating that shared type off the AIFS module name is a
    real rename touching 8+ files, not a mechanical deletion; left in place rather than risk
    money-path-adjacent test collateral. src/strategy/openmeteo_ecmwf_ifs9_aifs_soft_anchor.py
    and scripts/validate_member_vote_smoothing_3way.py are downstream of the same two types and
    were restored alongside them for the same reason.
  - src/data/forecast_source_registry.py's "ecmwf_aifs_ens" source spec / "A1" product spec:
    ~12 test files (test_replacement_forecast_metric_identity.py, _product_window.py,
    _cycle_phase_admission.py, _bundle_reader_staleness.py, _bundle_reader_tradeable_latest.py,
    _switch_decision.py, _materialization_seed_builder.py, _materialization_preflight.py,
    _live_schema.py, test_cycle_monotone_materialization.py,
    test_replacement_download_cycle_currency_gate.py, test_no_internal_version_suffixes.py) read
    this already-disabled/BLOCKED registry entry as a generic fixture for otherwise-unrelated
    registry-consumer behavior. The audit itself flagged this needed a pre-check ("confirm no
    test asserts on registry completeness/count -- not confirmed safe in this audit"); the check
    came back unsafe.
REPLACEMENT_SOURCE_ID / REPLACEMENT_PRODUCT_ID in src/data/replacement_forecast_calibration_
block.py (identity "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor[_v1]") are the STILL-LIVE
bayes-fusion replacement product family -- "aifs" survives there only as historical naming
lineage, not as the deleted AIFS ingest. This antibody targets the deleted MODULE PATHS
specifically (by import statement), not the substring "aifs" in general, so it does not
false-positive on any of the above.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Dotted module paths that no longer exist. Deleted 2026-07-20 (see docstring above) -- the
# genuinely AIFS-GRIB/HTTP-ingest-specific cluster with zero generic reuse.
_DELETED_MODULES = frozenset(
    {
        "src.data.ecmwf_aifs_grib_identity",
        "src.data.ecmwf_aifs_grib_samples",
        "src.data.ecmwf_aifs_ens_request",
        "src.data.replacement_forecast_finetune_artifact",
        "src.strategy.openmeteo_ecmwf_ifs9_aifs_finetune",
    }
)

# Deleted standalone scripts (not import targets, but must not resurrect on disk either).
_DELETED_SCRIPTS = (
    "scripts/build_replacement_forecast_finetune_artifact.py",
    "scripts/fit_emos_center_calibration.py",
    "scripts/measure_fusion_aifs_drop_performance.py",
)


def _imported_modules(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _violations(root: Path) -> list[str]:
    violations: list[str] = []
    for py_file in root.rglob("*.py"):
        modules = _imported_modules(py_file.read_text(encoding="utf-8"))
        for module in modules:
            if any(module == deleted or module.startswith(deleted + ".") for deleted in _DELETED_MODULES):
                violations.append(f"{py_file.relative_to(REPO)} imports deleted module {module!r}")
    return violations


def test_no_module_under_src_imports_deleted_aifs_namespace():
    violations = _violations(REPO / "src")
    assert violations == [], "deleted AIFS module namespace resurrected:\n" + "\n".join(violations)


def test_no_script_imports_deleted_aifs_namespace():
    violations = _violations(REPO / "scripts")
    assert violations == [], "deleted AIFS module namespace resurrected:\n" + "\n".join(violations)


def test_deleted_aifs_files_do_not_exist_on_disk():
    still_present = [
        module.replace(".", "/") + ".py"
        for module in _DELETED_MODULES
        if (REPO / (module.replace(".", "/") + ".py")).exists()
    ]
    still_present += [script for script in _DELETED_SCRIPTS if (REPO / script).exists()]

    assert still_present == [], "deleted AIFS files resurrected on disk:\n" + "\n".join(still_present)
