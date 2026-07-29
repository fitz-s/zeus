# Lifecycle: created=2026-07-28; last_reviewed=2026-07-28; last_reused=2026-07-28
# Purpose: Mutation-style regression coverage for the manifest retired-field
#          exemption: prove read_manifest's compat shim is behaviorally inert
#          AND prove the single-live-semantics checker's exact AST use-site
#          contract fails closed on every misuse shape the exemption could be
#          smuggled through, not merely that the current file passes.
# Reuse: pytest tests/test_manifest_retired_field_exemption_contract.py
# Authority basis: PR #457 adversarial review — the checker's five-name
#   control-target taint heuristic (mode/category/lane/runtime/semantics)
#   does not catch status/authority/enabled/return-value/call-argument/
#   persistence misuse; the exact-site contract replaces it for this one
#   named constant.

from __future__ import annotations

import json
from pathlib import Path

from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION, PRODUCT_ID, SOURCE_ID
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, read_manifest, write_manifest
from scripts.check_single_live_semantics import violations

MANIFEST_MODULE_PATH = "src/data/raw_forecast_artifact_manifest.py"


def _built_manifest(tmp_path: Path) -> RawForecastArtifactManifest:
    artifact = tmp_path / "payload.json"
    artifact.write_text(json.dumps({"ok": True}), encoding="utf-8")
    return RawForecastArtifactManifest.from_file(
        artifact,
        source_id=SOURCE_ID,
        product_id=PRODUCT_ID,
        data_version=HIGH_DATA_VERSION,
        source_cycle_time="2026-06-18T06:00:00+00:00",
        source_available_at="2026-06-18T08:00:00+00:00",
        captured_at="2026-06-18T08:05:00+00:00",
        request_url="https://example.invalid/openmeteo",
        request_params={"city": "Karachi"},
        product_metadata={"city": "Karachi", "target_date": "2026-06-19"},
    )


# --- Behavior: the retired field is inert, not merely absent -----------------


def test_legacy_manifest_with_retired_field_loads_identically_to_without_it(
    tmp_path: Path,
) -> None:
    """A pre-cutover manifest carrying trade_authority_status must decode to
    the exact same object as the current-shape manifest without it — proving
    the field is discarded, not merely tolerated into some side channel."""
    clean_path = tmp_path / "clean.json"
    legacy_path = tmp_path / "legacy.json"
    write_manifest(_built_manifest(tmp_path), clean_path)

    payload = json.loads(clean_path.read_text(encoding="utf-8"))
    assert "trade_authority_status" not in payload
    legacy_payload = dict(payload)
    legacy_payload["trade_authority_status"] = "LIVE_AUTHORITY"
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    clean_loaded = read_manifest(clean_path)
    legacy_loaded = read_manifest(legacy_path)

    assert clean_loaded == legacy_loaded
    assert "trade_authority_status" not in legacy_loaded.to_dict()


