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
