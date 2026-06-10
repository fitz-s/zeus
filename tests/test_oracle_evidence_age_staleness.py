# Created: 2026-06-09
# Last reused/audited: 2026-06-09
# Authority basis: oracle shadow outage report §4 staleness-laundering fix;
#   _evidence_age_hours_for_record replaces file-mtime with last_observed_date
#   for oracle_shadow_snapshot records so STALE fires on stale EVIDENCE not stale FILE.
"""Antibody: evidence-age staleness fix for oracle_shadow_snapshot records.

The defect (§4 oracle shadow outage report 2026-06-09): the bridge regenerates
oracle_error_rates.json daily, resetting the file mtime.  For cities using
oracle_shadow_snapshot as their source (newly-onboarded cities before canonical
DB evidence accumulates), this means the STALE classification never fires even
when the underlying snapshot evidence is months old.

The fix: _evidence_age_hours_for_record() derives age from last_observed_date
(the bridge-written date of the newest snapshot comparison) for snapshot-sourced
records, while canonical-sourced records continue to use the file mtime.

HK-shaped fixture per task spec: n=42, April evidence (2026-04-30),
oracle_shadow_snapshot source_role, today's file → must classify STALE.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from src.strategy import oracle_penalty
from src.strategy.oracle_status import OracleStatus


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_oracle(monkeypatch, tmp_path):
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    oracle_penalty._reset_for_test()
    yield
    oracle_penalty._reset_for_test()


def _write_oracle_file(tmp_path, payload: dict):
    path = tmp_path / "data" / "oracle_error_rates.json"
    path.write_text(json.dumps(payload))
    return path


# ---------------------------------------------------------------------------
# Core antibody: HK-shaped fixture (April evidence, today's file → STALE)
# ---------------------------------------------------------------------------


def test_snapshot_record_with_old_evidence_date_is_stale(tmp_path) -> None:
    """Regenerating the file does NOT refresh a stale-evidence snapshot city.

    HK-shaped: n=42, last_observed_date=2026-04-30 (>7 days ago regardless
    of when this test runs), oracle_shadow_snapshot source_role.
    The record must be classified STALE and carry a sub-1.0 multiplier.
    """
    _write_oracle_file(tmp_path, {
        "HongKong": {
            "high": {
                "n": 42,
                "mismatches": 0,
                "source_role": "oracle_shadow_snapshot",
                "last_observed_date": "2026-04-30",  # ~40+ days old
            }
        }
    })
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info("HongKong", "high")

    assert info.status == OracleStatus.STALE, (
        f"April-evidence snapshot city must be STALE, got {info.status}"
    )
    assert info.penalty_multiplier == 0.7, (
        f"STALE multiplier must be 0.7, got {info.penalty_multiplier}"
    )
    assert info.artifact_age_hours is not None and info.artifact_age_hours > 24 * 7, (
        "Evidence age must exceed 7 days"
    )


def test_snapshot_record_fresh_evidence_is_not_stale(tmp_path) -> None:
    """A snapshot record with recent last_observed_date (today) is not STALE."""
    today = date.today().isoformat()
    _write_oracle_file(tmp_path, {
        "Tokyo": {
            "high": {
                "n": 50,
                "mismatches": 0,
                "source_role": "oracle_shadow_snapshot",
                "last_observed_date": today,
            }
        }
    })
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info("Tokyo", "high")

    # Should not be STALE — fresh evidence today means age < 24h.
    assert info.status != OracleStatus.STALE, (
        f"Today-evidence snapshot city must not be STALE, got {info.status}"
    )
    assert info.artifact_age_hours is not None and info.artifact_age_hours < 24, (
        "Evidence age for today's snapshot must be < 24h"
    )


def test_canonical_record_uses_file_mtime_not_evidence_date(tmp_path) -> None:
    """Canonical-sourced records are not subject to evidence-date staleness.

    A canonical record with an old last_observed_date but a fresh file must
    NOT be classified STALE (the file mtime is the authority for canonical
    records since canonical evidence is gathered from the live DB daily).
    """
    _write_oracle_file(tmp_path, {
        "London": {
            "high": {
                "n": 481,
                "mismatches": 0,
                "source_role": "canonical_observation_instants_v2",
                "last_observed_date": "2020-01-01",  # very old date; irrelevant for canonical
            }
        }
    })
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info("London", "high")

    # File was just written → file mtime is seconds old → NOT STALE.
    assert info.status != OracleStatus.STALE, (
        f"Canonical-source city with fresh file must not be STALE, got {info.status}"
    )


def test_snapshot_record_missing_evidence_date_falls_back_to_file_mtime(tmp_path) -> None:
    """When last_observed_date is absent, fall back to file mtime (conservative)."""
    _write_oracle_file(tmp_path, {
        "Seoul": {
            "high": {
                "n": 30,
                "mismatches": 0,
                "source_role": "oracle_shadow_snapshot",
                # no last_observed_date
            }
        }
    })
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info("Seoul", "high")

    # File was just written → file mtime is seconds old → NOT STALE.
    assert info.status != OracleStatus.STALE, (
        "Snapshot record without evidence date and fresh file must not be STALE"
    )


def test_evidence_age_used_not_file_age_for_snapshot(tmp_path) -> None:
    """Evidence age on returned OracleInfo derives from last_observed_date for
    snapshot records, not from file mtime."""
    old_date = "2026-04-30"
    _write_oracle_file(tmp_path, {
        "Bangkok": {
            "high": {
                "n": 35,
                "mismatches": 0,
                "source_role": "oracle_shadow_snapshot",
                "last_observed_date": old_date,
            }
        }
    })
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info("Bangkok", "high")

    # File mtime is fresh (written just now), but evidence age must reflect
    # the April date — well over 7 days.
    assert info.artifact_age_hours is not None
    assert info.artifact_age_hours > 24 * 7, (
        f"Evidence age must reflect last_observed_date ({old_date}), "
        f"not file mtime; got {info.artifact_age_hours:.1f}h"
    )
