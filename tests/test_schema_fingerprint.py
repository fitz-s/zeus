# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: refactor-auth-econ-split B2 — cancel SCHEMA_VERSION counter
"""Verify schema content-hash fingerprint matches the pinned value.

Replaces the test coverage formerly provided by tests/state/_schema_pinned_hash.txt
and the check_schema_version.py fixture call in conftest.py.

Fails if DDL in init_schema or init_schema_forecasts has drifted from the
pinned value in architecture/_schema_fingerprint.txt without a deliberate
--write-pin update.
"""

import pathlib
import sys

import pytest

_ZEUS_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ZEUS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ZEUS_ROOT))

from scripts.check_schema_fingerprint import PIN_FILE, compute_fingerprint  # noqa: E402


def test_schema_fingerprint_matches_pin() -> None:
    """Computed DDL fingerprint must match architecture/_schema_fingerprint.txt."""
    assert PIN_FILE.exists(), (
        f"Pin file {PIN_FILE} missing. "
        "Run: python scripts/check_schema_fingerprint.py --write-pin"
    )
    expected = PIN_FILE.read_text().strip()
    actual = compute_fingerprint()
    assert actual == expected, (
        f"Schema DDL drift detected.\n"
        f"  computed : {actual}\n"
        f"  pinned   : {expected}\n"
        "If intentional: python scripts/check_schema_fingerprint.py --write-pin"
    )
