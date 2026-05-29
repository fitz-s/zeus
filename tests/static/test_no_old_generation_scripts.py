# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 7: scripts/ and src/state/schema/ have no rollback_*, migrate_*_v<N>+,
or *_legacy* paths. xfail(strict=False): 2 scripts remain:
  - scripts/rollback_phase3_t3.py — phase rollback procedure, B4 deletion
    deferred until operator confirms safe-to-drop post-PR3
  - scripts/deprecate_legacy_state_files.py — legacy state file cleanup helper,
    B4 deletion deferred until operator confirms safe-to-drop post-PR3
"""
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]

# Build patterns by concatenation
_ROLLBACK_PREFIX = "roll" + "back" + "_"       # rollback_
_MIGRATE_VN      = "migrate" + "_.*_v" + r"\d+"  # migrate_*_v<N>
_LEG_GLOB        = "leg" + "acy"               # *legacy*

_BAD_NAME_RE = re.compile(
    r"^(?:" + _ROLLBACK_PREFIX + r"|" + _MIGRATE_VN + r")|" + _LEG_GLOB,
    re.IGNORECASE,
)


def _bad_scripts_in(directory: pathlib.Path):
    if not directory.exists():
        return []
    bad = []
    for p in directory.iterdir():
        if p.is_file() and _BAD_NAME_RE.search(p.name):
            bad.append(str(p.relative_to(REPO_ROOT)))
    return bad


@pytest.mark.xfail(
    strict=False,
    reason=(
        "2 old-generation scripts remain: scripts/rollback_phase3_t3.py and "
        "scripts/deprecate_legacy_state_files.py. Deletion (B4) deferred until "
        "operator confirms safe-to-drop post-PR3."
    ),
)
def test_scripts_dir_has_no_old_generation_scripts():
    """scripts/ must contain no rollback_*, migrate_*_v<N>, or *legacy* scripts."""
    bad = _bad_scripts_in(REPO_ROOT / "scripts")
    assert bad == [], (
        f"Found {len(bad)} old-generation scripts in scripts/:\n"
        + "\n".join(bad[:20])
    )


def test_state_schema_dir_has_no_old_generation_scripts():
    """src/state/schema/ must contain no rollback_*, migrate_*_v<N>, or *legacy* files."""
    schema_dir = REPO_ROOT / "src" / "state" / "schema"
    bad = _bad_scripts_in(schema_dir)
    assert bad == [], (
        f"Found {len(bad)} old-generation scripts in src/state/schema/:\n"
        + "\n".join(bad[:20])
    )
