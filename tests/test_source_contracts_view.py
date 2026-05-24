# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Read-through proofs that SourceContract is a view, not a 4th registry.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md;
#   operator directive 2026-05-24 "don't reinvent what we have, drop or reshape if needed".
"""Read-through proofs for SourceContract — it must be a VIEW, never a 4th registry.

These tests are the antibody against the composer silently accreting its own
authority fields (the duplication the operator directive forbids).
"""
from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "architecture" / "data_sources_registry_2026_05_08.yaml"


def test_contract_authority_delegates_to_temporal() -> None:
    """live_authorization / backfill_only must READ THROUGH to TemporalPolicy,
    not be stored as independent fields (no drift surface)."""
    from src.data.source_contracts import load_source_contract

    c = load_source_contract("ecmwf_open_data_mx2t6_high")
    assert c.live_authorization is c.temporal.live_authorization
    assert c.backfill_only is c.temporal.backfill_only
    assert c.source_id == c.temporal.source_id

    # The dataclass must NOT carry its own live_authorization/backfill_only data fields
    # (they are properties delegating to temporal). If someone adds them as stored
    # fields, this fails — keeping the view honest.
    field_names = {f for f in c.__dataclass_fields__}
    assert "live_authorization" not in field_names
    assert "backfill_only" not in field_names


def test_family_reads_through_to_data_sources_registry() -> None:
    """family / publisher must equal the data_sources_registry entry — not a copy."""
    from src.data.source_contracts import load_source_contract

    with REGISTRY_PATH.open() as f:
        registry = {s["id"]: s for s in yaml.safe_load(f).get("sources", [])}

    c = load_source_contract("ecmwf_open_data_mx2t6_high")
    assert c.family == registry["ecmwf_open_data"]["category"]
    assert c.publisher == registry["ecmwf_open_data"]["publisher"]


def test_forecast_tier_reads_through_to_forecast_registry() -> None:
    """forecast_tier / forecast_roles must equal forecast_source_registry — not a copy."""
    from src.data.forecast_source_registry import SOURCES
    from src.data.source_contracts import load_source_contract

    c = load_source_contract("ecmwf_open_data_mx2t6_high")
    spec = SOURCES["ecmwf_open_data"]
    assert c.forecast_tier == spec.tier
    assert c.forecast_roles == tuple(spec.allowed_roles)


def test_alias_gap_surfaces_as_none_not_invented() -> None:
    """The tigge (calendar) vs tigge_mars (registry) alias gap must surface as
    family=None — the composer must NOT invent an alias mapping (that is the lint's job)."""
    from src.data.source_contracts import load_source_contract

    c = load_source_contract("tigge_archive_backfill")
    assert c.source_id == "tigge"          # calendar source_id
    assert c.family is None                # registry has 'tigge_mars', not 'tigge'
    # backfill authority still resolves correctly through temporal:
    assert c.backfill_only is True
    assert c.live_authorization is False
