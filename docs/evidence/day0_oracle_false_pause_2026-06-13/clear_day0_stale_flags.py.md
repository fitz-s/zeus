# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/evidence/day0_oracle_false_pause_2026-06-13/diagnosis.md
#   — clears the 174 stale day0-oracle false-pause flags AFTER the WU-side
#   coverage-gate code fix deploys. OPERATOR-GATED. Run only post-deploy, else
#   the 10-min WU check interval re-flags them. NOTE: the 24h TTL auto-expires
#   these flags anyway — this script is an OPTIONAL accelerant for faster order
#   resumption, NOT required for correctness.
#
# INV-37: this performs a single-DB write to zeus-world only (no cross-DB
# write), via the module's own clear_day0_oracle_anomaly() which uses the
# standard world LIVE writer lock. No ATTACH needed.
#
# Usage (DRY-RUN default):
#   /Users/leofitz/zeus/.venv/bin/python /tmp/clear_day0_stale_flags.py
#   /Users/leofitz/zeus/.venv/bin/python /tmp/clear_day0_stale_flags.py --apply
import sys

RO = "file:/Users/leofitz/zeus/state/zeus-world.db?mode=ro"

def main() -> int:
    apply = "--apply" in sys.argv
    import sqlite3
    conn = sqlite3.connect(RO, uri=True)
    try:
        rows = conn.execute(
            "SELECT city, target_date FROM day0_oracle_anomaly_flags ORDER BY city, target_date"
        ).fetchall()
    finally:
        conn.close()
    print(f"{len(rows)} active day0_oracle_anomaly_flags found.")
    for city, td in rows:
        print(f"  {city} {td}")
    if not apply:
        print("\nDRY-RUN. Re-run with --apply to clear (operator-gated; post-deploy only).")
        return 0
    from src.data.day0_oracle_anomaly import clear_day0_oracle_anomaly
    cleared = 0
    for city, td in rows:
        if clear_day0_oracle_anomaly(str(city), str(td)):
            cleared += 1
    print(f"\nCLEARED {cleared}/{len(rows)} flags via clear_day0_oracle_anomaly().")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
