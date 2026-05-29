# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: P2_LEDGER_SEAM_FINDINGS_2026-05-29 §"Exact wiring plan" steps 1-3;
#   residual_value.residual_celsius + residual_key.source_kind_for_data_version antibodies.
"""Relationship tests for _strict_evidence_row.

These tests verify the cross-module invariants BEFORE the pure-function extraction is
implemented (RED → GREEN TDD).

Invariants tested:
  (a) opendata data_version row -> source_kind == "opendata_live", residual_c is correct
  (b) MIXED-unit row (members_unit degC, settlement degF) -> residual is unit-correct,
      NOT off by ~50C (the masked D-U1 bug)
  (c) No produced row has source_kind == "prior" (the hardcoded lineage collapse the
      source_kind_for_data_version antibody prevents)
"""

from __future__ import annotations

import pytest

from scripts.build_ens_residual_evidence import _strict_evidence_row


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_e(
    *,
    city: str = "Chicago",
    target_date: str = "2025-08-01",
    snapshot_id: str = "snap-1",
    settlement_id: str = "settle-1",
    issue_time: str = "2025-07-30T00:00:00",
    cycle: str = "00",
    lead_hours: float = 48.0,
    available_at: str = "2025-07-30T01:00:00",
    members_json: str = "[72.0, 74.0, 76.0]",
    members_unit: str = "degF",
    data_version: str = "ecmwf_opendata_mx2t3_v1",
    contributes_to_target_extrema: int = 1,
    boundary_ambiguous: int = 0,
    settlement_value_c: float = 77.0,
    settlement_unit: str = "degF",
) -> dict:
    return {
        "city": city,
        "target_date": target_date,
        "snapshot_id": snapshot_id,
        "settlement_id": settlement_id,
        "issue_time": issue_time,
        "cycle": cycle,
        "lead_hours": lead_hours,
        "available_at": available_at,
        "members_json": members_json,
        "members_unit": members_unit,
        "data_version": data_version,
        "contributes_to_target_extrema": contributes_to_target_extrema,
        "boundary_ambiguous": boundary_ambiguous,
        "settlement_value_c": settlement_value_c,
        "settlement_unit": settlement_unit,
    }


_CHICAGO_LAT = {"Chicago": 41.85}
_METRIC = "high"


# ---------------------------------------------------------------------------
# (a) opendata row -> source_kind == "opendata_live", residual correct
# ---------------------------------------------------------------------------

def test_opendata_source_kind_is_opendata_live():
    e = _make_e(data_version="ecmwf_opendata_mx2t3_v1", members_unit="degF",
                settlement_value_c=77.0, settlement_unit="degF")
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is not None
    assert row["source_kind"] == "opendata_live", (
        f"expected 'opendata_live' but got {row['source_kind']!r}"
    )


def test_opendata_residual_c_is_correct():
    # members [72, 74, 76] degF -> mean 74 degF -> (74-32)*5/9 = 23.333 C
    # settlement 77 degF -> (77-32)*5/9 = 25.0 C
    # residual = 23.333 - 25.0 = -1.667 C
    e = _make_e(members_json="[72.0, 74.0, 76.0]", members_unit="degF",
                settlement_value_c=77.0, settlement_unit="degF",
                data_version="ecmwf_opendata_mx2t3_v1")
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is not None
    expected = (74.0 - 32.0) * 5.0 / 9.0 - (77.0 - 32.0) * 5.0 / 9.0
    assert abs(row["residual_c"] - round(expected, 3)) < 0.01, (
        f"expected residual_c ~ {expected:.3f} but got {row['residual_c']}"
    )


# ---------------------------------------------------------------------------
# (a2) CANONICAL settlement_unit vocabulary {'F','C'} (the DB CHECK vocab).
# The DB columns store settlement_unit as 'F'/'C' (not degF/degC). residual_celsius
# must accept that vocabulary on the settlement side or the evidence path crashes on
# every row whose settlement_unit comes from the canonical column. This is the RED
# anchor for the residual_value {F,C}-vocab fix — pre-fix it raised ResidualUnitError.
# ---------------------------------------------------------------------------

def test_settlement_unit_canonical_F_vocab_residual_correct():
    # members [72,74,76] F -> mean 74F -> 23.333C ; settlement 77 'F' -> 25.0C
    # residual = -1.667C. Settlement unit is the canonical 'F' (DB CHECK vocab), NOT 'degF'.
    e = _make_e(members_json="[72.0, 74.0, 76.0]", members_unit="F",
                settlement_value_c=77.0, settlement_unit="F",
                data_version="ecmwf_opendata_mx2t3_v1")
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is not None
    expected = (74.0 - 32.0) * 5.0 / 9.0 - (77.0 - 32.0) * 5.0 / 9.0
    assert abs(row["residual_c"] - round(expected, 3)) < 0.01, (
        f"canonical 'F' settlement vocab must produce residual ~{expected:.3f}, "
        f"got {row['residual_c']} (residual_value must accept {{F,C}}, not only degF/degC)"
    )


