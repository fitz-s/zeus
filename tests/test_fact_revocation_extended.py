# Created: 2026-05-23
# Last reused or audited: 2026-07-12
# Authority basis: docs/archive/2026-Q2/operations_historical/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-E;
#   docs/rebuild/quarantine_excision_2026-07-11.md DIQ packet (owner-local reshape).
# Lifecycle: created=2026-05-23; last_reviewed=2026-07-12; last_reused=never
# Purpose: Unit tests for extended revocation functions (PR-E) and downstream
#          exclusion filters in evidence_report + refit_platt.
#          Supersedes tests/test_decision_integrity_quarantine_extended.py.
# Reuse: Run when any per-table revocation function, evidence_report.py exclusion,
#        or refit_platt exclusion changes.

"""DIQ packet — Extended revocation + downstream exclusion tests.

Coverage:
  1. Tag-coverage: all six per-table revocation functions tag qualifying rows
     and pass through NULL contributes (legacy).
  2. test_promotion_readiness_excludes_revoked_decisions: build_evidence_report
     excludes decision_events rows tagged in fact_revocations.
  3. test_calibration_rebuild_excludes_revoked_pairs: _fetch_pairs_for_bucket
     (via refit_platt) excludes calibration_pairs rows tagged in fact_revocations
     (owner-local: co-located in the forecasts DB, no ATTACH needed).
  4. test_regret_decomposition_excludes_revoked_rows: build_evidence_report
     excludes regret_decompositions rows whose decision_event_id is revoked.
  5. revoke_all_tables_for_noncontributing_forecast aggregates per-table results.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Add scripts/ to path so we can import refit_platt helpers.
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from src.state.fact_revocation import (
    REASON_NON_CONTRIBUTING,
    _de_natural_pk_hash,
    revoke_all_tables_for_noncontributing_forecast,
    revoke_calibration_pairs_for_noncontributing_forecast,
    revoke_decision_events_for_noncontributing_forecast,
    revoke_decisions_for_noncontributing_forecast,
    revoke_probability_trace_fact_for_noncontributing_forecast,
    revoke_selection_family_fact_for_noncontributing_forecast,
    revoke_selection_hypothesis_fact_for_noncontributing_forecast,
)
from src.state.schema.fact_revocations_schema import ensure_table


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    """In-memory DB with ensemble_snapshots + fact_revocations table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            contributes_to_target_extrema INTEGER,
            forecast_window_attribution_status TEXT,
            source_run_id TEXT
        )
    """)
    ensure_table(conn)
    conn.commit()
    return conn


def _snap(conn, *, contributes, attribution="OK", source_run_id=None) -> int:
    cur = conn.execute(
        """INSERT INTO ensemble_snapshots
           (city, target_date, temperature_metric,
            contributes_to_target_extrema, forecast_window_attribution_status, source_run_id)
           VALUES ('Bangkok', '2026-05-22', 'high', ?, ?, ?)""",
        (contributes, attribution, source_run_id),
    )
    conn.commit()
    return cur.lastrowid


def _revocation_count(conn, table_name: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM fact_revocations WHERE table_name=? AND reason_code=?",
        (table_name, REASON_NON_CONTRIBUTING),
    ).fetchone()[0]


def _revoked_ids(conn, table_name: str) -> set[str]:
    rows = conn.execute(
        "SELECT row_id FROM fact_revocations WHERE table_name=? AND reason_code=?",
        (table_name, REASON_NON_CONTRIBUTING),
    ).fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# Tag-coverage: calibration_pairs
# ---------------------------------------------------------------------------

@pytest.fixture()
def cp2_db():
    conn = _make_db()
    conn.execute("""
        CREATE TABLE calibration_pairs (
            pair_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            snapshot_id INTEGER,
            p_raw REAL NOT NULL DEFAULT 0.5,
            outcome INTEGER NOT NULL DEFAULT 1,
            lead_days REAL NOT NULL DEFAULT 1.0,
            season TEXT NOT NULL DEFAULT 'winter',
            cluster TEXT NOT NULL DEFAULT 'C1',
            forecast_available_at TEXT NOT NULL DEFAULT '2026-05-22T00:00:00',
            decision_group_id TEXT NOT NULL DEFAULT 'dg-1',
            dataset_id TEXT NOT NULL DEFAULT 'v1',
            training_allowed INTEGER NOT NULL DEFAULT 1,
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            bin_source TEXT NOT NULL DEFAULT 'legacy',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def _cp2_insert(conn, *, snapshot_id) -> int:
    cur = conn.execute(
        "INSERT INTO calibration_pairs (city, target_date, temperature_metric, snapshot_id) "
        "VALUES ('Bangkok', '2026-05-22', 'high', ?)",
        (snapshot_id,),
    )
    conn.commit()
    return cur.lastrowid


def test_calibration_pairs_contributes_zero_revoked(cp2_db):
    snap_id = _snap(cp2_db, contributes=0)
    pair_id = _cp2_insert(cp2_db, snapshot_id=snap_id)
    result = revoke_calibration_pairs_for_noncontributing_forecast(cp2_db)
    assert result["newly_revoked"] == 1
    assert str(pair_id) in _revoked_ids(cp2_db, "calibration_pairs")


def test_calibration_pairs_contributes_one_skipped(cp2_db):
    snap_id = _snap(cp2_db, contributes=1)
    _cp2_insert(cp2_db, snapshot_id=snap_id)
    result = revoke_calibration_pairs_for_noncontributing_forecast(cp2_db)
    assert result["candidates_found"] == 0
    assert _revocation_count(cp2_db, "calibration_pairs") == 0


def test_calibration_pairs_null_contributes_not_revoked(cp2_db):
    snap_id = _snap(cp2_db, contributes=None)
    _cp2_insert(cp2_db, snapshot_id=snap_id)
    result = revoke_calibration_pairs_for_noncontributing_forecast(cp2_db)
    assert result["candidates_found"] == 0


def test_calibration_pairs_dry_run(cp2_db):
    snap_id = _snap(cp2_db, contributes=0)
    _cp2_insert(cp2_db, snapshot_id=snap_id)
    result = revoke_calibration_pairs_for_noncontributing_forecast(cp2_db, dry_run=True)
    assert result["dry_run"] is True
    assert result["candidates_found"] == 1
    assert _revocation_count(cp2_db, "calibration_pairs") == 0


def test_calibration_pairs_source_run_id_in_meta(cp2_db):
    snap_id = _snap(cp2_db, contributes=0, source_run_id="run-abc")
    _cp2_insert(cp2_db, snapshot_id=snap_id)
    revoke_calibration_pairs_for_noncontributing_forecast(cp2_db)
    import json
    meta_json = cp2_db.execute(
        "SELECT meta_json FROM fact_revocations WHERE table_name='calibration_pairs'"
    ).fetchone()[0]
    meta = json.loads(meta_json)
    assert meta.get("source_run_id") == "run-abc"


# ---------------------------------------------------------------------------
# Tag-coverage: probability_trace_fact
# ---------------------------------------------------------------------------

@pytest.fixture()
def ptf_db():
    conn = _make_db()
    conn.execute("""
        CREATE TABLE probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            decision_snapshot_id TEXT,
            trace_status TEXT NOT NULL DEFAULT 'complete',
            missing_reason_json TEXT NOT NULL DEFAULT '[]',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def _ptf_insert(conn, *, trace_id, snapshot_id) -> None:
    conn.execute(
        "INSERT INTO probability_trace_fact (trace_id, decision_id, decision_snapshot_id) "
        "VALUES (?, ?, ?)",
        (trace_id, f"dec-{trace_id}", str(snapshot_id) if snapshot_id is not None else None),
    )
    conn.commit()


def test_probability_trace_fact_contributes_zero_revoked(ptf_db):
    snap_id = _snap(ptf_db, contributes=0)
    _ptf_insert(ptf_db, trace_id="trace-1", snapshot_id=snap_id)
    result = revoke_probability_trace_fact_for_noncontributing_forecast(ptf_db)
    assert result["newly_revoked"] == 1
    assert "trace-1" in _revoked_ids(ptf_db, "probability_trace_fact")


def test_probability_trace_fact_null_contributes_skipped(ptf_db):
    snap_id = _snap(ptf_db, contributes=None)
    _ptf_insert(ptf_db, trace_id="trace-null", snapshot_id=snap_id)
    result = revoke_probability_trace_fact_for_noncontributing_forecast(ptf_db)
    assert result["candidates_found"] == 0


# ---------------------------------------------------------------------------
# Tag-coverage: selection_family_fact
# ---------------------------------------------------------------------------

@pytest.fixture()
def sff_db():
    conn = _make_db()
    conn.execute("""
        CREATE TABLE selection_family_fact (
            family_id TEXT PRIMARY KEY,
            cycle_mode TEXT NOT NULL DEFAULT 'shadow',
            decision_snapshot_id TEXT,
            city TEXT,
            target_date TEXT,
            strategy_key TEXT,
            discovery_mode TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            meta_json TEXT NOT NULL DEFAULT '{}',
            decision_time_status TEXT
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def _sff_insert(conn, *, family_id, snapshot_id) -> None:
    conn.execute(
        "INSERT INTO selection_family_fact (family_id, decision_snapshot_id) VALUES (?, ?)",
        (family_id, str(snapshot_id) if snapshot_id is not None else None),
    )
    conn.commit()


def test_selection_family_fact_contributes_zero_revoked(sff_db):
    snap_id = _snap(sff_db, contributes=0)
    _sff_insert(sff_db, family_id="fam-1", snapshot_id=snap_id)
    result = revoke_selection_family_fact_for_noncontributing_forecast(sff_db)
    assert result["newly_revoked"] == 1
    assert "fam-1" in _revoked_ids(sff_db, "selection_family_fact")


def test_selection_family_fact_null_contributes_skipped(sff_db):
    snap_id = _snap(sff_db, contributes=None)
    _sff_insert(sff_db, family_id="fam-null", snapshot_id=snap_id)
    result = revoke_selection_family_fact_for_noncontributing_forecast(sff_db)
    assert result["candidates_found"] == 0


# ---------------------------------------------------------------------------
# Tag-coverage: selection_hypothesis_fact
# ---------------------------------------------------------------------------

@pytest.fixture()
def shf_db():
    conn = _make_db()
    conn.execute("""
        CREATE TABLE selection_family_fact (
            family_id TEXT PRIMARY KEY,
            cycle_mode TEXT NOT NULL DEFAULT 'shadow',
            decision_snapshot_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            meta_json TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE selection_hypothesis_fact (
            hypothesis_id TEXT PRIMARY KEY,
            family_id TEXT NOT NULL,
            decision_id TEXT,
            candidate_id TEXT,
            city TEXT NOT NULL DEFAULT 'Bangkok',
            target_date TEXT NOT NULL DEFAULT '2026-05-22',
            range_label TEXT NOT NULL DEFAULT '>=30',
            direction TEXT NOT NULL DEFAULT 'buy_yes',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            meta_json TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def test_selection_hypothesis_fact_contributes_zero_revoked(shf_db):
    snap_id = _snap(shf_db, contributes=0)
    shf_db.execute(
        "INSERT INTO selection_family_fact (family_id, decision_snapshot_id) VALUES ('fam-A', ?)",
        (str(snap_id),),
    )
    shf_db.execute(
        "INSERT INTO selection_hypothesis_fact (hypothesis_id, family_id) VALUES ('hyp-1', 'fam-A')"
    )
    shf_db.commit()
    result = revoke_selection_hypothesis_fact_for_noncontributing_forecast(shf_db)
    assert result["newly_revoked"] == 1
    assert "hyp-1" in _revoked_ids(shf_db, "selection_hypothesis_fact")


def test_selection_hypothesis_fact_null_contributes_skipped(shf_db):
    snap_id = _snap(shf_db, contributes=None)
    shf_db.execute(
        "INSERT INTO selection_family_fact (family_id, decision_snapshot_id) VALUES ('fam-B', ?)",
        (str(snap_id),),
    )
    shf_db.execute(
        "INSERT INTO selection_hypothesis_fact (hypothesis_id, family_id) VALUES ('hyp-null', 'fam-B')"
    )
    shf_db.commit()
    result = revoke_selection_hypothesis_fact_for_noncontributing_forecast(shf_db)
    assert result["candidates_found"] == 0


# ---------------------------------------------------------------------------
# Tag-coverage: decision_events
# ---------------------------------------------------------------------------

@pytest.fixture()
def de_db():
    conn = _make_db()
    conn.execute("""
        CREATE TABLE opportunity_fact (
            decision_id TEXT PRIMARY KEY,
            snapshot_id TEXT,
            should_trade INTEGER NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE decision_events (
            market_slug TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            target_date TEXT NOT NULL,
            observation_time TEXT NOT NULL,
            decision_seq INTEGER NOT NULL,
            decision_event_id TEXT,
            decision_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            outcome TEXT NOT NULL DEFAULT 'buy_yes',
            side TEXT NOT NULL DEFAULT 'buy',
            strategy_key TEXT NOT NULL DEFAULT 'test_strat',
            source TEXT NOT NULL DEFAULT 'live_decision',
            schema_version INTEGER NOT NULL DEFAULT 28,
            observation_available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit',
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def _de_insert(conn, *, decision_event_id, snapshot_id) -> None:
    conn.execute(
        "INSERT INTO opportunity_fact (decision_id, snapshot_id) VALUES (?, ?)",
        (decision_event_id, str(snapshot_id) if snapshot_id is not None else None),
    )
    conn.execute(
        """INSERT INTO decision_events
           (market_slug, temperature_metric, target_date, observation_time, decision_seq,
            decision_event_id)
           VALUES ('BKK-high-ge30', 'high', '2026-05-22', '2026-05-22T12:00:00', 1, ?)""",
        (decision_event_id,),
    )
    conn.commit()


def test_decision_events_contributes_zero_revoked(de_db):
    snap_id = _snap(de_db, contributes=0)
    _de_insert(de_db, decision_event_id="dec-evt-1", snapshot_id=snap_id)
    result = revoke_decision_events_for_noncontributing_forecast(de_db)
    assert result["newly_revoked"] == 1
    # row_id is the 5-col natural PK hash (MAJOR-1 fix), not decision_event_id.
    expected_id = _de_natural_pk_hash(
        "BKK-high-ge30", "high", "2026-05-22", "2026-05-22T12:00:00", 1
    )
    assert expected_id in _revoked_ids(de_db, "decision_events")


def test_decision_events_contributes_one_skipped(de_db):
    snap_id = _snap(de_db, contributes=1)
    _de_insert(de_db, decision_event_id="dec-evt-good", snapshot_id=snap_id)
    result = revoke_decision_events_for_noncontributing_forecast(de_db)
    assert result["candidates_found"] == 0


def test_decision_events_null_contributes_skipped(de_db):
    snap_id = _snap(de_db, contributes=None)
    _de_insert(de_db, decision_event_id="dec-evt-null", snapshot_id=snap_id)
    result = revoke_decision_events_for_noncontributing_forecast(de_db)
    assert result["candidates_found"] == 0


# ---------------------------------------------------------------------------
# Downstream exclusion: test_promotion_readiness_excludes_revoked_decisions
# ---------------------------------------------------------------------------

def _make_evidence_report_db() -> sqlite3.Connection:
    """In-memory DB with decision_events + decision_certificates + fact_revocations.

    C2 (2026-06-16): build_evidence_report's n_decisions denominator now reads
    decision_certificates (FinalIntentCertificate, strategy_key in payload_json), since
    decision_events is a 0-row dead lane in production. We create both tables: decision_events
    is retained because the regret/settled-analytics join still goes through it (and that path
    still applies revocation exclusion — see test_regret_decomposition_excludes_revoked_rows).
    """
    from src.state.schema.decision_certificates_schema import CREATE_CERTIFICATES_SQL

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE decision_events (
            market_slug TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            target_date TEXT NOT NULL,
            observation_time TEXT NOT NULL,
            decision_seq INTEGER NOT NULL,
            decision_event_id TEXT,
            strategy_key TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'live_decision',
            decision_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            outcome TEXT NOT NULL DEFAULT 'buy_yes',
            side TEXT NOT NULL DEFAULT 'buy',
            schema_version INTEGER NOT NULL DEFAULT 28,
            observation_available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit',
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        )
    """)
    conn.execute(CREATE_CERTIFICATES_SQL)
    ensure_table(conn)
    conn.commit()
    return conn


def _insert_final_intent_certificate(
    conn: sqlite3.Connection, *, cert_id: str, strategy_key: str, decision_time: str
) -> None:
    """Seed a VERIFIED FinalIntentCertificate row for the given strategy_key.

    Mirrors the active C2 denominator lane (strategy_key embedded in payload_json). No
    decision_event_id is carried — the production FinalIntentCertificate payload does not
    have one (src/decision_kernel/certificates/execution.py:167-228), which is exactly why
    revocation exclusion (keyed on opportunity_fact.decision_id = decision_event_id) cannot
    be applied to this denominator.
    """
    import json

    payload = json.dumps({"strategy_key": strategy_key})
    conn.execute(
        """INSERT INTO decision_certificates (
            certificate_id, certificate_type, schema_version, canonicalization_version,
            semantic_key, claim_type, mode, decision_time,
            authority_id, authority_version, algorithm_id, algorithm_version,
            payload_json, payload_hash, certificate_hash, verifier_status, created_at
        ) VALUES (?, 'FinalIntentCertificate', 1, '1.0',
                  ?, 'FINAL_INTENT', 'NO_SUBMIT', ?,
                  'test_authority', '1.0', 'test_algorithm', '1.0',
                  ?, 'hash_' || ?, 'cert_hash_' || ?, 'VERIFIED', ?)""",
        (cert_id, cert_id, decision_time, payload, cert_id, cert_id, decision_time),
    )


def test_promotion_readiness_excludes_revoked_decisions():
    """C2 (2026-06-16): n_decisions reads decision_certificates and is NOT narrowed by revocation.

    History: this test verified that the OLD decision_events-based n_decisions denominator
    excluded revoked rows (NOT EXISTS on opportunity_fact.row_id = de.decision_event_id).
    The C2 fix migrated the denominator to decision_certificates (decision_events is a 0-row
    dead lane). FinalIntentCertificate payloads carry no decision_event_id, so there is no key
    to join the revocation rows against — the denominator exclusion is therefore not
    reconstructible on certificates. This is acceptable because:
      - n_decisions is telemetry-only (no ARM/promotion gate reads it; only reported in
        promotion_readiness_job.py), and
      - revocation integrity on the GATE-relevant settled analytics (n_settled / n_wins, joined
        through decision_events.decision_event_id) is RETAINED and covered by
        test_regret_decomposition_excludes_revoked_rows.
    This test now pins the new contract: revoking a decision does NOT change n_decisions.
    """
    from src.analysis.evidence_report import build_evidence_report

    conn = _make_evidence_report_db()

    # Seed 2 FinalIntentCertificate rows for the same strategy (the active C2 denominator lane),
    # plus the parallel decision_events rows (regret/settled join lineage).
    now = datetime.now(timezone.utc).isoformat()
    for i in range(1, 3):
        conn.execute(
            """INSERT INTO decision_events
               (market_slug, temperature_metric, target_date, observation_time, decision_seq,
                decision_event_id, strategy_key)
               VALUES (?, 'high', '2026-05-22', ?, ?, ?, 'strat-A')""",
            (f"mkt-{i}", now, i, f"dec-evt-{i}"),
        )
        _insert_final_intent_certificate(
            conn, cert_id=f"cert-{i}", strategy_key="strat-A", decision_time=now
        )
    conn.commit()

    # Baseline: both certificates counted.
    report_before = build_evidence_report(
        "strat-A", 0, conn=conn, breakeven_win_rate=0.52
    )
    assert report_before.n_decisions == 2

    # Revoke dec-evt-1 under opportunity_fact. The certificate denominator has no
    # decision_event_id to match, so the count is unaffected (the C2 contract).
    conn.execute(
        """INSERT INTO fact_revocations
           (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
           VALUES ('opportunity_fact', 'dec-evt-1', ?, NULL, ?, '{}')""",
        (REASON_NON_CONTRIBUTING, now),
    )
    conn.commit()

    # After revocation: n_decisions UNCHANGED (telemetry-only denominator, no cert join key).
    # Settled-analytics exclusion is covered separately (test_regret_decomposition_*).
    report_after = build_evidence_report(
        "strat-A", 0, conn=conn, breakeven_win_rate=0.52
    )
    assert report_after.n_decisions == 2, (
        f"C2: certificate denominator is not narrowed by revocation, got {report_after.n_decisions}"
    )


# ---------------------------------------------------------------------------
# Downstream exclusion: test_regret_decomposition_excludes_revoked_rows
# ---------------------------------------------------------------------------

def _make_regret_db() -> sqlite3.Connection:
    """In-memory DB with decision_events + regret_decompositions + fact_revocations."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE decision_events (
            market_slug TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            target_date TEXT NOT NULL,
            observation_time TEXT NOT NULL,
            decision_seq INTEGER NOT NULL,
            decision_event_id TEXT,
            strategy_key TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'live_decision',
            decision_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            outcome TEXT NOT NULL DEFAULT 'buy_yes',
            side TEXT NOT NULL DEFAULT 'buy',
            schema_version INTEGER NOT NULL DEFAULT 28,
            observation_available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit',
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        )
    """)
    conn.execute("""
        CREATE TABLE regret_decompositions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id TEXT,
            strategy_id TEXT NOT NULL,
            cohort_tag TEXT NOT NULL DEFAULT '',
            decision_event_id TEXT NOT NULL,
            total_regret_usd REAL NOT NULL,
            computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    ensure_table(conn)
    conn.commit()
    return conn


def test_regret_decomposition_excludes_revoked_rows():
    """build_evidence_report excludes regret rows whose decision_event_id is revoked."""
    from src.analysis.evidence_report import build_evidence_report

    conn = _make_regret_db()
    now = datetime.now(timezone.utc).isoformat()

    # Insert 2 decision_events.
    for i in range(1, 3):
        conn.execute(
            """INSERT INTO decision_events
               (market_slug, temperature_metric, target_date, observation_time, decision_seq,
                decision_event_id, strategy_key)
               VALUES (?, 'high', '2026-05-22', ?, ?, ?, 'strat-B')""",
            (f"mkt-{i}", now, i, f"rde-evt-{i}"),
        )
    # Both decisions have settled regret.
    for i in range(1, 3):
        conn.execute(
            """INSERT INTO regret_decompositions
               (experiment_id, strategy_id, cohort_tag, decision_event_id, total_regret_usd)
               VALUES ('exp-1', 'strat-B', '', ?, 1.0)""",
            (f"rde-evt-{i}",),
        )
    conn.commit()

    report_before = build_evidence_report(
        "strat-B", 0, conn=conn, breakeven_win_rate=0.52
    )
    # Both settled regret rows visible.
    assert report_before.n_settled == 2

    # Revoke rde-evt-1 under opportunity_fact (evidence_report regret filter checks
    # table_name='opportunity_fact' AND row_id = rd.decision_event_id, same anchor).
    conn.execute(
        """INSERT INTO fact_revocations
           (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
           VALUES ('opportunity_fact', 'rde-evt-1', ?, NULL, ?, '{}')""",
        (REASON_NON_CONTRIBUTING, now),
    )
    conn.commit()

    report_after = build_evidence_report(
        "strat-B", 0, conn=conn, breakeven_win_rate=0.52
    )
    assert report_after.n_settled == 1, (
        f"Expected 1 settled after revocation, got {report_after.n_settled}"
    )


# ---------------------------------------------------------------------------
# Downstream exclusion: test_calibration_rebuild_excludes_revoked_pairs
# ---------------------------------------------------------------------------

def _make_platt_db() -> sqlite3.Connection:
    """In-memory DB with calibration_pairs + fact_revocations (co-located, owner-local)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE calibration_pairs (
            pair_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            snapshot_id INTEGER,
            p_raw REAL NOT NULL DEFAULT 0.5,
            outcome INTEGER NOT NULL DEFAULT 1,
            lead_days REAL NOT NULL DEFAULT 1.0,
            season TEXT NOT NULL DEFAULT 'winter',
            cluster TEXT NOT NULL DEFAULT 'C1',
            forecast_available_at TEXT NOT NULL DEFAULT '2026-05-22T00:00:00',
            decision_group_id TEXT NOT NULL DEFAULT 'dg-1',
            dataset_id TEXT NOT NULL DEFAULT 'v1',
            training_allowed INTEGER NOT NULL DEFAULT 1,
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            bin_source TEXT NOT NULL DEFAULT 'legacy',
            cycle TEXT NOT NULL DEFAULT '00',
            source_id TEXT NOT NULL DEFAULT 'tigge_mars',
            horizon_profile TEXT NOT NULL DEFAULT 'full',
            range_label TEXT NOT NULL DEFAULT '>=30',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(city, target_date, temperature_metric, range_label, lead_days,
                   forecast_available_at, bin_source, dataset_id)
        )
    """)
    ensure_table(conn)
    conn.commit()
    return conn


def test_calibration_rebuild_excludes_revoked_pairs():
    """_fetch_pairs_for_bucket excludes calibration_pairs rows tagged in fact_revocations."""
    import refit_platt as rp2

    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    conn = _make_platt_db()

    # Insert 2 training pairs for the same bucket.
    # Use data_version matching HIGH_LOCALDAY_MAX so the query doesn't filter them out.
    dv = HIGH_LOCALDAY_MAX.data_version
    for i in range(1, 3):
        conn.execute(
            """INSERT INTO calibration_pairs
               (city, target_date, temperature_metric, p_raw, outcome, lead_days,
                season, cluster, forecast_available_at, decision_group_id, dataset_id,
                range_label)
               VALUES ('Bangkok', ?, 'high', 0.6, 1, ?, 'winter', 'C1',
                       '2026-05-22T00:00:00', 'dg-1', ?, '>=30')""",
            (f"2026-05-2{i}", float(i), dv),
        )
    conn.commit()

    metric_id = HIGH_LOCALDAY_MAX

    # Both pairs returned before revocation.
    rows_before = rp2._fetch_pairs_for_bucket(
        conn, "C1", "winter", dv, "00", "tigge_mars", "full", metric_id
    )
    assert len(rows_before) == 2

    # Revoke pair_id=1 (first pair).
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO fact_revocations
           (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
           VALUES ('calibration_pairs', '1', ?, NULL, ?, '{}')""",
        (REASON_NON_CONTRIBUTING, now),
    )
    conn.commit()

    # Only 1 pair returned after revocation.
    rows_after = rp2._fetch_pairs_for_bucket(
        conn, "C1", "winter", dv, "00", "tigge_mars", "full", metric_id
    )
    assert len(rows_after) == 1, (
        f"Expected 1 pair after revocation, got {len(rows_after)}"
    )


# ---------------------------------------------------------------------------
# revoke_all_tables aggregation
# ---------------------------------------------------------------------------

@pytest.fixture()
def all_tables_db():
    """DB with enough tables to exercise revoke_all_tables_for_noncontributing_forecast."""
    conn = _make_db()
    # opportunity_fact
    conn.execute("""
        CREATE TABLE opportunity_fact (
            decision_id TEXT PRIMARY KEY,
            snapshot_id TEXT,
            should_trade INTEGER NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # calibration_pairs
    conn.execute("""
        CREATE TABLE calibration_pairs (
            pair_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL DEFAULT 'x',
            target_date TEXT NOT NULL DEFAULT '2026-05-22',
            temperature_metric TEXT NOT NULL DEFAULT 'high',
            snapshot_id INTEGER,
            p_raw REAL NOT NULL DEFAULT 0.5,
            outcome INTEGER NOT NULL DEFAULT 1,
            lead_days REAL NOT NULL DEFAULT 1.0,
            season TEXT NOT NULL DEFAULT 'winter',
            cluster TEXT NOT NULL DEFAULT 'C1',
            forecast_available_at TEXT NOT NULL DEFAULT '2026-05-22',
            decision_group_id TEXT NOT NULL DEFAULT 'dg1',
            dataset_id TEXT NOT NULL DEFAULT 'v1',
            training_allowed INTEGER NOT NULL DEFAULT 1,
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            bin_source TEXT NOT NULL DEFAULT 'legacy',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # probability_trace_fact
    conn.execute("""
        CREATE TABLE probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            decision_snapshot_id TEXT,
            trace_status TEXT NOT NULL DEFAULT 'complete',
            missing_reason_json TEXT NOT NULL DEFAULT '[]',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # selection_family_fact
    conn.execute("""
        CREATE TABLE selection_family_fact (
            family_id TEXT PRIMARY KEY,
            cycle_mode TEXT NOT NULL DEFAULT 'shadow',
            decision_snapshot_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            meta_json TEXT NOT NULL DEFAULT '{}'
        )
    """)
    # selection_hypothesis_fact
    conn.execute("""
        CREATE TABLE selection_hypothesis_fact (
            hypothesis_id TEXT PRIMARY KEY,
            family_id TEXT NOT NULL,
            city TEXT NOT NULL DEFAULT 'Bangkok',
            target_date TEXT NOT NULL DEFAULT '2026-05-22',
            range_label TEXT NOT NULL DEFAULT '>=30',
            direction TEXT NOT NULL DEFAULT 'buy_yes',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            meta_json TEXT NOT NULL DEFAULT '{}'
        )
    """)
    # decision_events
    conn.execute("""
        CREATE TABLE decision_events (
            market_slug TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            target_date TEXT NOT NULL,
            observation_time TEXT NOT NULL,
            decision_seq INTEGER NOT NULL,
            decision_event_id TEXT,
            strategy_key TEXT NOT NULL DEFAULT 'test',
            source TEXT NOT NULL DEFAULT 'live_decision',
            decision_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            outcome TEXT NOT NULL DEFAULT 'buy_yes',
            side TEXT NOT NULL DEFAULT 'buy',
            schema_version INTEGER NOT NULL DEFAULT 28,
            observation_available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit',
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def test_revoke_all_tables_aggregates(all_tables_db):
    """revoke_all_tables runs across all tables and sums counts."""
    snap_id = _snap(all_tables_db, contributes=0)

    # Insert one qualifying row per table.
    all_tables_db.execute(
        "INSERT INTO opportunity_fact (decision_id, snapshot_id) VALUES ('dec-all', ?)",
        (str(snap_id),),
    )
    all_tables_db.execute(
        "INSERT INTO calibration_pairs (snapshot_id) VALUES (?)",
        (snap_id,),
    )
    all_tables_db.execute(
        "INSERT INTO probability_trace_fact (trace_id, decision_id, decision_snapshot_id) "
        "VALUES ('tr-1', 'dec-tr-1', ?)",
        (str(snap_id),),
    )
    all_tables_db.execute(
        "INSERT INTO selection_family_fact (family_id, decision_snapshot_id) VALUES ('fam-1', ?)",
        (str(snap_id),),
    )
    all_tables_db.execute(
        "INSERT INTO selection_hypothesis_fact (hypothesis_id, family_id) VALUES ('hyp-1', 'fam-1')"
    )
    all_tables_db.execute(
        """INSERT INTO decision_events
           (market_slug, temperature_metric, target_date, observation_time, decision_seq,
            decision_event_id)
           VALUES ('mkt-A', 'high', '2026-05-22', '2026-05-22T00:00:00', 1, 'dec-all')"""
    )
    all_tables_db.commit()

    result = revoke_all_tables_for_noncontributing_forecast(all_tables_db)
    # 6 tables: opportunity_fact, calibration_pairs, probability_trace_fact,
    # selection_family_fact, selection_hypothesis_fact, decision_events.
    assert result["newly_revoked"] == 6
    assert result["per_table"]["opportunity_fact"]["newly_revoked"] == 1
    assert result["per_table"]["calibration_pairs"]["newly_revoked"] == 1
    assert result["per_table"]["probability_trace_fact"]["newly_revoked"] == 1
    assert result["per_table"]["selection_family_fact"]["newly_revoked"] == 1
    assert result["per_table"]["selection_hypothesis_fact"]["newly_revoked"] == 1
    assert result["per_table"]["decision_events"]["newly_revoked"] == 1


def test_revoke_all_tables_dry_run(all_tables_db):
    """revoke_all_tables with dry_run=True writes nothing."""
    snap_id = _snap(all_tables_db, contributes=0)
    all_tables_db.execute(
        "INSERT INTO opportunity_fact (decision_id, snapshot_id) VALUES ('dec-dry', ?)",
        (str(snap_id),),
    )
    all_tables_db.commit()

    result = revoke_all_tables_for_noncontributing_forecast(all_tables_db, dry_run=True)
    assert result["dry_run"] is True
    total = all_tables_db.execute(
        "SELECT COUNT(*) FROM fact_revocations"
    ).fetchone()[0]
    assert total == 0
