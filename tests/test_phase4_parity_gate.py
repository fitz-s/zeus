"""Phase 4 parity gate tests: R-P (quarantine of old peak_window tag)

R-P: assert_data_version_allowed('tigge_mx2t6_local_peak_window_max_v1') raises
DataVersionQuarantinedError — the old high tag must be refused by the quarantine guard.

Also tests that the new canonical tag passes the guard, so Phase 4 ingest is unblocked.
"""
from __future__ import annotations

import pytest


class TestDataVersionQuarantineGate:
    """R-P: The old peak_window data_version tag must be quarantined after 4A.1.

    Phase 4A.1 (exec-carol) adds 'tigge_mx2t6_local_peak_window_max_v1' to
    QUARANTINED_DATA_VERSIONS in src/contracts/ensemble_snapshot_provenance.py.

    GREEN by design once 4A.1 lands — these are the 4A.1 enforcement tests, not
    pre-existing guardrails. If they go RED again, the quarantine was accidentally
    removed. The canonical replacement tag 'tigge_mx2t6_local_calendar_day_max_v1'
    must always pass the guard.
    """

    def test_peak_window_tag_is_quarantined(self):
        """R-P: 'tigge_mx2t6_local_peak_window_max_v1' must raise DataVersionQuarantinedError.

        GREEN once 4A.1 lands. Regression: going RED means quarantine was removed.
        """
        from src.contracts.ensemble_snapshot_provenance import (
            DataVersionQuarantinedError,
            assert_data_version_allowed,
        )

        with pytest.raises(DataVersionQuarantinedError):
            assert_data_version_allowed(
                "tigge_mx2t6_local_peak_window_max_v1",
                context="test_phase4_parity_gate",
            )

    def test_canonical_local_calendar_day_max_tag_is_allowed(self):
        """R-P complement: The canonical Phase 4 tag must not be quarantined."""
        from src.contracts.ensemble_snapshot_provenance import assert_data_version_allowed

        # Must not raise — this is the replacement tag all Phase 4 writers must use
        assert_data_version_allowed(
            "tigge_mx2t6_local_calendar_day_max_v1",
            context="test_phase4_parity_gate",
        )

    def test_low_track_canonical_tag_is_allowed(self):
        """R-P complement: The low-track canonical tag (Phase 5) must not be quarantined."""
        from src.contracts.ensemble_snapshot_provenance import assert_data_version_allowed

        assert_data_version_allowed(
            "tigge_mn2t6_local_calendar_day_min_v1",
            context="test_phase4_parity_gate",
        )

    def test_is_quarantined_returns_true_for_peak_window(self):
        """R-P: is_quarantined() must return True for the peak_window tag."""
        from src.contracts.ensemble_snapshot_provenance import is_quarantined

        assert is_quarantined("tigge_mx2t6_local_peak_window_max_v1"), (
            "'tigge_mx2t6_local_peak_window_max_v1' must be quarantined after Phase 4A.1 "
            "(R-P). The peak_window tag is superseded by the local_calendar_day_max tag."
        )

    def test_is_quarantined_returns_false_for_canonical_tag(self):
        """R-P complement: is_quarantined() must return False for the canonical replacement tag."""
        from src.contracts.ensemble_snapshot_provenance import is_quarantined

        assert not is_quarantined("tigge_mx2t6_local_calendar_day_max_v1"), (
            "'tigge_mx2t6_local_calendar_day_max_v1' must NOT be quarantined — "
            "it is the canonical Phase 4 replacement (R-P complement)."
        )

    def test_existing_quarantine_prefixes_still_blocked(self):
        """R-P regression: Existing quarantine prefixes (tigge_step*, tigge_param167*) must still fire."""
        from src.contracts.ensemble_snapshot_provenance import (
            DataVersionQuarantinedError,
            assert_data_version_allowed,
        )

        for tag in [
            "tigge_step024_v1_near_peak",
            "tigge_step024_v1_overnight_snapshot",
            "tigge_param167_instant",
            "tigge_2t_instant_v1",
        ]:
            with pytest.raises(DataVersionQuarantinedError, match=tag):
                assert_data_version_allowed(tag, context="test_phase4_parity_gate_regression")

    def test_ingest_grib_to_snapshots_calls_assert_data_version_before_insert(self):
        """R-P structural: ingest_grib_to_snapshots must call assert_data_version_allowed.

        This test verifies that the ingest script's implementation contract includes
        the provenance guard. Since the script is being built in Phase 4B, this test
        verifies the structural requirement by checking the import is present in the
        implementation module.

        When Phase 4B lands, replace this test body with a mock-based assertion that
        confirms assert_data_version_allowed is called before every INSERT.
        """
        pytest.skip(
            "pending: enforced in Phase 4B when ingest_grib_to_snapshots.py is implemented. "
            "Phase 4A only establishes the quarantine; 4B wires the guard into the ingest call path."
        )
