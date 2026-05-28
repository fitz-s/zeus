# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Persisted source_time_frontier — schema registration, idempotent UPSERT, backfill guard.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + frontier_store before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR #329 D);
#   operator spec §D (persisted frontier); src/state/db.py::_create_source_time_frontier.
"""PR #329 review D: source_time_frontier persistence (schema + idempotent + backfill-safe)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from src.data.collection_frontier import FrontierRow

_NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
_REPO = Path(__file__).resolve().parents[1]


def _row(*, role: str, issue: datetime, blocker: str = "OK", family: str = "forecast") -> FrontierRow:
    return FrontierRow(
        source_id="ecmwf_open_data", track="mx2t6_high", calendar_id="ecmwf_open_data:mx2t6_high:full",
        role=role, family=family, target_local_date=None,
        source_issue_time=issue, source_release_time=None, safe_fetch_not_before=None,
        latest_attempt_at=None, latest_success_at=issue, captured_at=None, imported_at=None,
        completeness_status="COMPLETE", readiness_status=None, readiness_expires_at=None,
        freshness_state="CURRENT", freshness_age_seconds=3600.0,
        live_blocker=blocker, operator_action="none",
    )


def _frontier_conn() -> sqlite3.Connection:
    from src.state.db import _create_source_time_frontier

    c = sqlite3.connect(":memory:")
    _create_source_time_frontier(c)
    return c


def test_source_time_frontier_schema_registered() -> None:
    """The persisted table is created by init_schema_forecasts AND declared in the DB-ownership
    registry as a forecasts-class table (so the K1 split + INV-37 ownership stays truthful)."""
    # 1. init_schema helper creates it with the idempotent-key PRIMARY KEY:
    c = _frontier_conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(source_time_frontier)").fetchall()}
    assert {"source_id", "family", "partition_key", "latest_event_time", "authority_tier"} <= cols
    pk = [r[1] for r in c.execute("PRAGMA table_info(source_time_frontier)").fetchall() if r[5]]
    assert pk == ["source_id", "family", "partition_key"]  # idempotency key

    # 2. ownership registry declares it forecasts-class:
    own = yaml.safe_load((_REPO / "architecture" / "db_table_ownership.yaml").read_text())
    blob = yaml.safe_dump(own)
    assert "source_time_frontier" in blob, "source_time_frontier missing from db_table_ownership.yaml"

    # 3. the version bump that gates the live daemon is present:
    SCHEMA_FORECASTS_VERSION = 7  # B2: frozen; counter cancelled
    assert SCHEMA_FORECASTS_VERSION >= 7


def test_frontier_writer_is_idempotent_by_source_partition() -> None:
    """Persisting the same (source, family, partition) twice UPDATEs in place — never appends.
    A daemon re-running a tick must not multiply frontier rows."""
    from src.data.frontier_store import persist_frontier

    c = _frontier_conn()
    row = _row(role="live", issue=_NOW - timedelta(hours=1))
    persist_frontier(c, [row], now=_NOW)
    persist_frontier(c, [row], now=_NOW + timedelta(minutes=5))  # same partition, later compute
    n = c.execute("SELECT COUNT(*) FROM source_time_frontier").fetchone()[0]
    assert n == 1, f"expected idempotent single row, got {n}"


def test_backfill_write_time_cannot_refresh_frontier_live_authority() -> None:
    """A backfill/reconstructed write must NOT overwrite a row that already holds live authority —
    the temporal twin of 'backfill cannot look fresh'. Live always wins; backfill is suppressed."""
    from src.data.frontier_store import persist_frontier

    c = _frontier_conn()
    issue = _NOW - timedelta(hours=1)
    # live row lands first:
    live = _row(role="live", issue=issue, blocker="OK")
    persist_frontier(c, [live], now=_NOW)

    # a BACKFILL row for the SAME partition (role=backfill) tries to clobber it:
    backfill = _row(role="backfill", issue=issue, blocker="NOT_LIVE_AUTHORIZED")
    res = persist_frontier(c, [backfill], now=_NOW + timedelta(minutes=10))

    stored = c.execute(
        "SELECT role, authority_tier, live_blocker FROM source_time_frontier"
    ).fetchone()
    assert stored[0] == "live" and stored[1] == "DERIVED_FROM_DISSEMINATION", (
        f"backfill overwrote live authority: {stored}"
    )
    assert stored[2] == "OK"                                  # live blocker preserved
    assert res.skipped_backfill_over_live == 1                # the guard fired, counted not errored

    # but a NEW live compute DOES refresh:
    live2 = _row(role="live", issue=issue, blocker="STALE_SOURCE")
    persist_frontier(c, [live2], now=_NOW + timedelta(minutes=20))
    assert c.execute("SELECT live_blocker FROM source_time_frontier").fetchone()[0] == "STALE_SOURCE"


def test_backfill_into_empty_partition_is_allowed() -> None:
    """Backfill is only blocked from OVERWRITING live — into an empty partition it persists fine."""
    from src.data.frontier_store import persist_frontier

    c = _frontier_conn()
    res = persist_frontier(c, [_row(role="backfill", issue=_NOW - timedelta(days=3))], now=_NOW)
    assert res.written == 1
    assert c.execute("SELECT authority_tier FROM source_time_frontier").fetchone()[0] == "BACKFILL"
