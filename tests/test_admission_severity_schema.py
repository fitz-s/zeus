# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: Navigation Topology v2 PLAN §3 Phase 1 exit criteria; admission_severity.yaml schema_version 1; sunset 2026-11-07

"""Schema validator for architecture/admission_severity.yaml.

Asserts:
  1. File loads as valid YAML.
  2. schema_version == 1 present.
  3. metadata block present with required keys.
  4. issue_severity list is present and non-empty.
  5. Every issue_severity entry has: rule_id, emitter_path, current_severity,
     target_severity, reversibility_class, sunset_date, rationale.
  6. target_severity in {ADVISORY, BLOCKING, silent} (silent is the silent-admit
     value used in K2 companion-loop-break entries in §2.2).
  7. reversibility_class in {WORKING, ARCHIVE, TRUTH_REWRITE, ON_CHAIN}.
  8. All sunset_dates are after 2026-05-07 (Phase 1 ship date).
  9. typed_intent_enum list is present with at least 1 entry.
  10. Every typed_intent_enum entry has: id, description, sunset_date.

Per Navigation Topology v2 PLAN §3 Phase 1 exit criteria.
"""

from __future__ import annotations

import pathlib
from datetime import date

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
ADMISSION_SEVERITY_YAML = REPO_ROOT / "architecture" / "admission_severity.yaml"

PHASE_1_SHIP_DATE = date(2026, 5, 7)

VALID_TARGET_SEVERITIES = {"ADVISORY", "BLOCKING", "silent"}
VALID_REVERSIBILITY_CLASSES = {"WORKING", "ARCHIVE", "TRUTH_REWRITE", "ON_CHAIN"}

REQUIRED_ISSUE_ENTRY_FIELDS = {
    "rule_id",
    "emitter_path",
    "current_severity",
    "target_severity",
    "reversibility_class",
    "sunset_date",
    "rationale",
}

