from __future__ import annotations

from pathlib import Path
import sqlite3

from src.architecture.decorators import capability, protects
from src.state.projection import (
    CANONICAL_POSITION_CURRENT_COLUMNS,
    ordered_values,
    normalize_position_event_env,
    require_payload_fields,
    table_columns,
    upsert_position_current,
    validate_event_projection_batch,
)


ARCHITECTURE_KERNEL_SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "architecture/2026_04_02_architecture_kernel.sql"
)

CANONICAL_POSITION_EVENT_COLUMNS = (
    "event_id",
    "position_id",
    "event_version",
    "sequence_no",
    "event_type",
    "occurred_at",
    "phase_before",
    "phase_after",
    "strategy_key",
    "decision_id",
    "snapshot_id",
    "order_id",
    "command_id",
    "caused_by",
    "idempotency_key",
    "venue_status",
    "source_module",
    "env",
    "payload_json",
)

TOKEN_SUPPRESSION_COLUMNS = (
    "token_id",
    "condition_id",
    "suppression_reason",
    "source_module",
    "created_at",
    "updated_at",
    "evidence_json",
)


def load_architecture_kernel_sql() -> str:
    return ARCHITECTURE_KERNEL_SQL_PATH.read_text()


def assert_canonical_transaction_schema(conn: sqlite3.Connection) -> None:
    event_columns = table_columns(conn, "position_events")
    current_columns = table_columns(conn, "position_current")
    if not event_columns or not current_columns:
        raise RuntimeError(
            "canonical transaction boundary requires migrated position_events and position_current tables"
        )
    if not set(CANONICAL_POSITION_EVENT_COLUMNS).issubset(event_columns):
        raise RuntimeError("canonical position_events schema not installed")
    if not set(CANONICAL_POSITION_CURRENT_COLUMNS).issubset(current_columns):
        raise RuntimeError("canonical position_current schema not installed")


def _ensure_token_suppression_reason_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'token_suppression'"
    ).fetchone()
    create_sql = str(row[0] if row and row[0] else "")
    if not create_sql or "chain_only_quarantined" in create_sql:
        return

    with conn:
        conn.execute("ALTER TABLE token_suppression RENAME TO token_suppression_old")
        conn.execute(
            """
            CREATE TABLE token_suppression (
                token_id TEXT PRIMARY KEY,
                condition_id TEXT,
                suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
                    'operator_quarantine_clear',
                    'chain_only_quarantined',
                    'settled_position'
                )),
                source_module TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        old_columns = table_columns(conn, "token_suppression_old")
        shared_columns = [column for column in TOKEN_SUPPRESSION_COLUMNS if column in old_columns]
        if shared_columns:
            conn.execute(
                f"""
                INSERT INTO token_suppression ({", ".join(shared_columns)})
                SELECT {", ".join(shared_columns)}
                FROM token_suppression_old
                """
            )
        conn.execute("DROP TABLE token_suppression_old")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_token_suppression_reason
                ON token_suppression(suppression_reason, updated_at)
            """
        )


