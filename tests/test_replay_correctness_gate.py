# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN Phase 0.G; ADR-5; RISK_REGISTER R2
"""Seeded regression test for the replay-correctness gate (Phase 0.G).

Injects a deliberate mismatch into a temp DB copy and verifies:
  1. Gate returns 0 on clean match.
  2. Gate returns non-zero when projection differs from baseline.
  3. ritual_signal line is emitted on each invocation.

The DB is opened read-only by the gate; the test uses copy_db_readonly_temp()
to make a mutable copy for the mismatch injection.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Import the module under test.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.replay_correctness_gate import (  # noqa: E402
    BASELINE_DIR,
    NON_DETERMINISTIC_EVENT_TYPES,
    RITUAL_SIGNAL_DIR,
    SEED_WINDOW_DAYS,
    compare,
    copy_db_readonly_temp,
    extract_seed_events,
    main,
    write_baseline,
    _compute_projection,
)

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DB = ROOT / "state" / "zeus_trades.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_baseline(tmp_path: Path, projection: dict) -> Path:
    """Write a baseline JSON for the given projection into tmp_path."""
    bdir = tmp_path / "replay_baseline"
    bdir.mkdir(parents=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bfile = bdir / f"{today}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "0.1.0",
        "projection": projection,
        "non_deterministic_excluded": sorted(NON_DETERMINISTIC_EVENT_TYPES),
    }
    bfile.write_text(json.dumps(payload))
    return bdir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not CANONICAL_DB.exists(),
    reason="zeus_trades.db not present in this environment",
)
def test_gate_clean_match(tmp_path, monkeypatch):
    """Gate returns 0 when projection matches the baseline it was bootstrapped from."""
    # Extract real events and build a projection.
    events, excluded = extract_seed_events(CANONICAL_DB)
    projection = _compute_projection(events)

    # Write a baseline matching that projection.
    bdir = _make_temp_baseline(tmp_path, projection)
    signal_dir = tmp_path / "ritual_signal"
    signal_dir.mkdir()

    monkeypatch.setattr("scripts.replay_correctness_gate.BASELINE_DIR", bdir)
    monkeypatch.setattr("scripts.replay_correctness_gate.RITUAL_SIGNAL_DIR", signal_dir)

    rc = main(["--db", str(CANONICAL_DB)])
    assert rc == 0, "gate should return 0 on clean match"


@pytest.mark.skipif(
    not CANONICAL_DB.exists(),
    reason="zeus_trades.db not present in this environment",
)
def test_gate_mismatch_returns_nonzero(tmp_path, monkeypatch):
    """Gate returns non-zero when the DB has been tampered (deliberate mismatch).

    Injects a fake opportunity_fact row into a temp DB copy, then compares
    that against the baseline derived from the original DB. The extra row
    changes the content_hash, so the gate must detect the mismatch.
    """
    # --- Baseline: projection from the ORIGINAL DB ---
    events_orig, excluded = extract_seed_events(CANONICAL_DB)
    projection_orig = _compute_projection(events_orig)
    bdir = _make_temp_baseline(tmp_path, projection_orig)
    signal_dir = tmp_path / "ritual_signal"
    signal_dir.mkdir()

    monkeypatch.setattr("scripts.replay_correctness_gate.BASELINE_DIR", bdir)
    monkeypatch.setattr("scripts.replay_correctness_gate.RITUAL_SIGNAL_DIR", signal_dir)

    # --- Mismatch: copy DB, inject a fake row ---
    tampered_db = copy_db_readonly_temp(CANONICAL_DB)
    try:
        conn = sqlite3.connect(str(tampered_db))
        try:
            # Inject a synthetic opportunity_fact row with a future timestamp
            # so it falls inside the 7-day seed window.
            inject_ts = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO opportunity_fact
                  (decision_id, candidate_id, city, target_date, range_label,
                   direction, strategy_key, discovery_mode, entry_method,
                   snapshot_id, p_raw, p_cal, p_market, alpha, best_edge,
                   ci_width, rejection_stage, rejection_reason_json,
                   availability_status, should_trade, recorded_at)
                VALUES
                  ('REPLAY_TEST_INJECTED', 'FAKE_CANDIDATE', 'TestCity',
                   '2999-01-01', 'above', 'buy_yes', 'settlement_capture',
                   'test', 'test', NULL,
                   0.5, 0.5, 0.5, 0.05, 0.05,
                   0.1, NULL, NULL,
                   'ok', 0, ?)
                """,
                (inject_ts,),
            )
            conn.commit()
        finally:
            conn.close()

        # Gate should see more events → different hash → non-zero exit.
        rc = main(["--db", str(tampered_db)])
        assert rc != 0, (
            "gate must return non-zero when projection differs from baseline"
        )

        # Confirm the diff is recorded in output (gate prints JSON to stdout).
        # The ritual_signal should have outcome=blocked.
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")
        signal_file = signal_dir / f"{month_key}.jsonl"
        assert signal_file.exists(), "ritual_signal file must be created"
        lines = [json.loads(l) for l in signal_file.read_text().splitlines() if l.strip()]
        # At least one record should be a mismatch outcome.
        outcomes = [l["outcome"] for l in lines]
        assert "blocked" in outcomes, f"expected 'blocked' in ritual_signal outcomes, got {outcomes}"

    finally:
        tampered_db.unlink(missing_ok=True)


