# Created: 2026-05-17
# last_reviewed=2026-05-17
# Authority basis: F7 audit / FIX_SEV1_BUNDLE.md §F7


def up(conn):
    """Add command_id column to execution_fact, linking rows to venue_commands.

    Idempotent: checks PRAGMA table_info before issuing ALTER TABLE so
    re-running (e.g. after runner failure) does not raise OperationalError.
    """
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(execution_fact)")}
    if "command_id" not in existing_cols:
        conn.execute("ALTER TABLE execution_fact ADD COLUMN command_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_execution_fact_command_id"
        " ON execution_fact(command_id) WHERE command_id IS NOT NULL"
    )
