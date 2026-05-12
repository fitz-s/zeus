# Created: 2026-05-11
# Last reused or audited: 2026-05-11
# Authority basis: PLAN docs/operations/task_2026-05-11_init_schema_boot_invariant/PLAN.md §5.6
"""Schema-version drift detector.

Run by tests/conftest.py session fixture and as a standalone CI gate.
Fails if sqlite_master hash of a fresh init_schema DB does not match
tests/state/_schema_pinned_hash.txt.

Remediation on failure:
  1. Bump SCHEMA_VERSION in src/state/db.py.
  2. Re-run this script with --write-pin to update the pinned hash.
     python scripts/check_schema_version.py --write-pin
"""

import hashlib
import pathlib
import sqlite3
import sys

# Ensure zeus repo root is on sys.path (scripts/ is one level below root).
_ZEUS_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ZEUS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ZEUS_ROOT))

assert sqlite3.sqlite_version_info >= (3, 37, 0), (
    f"SQLite {sqlite3.sqlite_version} < 3.37.0; PRAGMA user_version page-1 guarantee may not hold."
)

from src.state.db import SCHEMA_VERSION, init_schema  # noqa: E402

PINNED = pathlib.Path("tests/state/_schema_pinned_hash.txt")


def fresh_hash() -> str:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    rows = sorted(
        conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    )
    return hashlib.sha256(repr(rows).encode()).hexdigest()


if __name__ == "__main__":
    write_pin = "--write-pin" in sys.argv
    actual = fresh_hash()

    if write_pin:
        PINNED.parent.mkdir(parents=True, exist_ok=True)
        PINNED.write_text(actual + "\n")
        print(f"Pinned hash written: {actual}")
        sys.exit(0)

    expected = PINNED.read_text().strip() if PINNED.exists() else ""
    if actual != expected:
        print(f"SCHEMA DRIFT: actual={actual} pinned={expected} SV={SCHEMA_VERSION}")
        print(
            f"If intended: bump SCHEMA_VERSION in src/state/db.py "
            f"+ write '{actual}' to {PINNED}"
        )
        sys.exit(1)
    print(f"Schema hash OK: {actual} (SV={SCHEMA_VERSION})")
