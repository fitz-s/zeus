# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: RUN_16_track_G_financial_reconciliation.md §8 — F106/F108/F111 antibodies

"""
Track G — Financial Reconciliation READ-side antibodies (F106, F108, F111).

Source report:
  docs/operations/task_2026-05-16_post_pr126_audit/RUN_16_track_G_financial_reconciliation.md

Root cause (verbatim from §1 of report):
  "Schema mismatch: position_lots.position_id (INTEGER) ≠ position_current.position_id
   (TEXT UUID); USING(position_id) silently empty."

Canonical join (verbatim from §1):
  "position_lots.source_command_id → venue_commands.command_id → venue_commands.position_id
   (TEXT UUID)"

F106 — static scan: no broken USING(position_id) cross-join between position_lots and
        position_current may exist in src/ or scripts/.
F106 — functional: canonical bridge SQL returns correct cost aggregation.
F108 — functional: multi-revision venue_trade_facts must be deduplicated per trade_id.
F108 — static scan: bare SUM(filled_size) without dedup guard is forbidden adjacent to
        venue_trade_facts.
F111 — static scan: SUM(shares) over position_current in src/ must exclude closed phases
        when representing live exposure.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[2]
_SRC_DIR = _REPO_ROOT / "src"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"


def _python_files(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return [p for p in base.rglob("*.py") if "__pycache__" not in str(p)]


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# F106 — static scan: no broken USING(position_id) cross-join
# ---------------------------------------------------------------------------

def test_f106_no_broken_position_id_cross_join_in_src():
    """F106 SEV-1 META: 'USING(position_id)' between position_lots and position_current
    is the broken keyspace cross-join (INTEGER ≠ TEXT UUID). No such pattern may exist
    in src/ or scripts/.

    Canonical join: position_lots.source_command_id → venue_commands.command_id →
    venue_commands.position_id (TEXT UUID).
    """
    # Pattern: a SQL string that references BOTH tables AND uses USING(position_id)
    using_pattern = re.compile(r"USING\s*\(\s*position_id\s*\)", re.IGNORECASE)
    cross_join_pattern = re.compile(r"position_lots", re.IGNORECASE)
    target_table_pattern = re.compile(r"position_current", re.IGNORECASE)

    violations: list[str] = []
    for path in _python_files(_SRC_DIR) + _python_files(_SCRIPTS_DIR):
        content = _read_file(path)
        if not (cross_join_pattern.search(content) and target_table_pattern.search(content)):
            continue
        if using_pattern.search(content):
            # Find the line numbers for reporting
            for lineno, line in enumerate(content.splitlines(), 1):
                if using_pattern.search(line):
                    violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not violations, (
        "F106 antibody: USING(position_id) between position_lots and position_current "
        "produces a silent empty join (INTEGER ≠ TEXT UUID keyspaces). "
        "Use canonical bridge: position_lots.source_command_id → venue_commands.command_id "
        "→ venue_commands.position_id.\n"
        "Violations found:\n" + "\n".join(violations)
    )


def test_f106_no_direct_position_id_equality_cross_join():
    """F106: Also prohibit pl.position_id = pc.position_id style cross-joins between
    the two tables (alias forms of the same broken join).
    """
    # Look for: both tables in same SQL block AND position_id equality between them
    # Use a conservative check: file contains both tables + direct position_id equality pattern
    alias_eq_pattern = re.compile(
        r"(?:pl|lot)\.position_id\s*=\s*(?:pc|cur)\.position_id"
        r"|(?:pc|cur)\.position_id\s*=\s*(?:pl|lot)\.position_id",
        re.IGNORECASE,
    )
    lots_pattern = re.compile(r"position_lots", re.IGNORECASE)
    current_pattern = re.compile(r"position_current", re.IGNORECASE)

    violations: list[str] = []
    for path in _python_files(_SRC_DIR) + _python_files(_SCRIPTS_DIR):
        content = _read_file(path)
        if not (lots_pattern.search(content) and current_pattern.search(content)):
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            if alias_eq_pattern.search(line):
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not violations, (
        "F106 antibody: direct position_id equality join between position_lots and "
        "position_current aliases (pl/lot vs pc/cur) is the broken INTEGER↔TEXT UUID cross-join. "
        "Violations:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# F106 — functional: canonical join via venue_commands returns correct results
# ---------------------------------------------------------------------------

def test_f106_canonical_join_via_venue_commands_returns_correct_cost():
    """F106 functional: the canonical join path
       position_lots.source_command_id → venue_commands.command_id → venue_commands.position_id
    returns the correct cost aggregation when rows exist.

    Also verifies that the broken USING(position_id) path returns empty (confirming
    the schema mismatch is real and the test covers the actual defect).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Minimal schema: only the columns needed for this probe
    conn.executescript("""
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            cost_basis_usd REAL,
            shares REAL,
            phase TEXT
        );
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL
        );
        CREATE TABLE position_lots (
            lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            source_command_id TEXT,
            shares TEXT NOT NULL,
            entry_price_avg TEXT NOT NULL,
            state TEXT NOT NULL
        );
    """)

    # Seed: one TEXT UUID position, one venue_command bridging to it, one lot
    conn.execute(
        "INSERT INTO position_current VALUES ('abc-uuid-001', 1.86, 6.0, 'active')"
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES ('cmd-001', 'abc-uuid-001')"
    )
    # position_lots.position_id is INTEGER (42) — not the UUID
    conn.execute(
        "INSERT INTO position_lots (position_id, source_command_id, shares, entry_price_avg, state) "
        "VALUES (42, 'cmd-001', '6.0', '0.31', 'CONFIRMED_EXPOSURE')"
    )
    conn.commit()

    # Broken path: USING(position_id) — should return NULL (empty join)
    broken_rows = conn.execute("""
        SELECT pc.position_id, SUM(CAST(pl.shares AS REAL) * CAST(pl.entry_price_avg AS REAL)) AS lots_cost
          FROM position_current pc
          LEFT JOIN position_lots pl USING (position_id)
         WHERE pc.phase NOT IN ('voided')
         GROUP BY pc.position_id
    """).fetchall()
    broken_cost = broken_rows[0]["lots_cost"] if broken_rows else None
    assert broken_cost is None, (
        f"F106: USING(position_id) should return NULL cost (broken join), got {broken_cost!r}"
    )

    # Canonical path: source_command_id → venue_commands → position_id (TEXT UUID)
    canonical_rows = conn.execute("""
        WITH lot_cost AS (
            SELECT vc.position_id AS pos_uuid,
                   SUM(CAST(pl.shares AS REAL) * CAST(pl.entry_price_avg AS REAL)) AS lots_cost,
                   SUM(CAST(pl.shares AS REAL)) AS lots_shares
              FROM position_lots pl
              JOIN venue_commands vc ON vc.command_id = pl.source_command_id
             WHERE pl.state = 'CONFIRMED_EXPOSURE'
             GROUP BY vc.position_id
        )
        SELECT pc.position_id, pc.cost_basis_usd, lc.lots_cost, lc.lots_shares
          FROM position_current pc
          LEFT JOIN lot_cost lc ON lc.pos_uuid = pc.position_id
         WHERE pc.phase NOT IN ('voided')
    """).fetchall()

    assert len(canonical_rows) == 1
    row = canonical_rows[0]
    assert row["position_id"] == "abc-uuid-001"
    assert row["lots_cost"] == pytest.approx(1.86, abs=1e-6), (
        f"F106: canonical join must return 6.0 × 0.31 = 1.86, got {row['lots_cost']!r}"
    )
    assert row["lots_shares"] == pytest.approx(6.0, abs=1e-6)

    conn.close()


