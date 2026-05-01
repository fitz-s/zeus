# Created: 2026-04-30
# Last reused/audited: 2026-04-30
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §2.4 + §6 antibody #9
"""Antibody #9: IngestionGuard provenance contract tests.

Asserts:
1. ProvenanceGuard rejects rows missing source, authority, data_version, or provenance_json.
2. ProvenanceGuard rejects rows with invalid authority values.
3. ProvenanceGuard rejects rows with provenance_json missing required keys.
4. Legacy read-time tolerance works: absent provenance tagged legacy_v0/UNVERIFIED.
5. Valid full provenance passes without raising.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("ZEUS_MODE", "live")

from src.data.ingestion_guard import (
    ProvenanceGuard,
    ProvenanceViolation,
    LEGACY_V0_DATA_VERSION,
    LEGACY_V0_AUTHORITY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_PROVENANCE = {
    "request_url": "https://api.weather.com/v1/location/EGLL/observations.json",
    "fetched_at": "2026-04-30T12:00:00Z",
    "parser_version": "wu_daily_v2.1",
}


def _valid_write_kwargs(**overrides) -> dict:
    base = {
        "source": "wu_icao",
        "authority": "VERIFIED",
        "data_version": "wu_daily_v2",
        "provenance_json": VALID_PROVENANCE.copy(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: valid provenance passes
# ---------------------------------------------------------------------------

class TestValidProvenancePasses:
    def test_full_valid_provenance_does_not_raise(self):
        guard = ProvenanceGuard()
        guard.validate_write(**_valid_write_kwargs())  # must not raise

    def test_unverified_authority_is_valid(self):
        guard = ProvenanceGuard()
        guard.validate_write(**_valid_write_kwargs(authority="UNVERIFIED"))

    def test_quarantined_authority_is_valid(self):
        guard = ProvenanceGuard()
        guard.validate_write(**_valid_write_kwargs(authority="QUARANTINED"))

    def test_extra_provenance_keys_allowed(self):
        guard = ProvenanceGuard()
        prov = {**VALID_PROVENANCE, "extra_key": "extra_value", "batch_id": "abc123"}
        guard.validate_write(**_valid_write_kwargs(provenance_json=prov))


# ---------------------------------------------------------------------------
# Tests: missing source rejected
# ---------------------------------------------------------------------------

class TestMissingSourceRejected:
    def test_empty_source_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation, match="source"):
            guard.validate_write(**_valid_write_kwargs(source=""))

    def test_none_source_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation, match="source"):
            guard.validate_write(**_valid_write_kwargs(source=None))  # type: ignore[arg-type]

    def test_whitespace_only_source_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation, match="source"):
            guard.validate_write(**_valid_write_kwargs(source="   "))


# ---------------------------------------------------------------------------
# Tests: invalid authority rejected
# ---------------------------------------------------------------------------

class TestInvalidAuthorityRejected:
    def test_unknown_authority_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation, match="authority"):
            guard.validate_write(**_valid_write_kwargs(authority="TRUSTED"))

    def test_lowercase_authority_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation, match="authority"):
            guard.validate_write(**_valid_write_kwargs(authority="verified"))

    def test_empty_authority_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation, match="authority"):
            guard.validate_write(**_valid_write_kwargs(authority=""))


# ---------------------------------------------------------------------------
# Tests: missing data_version rejected
# ---------------------------------------------------------------------------

class TestMissingDataVersionRejected:
    def test_empty_data_version_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation, match="data_version"):
            guard.validate_write(**_valid_write_kwargs(data_version=""))

    def test_none_data_version_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation, match="data_version"):
            guard.validate_write(**_valid_write_kwargs(data_version=None))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests: provenance_json missing required keys rejected
# ---------------------------------------------------------------------------

class TestProvenanceJsonMissingKeys:
    def test_missing_request_url_raises(self):
        guard = ProvenanceGuard()
        prov = {k: v for k, v in VALID_PROVENANCE.items() if k != "request_url"}
        with pytest.raises(ProvenanceViolation, match="request_url"):
            guard.validate_write(**_valid_write_kwargs(provenance_json=prov))

    def test_missing_fetched_at_raises(self):
        guard = ProvenanceGuard()
        prov = {k: v for k, v in VALID_PROVENANCE.items() if k != "fetched_at"}
        with pytest.raises(ProvenanceViolation, match="fetched_at"):
            guard.validate_write(**_valid_write_kwargs(provenance_json=prov))

    def test_missing_parser_version_raises(self):
        guard = ProvenanceGuard()
        prov = {k: v for k, v in VALID_PROVENANCE.items() if k != "parser_version"}
        with pytest.raises(ProvenanceViolation, match="parser_version"):
            guard.validate_write(**_valid_write_kwargs(provenance_json=prov))

    def test_empty_provenance_json_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation, match="provenance_json"):
            guard.validate_write(**_valid_write_kwargs(provenance_json={}))

    def test_non_dict_provenance_json_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation, match="provenance_json"):
            guard.validate_write(**_valid_write_kwargs(provenance_json="not_a_dict"))  # type: ignore[arg-type]

    def test_missing_all_three_required_keys_raises(self):
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation) as exc_info:
            guard.validate_write(**_valid_write_kwargs(provenance_json={"some_key": "val"}))
        # Error should mention all missing keys
        msg = str(exc_info.value)
        # At least one required key should be mentioned
        assert any(k in msg for k in ("request_url", "fetched_at", "parser_version"))


# ---------------------------------------------------------------------------
# Tests: multiple simultaneous violations
# ---------------------------------------------------------------------------

class TestMultipleViolations:
    def test_multiple_violations_reported_in_one_raise(self):
        """ProvenanceViolation message should mention all failures."""
        guard = ProvenanceGuard()
        with pytest.raises(ProvenanceViolation) as exc_info:
            guard.validate_write(
                source="",
                authority="INVALID",
                data_version="",
                provenance_json={},
            )
        msg = str(exc_info.value)
        # Multiple violations should be listed
        assert "source" in msg
        assert "authority" in msg
        assert "data_version" in msg


# ---------------------------------------------------------------------------
# Tests: legacy read-time tolerance (SC-3)
# ---------------------------------------------------------------------------

class TestLegacyReadTimeTolerance:
    def test_row_with_no_data_version_gets_legacy_v0(self):
        """Row without data_version gets LEGACY_V0_DATA_VERSION at read time."""
        row = {"city": "London", "target_date": "2025-01-01", "data_version": None, "authority": None, "source": None}
        result = ProvenanceGuard.apply_legacy_read_tolerance(row)
        assert result["data_version"] == LEGACY_V0_DATA_VERSION
        assert result["authority"] == LEGACY_V0_AUTHORITY

    def test_row_with_no_authority_gets_unverified(self):
        row = {"city": "Tokyo", "data_version": None, "authority": "", "source": ""}
        result = ProvenanceGuard.apply_legacy_read_tolerance(row)
        assert result["authority"] == "UNVERIFIED"

    def test_row_with_existing_data_version_preserved(self):
        row = {"data_version": "wu_daily_v2", "authority": "VERIFIED", "source": "wu_icao"}
        result = ProvenanceGuard.apply_legacy_read_tolerance(row)
        assert result["data_version"] == "wu_daily_v2"
        assert result["authority"] == "VERIFIED"

    def test_apply_tolerance_returns_same_dict(self):
        """apply_legacy_read_tolerance updates in-place and returns same dict."""
        row = {"city": "Seoul", "data_version": None, "authority": None, "source": None}
        result = ProvenanceGuard.apply_legacy_read_tolerance(row)
        assert result is row  # same object

    def test_legacy_tolerance_is_read_only_pattern(self):
        """Demonstrate that ProvenanceGuard.validate_write is WRITE-time;
        apply_legacy_read_tolerance is READ-time. These are separate paths."""
        guard = ProvenanceGuard()
        # Write-time rejects missing fields
        with pytest.raises(ProvenanceViolation):
            guard.validate_write(source="", authority="", data_version="", provenance_json={})

        # Read-time tolerates missing fields
        row = {"data_version": None, "authority": None, "source": None}
        result = ProvenanceGuard.apply_legacy_read_tolerance(row)
        # No exception, returns tolerant values
        assert result["data_version"] == LEGACY_V0_DATA_VERSION
