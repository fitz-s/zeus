# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: refactor-auth-econ-split B2 — cancel SCHEMA_VERSION counter
"""Schema content-hash fingerprint detector.

Replaces check_schema_version.py (deleted in B2).  Instead of a hand-bumped
integer counter, schema drift is detected by comparing a SHA-256 over the
canonicalized sqlite_master content of a freshly-initialised world DB and a
freshly-initialised forecast DB.

Usage
-----
  # Verify pin matches current DDL:
  python scripts/check_schema_fingerprint.py

  # Rewrite pin after intentional DDL change:
  python scripts/check_schema_fingerprint.py --write-pin

The pin file lives at architecture/_schema_fingerprint.txt.

CI integration
--------------
Add to tests/conftest.py session fixture (replacing the old
check_schema_version.py call) or run as a standalone pre-commit hook.
On drift the script exits non-zero and prints the computed hash so the
developer can run --write-pin after verifying the change is intentional.
"""

import hashlib
import pathlib
import sqlite3
import sys

_ZEUS_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ZEUS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ZEUS_ROOT))

assert sqlite3.sqlite_version_info >= (3, 37, 0), (
    f"SQLite {sqlite3.sqlite_version} < 3.37.0; PRAGMA user_version guarantee may not hold."
)

from src.state.db import init_schema, init_schema_forecasts  # noqa: E402

PIN_FILE = _ZEUS_ROOT / "architecture" / "_schema_fingerprint.txt"


def _schema_rows(conn: sqlite3.Connection) -> list:
    return sorted(
        conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    )


def compute_fingerprint() -> str:
    """Return SHA-256 hex over canonicalized DDL of both world and forecast schemas."""
    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    world_rows = _schema_rows(world_conn)
    world_conn.close()

    forecast_conn = sqlite3.connect(":memory:")
    init_schema_forecasts(forecast_conn)
    forecast_rows = _schema_rows(forecast_conn)
    forecast_conn.close()

    canonical = repr(("world", world_rows, "forecasts", forecast_rows))
    return hashlib.sha256(canonical.encode()).hexdigest()


if __name__ == "__main__":
    write_pin = "--write-pin" in sys.argv
    actual = compute_fingerprint()

    if write_pin:
        PIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        PIN_FILE.write_text(actual + "\n")
        print(f"Schema fingerprint written: {actual}")
        sys.exit(0)

    expected = PIN_FILE.read_text().strip() if PIN_FILE.exists() else ""
    if actual != expected:
        print(f"SCHEMA DRIFT DETECTED")
        print(f"  computed : {actual}")
        print(f"  pinned   : {expected}")
        print(f"If intentional: run `python scripts/check_schema_fingerprint.py --write-pin`")
        sys.exit(1)
    print(f"Schema fingerprint OK: {actual}")
