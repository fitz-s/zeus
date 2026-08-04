# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after deep-review NO-GO, plus round-3 fixes X3 (per-observation
#   source_manifest_json -- moved out of family_book_states, see
#   src/events/family_book_manifest.py "X3") and H3 (sampling_reason
#   'DECISION' renamed 'PRE_VETO_SELECTED' + orthogonal state_changed/
#   heartbeat_due/pre_veto_selected booleans + selected_bin_id/selected_side
#   identity -- a PRE_VETO_SELECTED observation records that the spine
#   selected a candidate at the decision-production seam, which a later
#   actionability veto in the SAME cycle may still reject; this is NOT a
#   record of final/submitted status, and selection-triggered sampling is
#   not missing-at-random -- analysts must be able to stratify on it).
"""family_book_observations -- append-only, sampled decision-time-series.

One row per SAMPLED decision (sampling_reason: STATE_CHANGE, HEARTBEAT, or
PRE_VETO_SELECTED, by precedence -- policy in
src/events/family_book_telemetry_writer.py; the three orthogonal booleans
below record which conditions actually held, since more than one commonly
does). Persists the probability surfaces a later market/model blend actually
needs (ordered model q and market-implied q, not a lossy scalar center) plus
decision/receipt identity, topology/unit identity, the referenced state_id,
and THIS observation's own per-bin source-snapshot provenance.

EVIDENCE ONLY -- never decision authority. market_center_native is a demoted,
versioned diagnostic (market_center_status/market_center_version columns),
never the research authority; the ordered q vectors are.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2
SAMPLING_POLICY_VERSION = 2
MARKET_CENTER_VERSION = "market_center_native_v1"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS family_book_observations (
    observation_id           TEXT PRIMARY KEY,
    family_id                TEXT NOT NULL,
    city                     TEXT NOT NULL,
    target_date              TEXT NOT NULL,
    temperature_metric       TEXT NOT NULL,
    decision_id              TEXT NOT NULL,
    receipt_hash             TEXT NOT NULL,
    state_id                 TEXT NOT NULL,
    source_manifest_json     TEXT NOT NULL,
    decision_time            TEXT NOT NULL,
    causal_snapshot_id       TEXT,
    predictive_identity_hash TEXT,
    our_mu_native            REAL,
    our_sigma_native         REAL,
    measurement_unit         TEXT NOT NULL CHECK (measurement_unit IN ('C', 'F')),
    model_q_json             TEXT,
    model_q_identity_hash    TEXT,
    market_q_json            TEXT,
    market_q_basis           TEXT,
    market_q_depth_score     REAL,
    market_q_spread_score    REAL,
    market_q_projection_error REAL,
    market_q_book_hash       TEXT,
    market_center_native     REAL,
    market_center_status     TEXT NOT NULL,
    market_center_version    TEXT NOT NULL,
    complete_book            INTEGER NOT NULL CHECK (complete_book IN (0, 1)),
    -- Exactly the three reasons _sampling_decision can emit. 'WORKER_BOOTSTRAP'
    -- was permitted here but no code path ever wrote it: a declared value that
    -- can never hold a real observation is HOLLOW, and a future reader would
    -- reasonably infer a bootstrap sampling mode that does not exist.
    sampling_reason          TEXT NOT NULL CHECK (
        sampling_reason IN ('STATE_CHANGE', 'HEARTBEAT', 'PRE_VETO_SELECTED')
    ),
    state_changed            INTEGER NOT NULL CHECK (state_changed IN (0, 1)),
    heartbeat_due            INTEGER NOT NULL CHECK (heartbeat_due IN (0, 1)),
    pre_veto_selected        INTEGER NOT NULL CHECK (pre_veto_selected IN (0, 1)),
    selected_bin_id          TEXT,
    selected_side            TEXT,
    sampling_policy_version  INTEGER NOT NULL,
    capture_seam             TEXT NOT NULL,
    schema_version           INTEGER NOT NULL
)
"""

# Dedup-of-retries only: the SAME (family_id, receipt_hash, decision_time)
# triple recurring (e.g. a duplicate enqueue) writes zero new rows; this is
# NOT the sampling volume control -- the writer thread decides whether an
# observation is sampling-worthy at all (STATE_CHANGE/HEARTBEAT/
# PRE_VETO_SELECTED) before this INSERT ever runs
# (src/events/family_book_telemetry_writer.py).
CREATE_UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_family_book_observations_identity
    ON family_book_observations(family_id, receipt_hash, decision_time)
"""

CREATE_FAMILY_TIME_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_family_book_observations_family_time
    ON family_book_observations(family_id, decision_time)
"""

CREATE_STATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_family_book_observations_state
    ON family_book_observations(state_id)
"""

CREATE_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_family_book_observations_no_update
BEFORE UPDATE ON family_book_observations
BEGIN
    SELECT RAISE(ABORT, 'family_book_observations is append-only');
END
"""

CREATE_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_family_book_observations_no_delete
BEFORE DELETE ON family_book_observations
BEGIN
    SELECT RAISE(ABORT, 'family_book_observations is append-only');
END
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_UNIQUE_INDEX_SQL)
    conn.execute(CREATE_FAMILY_TIME_INDEX_SQL)
    conn.execute(CREATE_STATE_INDEX_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


_COLUMNS = (
    "observation_id", "family_id", "city", "target_date", "temperature_metric",
    "decision_id", "receipt_hash", "state_id", "source_manifest_json", "decision_time",
    "causal_snapshot_id", "predictive_identity_hash", "our_mu_native", "our_sigma_native",
    "measurement_unit", "model_q_json", "model_q_identity_hash", "market_q_json",
    "market_q_basis", "market_q_depth_score", "market_q_spread_score",
    "market_q_projection_error", "market_q_book_hash", "market_center_native",
    "market_center_status", "market_center_version", "complete_book", "sampling_reason",
    "state_changed", "heartbeat_due", "pre_veto_selected", "selected_bin_id", "selected_side",
    "sampling_policy_version", "capture_seam", "schema_version",
)


def insert_observation(conn: sqlite3.Connection, row: dict) -> bool:
    """Insert one observation row (dict keyed exactly by column name).

    Targeted ``ON CONFLICT(family_id, receipt_hash, decision_time) DO
    NOTHING`` -- an accidental duplicate enqueue of the identical decision is
    a free no-op; any other integrity violation raises normally.
    """
    placeholders = ", ".join("?" for _ in _COLUMNS)
    cur = conn.execute(
        f"""
        INSERT INTO family_book_observations ({", ".join(_COLUMNS)})
        VALUES ({placeholders})
        ON CONFLICT(family_id, receipt_hash, decision_time) DO NOTHING
        """,
        tuple(row[c] for c in _COLUMNS),
    )
    return cur.rowcount > 0
