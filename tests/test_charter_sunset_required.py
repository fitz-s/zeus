# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ANTI_DRIFT_CHARTER §M3; IMPLEMENTATION_PLAN Phase 2 D-4

"""Schema validator: every entry in capabilities.yaml, reversibility.yaml, and
invariants.yaml must carry a sunset_date field that:
  1. Is present (not missing or null)
  2. Parses as an ISO date (YYYY-MM-DD)
  3. Is in the future relative to today (2026-05-06 baseline; test uses date.today())

The file-level `metadata` block is skipped — only top-level list entries are
validated. Non-entry keys (schema_version, metadata) are excluded.

Per ANTI_DRIFT_CHARTER §M3: "A YAML field without sunset_date fails the schema
validator. No new field escapes a clock."
Per IMPLEMENTATION_PLAN Phase 1 exit criterion: schema validators green.
"""

from __future__ import annotations

import pathlib
from datetime import date

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent

YAML_FILES = [
    REPO_ROOT / "architecture" / "capabilities.yaml",
    REPO_ROOT / "architecture" / "reversibility.yaml",
    REPO_ROOT / "architecture" / "invariants.yaml",
]

# Top-level list keys for each YAML (skip metadata / schema_version blocks)
_LIST_KEYS: dict[str, str] = {
    "capabilities.yaml": "capabilities",
    "reversibility.yaml": "reversibility_classes",
    "invariants.yaml": "invariants",
}


def _entries_for(yaml_path: pathlib.Path) -> list[dict]:
    with yaml_path.open() as f:
        data = yaml.safe_load(f)
    list_key = _LIST_KEYS.get(yaml_path.name)
    if list_key is None:
        # Fallback: iterate top-level values that are lists
        for v in data.values():
            if isinstance(v, list):
                return v
        return []
    return data.get(list_key, [])


def _parametrize() -> list[tuple[str, str, dict]]:
    cases = []
    for yaml_path in YAML_FILES:
        short_name = yaml_path.name
        for entry in _entries_for(yaml_path):
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id", repr(entry)[:40])
            cases.append((short_name, str(entry_id), entry))
    return cases


CASES = _parametrize()


@pytest.mark.parametrize(
    "yaml_file,entry_id,entry",
    CASES,
    ids=[f"{f}::{i}" for f, i, _ in CASES],
)
def test_entry_has_sunset_date(yaml_file: str, entry_id: str, entry: dict) -> None:
    """Every YAML entry must have a sunset_date that is a future ISO date."""
    assert "sunset_date" in entry, (
        f"{yaml_file} entry {entry_id!r}: missing 'sunset_date' field. "
        "Per ANTI_DRIFT_CHARTER §M3 every artifact must carry a machine-readable sunset."
    )

    raw = entry["sunset_date"]
    assert raw is not None, (
        f"{yaml_file} entry {entry_id!r}: 'sunset_date' is null. "
        "Must be a non-null ISO date string (YYYY-MM-DD)."
    )

    try:
        parsed = date.fromisoformat(str(raw))
    except ValueError as exc:
        pytest.fail(
            f"{yaml_file} entry {entry_id!r}: 'sunset_date' {raw!r} does not parse "
            f"as ISO date: {exc}"
        )

    today = date.today()
    assert parsed >= today, (
        f"{yaml_file} entry {entry_id!r}: sunset_date {parsed} is in the past "
        f"(today={today}). Re-affirm or archive this entry per charter §M3."
    )


# ---------------------------------------------------------------------------
# Phase 4.D extension: verify _SUNSET_DATE constant on all 5 gate modules
# (ANTI_DRIFT_CHARTER §5 M3 — operational gates expire after 90 days)
# ---------------------------------------------------------------------------

import importlib
import re as _re
from datetime import date as _date

# Map of (module_import_path, constant_name) for each gate.
# Gate 2 uses public SUNSET_DATE (no underscore); all others use _SUNSET_DATE.
_GATE_MODULES: list[tuple[str, str]] = [
    ("src.architecture.gate_edit_time", "_SUNSET_DATE"),       # Gate 1
    ("src.execution.live_executor", "SUNSET_DATE"),             # Gate 2
    ("src.architecture.gate_commit_time", "_SUNSET_DATE"),     # Gate 3
    # Gate 4 is a CI workflow (no Python module constant; sunset managed via workflow file).
    ("src.architecture.gate_runtime", "_SUNSET_DATE"),          # Gate 5
]

_EXPECTED_SUNSET = "2026-08-04"


@pytest.mark.parametrize("module_path,const_name", _GATE_MODULES,
                         ids=[m.split(".")[-1] for m, _ in _GATE_MODULES])
def test_gate_module_has_sunset_date_constant(module_path: str, const_name: str) -> None:
    """Every gate module must export a SUNSET_DATE constant = 2026-08-04.

    Per ANTI_DRIFT_CHARTER §5 M3: operational gates (Phase 4) have a 90-day
    sunset from authoring. The constant must be present and be a valid future
    ISO date string. Tests fail if the gate module drops the constant or the
    date is in the past.
    """
    mod = importlib.import_module(module_path)
    assert hasattr(mod, const_name), (
        f"{module_path} missing {const_name!r} constant. "
        "Per ANTI_DRIFT_CHARTER §5 M3 all operational gate modules must carry "
        "a machine-readable sunset date."
    )
    raw = getattr(mod, const_name)
    assert isinstance(raw, str), (
        f"{module_path}.{const_name} must be a string, got {type(raw)}"
    )
    try:
        parsed = _date.fromisoformat(raw)
    except ValueError as exc:
        pytest.fail(
            f"{module_path}.{const_name} = {raw!r} does not parse as ISO date: {exc}"
        )
    today = _date.today()
    assert parsed >= today, (
        f"{module_path}.{const_name} = {parsed} is in the past (today={today}). "
        "Re-affirm or archive this gate per ANTI_DRIFT_CHARTER §5 M3."
    )
    assert raw == _EXPECTED_SUNSET, (
        f"{module_path}.{const_name} = {raw!r} but expected {_EXPECTED_SUNSET!r}. "
        "All Phase 4 gates share a 90-day sunset from 2026-05-06 authoring."
    )