@pytest.mark.skipif(
    not CANONICAL_DB.exists(),
    reason="zeus_trades.db not present in this environment",
)
def test_bootstrap_writes_valid_baseline(tmp_path, monkeypatch):
    """--bootstrap creates a valid JSON baseline and returns 0."""
    bdir = tmp_path / "replay_baseline"
    bdir.mkdir()
    signal_dir = tmp_path / "ritual_signal"
    signal_dir.mkdir()

    monkeypatch.setattr("scripts.replay_correctness_gate.BASELINE_DIR", bdir)
    monkeypatch.setattr("scripts.replay_correctness_gate.RITUAL_SIGNAL_DIR", signal_dir)

    rc = main(["--db", str(CANONICAL_DB), "--bootstrap"])
    assert rc == 0

    files = list(bdir.glob("*.json"))
    assert len(files) == 1, "exactly one baseline file should be written"
    payload = json.loads(files[0].read_text())
    assert "projection" in payload
    assert "content_hash" in payload["projection"]
    assert "event_count" in payload["projection"]
    assert payload["projection"]["event_count"] >= 0


def test_compare_match():
    """compare() returns True when projections are identical."""
    proj = {
        "event_count": 10,
        "content_hash": "abc123",
        "counts_by_type": {"a::b": 10},
        "seed_window_days": 7,
        "seed_cutoff_utc": "2026-04-29T00:00:00+00:00",
    }
    baseline = {"projection": proj}
    matched, diff = compare(proj, baseline)
    assert matched is True
    assert diff == {}


def test_compare_mismatch_hash():
    """compare() returns False when content_hash differs."""
    proj = {
        "event_count": 10,
        "content_hash": "abc123",
        "counts_by_type": {"a::b": 10},
        "seed_window_days": 7,
        "seed_cutoff_utc": "2026-04-29T00:00:00+00:00",
    }
    baseline = {
        "projection": {
            **proj,
            "content_hash": "different_hash",
        }
    }
    matched, diff = compare(proj, baseline)
    assert matched is False
    assert "content_hash" in diff


def test_non_deterministic_types_excluded():
    """NON_DETERMINISTIC_EVENT_TYPES contains the R2-mitigation exclusions."""
    required = {"model_response", "model_call", "web_fetch", "http_fetch"}
    assert required.issubset(NON_DETERMINISTIC_EVENT_TYPES), (
        f"R2 mitigation: expected {required} in exclusion set"
    )


def test_gate_missing_db(tmp_path, monkeypatch):
    """Gate returns 2 (not 0 or 1) when DB path does not exist."""
    signal_dir = tmp_path / "ritual_signal"
    signal_dir.mkdir()
    monkeypatch.setattr("scripts.replay_correctness_gate.RITUAL_SIGNAL_DIR", signal_dir)
    rc = main(["--db", str(tmp_path / "nonexistent.db")])
    assert rc == 2


def test_gate_no_baseline(tmp_path, monkeypatch):
    """Gate returns 2 when no baseline exists and --bootstrap not passed."""
    bdir = tmp_path / "replay_baseline"
    bdir.mkdir()
    signal_dir = tmp_path / "ritual_signal"
    signal_dir.mkdir()
    monkeypatch.setattr("scripts.replay_correctness_gate.BASELINE_DIR", bdir)
    monkeypatch.setattr("scripts.replay_correctness_gate.RITUAL_SIGNAL_DIR", signal_dir)

    # Use any existing DB (even empty will work for this path).
    if not CANONICAL_DB.exists():
        pytest.skip("canonical DB not present")
    rc = main(["--db", str(CANONICAL_DB)])
    assert rc == 2
