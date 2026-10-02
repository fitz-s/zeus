# Created: 2026-10-02
# Last reused/audited: 2026-10-02
# Authority basis: Day0 law H = max(H_confirmed, H_remaining) (60f1f591b, c27684d0c); the
#   remaining-window read is keyed on the LAST OBSERVATION, never the decision clock
#   (day0 causal cut replay 2026-09-06). Live 2026-10-02: held Chongqing 10-02 lost every
#   post-midnight run at 16:00Z (local day end) while its last observation was 15:05Z.
"""A stored body's local-day coverage proof is re-parsed at the family's last observation."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.data import replacement_current_value_serving as serving
from src.data.bayes_precision_fusion_download import _parse_batched_single_runs_payload

TZ = "Asia/Shanghai"  # UTC+8: 2026-10-02 local day is [10-01T16Z, 10-02T16Z)
DAY = date(2026, 10, 2)
TAU = "2026-10-02T15:05:56+00:00"


def _payload(first_utc_hour: int) -> dict:
    """An hourly body whose first sample is ``first_utc_hour`` UTC on 10-02 (a 06Z run sees 14:00 local)."""
    start = datetime(2026, 10, 2, first_utc_hour, tzinfo=timezone.utc)
    times = [(start + timedelta(hours=h)).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M")
             for h in range(0, 30)]
    return {"utc_offset_seconds": 28800, "timezone": TZ, "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": times, "temperature_2m": [20.0 + (h % 5) for h in range(30)]}}


def _row(cut: str, tau: str | None) -> dict:
    row = {"physical_proof_cutoff": cut, "captured_at": "2026-10-02T12:32:04+00:00",
           "timezone_requested": TZ, "target_date": DAY.isoformat()}
    if tau is not None:
        row["day0_remaining_from"] = tau
    return row


def _covers(row: dict, payload: dict) -> bool:
    values = _parse_batched_single_runs_payload(payload, ["ecmwf_ifs"], DAY, TZ,
                                                decision_at=serving._remaining_window_boundary(row))
    return "ecmwf_ifs" in values


def test_post_day_run_issued_after_midnight_stays_proven_at_its_last_observation():
    # 06Z ecmwf/ukmo body: first slot 06Z = 14:00 local, so it owns [15Z, 16Z) at tau=15:05Z.
    body = _payload(6)
    assert _covers(_row("2026-10-02T16:30:00+00:00", TAU), body)
    assert serving._remaining_window_boundary(_row("2026-10-02T16:30:00+00:00", TAU)) == TAU


@pytest.mark.parametrize("tau", [None, "2026-10-02T16:00:00+00:00", "2026-10-02T16:20:00+00:00"])
def test_without_an_in_day_tau_the_whole_day_law_still_rejects_the_partial_run(tau):
    body = _payload(6)
    row = _row("2026-10-02T16:30:00+00:00", tau)
    assert serving._remaining_window_boundary(row) == "2026-10-02T16:30:00+00:00"
    assert not _covers(row, body)


def test_tau_after_the_cut_or_unparseable_is_never_admitted():
    assert serving._remaining_window_boundary(_row("2026-10-02T16:30:00+00:00", "2026-10-02T17:00:00+00:00")) \
        == "2026-10-02T16:30:00+00:00"
    assert serving._remaining_window_boundary(_row("2026-10-02T16:30:00+00:00", "not-a-time")) \
        == "2026-10-02T16:30:00+00:00"
    assert serving._remaining_window_boundary(_row("2026-10-02T16:30:00+00:00", "2026-10-02T15:05:56")) \
        == "2026-10-02T16:30:00+00:00"


@pytest.mark.parametrize("cut", ["2026-10-02T15:30:00+00:00", "2026-10-01T10:00:00+00:00"])
def test_pre_day_end_boundary_is_the_decision_cut_whatever_tau_is(cut):
    for tau in (None, TAU, "2026-10-02T09:00:00+00:00"):
        assert serving._remaining_window_boundary(_row(cut, tau)) == cut


def test_identity_text_without_tau_is_byte_for_byte_unchanged():
    schema = serving.CurrentValueServingSchema(
        has_captured_at=True, has_source_available_at=True, has_recorded_at=True, has_coverage_status=True,
        product_identity_columns=serving._PRODUCT_IDENTITY_COLUMNS, has_artifacts=True)
    base = serving._product_identity_select(schema, decision_iso="2026-10-02T16:30:00+00:00")
    assert serving._product_identity_select(schema, decision_iso="2026-10-02T16:30:00+00:00",
                                            day0_remaining_from_iso=None) == base
    assert "day0_remaining_from" not in base
    with_tau = serving._product_identity_select(schema, decision_iso="2026-10-02T16:30:00+00:00",
                                                day0_remaining_from_iso=TAU)
    assert f"'day0_remaining_from', '{TAU}'" in with_tau


def test_the_proof_reparse_reads_the_boundary_helper():
    """Wiring: the coverage re-parse consumes the boundary, not the raw cut."""
    import inspect
    source = inspect.getsource(serving._physical_response_has_authority)
    assert "decision_at=_remaining_window_boundary(row)" in source
    assert 'decision_at=str(row.get("physical_proof_cutoff")' not in source


def test_frozen_identity_carries_tau_only_when_supplied():
    """The commit/held replay re-proves the same remaining window the selector proved."""
    import inspect
    source = inspect.getsource(serving._physical_response_provenance)
    assert 'original_identity["day0_remaining_from"] = row["day0_remaining_from"]' in source
    assert 'if row.get("day0_remaining_from") is not None' in source


def test_every_bpf_current_read_in_the_materializer_names_the_family_tau():
    import inspect
    from src.data import replacement_forecast_materializer as m
    source = inspect.getsource(m._replacement_bayes_precision_fusion_override)
    import re
    reads = re.findall(r"read_(?:current_instrument_values|freshest_coherent_instrument_values)\((.*?)\n\s*\)",
                       source, flags=re.S)
    assert len(reads) == 3
    assert all("day0_remaining_from_iso=_day0_remaining_from_iso(request)" in call for call in reads)