def _ensure_day0_window_entered_event_type(conn: sqlite3.Connection) -> None:
    """Day0-canonical-event feature slice (2026-04-24): add DAY0_WINDOW_
    ENTERED to position_events.event_type CHECK constraint.

    Fresh DBs created by `CREATE TABLE IF NOT EXISTS` in the kernel SQL
    get the new CHECK automatically. Legacy DBs have the pre-slice CHECK
    (missing DAY0_WINDOW_ENTERED), which would reject writes of the new
    typed event. SQLite doesn't support ALTER CHECK, so rebuild pattern:
    create new table with updated CHECK, copy rows, drop old, rename.

    Idempotent: detects presence of 'DAY0_WINDOW_ENTERED' in the existing
    CREATE TABLE sql and skips rebuild if already migrated.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'position_events'"
    ).fetchone()
    if row is None:
        # Table not yet created (kernel script did the creation above);
        # nothing to migrate.
        return
    create_sql = str(row[0] if row and row[0] else "")
    if not create_sql or "DAY0_WINDOW_ENTERED" in create_sql:
        return  # already has the new type

    # Rebuild path: copy rows through an identical-schema-plus-new-event-
    # type table, preserving PRIMARY KEY + all columns.
    with conn:
        conn.execute("ALTER TABLE position_events RENAME TO position_events_pre_day0_v1")
        # Re-executing the kernel SQL recreates position_events with the
        # new CHECK (because the renamed-old table no longer collides).
        conn.executescript(load_architecture_kernel_sql())
        old_columns = table_columns(conn, "position_events_pre_day0_v1")
        new_columns = table_columns(conn, "position_events")
        shared_columns = [c for c in new_columns if c in old_columns]
        if shared_columns:
            conn.execute(
                f"""
                INSERT INTO position_events ({", ".join(shared_columns)})
                SELECT {", ".join(shared_columns)}
                FROM position_events_pre_day0_v1
                """
            )
        conn.execute("DROP TABLE position_events_pre_day0_v1")


def _ensure_position_current_authority_columns(conn: sqlite3.Connection) -> None:
    """PR D0b (Finding D0/D2-wire / Part-2 audit, 2026-05-27): durable
    authority projection columns on `position_current`.

    Adds (additive, NULL-default) columns required by the typed training-
    eligibility gate and crash-recovery loader:
      fill_authority      TEXT
      recovery_authority  TEXT
      chain_shares        REAL
      chain_seen_at       TEXT
      chain_absence_at    TEXT

    ALTER TABLE ADD COLUMN is supported by SQLite for NULL-default
    columns without a rebuild. Idempotent: skips columns that already
    exist (so the daemon-restart path is safe on partial migrations).

    Legacy rows persist with NULL values until a future cycle rewrites
    the projection through `build_position_current_projection`. The
    training gate `is_training_eligible_position` fails-closed on NULL
    fill_authority (treats it as unrecognised authority).
    """
    existing = table_columns(conn, "position_current")
    if not existing:
        # Table not yet created; kernel SQL will create it with the new
        # columns directly.
        return
    additions = (
        ("fill_authority", "TEXT"),
        ("recovery_authority", "TEXT"),
        ("chain_shares", "REAL"),
        # F1 (docs/findings_2026_05_28.md §F1, 2026-05-28): chain-observed
        # economics columns added so balance-only rescue persists venue
        # truth on chain_avg_price / chain_cost_basis_usd without
        # overwriting submitted entry_price / cost_basis_usd / size_usd.
        ("chain_avg_price", "REAL"),
        ("chain_cost_basis_usd", "REAL"),
        ("chain_seen_at", "TEXT"),
        ("chain_absence_at", "TEXT"),
        # BUG #128 (SEV1, 2026-06-02): durable realized-P&L projection. Pre-fix
        # realized P&L lived ONLY in-memory + positions.json; a filled+settled
        # order left no queryable position_current record. Additive nullable
        # columns populated by build_position_current_projection on close.
        ("realized_pnl_usd", "REAL"),
        ("exit_price", "REAL"),
        ("settlement_price", "REAL"),
        ("settled_at", "TEXT"),
        ("exit_reason", "TEXT"),
        # EDLI bridge / live exit evidence: entry-time belief CI width must
        # survive daemon restarts so CI-separated exits do not degrade to a
        # point-estimate gate.
        ("entry_ci_width", "REAL"),
        # Monitor authority bits (2026-06-17): position_current already
        # persisted last_monitor_prob / last_monitor_market_price, but not
        # whether those values were fresh. Reload paths then reconstructed
        # open positions with false missing authority and could manufacture
        # INCOMPLETE_EXIT_CONTEXT even after a fresh monitor event.
        ("last_monitor_prob_is_fresh", "INTEGER"),
        ("last_monitor_market_price_is_fresh", "INTEGER"),
        # Exit-retry persistence (2026-06-12 infinite-loop incident): the
        # chain-truth gate's _mark_exit_retry incremented exit_retry_count
        # ONLY in memory — every load_portfolio() reset it to 0, so the
        # MAX_EXIT_RETRIES → backoff_exhausted terminal was unreachable and
        # exit_pending_missing positions retried forever (HK 06-09: 724
        # identical EXIT_ORDER_REJECTED events). Persisted so the bounded
        # backoff design actually bounds.
        ("exit_retry_count", "INTEGER"),
        ("next_exit_retry_at", "TEXT"),
    )
    with conn:
        for col_name, col_type in additions:
            if col_name in existing:
                continue
            conn.execute(
                f"ALTER TABLE position_current ADD COLUMN {col_name} {col_type}"
            )


def _ensure_venue_position_observed_event_type(conn: sqlite3.Connection) -> None:
    """PR D0 (Finding D0 / Part-2 audit, 2026-05-27): add VENUE_POSITION_OBSERVED
    to position_events.event_type CHECK constraint.

    Same pattern as _ensure_day0_window_entered_event_type. Fresh DBs from
    the kernel SQL get the new CHECK automatically; legacy DBs need the
    rebuild path because SQLite cannot ALTER a CHECK constraint.

    Idempotent: detects presence of 'VENUE_POSITION_OBSERVED' in existing
    CREATE TABLE sql and skips rebuild if already migrated.

    The new event type is emitted by chain reconciliation when it rescues a
    pending entry from aggregate venue balance WITHOUT a linked venue trade
    fact (degraded recovery; fill_authority=venue_position_observed). It
    carries an explicit training_eligible=false signal in the payload so
    downstream learning gates can reject it via type boundary rather than
    snapshot-keyed scanner heuristics.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'position_events'"
    ).fetchone()
    if row is None:
        # Table not yet created (kernel script will create it with the new
        # CHECK directly); nothing to migrate.
        return
    create_sql = str(row[0] if row and row[0] else "")
    if not create_sql or "VENUE_POSITION_OBSERVED" in create_sql:
        return  # already has the new type

    # Rebuild path: identical-schema-plus-new-event-type table, copy rows,
    # preserving PRIMARY KEY + all columns.
    with conn:
        conn.execute("ALTER TABLE position_events RENAME TO position_events_pre_d0_v1")
        conn.executescript(load_architecture_kernel_sql())
        old_columns = table_columns(conn, "position_events_pre_d0_v1")
        new_columns = table_columns(conn, "position_events")
        shared_columns = [c for c in new_columns if c in old_columns]
        if shared_columns:
            conn.execute(
                f"""
                INSERT INTO position_events ({", ".join(shared_columns)})
                SELECT {", ".join(shared_columns)}
                FROM position_events_pre_d0_v1
                WHERE occurred_at LIKE '____-__-__T%' OR occurred_at = 'QUARANTINE'
                """
            )
        conn.execute("DROP TABLE position_events_pre_d0_v1")