# ---------------------------------------------------------------------------
# F108 — functional: multi-revision venue_trade_facts must be deduplicated
# ---------------------------------------------------------------------------

def test_f108_latest_trade_fact_cte_deduplicates_revision_rows():
    """F108 SEV-1: venue_trade_facts stores per-trade lifecycle revisions sharing trade_id.

    Verbatim from RUN_16 §4:
    'bare SUM(filled_size) over-counts by 1×–4×. Correct form:
    SUM(MIN(filled_size) per (position, trade_id))'

    This test constructs a 3-revision trade lifecycle (MATCHED→MINED→CONFIRMED)
    and asserts:
    1. Bare SUM produces 3× overcount.
    2. latest_trade_fact CTE (MAX(local_sequence) per trade_id) produces correct count.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE venue_trade_facts (
            trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            state TEXT NOT NULL,
            filled_size TEXT NOT NULL,
            fill_price TEXT NOT NULL,
            local_sequence INTEGER NOT NULL,
            observed_at TEXT
        );
    """)

    # One trade_id with 3 lifecycle revisions — all with filled_size='100'
    for seq, state in enumerate(("MATCHED", "MINED", "CONFIRMED"), start=1):
        conn.execute(
            "INSERT INTO venue_trade_facts (trade_id, command_id, state, filled_size, fill_price, local_sequence) "
            "VALUES (?, 'cmd-001', ?, '100', '0.50', ?)",
            ("trade-abc-001", state, seq),
        )
    conn.commit()

    # Bare SUM: over-counts 3× (300 instead of 100)
    bare_sum = conn.execute(
        "SELECT SUM(CAST(filled_size AS REAL)) FROM venue_trade_facts WHERE command_id = 'cmd-001'"
    ).fetchone()[0]
    assert bare_sum == pytest.approx(300.0), (
        f"F108 prerequisite: bare SUM should be 300 (3× overcount), got {bare_sum}"
    )

    # latest_trade_fact CTE: deduplicated to 100 (single latest revision)
    deduped_sum = conn.execute("""
        WITH latest_trade_fact AS (
            SELECT trade_id, MAX(local_sequence) AS max_sequence
              FROM venue_trade_facts
             GROUP BY trade_id
        )
        SELECT SUM(CAST(tf.filled_size AS REAL)) AS total_filled
          FROM venue_trade_facts tf
          JOIN latest_trade_fact latest
            ON latest.trade_id = tf.trade_id
           AND latest.max_sequence = tf.local_sequence
         WHERE tf.command_id = 'cmd-001'
    """).fetchone()[0]

    assert deduped_sum == pytest.approx(100.0), (
        f"F108: latest_trade_fact CTE must return 100.0 (single-revision), got {deduped_sum!r}. "
        "Bare SUM(filled_size) over-counts lifecycle revisions by 1×–4×."
    )

    conn.close()


