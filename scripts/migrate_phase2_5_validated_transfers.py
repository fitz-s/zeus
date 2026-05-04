# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: DESIGN_PHASE2_5_TRANSFER_POLICY_REPLACEMENT.md
"""Phase 2.5 schema migration: add validated_calibration_transfers table.

Table records OOS-validated transfer evidence between (train_domain, test_domain)
pairs. Initially empty — every transfer is SHADOW_ONLY by default until an
operator runs a holdout experiment and inserts a row.

evaluate_calibration_transfer() consults this table to decide whether a
forecast in one domain may legitimately use a Platt model trained on another
domain.

Idempotent: CREATE TABLE IF NOT EXISTS; safe to re-run.
Daemon must remain locked (precedence>=200 control_overrides row) per
LIVE_TRADING_LOCKED_2026-05-04.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("phase2_5_migrate")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS validated_calibration_transfers (
    transfer_id TEXT PRIMARY KEY,
    -- Train domain (source of the Platt model)
    train_source_id TEXT NOT NULL,
    train_cycle_hour_utc TEXT NOT NULL,
    train_horizon_profile TEXT NOT NULL,
    train_data_version TEXT NOT NULL,
    train_metric TEXT NOT NULL,
    train_season TEXT NOT NULL,
    -- Test domain (forecast at live-time)
    test_source_id TEXT NOT NULL,
    test_cycle_hour_utc TEXT NOT NULL,
    test_horizon_profile TEXT NOT NULL,
    test_data_version TEXT NOT NULL,
    test_metric TEXT NOT NULL,
    test_season TEXT NOT NULL,
    -- OOS metrics from the validation run
    brier_score REAL,
    log_loss REAL,
    calibration_slope REAL,
    calibration_intercept REAL,
    reliability_passed INTEGER,
    executable_ev_delta_bps REAL,
    n_test_pairs INTEGER NOT NULL,
    -- Audit + freshness
    validated_at TEXT NOT NULL,
    validated_by TEXT NOT NULL,
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
    expires_at TEXT,  -- optional staleness boundary (NULL = no auto-expire)
    notes TEXT,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_validated_calibration_transfers_lookup
ON validated_calibration_transfers (
    test_source_id, test_cycle_hour_utc, test_horizon_profile,
    test_data_version, test_metric, test_season,
    train_source_id, train_cycle_hour_utc, train_horizon_profile,
    train_data_version
);
"""


def _check_daemon_down(conn) -> tuple[bool, str]:
    rows = conn.execute(
        """
        SELECT issued_by, value, precedence, effective_until
        FROM control_overrides
        WHERE target_type='global' AND target_key='entries' AND action_type='gate'
          AND value='true'
          AND (effective_until IS NULL OR effective_until > strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ORDER BY precedence DESC
        """
    ).fetchall()
    if not rows:
        return False, "no active entries-paused override"
    top = rows[0]
    if top[2] < 200:
        return False, f"top precedence {top[2]} < 200"
    return True, f"locked by {top[0]} precedence={top[2]} until={top[3] or 'NEVER'}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-lock-check", action="store_true")
    args = parser.parse_args()

    from src.state.db import get_world_connection
    conn = get_world_connection()
    try:
        if not args.skip_lock_check:
            ok, msg = _check_daemon_down(conn)
            logger.info("daemon-lock check: %s — %s", "PASS" if ok else "FAIL", msg)
            if not ok and not args.dry_run:
                logger.error("Refusing to migrate: %s", msg)
                return 2

        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='validated_calibration_transfers'"
        ).fetchone()
        if existing:
            logger.info("validated_calibration_transfers already exists; running CREATE IF NOT EXISTS to ensure schema parity")

        if not args.dry_run:
            conn.executescript(CREATE_TABLE_SQL + CREATE_INDEX_SQL)
            conn.commit()
            logger.info("CREATE TABLE + CREATE INDEX applied")
        else:
            logger.info("[dry-run] would apply: CREATE TABLE validated_calibration_transfers + index")

        # Verification
        if not args.dry_run:
            cols = conn.execute("PRAGMA table_info(validated_calibration_transfers)").fetchall()
            logger.info("table columns: %s", [c[1] for c in cols])
            count = conn.execute("SELECT COUNT(*) FROM validated_calibration_transfers").fetchone()[0]
            logger.info("validated_transfers row count: %d (expected 0 initially)", count)

        print(json.dumps({
            "status": "ok",
            "dry_run": args.dry_run,
            "table_existed_before": bool(existing),
        }, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