def _ensure_review_required_event_type(conn: sqlite3.Connection) -> None:
    """PR #352 (Part-3 audit F1/F4, 2026-05-27): add REVIEW_REQUIRED to the
    position_events.event_type CHECK constraint.

    Same rebuild pattern as _ensure_venue_position_observed_event_type (SQLite
    cannot ALTER a CHECK). Fresh DBs get the new CHECK from the kernel SQL;
    legacy DBs are rebuilt. Idempotent: skips if 'REVIEW_REQUIRED' already
    present in the table DDL.

    Ordering note: this runs AFTER _ensure_venue_position_observed_event_type,
    whose rebuild now loads kernel SQL that already contains REVIEW_REQUIRED —
    so on a DB missing both, the venue rebuild adds both and this helper early-
    returns. On a DB that already has VENUE_POSITION_OBSERVED (post-#351) but
    not REVIEW_REQUIRED, this helper performs the rebuild.

    REVIEW_REQUIRED is the durable event emitted when chain reconciliation
    detects an unresolved chain/local size mismatch with no canonical baseline
    to correct against — the review requirement now survives restart instead of
    living only in a mutated runtime Position field.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'position_events'"
    ).fetchone()
    if row is None:
        return
    create_sql = str(row[0] if row and row[0] else "")
    if not create_sql or "REVIEW_REQUIRED" in create_sql:
        return  # already has the new type

    with conn:
        conn.execute("ALTER TABLE position_events RENAME TO position_events_pre_f4_v1")
        conn.executescript(load_architecture_kernel_sql())
        old_columns = table_columns(conn, "position_events_pre_f4_v1")
        new_columns = table_columns(conn, "position_events")
        shared_columns = [c for c in new_columns if c in old_columns]
        if shared_columns:
            conn.execute(
                f"""
                INSERT INTO position_events ({", ".join(shared_columns)})
                SELECT {", ".join(shared_columns)}
                FROM position_events_pre_f4_v1
                """
            )
        conn.execute("DROP TABLE position_events_pre_f4_v1")


def apply_architecture_kernel_schema(conn: sqlite3.Connection) -> None:
    """Apply canonical architecture schema and required runtime support tables."""
    event_columns = table_columns(conn, "position_events")
    if event_columns and not set(CANONICAL_POSITION_EVENT_COLUMNS).issubset(
        event_columns
    ):
        raise RuntimeError(
            "legacy position_events table blocks canonical schema bootstrap; "
            "freeze a dedicated migration packet before changing live/runtime schema"
        )

    # B070 legacy collision guard: kernel SQL declares `control_overrides`
    # as a VIEW backed by `control_overrides_history`. On a legacy DB where
    # `control_overrides` already exists as a TABLE, `CREATE VIEW IF NOT
    # EXISTS` silently no-ops (SQLite treats name as already-defined across
    # both types). Result: writes go to the new history table, reads still
    # hit the stale legacy table — silent split-brain. Fail-fast and point
    # at the migration script.
    co_row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name='control_overrides'"
    ).fetchone()
    if co_row is not None and str(co_row[0] if isinstance(co_row, tuple) else co_row["type"]) == "table":
        raise RuntimeError(
            "legacy control_overrides TABLE blocks B070 event-sourced VIEW "
            "bootstrap; run scripts/migrate_b070_control_overrides_to_history.py "
            "--apply with ZEUS_DESTRUCTIVE_CONFIRMED=1 before restarting the daemon"
        )

    conn.executescript(load_architecture_kernel_sql())
    _ensure_token_suppression_reason_schema(conn)
    _ensure_day0_window_entered_event_type(conn)
    _ensure_venue_position_observed_event_type(conn)
    _ensure_review_required_event_type(conn)
    _ensure_position_current_authority_columns(conn)
    # Legacy-DB column reconciliation: `CREATE TABLE IF NOT EXISTS` in the
    # kernel SQL no-ops when position_current exists from a pre-kernel
    # schema. Backfill every canonical column that the legacy table is
    # missing. Plain TEXT affinity matches the existing 3-token-column
    # pattern and satisfies assert_canonical_transaction_schema's set-
    # membership check below. Runtime writers go through
    # require_payload_fields and always supply every canonical field, so
    # the absence of NOT NULL / CHECK constraints on ALTER-migrated
    # columns does not affect write-path correctness.
    existing_columns = table_columns(conn, "position_current")
    for column in CANONICAL_POSITION_CURRENT_COLUMNS:
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE position_current ADD COLUMN {column} TEXT;")
    from src.execution.settlement_commands import ensure_settlement_schema_ready

    ensure_settlement_schema_ready(conn)
    assert_canonical_transaction_schema(conn)


def backfill_fill_authority(conn: sqlite3.Connection) -> dict:
    """F3 (docs/findings_2026_05_28.md §F3, 2026-05-28): deterministic
    fill_authority backfill for legacy NULL rows in position_current.

    After migration adds the nullable fill_authority column, rows created
    before that migration have NULL authority. This function classifies
    each NULL row into one of four values (first-match wins):

      venue_confirmed_full     — SUM(filled_size) of linked ENTRY/BUY
                                 venue_trade_facts (MATCHED/MINED/CONFIRMED)
                                 equals or exceeds position_current.shares
      venue_confirmed_partial  — linked trade facts exist and sum > 0 but
                                 sum < position_current.shares
      venue_position_observed  — VENUE_POSITION_OBSERVED event exists for
                                 position_id in position_events
      legacy_unknown           — none of the above

    Idempotent: iterates only rows WHERE fill_authority IS NULL; rows
    already classified on a prior run are untouched (counts for those
    rows will be zero on re-run).

    Linkage path: position_current.position_id
                  → venue_commands.position_id
                  → venue_trade_facts.command_id
    Only ENTRY/BUY commands are summed (matching the trigger contract in
    db.py position_lots_optimistic_trade_authority).

    Wrapped in a single SAVEPOINT for atomicity.

    Returns dict with keys venue_confirmed_full, venue_confirmed_partial,
    venue_position_observed, legacy_unknown — each value is the count of
    rows classified in this run.
    """
    import secrets

    counts: dict = {
        "venue_confirmed_full": 0,
        "venue_confirmed_partial": 0,
        "venue_position_observed": 0,
        "legacy_unknown": 0,
    }

    unclassified = conn.execute(
        "SELECT position_id, COALESCE(shares, 0.0) AS shares "
        "FROM position_current "
        "WHERE fill_authority IS NULL"
    ).fetchall()

    if not unclassified:
        return counts

    sp_name = f"sp_bfa_{secrets.token_hex(6)}"
    conn.execute(f"SAVEPOINT {sp_name}")
    try:
        for row in unclassified:
            position_id = row[0]
            position_shares = float(row[1] or 0.0)

            # Rule 1 / 2: sum filled_size from linked ENTRY/BUY trade facts
            fill_sum_row = conn.execute(
                """
                SELECT COALESCE(SUM(CAST(tf.filled_size AS REAL)), 0.0)
                  FROM venue_trade_facts tf
                  JOIN venue_commands cmd ON cmd.command_id = tf.command_id
                 WHERE cmd.position_id = ?
                   AND UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
                   AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
                   AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
                   AND CAST(tf.filled_size AS REAL) > 0
                """,
                (position_id,),
            ).fetchone()
            fill_sum = float(fill_sum_row[0] if fill_sum_row else 0.0)

            if fill_sum > 0 and fill_sum >= position_shares - 1e-9:
                authority = "venue_confirmed_full"
            elif fill_sum > 0:
                authority = "venue_confirmed_partial"
            else:
                # Rule 3: VENUE_POSITION_OBSERVED event
                obs_row = conn.execute(
                    "SELECT 1 FROM position_events "
                    "WHERE position_id = ? AND event_type = 'VENUE_POSITION_OBSERVED' "
                    "LIMIT 1",
                    (position_id,),
                ).fetchone()
                if obs_row is not None:
                    authority = "venue_position_observed"
                else:
                    authority = "legacy_unknown"

            conn.execute(
                "UPDATE position_current SET fill_authority = ? WHERE position_id = ?",
                (authority, position_id),
            )
            counts[authority] += 1

        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        raise

    return counts


@capability("canonical_position_write", lease=True)
@protects("INV-04", "INV-08")
def append_many_and_project(
    conn: sqlite3.Connection, events: list[dict], projection: dict
) -> None:
    """Batch canonical event append with a single final projection update.

    Atomicity (DR-33-B, 2026-04-24): uses an explicit SAVEPOINT (not
    `with conn:`) so callers that already hold an outer SAVEPOINT can
    invoke this function without the Python `with conn:` idiom silently
    releasing their outer SAVEPOINT. Per memory rule L30 (`with conn:`
    inside SAVEPOINT atomicity collision): Python sqlite3's `with conn:`
    commits + releases the innermost active SAVEPOINT on clean exit,
    which — when this function was invoked inside a caller's SAVEPOINT —
    broke the caller's ROLLBACK path on subsequent errors. Explicit
    SAVEPOINT nesting avoids the collision: nested SAVEPOINTs are
    released independently in SQLite, so the caller's outer SAVEPOINT
    survives a clean release of this function's inner SAVEPOINT.

    Torn-state closure: the pre-DR-33-B `cycle_runtime.py:1246-1252`
    pattern explicitly placed `_dual_write_canonical_entry_if_available`
    OUTSIDE the `sp_candidate_*` SAVEPOINT guard because the `with conn:`
    in this function would have released sp_candidate_* on commit. With
    DR-33-B, the dual-write can run INSIDE sp_candidate_* — if the
    dual-write fails, ROLLBACK TO sp_candidate_* correctly rolls back
    both the trade_decisions writes and the position_events writes.

    Callers outside any SAVEPOINT (top-level): SQLite opens an implicit
    transaction at the first SAVEPOINT, and clean RELEASE at the
    outermost level commits. Existing top-level callers continue to
    work unchanged.
    """
    import secrets

    assert_canonical_transaction_schema(conn)
    require_payload_fields(
        projection, CANONICAL_POSITION_CURRENT_COLUMNS, label="projection"
    )
    prepared_events: list[dict] = []
    for idx, event in enumerate(events, 1):
        prepared = dict(event)
        if prepared.get("env") in (None, ""):
            raise ValueError("canonical position event missing env")
        prepared["env"] = normalize_position_event_env(prepared["env"])
        require_payload_fields(
            prepared, CANONICAL_POSITION_EVENT_COLUMNS, label=f"event[{idx}]"
        )
        prepared_events.append(prepared)
    validate_event_projection_batch(prepared_events, projection)
    sp_name = f"sp_ampp_{secrets.token_hex(6)}"
    conn.execute(f"SAVEPOINT {sp_name}")
    try:
        for event in prepared_events:
            conn.execute(
                f"""
                INSERT INTO position_events ({", ".join(CANONICAL_POSITION_EVENT_COLUMNS)})
                VALUES ({", ".join(["?"] * len(CANONICAL_POSITION_EVENT_COLUMNS))})
                """,
                ordered_values(event, CANONICAL_POSITION_EVENT_COLUMNS),
            )
        projection_with_event_type = dict(projection)
        projection_with_event_type["_canonical_event_type"] = prepared_events[-1].get("event_type")
        upsert_position_current(conn, projection_with_event_type)
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        raise
