# Created: 2026-09-30
# Last audited: 2026-09-30
# Authority basis: position_events growth fix; src/state/day0_receipt_store.py.
"""Day0 monitor receipt witnesses are stored once, content-addressed."""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
from src.state.day0_receipt_store import REF_KEY, resolve_receipt
from tests.test_phase2_exit_emitter_revival import _make_position

METHOD = "day0_observation_conditioned_daily_extrema"


@pytest.fixture
def conn():
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema, init_schema_trade_only

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    init_schema_trade_only(c)
    init_collateral_schema(c)
    yield c
    c.close()


def _receipt(heavy_tag: str, obs_time: str) -> dict:
    return {
        "selected_method": METHOD,
        "metric": "high",
        "probability_content_identity": "pci-" + obs_time,
        "remaining_window": {"source": "day0_observed_bound_conditioned_daily_extrema"},
        "observation": {
            "observation_time": obs_time,
            "station_id": "ZGSZ",
            "statistical_probability_conditioning": {"blob": heavy_tag * 5000},
            "day0_causal_evidence_bundle": {"bundle": heavy_tag * 2000},
            "day0_remaining_vector_witness": {"vec": [heavy_tag] * 500},
        },
    }


def _write(conn, pos, seq: int, receipt: dict) -> dict:
    from src.state.db import append_many_and_project

    pos.selected_method = METHOD
    pos._day0_monitor_probability_receipt = receipt
    pos.last_monitor_at = f"2026-09-30T15:{seq:02d}:00+00:00"
    events, projection = build_monitor_refreshed_canonical_write(
        pos, sequence_no=seq, phase_after="active", source_module="tests"
    )
    append_many_and_project(conn, events, projection)
    return events[0]


def _row_payload(conn, seq: int) -> dict:
    row = conn.execute(
        "SELECT payload_json FROM position_events WHERE sequence_no = ?", (seq,)
    ).fetchone()
    return json.loads(row["payload_json"])


def test_writer_dedups_identical_witnesses_and_bounds_row_size(conn):
    pos = _make_position()
    _write(conn, pos, 10, _receipt("a", "2026-09-30T15:01:00+00:00"))
    _write(conn, pos, 11, _receipt("a", "2026-09-30T15:02:00+00:00"))
    _write(conn, pos, 12, _receipt("b", "2026-09-30T15:03:00+00:00"))

    assert conn.execute("SELECT COUNT(*) FROM day0_receipt_blob").fetchone()[0] == 2
    for seq in (10, 11, 12):
        size = conn.execute(
            "SELECT length(payload_json) FROM position_events WHERE sequence_no = ?",
            (seq,),
        ).fetchone()[0]
        assert size < 4_000, size  # was ~40 KB+ with the full witnesses inline
    blobs = conn.execute("SELECT length(payload) FROM day0_receipt_blob").fetchall()
    assert all(b[0] < 5_000 for b in blobs)  # zstd of repetitive JSON


def test_light_fields_stay_on_the_row_and_full_receipt_round_trips(conn):
    pos = _make_position()
    original = _receipt("a", "2026-09-30T15:01:00+00:00")
    _write(conn, pos, 10, json.loads(json.dumps(original)))

    slim = _row_payload(conn, 10)["day0_monitor_probability_receipt"]
    assert slim["selected_method"] == METHOD
    assert slim["remaining_window"]["source"].startswith("day0_observed_bound")
    assert slim["probability_content_identity"] == original["probability_content_identity"]
    assert slim["observation"]["observation_time"] == "2026-09-30T15:01:00+00:00"
    assert "day0_causal_evidence_bundle" not in slim["observation"]
    assert len(slim["observation"][REF_KEY]) == 64

    assert resolve_receipt(conn, slim) == original


def test_unresolvable_hash_raises_and_legacy_receipt_passes_through(conn):
    legacy = _receipt("a", "t")
    assert resolve_receipt(conn, legacy) is legacy
    dangling = {"observation": {REF_KEY: "0" * 64}}
    with pytest.raises(LookupError):
        resolve_receipt(conn, dangling)


def test_events_without_a_day0_receipt_are_untouched(conn):
    pos = _make_position()
    pos.selected_method = "other"
    from src.state.db import append_many_and_project

    events, projection = build_monitor_refreshed_canonical_write(
        pos, sequence_no=10, phase_after="active", source_module="tests"
    )
    before = events[0]["payload_json"]
    append_many_and_project(conn, events, projection)
    assert _row_payload(conn, 10) == json.loads(before)
    assert conn.execute("SELECT COUNT(*) FROM day0_receipt_blob").fetchone()[0] == 0
