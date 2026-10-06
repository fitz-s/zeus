# Created: 2026-10-06
# Last reused/audited: 2026-10-06
# Authority basis: live seed-transport profile 2026-10-06 (284 seed-only runs,
#   ~2.8 s each on the saturated priority lane; ~1.1 s per READY seed in the
#   blocked-attempt fingerprint with no marker present).
"""A missing blocked marker never needs the attempt fingerprint for the unchanged verdict."""
from __future__ import annotations

import json
from pathlib import Path

import src.data.replacement_forecast_live_materialization_queue as queue

PAYLOAD = {"city": "Seoul", "target_date": "2026-10-06", "temperature_metric": "high"}


def _count_fingerprints(monkeypatch, value="fp-1"):
    calls = []

    def fingerprint(**kwargs):
        calls.append(kwargs)
        return value

    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", fingerprint)
    monkeypatch.setattr(queue, "_blocked_evidence_holds", lambda *_a, **_k: True)
    return calls


def _state(tmp_path: Path, **kwargs):
    return queue._blocked_attempt_state(
        marker_dir=tmp_path / "blocked_attempts",
        input_json=tmp_path / "requests" / "r.json",
        payload=PAYLOAD,
        forecast_db=None,
        **kwargs,
    )


def test_markerless_skip_returns_unchanged_false_without_fingerprint(tmp_path, monkeypatch):
    calls = _count_fingerprints(monkeypatch)

    marker_path, fingerprint, unchanged = _state(tmp_path, markerless_unchanged_only=True)

    assert calls == []
    assert fingerprint is None
    assert unchanged is False
    assert marker_path == queue._blocked_attempt_marker_path(tmp_path / "blocked_attempts", PAYLOAD)


def test_default_still_computes_fingerprint_without_marker(tmp_path, monkeypatch):
    """Callers that write the first marker after a BLOCKED attempt need it."""
    calls = _count_fingerprints(monkeypatch)

    _marker_path, fingerprint, unchanged = _state(tmp_path)

    assert len(calls) == 1
    assert fingerprint == "fp-1"
    assert unchanged is False


def test_existing_marker_keeps_the_exact_unchanged_verdict(tmp_path, monkeypatch):
    calls = _count_fingerprints(monkeypatch)
    marker = queue._blocked_attempt_marker_path(tmp_path / "blocked_attempts", PAYLOAD)
    marker.parent.mkdir(parents=True)

    marker.write_text(json.dumps({"attempt_fingerprint": "fp-1"}), encoding="utf-8")
    assert _state(tmp_path, markerless_unchanged_only=True) == (marker, "fp-1", True)

    marker.write_text(json.dumps({"attempt_fingerprint": "other"}), encoding="utf-8")
    assert _state(tmp_path, markerless_unchanged_only=True) == (marker, "fp-1", False)
    assert len(calls) == 2
