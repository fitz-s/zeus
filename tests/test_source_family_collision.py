# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: SCAFFOLD.md §4 FM-10 + fatal_misreads.yaml FM-10 (source_family_false_equivalence)
"""Antibody tests: wrong source-family combinations raise TypeError at assertion boundary.

FM-10: SEMANTIC_SOURCE_FAMILY_FALSE_EQUIVALENCE — IFS vs ENS grid; settlement vs day0 vs hourly.
The wrong combinations must be UNCONSTRUCTABLE at the type boundary, not just validated at runtime.

Each test proves a specific cross-family collision raises TypeError when the assert guard is called.
This ensures the category of misread is impossible in any code path that uses the guard functions.
"""
from __future__ import annotations

import pytest

from src.contracts.source_family import (
    Day0SourceId,
    ENSGridId,
    HourlySourceId,
    IFSGridId,
    SettlementSourceId,
    assert_day0_source,
    assert_ens_grid,
    assert_hourly_source,
    assert_ifs_grid,
    assert_settlement_source,
)


# ---------------------------------------------------------------------------
# Grid family collision tests
# ---------------------------------------------------------------------------

class TestGridFamilyCollisions:
    def test_ifs_rejects_ens(self) -> None:
        """assert_ifs_grid must raise TypeError when given an ENSGridId."""
        ens_id = ENSGridId("ENS:EGLL:2026-06-01")
        with pytest.raises(TypeError, match="IFSGridId"):
            assert_ifs_grid(ens_id)

    def test_ens_rejects_ifs(self) -> None:
        """assert_ens_grid must raise TypeError when given an IFSGridId."""
        ifs_id = IFSGridId("IFS:EGLL:2026-06-01")
        with pytest.raises(TypeError, match="ENSGridId"):
            assert_ens_grid(ifs_id)

    def test_ifs_rejects_settlement(self) -> None:
        """assert_ifs_grid must raise TypeError when given a SettlementSourceId (cross-axis)."""
        sett_id = SettlementSourceId("NCDC:EGLL:2026-06-01")
        with pytest.raises(TypeError, match="IFSGridId"):
            assert_ifs_grid(sett_id)

    def test_ifs_accepts_ifs(self) -> None:
        """assert_ifs_grid must return the IFSGridId unchanged when correct type is passed."""
        ifs_id = IFSGridId("IFS:EGLL:2026-06-01")
        result = assert_ifs_grid(ifs_id)
        assert result is ifs_id

    def test_ens_accepts_ens(self) -> None:
        """assert_ens_grid must return the ENSGridId unchanged when correct type is passed."""
        ens_id = ENSGridId("ENS:EGLL:2026-06-01")
        result = assert_ens_grid(ens_id)
        assert result is ens_id


# ---------------------------------------------------------------------------
# Timeline family collision tests
# ---------------------------------------------------------------------------

class TestTimelineFamilyCollisions:
    def test_settlement_rejects_day0(self) -> None:
        """assert_settlement_source must raise TypeError when given a Day0SourceId."""
        day0_id = Day0SourceId("NCDC:EGLL:2026-06-01")
        with pytest.raises(TypeError, match="SettlementSourceId"):
            assert_settlement_source(day0_id)

    def test_settlement_rejects_hourly(self) -> None:
        """assert_settlement_source must raise TypeError when given a HourlySourceId."""
        hourly_id = HourlySourceId("NCDC:EGLL:2026-06-01T12")
        with pytest.raises(TypeError, match="SettlementSourceId"):
            assert_settlement_source(hourly_id)

    def test_day0_rejects_settlement(self) -> None:
        """assert_day0_source must raise TypeError when given a SettlementSourceId."""
        sett_id = SettlementSourceId("NCDC:EGLL:2026-06-01")
        with pytest.raises(TypeError, match="Day0SourceId"):
            assert_day0_source(sett_id)

    def test_day0_rejects_hourly(self) -> None:
        """assert_day0_source must raise TypeError when given a HourlySourceId."""
        hourly_id = HourlySourceId("NCDC:EGLL:2026-06-01T12")
        with pytest.raises(TypeError, match="Day0SourceId"):
            assert_day0_source(hourly_id)

    def test_hourly_rejects_settlement(self) -> None:
        """assert_hourly_source must raise TypeError when given a SettlementSourceId."""
        sett_id = SettlementSourceId("NCDC:EGLL:2026-06-01")
        with pytest.raises(TypeError, match="HourlySourceId"):
            assert_hourly_source(sett_id)

    def test_hourly_rejects_day0(self) -> None:
        """assert_hourly_source must raise TypeError when given a Day0SourceId."""
        day0_id = Day0SourceId("NCDC:EGLL:2026-06-01")
        with pytest.raises(TypeError, match="HourlySourceId"):
            assert_hourly_source(day0_id)

    def test_settlement_accepts_settlement(self) -> None:
        sett_id = SettlementSourceId("NCDC:EGLL:2026-06-01")
        result = assert_settlement_source(sett_id)
        assert result is sett_id

    def test_day0_accepts_day0(self) -> None:
        day0_id = Day0SourceId("NCDC:EGLL:2026-06-01")
        result = assert_day0_source(day0_id)
        assert result is day0_id

    def test_hourly_accepts_hourly(self) -> None:
        hourly_id = HourlySourceId("NCDC:EGLL:2026-06-01T12")
        result = assert_hourly_source(hourly_id)
        assert result is hourly_id


# ---------------------------------------------------------------------------
# Cross-axis collision (grid vs timeline)
# ---------------------------------------------------------------------------

class TestCrossAxisCollisions:
    def test_ifs_rejects_day0(self) -> None:
        """Grid family guard must reject timeline family inputs (cross-axis)."""
        day0_id = Day0SourceId("NCDC:EGLL:2026-06-01")
        with pytest.raises(TypeError, match="IFSGridId"):
            assert_ifs_grid(day0_id)

    def test_settlement_rejects_ifs(self) -> None:
        """Timeline family guard must reject grid family inputs (cross-axis)."""
        ifs_id = IFSGridId("IFS:EGLL:2026-06-01")
        with pytest.raises(TypeError, match="SettlementSourceId"):
            assert_settlement_source(ifs_id)


# ---------------------------------------------------------------------------
# Construction guard: empty identifier rejected at construction
# ---------------------------------------------------------------------------

class TestConstructionGuards:
    def test_ifs_empty_identifier_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            IFSGridId("")

    def test_ens_empty_identifier_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ENSGridId("")

    def test_settlement_empty_identifier_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            SettlementSourceId("")