def test_mixed_canonical_vocab_C_members_F_settlement():
    # members [22,24] 'C' -> 23C ; settlement 73.4 'F' -> 23C ; residual ~0C.
    # Both canonical-vocab AND mixed-unit: proves each side converts by its own unit.
    e = _make_e(members_json="[22.0, 24.0]", members_unit="C",
                settlement_value_c=73.4, settlement_unit="F",
                data_version="ecmwf_opendata_mx2t3_v1")
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is not None
    assert abs(row["residual_c"]) < 1.0, (
        f"residual_c={row['residual_c']!r} — canonical mixed vocab (C members / F "
        f"settlement) must be unit-correct, not the 50C corruption"
    )


# ---------------------------------------------------------------------------
# (b) MIXED-unit row: members_unit degC, settlement_unit degF -> NOT off by ~50C
# ---------------------------------------------------------------------------

def test_mixed_unit_residual_is_unit_correct_not_50c_off():
    """The D-U1 masked bug: legacy code converts settlement with members_unit (degC),
    leaving the settlement value as-is (~73.4) instead of converting from degF.
    That yields a ~50C error. The antibody-wired function must NOT reproduce this.

    members [22, 24] degC -> mean 23 C
    settlement 73.4 degF -> 23 C
    correct residual ~0.0 C
    legacy-wrong residual = 23 - 73.4 = -50.4 C
    """
    e = _make_e(members_json="[22.0, 24.0]", members_unit="degC",
                settlement_value_c=73.4, settlement_unit="degF",
                data_version="ecmwf_opendata_mx2t3_v1")
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is not None
    assert abs(row["residual_c"]) < 1.0, (
        f"residual_c={row['residual_c']!r} — looks like the legacy 50C corruption "
        f"(members_unit used for settlement conversion instead of settlement_unit)"
    )


# ---------------------------------------------------------------------------
# (c) No produced row has source_kind == "prior"
# ---------------------------------------------------------------------------

def test_tigge_row_source_kind_is_not_prior():
    e = _make_e(data_version="tigge_mx2t3_ecmwf_v1",
                members_unit="degF", settlement_value_c=77.0, settlement_unit="degF")
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is not None
    assert row["source_kind"] != "prior", (
        "source_kind must not be the hardcoded literal 'prior' — "
        "that collapses TIGGE and OpenData lineage"
    )
    assert row["source_kind"] == "tigge_prior"


def test_opendata_row_source_kind_is_not_prior():
    e = _make_e(data_version="ecmwf_opendata_mx2t3_v1",
                members_unit="degF", settlement_value_c=77.0, settlement_unit="degF")
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is not None
    assert row["source_kind"] != "prior"


# ---------------------------------------------------------------------------
# Additional structural checks
# ---------------------------------------------------------------------------

def test_settlement_unit_field_present_in_row():
    e = _make_e(settlement_unit="degF")
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is not None
    assert "settlement_unit" in row
    assert row["settlement_unit"] == "degF"


def test_none_members_json_returns_none():
    e = _make_e(members_json="null")
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is None, "None ensemble mean should produce None row"


def test_unknown_data_version_raises():
    e = _make_e(data_version="unknown_source_v1",
                members_unit="degF", settlement_value_c=77.0, settlement_unit="degF")
    with pytest.raises(ValueError, match="source_kind refused"):
        _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)


# ---------------------------------------------------------------------------
# (d) Forecast-window provenance fields propagated to ledger row (C1b)
# ---------------------------------------------------------------------------

def _make_e_with_provenance(**kwargs) -> dict:
    """Make a dict that includes forecast-window provenance fields."""
    base = _make_e(**kwargs)
    base["forecast_window_start_utc"] = "2025-07-31T06:00:00"
    base["forecast_window_end_utc"] = "2025-08-01T06:00:00"
    base["source_run_id"] = "ecmwf-run-abc123"
    # available_at already present from _make_e
    return base


def test_provenance_fields_present_in_output_row():
    """C1b: forecast_window_start/end_utc, source_run_id, available_at must appear
    in the strict ledger output row."""
    e = _make_e_with_provenance()
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is not None
    assert row["forecast_window_start_utc"] == "2025-07-31T06:00:00"
    assert row["forecast_window_end_utc"] == "2025-08-01T06:00:00"
    assert row["source_run_id"] == "ecmwf-run-abc123"
    assert row["available_at"] == "2025-07-30T01:00:00"


def test_missing_source_run_id_yields_none_not_keyerror():
    """C1b: legacy rows without source_run_id must yield None, not KeyError."""
    e = _make_e()
    # deliberately omit source_run_id (not in base fixture)
    assert "source_run_id" not in e
    row = _strict_evidence_row(e, metric=_METRIC, lat=_CHICAGO_LAT)
    assert row is not None
    assert row["source_run_id"] is None
