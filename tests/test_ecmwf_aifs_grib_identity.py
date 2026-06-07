# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect AIFS ENS GRIB identity proof for sampled-2t replacement shadow artifacts.
# Reuse: Run before trusting downloaded AIFS GRIB files as 51-member sampled-2t posterior inputs.
# Authority basis: ECMWF AIFS ENS sampled-2t shadow integration; not B0 calibration authority.
"""AIFS ENS GRIB identity scanner tests."""

from __future__ import annotations

from src.data.ecmwf_aifs_grib_identity import (
    PRODUCT_ID,
    SOURCE_ID,
    scan_aifs_ens_grib_identity,
)


def _messages(*, steps=(0, 6), **overrides):
    rows = []
    for step in steps:
        base = {
            "class": "ai",
            "stream": "enfo",
            "model": "aifs-ens",
            "shortName": "2t",
            "paramId": 167,
            "levtype": "sfc",
            "step": step,
        }
        rows.append({**base, "type": "cf", **overrides})
        for member in range(1, 51):
            rows.append({**base, "type": "pf", "number": member, **overrides})
    return rows


def test_aifs_grib_identity_accepts_ai_aifs_ens_2t_cf_pf_members() -> None:
    decision = scan_aifs_ens_grib_identity(_messages())

    assert decision.valid is True
    assert decision.reason_codes == ("AIFS_GRIB_IDENTITY_VALID",)
    assert decision.source_id == SOURCE_ID
    assert decision.product_id == PRODUCT_ID
    assert decision.expected_members == 51
    assert decision.step_hours == (0, 6)
    assert len(decision.member_ids) == 51
    assert "control" in decision.member_ids
    assert "pf:50" in decision.member_ids
    assert decision.trade_authority_status == "SHADOW_ONLY"
    assert decision.training_allowed is False


def test_aifs_grib_identity_blocks_wrong_class_model_stream_or_type() -> None:
    decision = scan_aifs_ens_grib_identity(
        _messages(**{"class": "od", "model": "ifs", "stream": "oper", "type": "fc"})
    )

    assert decision.valid is False
    assert "AIFS_GRIB_CLASS_MISMATCH" in decision.reason_codes
    assert "AIFS_GRIB_STREAM_MISMATCH" in decision.reason_codes
    assert "AIFS_GRIB_MODEL_MISMATCH" in decision.reason_codes
    assert "AIFS_GRIB_TYPE_MISMATCH" in decision.reason_codes


def test_aifs_grib_identity_rejects_waef_stream_without_explicit_product_proof() -> None:
    decision = scan_aifs_ens_grib_identity(_messages(stream="waef"))

    assert decision.valid is False
    assert "AIFS_GRIB_STREAM_MISMATCH" in decision.reason_codes


def test_aifs_grib_identity_blocks_wrong_param_levtype_or_step_grid() -> None:
    decision = scan_aifs_ens_grib_identity(_messages(**{"shortName": "mx2t3", "paramId": 201, "levtype": "pl", "step": 3}))

    assert decision.valid is False
    assert "AIFS_GRIB_PARAM_MISMATCH" in decision.reason_codes
    assert "AIFS_GRIB_LEVTYPE_MISMATCH" in decision.reason_codes
    assert "AIFS_GRIB_STEP_GRID_MISMATCH" in decision.reason_codes


def test_aifs_grib_identity_blocks_missing_control_or_partial_members() -> None:
    rows = [row for row in _messages(steps=(0,)) if not (row["type"] == "cf" or row.get("number") == 50)]

    decision = scan_aifs_ens_grib_identity(rows)

    assert decision.valid is False
    assert "AIFS_GRIB_CONTROL_MEMBER_MISSING_OR_DUPLICATED" in decision.reason_codes
    assert "AIFS_GRIB_PERTURBED_MEMBER_COUNT_MISMATCH" in decision.reason_codes
    assert "AIFS_GRIB_TOTAL_MEMBER_COUNT_MISMATCH" in decision.reason_codes


def test_aifs_grib_identity_uses_full_product_names_not_transcript_shorthand() -> None:
    for identifier in (SOURCE_ID, PRODUCT_ID):
        assert ("h" + "3") not in identifier.lower()
