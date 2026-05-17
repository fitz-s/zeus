# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/authority/ARCHIVAL_RULES.md
#   §"Special Case: Wave Packets"
"""
wave_family — Wave-packet atomic group logic.

Wave packets follow the naming pattern:
  task_<YYYY-MM-DD>_<slug>_wave<N>

They share evidence within the same wave family and must be treated as an
ATOMIC GROUP: all wave packets in a family are archived together, or none.

Public API:
  group_by_wave_family(candidates) -> dict[str, list[Path]]
      Group paths by their wave-family slug (date + slug, sans _waveN suffix).
      Non-wave paths are excluded from the returned dict.

  wave_family_exemption_atomic(family, check_one) -> bool
      Returns True if ANY member of the family FAILS check_one.
      When True, the whole family is exempted from archival (none move).
      When False, all members passed — family may be archived.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

# Regex matching the wave-packet stem.
# Captures: date (YYYY-MM-DD), slug (non-greedy text), wave number.
# Matches on the final path component (stem for files, name for dirs).
_WAVE_PATTERN = re.compile(
    r"^task_(?P<date>\d{4}-\d{2}-\d{2})_(?P<slug>.+?)_wave(?P<num>\d+)$"
)


def _wave_family_key(name: str) -> str | None:
    """
    Extract the family key (date + slug) from a wave-packet name.

    Returns None if name does not match the wave pattern.
    Family key = "<date>_<slug>" (the common prefix before _waveN).
    """
    m = _WAVE_PATTERN.match(name)
    if m is None:
        return None
    return f"{m.group('date')}_{m.group('slug')}"


def group_by_wave_family(candidates: list[Path]) -> dict[str, list[Path]]:
    """
    Group candidate paths by their wave-family key.

    Only paths whose name (or stem for files) matches the wave pattern are
    included. Non-wave candidates are silently excluded.

    Returns:
        dict mapping family_key -> list of matching paths (insertion order).
    """
    families: dict[str, list[Path]] = {}
    for path in candidates:
        # Use .name for directories, .stem for files
        name_for_match = path.stem if path.suffix else path.name
        key = _wave_family_key(name_for_match)
        if key is None:
            continue
        families.setdefault(key, []).append(path)
    return families


def wave_family_exemption_atomic(
    family: list[Path],
    check_one: Callable[[Path], bool],
) -> bool:
    """
    Determine whether the whole wave family is exempted from archival.

    Per ARCHIVAL_RULES §"Special Case: Wave Packets":
      Treat the family as an ATOMIC GROUP.
      If ANY member fails check_one (i.e., check_one returns False),
      the entire family is exempted — return True (family stays).
      If ALL members pass check_one (all return True),
      the family may be archived — return False (family does not stay).

    Args:
        family:    list of Path objects in the same wave family.
        check_one: callable(path) -> bool.
                   True  = this member PASSES the check (may archive).
                   False = this member FAILS the check (must stay).

    Returns:
        True  if any member fails → whole family exempted (stays).
        False if all members pass → family archivable.
    """
    if not family:
        return False
    return any(not check_one(p) for p in family)