# ---------------------------------------------------------------------------
# F108 — static scan: bare SUM(filled_size) without dedup guard
# ---------------------------------------------------------------------------

def test_f108_no_bare_sum_filled_size_without_dedup_guard_in_src():
    """F108 SEV-1: any SQL in src/ that aggregates filled_size from venue_trade_facts
    must include a deduplication guard (latest_trade_fact, MAX(local_sequence),
    GROUP BY trade_id, or DISTINCT trade_id).

    A bare SUM(filled_size) over venue_trade_facts will over-count by 1×–4×
    depending on the number of lifecycle revisions per trade_id.
    """
    # Pattern: SUM(...filled_size...) in a block that also references venue_trade_facts
    # without a dedup keyword nearby
    sum_filled_pattern = re.compile(r"SUM\s*\([^)]*filled_size[^)]*\)", re.IGNORECASE)
    trade_facts_pattern = re.compile(r"venue_trade_facts", re.IGNORECASE)
    dedup_guard_pattern = re.compile(
        r"latest_trade_fact|MAX\s*\([^)]*local_sequence[^)]*\)|GROUP\s+BY\s+trade_id|DISTINCT\s+trade_id",
        re.IGNORECASE,
    )

    violations: list[str] = []
    for path in _python_files(_SRC_DIR):
        content = _read_file(path)
        if not (sum_filled_pattern.search(content) and trade_facts_pattern.search(content)):
            continue
        # Check: does this file have a dedup guard wherever it uses SUM(filled_size)?
        # Strategy: if any SUM(filled_size) exists without ANY dedup guard in the file,
        # flag it (conservative — all current sites use the guard).
        if not dedup_guard_pattern.search(content):
            for lineno, line in enumerate(content.splitlines(), 1):
                if sum_filled_pattern.search(line):
                    violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not violations, (
        "F108 antibody: bare SUM(filled_size) over venue_trade_facts without a "
        "deduplication guard (latest_trade_fact CTE / MAX(local_sequence) / DISTINCT trade_id) "
        "will over-count by 1×–4× due to per-trade lifecycle revisions sharing trade_id.\n"
        "Violations:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# F111 — static scan: live-exposure SUM(shares) must exclude closed phases
# ---------------------------------------------------------------------------

def test_f111_live_exposure_sum_shares_excludes_closed_phases():
    """F111 SEV-3 SEM: economically_closed positions retain shares > 0 in
    position_current post-exit (by design — preserves PnL recompute).

    Any SUM(shares) over position_current in src/ that is intended to represent
    live exposure MUST include an explicit phase exclusion for closed/exited phases.

    Probes that filter only phase != 'voided' will over-state live exposure by up
    to +95 shares (verified on live DB in RUN_16 §7).

    This test flags files in src/ that aggregate SUM(shares) from position_current
    without any phase-exclusion guard mentioning 'economically_closed' or 'closed'.
    """
    sum_shares_pattern = re.compile(r"SUM\s*\([^)]*shares[^)]*\)", re.IGNORECASE)
    position_current_pattern = re.compile(r"position_current", re.IGNORECASE)
    # A query that sums shares over position_current should have a closed-phase exclusion
    closed_guard_pattern = re.compile(
        r"economically_closed|closed.*phase|phase.*closed|NOT IN.*closed",
        re.IGNORECASE,
    )

    violations: list[str] = []
    for path in _python_files(_SRC_DIR):
        content = _read_file(path)
        if not (sum_shares_pattern.search(content) and position_current_pattern.search(content)):
            continue
        if not closed_guard_pattern.search(content):
            for lineno, line in enumerate(content.splitlines(), 1):
                if sum_shares_pattern.search(line) and "shares" in line.lower():
                    violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not violations, (
        "F111 antibody: SUM(shares) over position_current without a closed-phase exclusion "
        "will over-state live exposure. economically_closed positions retain shares > 0 "
        "post-exit by design (for PnL recompute). Add phase exclusion for 'economically_closed'.\n"
        "Violations:\n" + "\n".join(violations)
    )