def test_write_manifest_never_emits_retired_field(tmp_path: Path) -> None:
    """The writer path must never re-emit the retired field regardless of
    input; it is a read-side compat shim only, not round-tripped."""
    target = tmp_path / "manifest.json"
    write_manifest(_built_manifest(tmp_path), target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "trade_authority_status" not in payload
    assert "trade_authority_status" not in target.read_text(encoding="utf-8")


def test_read_manifest_still_rejects_an_arbitrary_unknown_field(tmp_path: Path) -> None:
    """The compat shim is scoped to exactly one retired name; any other
    unknown field must still veto the read (no broadened tolerance)."""
    import pytest

    path = tmp_path / "manifest.json"
    write_manifest(_built_manifest(tmp_path), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown_authority_alias"] = "LIVE_AUTHORITY"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported fields"):
        read_manifest(path)


# --- Checker contract: exact AST use-site, not taint heuristic ---------------

_BASELINE_MODULE = (
    '_RETIRED_TOP_LEVEL_MANIFEST_FIELDS = frozenset({"trade_authority_status"})\n'
    "\n\n"
    "def read_manifest(raw, known):\n"
    "    unknown = set(raw) - known\n"
    "    unsupported = unknown - _RETIRED_TOP_LEVEL_MANIFEST_FIELDS\n"
    "    return unsupported\n"
)


def _write_module(tmp_path: Path, body: str, *, relative: str = MANIFEST_MODULE_PATH) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _module_violations(tmp_path: Path) -> list[str]:
    return [item for item in violations(tmp_path) if item.startswith(f"{MANIFEST_MODULE_PATH}:")]


def test_checker_accepts_the_baseline_definition_and_use_site(tmp_path: Path) -> None:
    _write_module(tmp_path, _BASELINE_MODULE)
    assert _module_violations(tmp_path) == []


def test_checker_rejects_retired_field_returned_from_a_helper(tmp_path: Path) -> None:
    body = _BASELINE_MODULE + "\n\ndef leak():\n    return _RETIRED_TOP_LEVEL_MANIFEST_FIELDS\n"
    _write_module(tmp_path, body)
    assert _module_violations(tmp_path) != []


def test_checker_rejects_retired_field_used_outside_read_manifest(tmp_path: Path) -> None:
    body = (
        '_RETIRED_TOP_LEVEL_MANIFEST_FIELDS = frozenset({"trade_authority_status"})\n'
        "\n\n"
        "def read_manifest(raw, known):\n"
        "    return set(raw) - known\n"
        "\n\n"
        "def other(raw, known):\n"
        "    unknown = set(raw) - known\n"
        "    return unknown - _RETIRED_TOP_LEVEL_MANIFEST_FIELDS\n"
    )
    _write_module(tmp_path, body)
    assert _module_violations(tmp_path) != []


def test_checker_rejects_retired_field_passed_as_call_argument(tmp_path: Path) -> None:
    body = _BASELINE_MODULE.replace(
        "    return unsupported\n",
        "    log(_RETIRED_TOP_LEVEL_MANIFEST_FIELDS)\n    return unsupported\n",
    )
    _write_module(tmp_path, body)
    assert _module_violations(tmp_path) != []


def test_checker_rejects_retired_field_in_a_branch_condition(tmp_path: Path) -> None:
    body = _BASELINE_MODULE.replace(
        "    unknown = set(raw) - known\n",
        "    if _RETIRED_TOP_LEVEL_MANIFEST_FIELDS:\n        pass\n    unknown = set(raw) - known\n",
    )
    _write_module(tmp_path, body)
    assert _module_violations(tmp_path) != []


def test_checker_rejects_retired_field_persisted_into_an_output_mapping(tmp_path: Path) -> None:
    body = _BASELINE_MODULE.replace(
        "    return unsupported\n",
        '    return {"status": _RETIRED_TOP_LEVEL_MANIFEST_FIELDS}\n',
    )
    _write_module(tmp_path, body)
    assert _module_violations(tmp_path) != []


def test_checker_rejects_retired_literal_duplicated_elsewhere_in_file(tmp_path: Path) -> None:
    body = _BASELINE_MODULE + '\nSTATUS_ALIAS = "trade_authority_status"\n'
    _write_module(tmp_path, body)
    assert _module_violations(tmp_path) != []


def test_checker_rejects_retired_literal_reconstructed_via_concatenation(tmp_path: Path) -> None:
    body = _BASELINE_MODULE + '\nSTATUS_ALIAS = "trade_" + "authority_status"\n'
    _write_module(tmp_path, body)
    assert _module_violations(tmp_path) != []


def test_checker_rejects_retired_field_defined_inside_a_function(tmp_path: Path) -> None:
    body = (
        "def build():\n"
        '    _RETIRED_TOP_LEVEL_MANIFEST_FIELDS = frozenset({"trade_authority_status"})\n'
        "    return _RETIRED_TOP_LEVEL_MANIFEST_FIELDS\n"
        "\n\n"
        "def read_manifest(raw, known):\n"
        "    return set(raw) - known\n"
    )
    _write_module(tmp_path, body)
    assert _module_violations(tmp_path) != []


def test_checker_rejects_retired_field_reshaped_with_an_extra_element(tmp_path: Path) -> None:
    body = _BASELINE_MODULE.replace(
        '{"trade_authority_status"}', '{"trade_authority_status", "other_field"}'
    )
    _write_module(tmp_path, body)
    assert _module_violations(tmp_path) != []


def test_checker_rejects_retired_constant_defined_in_a_second_file(tmp_path: Path) -> None:
    """The exact-site exemption is scoped to one file; the same constant
    defined anywhere else must still trip the blunt forbidden-token scan."""
    _write_module(tmp_path, _BASELINE_MODULE)
    _write_module(
        tmp_path,
        _BASELINE_MODULE,
        relative="src/data/some_other_forecast_module.py",
    )
    assert _module_violations(tmp_path) == []
    other_violations = [
        item
        for item in violations(tmp_path)
        if item.startswith("src/data/some_other_forecast_module.py:")
    ]
    assert other_violations != []