REQUIRED_INTENT_ENUM_FIELDS = {"id", "description", "sunset_date"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def severity_data() -> dict:
    assert ADMISSION_SEVERITY_YAML.exists(), (
        f"architecture/admission_severity.yaml not found at {ADMISSION_SEVERITY_YAML}. "
        "Phase 1 deliverable must be committed."
    )
    with ADMISSION_SEVERITY_YAML.open() as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "admission_severity.yaml must parse as a YAML mapping"
    return data


@pytest.fixture(scope="module")
def issue_entries(severity_data: dict) -> list[dict]:
    return severity_data.get("issue_severity", [])


@pytest.fixture(scope="module")
def intent_enum_entries(severity_data: dict) -> list[dict]:
    return severity_data.get("typed_intent_enum", [])


# ---------------------------------------------------------------------------
# Top-level structure
# ---------------------------------------------------------------------------

def test_loads_as_valid_yaml(severity_data: dict) -> None:
    """File must load as a non-empty YAML mapping."""
    assert severity_data, "admission_severity.yaml parsed as empty"


def test_schema_version_present(severity_data: dict) -> None:
    """schema_version must be present and == 1."""
    assert "schema_version" in severity_data, "Missing top-level 'schema_version' key"
    assert severity_data["schema_version"] == 1, (
        f"Expected schema_version=1, got {severity_data['schema_version']!r}"
    )


def test_metadata_present(severity_data: dict) -> None:
    """metadata block must be present with required keys."""
    assert "metadata" in severity_data, "Missing top-level 'metadata' block"
    meta = severity_data["metadata"]
    for key in ("charter_version", "catalog_size", "created", "authority_basis", "default_severity", "default_reversibility_class"):
        assert key in meta, f"metadata missing required key: {key!r}"


def test_issue_severity_list_present(issue_entries: list[dict]) -> None:
    """issue_severity list must be present and non-empty."""
    assert issue_entries, (
        "issue_severity list is missing or empty. "
        "Phase 1 requires at least the 9 emitter-site entries."
    )


def test_typed_intent_enum_present(intent_enum_entries: list[dict]) -> None:
    """typed_intent_enum list must be present with at least 9 entries (D1 amendment)."""
    assert intent_enum_entries, "typed_intent_enum list is missing or empty"
    assert len(intent_enum_entries) >= 9, (
        f"typed_intent_enum has {len(intent_enum_entries)} entries; "
        "expected >= 9 per critic-opus C3/D1 (plan_only, create_new, modify_existing, "
        "refactor, audit, hygiene, hotfix, rebase_keepup, other)."
    )


# ---------------------------------------------------------------------------
# Issue severity entry field validation
# ---------------------------------------------------------------------------

def _issue_entry_params() -> list[tuple[str, dict]]:
    if not ADMISSION_SEVERITY_YAML.exists():
        return []
    with ADMISSION_SEVERITY_YAML.open() as f:
        data = yaml.safe_load(f)
    return [(e.get("rule_id", repr(e)[:30]), e) for e in data.get("issue_severity", [])]


@pytest.mark.parametrize("rule_id,entry", _issue_entry_params(),
                         ids=[p[0] for p in _issue_entry_params()])
def test_issue_entry_required_fields(rule_id: str, entry: dict) -> None:
    """Every issue_severity entry must have all required fields."""
    missing = REQUIRED_ISSUE_ENTRY_FIELDS - set(entry.keys())
    assert not missing, (
        f"issue_severity entry {rule_id!r} missing required fields: {sorted(missing)}. "
        "Required: rule_id, emitter_path, current_severity, target_severity, "
        "reversibility_class, sunset_date, rationale."
    )


@pytest.mark.parametrize("rule_id,entry", _issue_entry_params(),
                         ids=[p[0] for p in _issue_entry_params()])
def test_issue_entry_target_severity_valid(rule_id: str, entry: dict) -> None:
    """target_severity must be in {ADVISORY, BLOCKING, silent}."""
    ts = entry.get("target_severity")
    assert ts in VALID_TARGET_SEVERITIES, (
        f"issue_severity entry {rule_id!r}: target_severity={ts!r} not in "
        f"{VALID_TARGET_SEVERITIES}."
    )


@pytest.mark.parametrize("rule_id,entry", _issue_entry_params(),
                         ids=[p[0] for p in _issue_entry_params()])
def test_issue_entry_reversibility_class_valid(rule_id: str, entry: dict) -> None:
    """reversibility_class must be in {WORKING, ARCHIVE, TRUTH_REWRITE, ON_CHAIN}."""
    rc = entry.get("reversibility_class")
    assert rc in VALID_REVERSIBILITY_CLASSES, (
        f"issue_severity entry {rule_id!r}: reversibility_class={rc!r} not in "
        f"{VALID_REVERSIBILITY_CLASSES}."
    )


@pytest.mark.parametrize("rule_id,entry", _issue_entry_params(),
                         ids=[p[0] for p in _issue_entry_params()])
def test_issue_entry_sunset_date_after_ship_date(rule_id: str, entry: dict) -> None:
    """sunset_date must be after 2026-05-07 (Phase 1 ship date)."""
    raw = entry.get("sunset_date")
    assert raw is not None, (
        f"issue_severity entry {rule_id!r}: sunset_date is null or missing."
    )
    try:
        parsed = date.fromisoformat(str(raw))
    except ValueError as exc:
        pytest.fail(
            f"issue_severity entry {rule_id!r}: sunset_date {raw!r} does not parse "
            f"as ISO date: {exc}"
        )
    assert parsed > PHASE_1_SHIP_DATE, (
        f"issue_severity entry {rule_id!r}: sunset_date {parsed} must be after "
        f"Phase 1 ship date {PHASE_1_SHIP_DATE}."
    )


# ---------------------------------------------------------------------------
# Typed-intent enum entry validation
# ---------------------------------------------------------------------------

def _intent_enum_params() -> list[tuple[str, dict]]:
    if not ADMISSION_SEVERITY_YAML.exists():
        return []
    with ADMISSION_SEVERITY_YAML.open() as f:
        data = yaml.safe_load(f)
    return [(e.get("id", repr(e)[:30]), e) for e in data.get("typed_intent_enum", [])]


@pytest.mark.parametrize("intent_id,entry", _intent_enum_params(),
                         ids=[p[0] for p in _intent_enum_params()])
def test_intent_enum_required_fields(intent_id: str, entry: dict) -> None:
    """Every typed_intent_enum entry must have id, description, sunset_date."""
    missing = REQUIRED_INTENT_ENUM_FIELDS - set(entry.keys())
    assert not missing, (
        f"typed_intent_enum entry {intent_id!r} missing required fields: {sorted(missing)}."
    )


@pytest.mark.parametrize("intent_id,entry", _intent_enum_params(),
                         ids=[p[0] for p in _intent_enum_params()])
def test_intent_enum_sunset_date_after_ship_date(intent_id: str, entry: dict) -> None:
    """Every typed_intent_enum entry sunset_date must be after 2026-05-07."""
    raw = entry.get("sunset_date")
    assert raw is not None, (
        f"typed_intent_enum entry {intent_id!r}: sunset_date is null or missing."
    )
    try:
        parsed = date.fromisoformat(str(raw))
    except ValueError as exc:
        pytest.fail(
            f"typed_intent_enum entry {intent_id!r}: sunset_date {raw!r} does not "
            f"parse as ISO date: {exc}"
        )
    assert parsed > PHASE_1_SHIP_DATE, (
        f"typed_intent_enum entry {intent_id!r}: sunset_date {parsed} must be after "
        f"Phase 1 ship date {PHASE_1_SHIP_DATE}."
    )
