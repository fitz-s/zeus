# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md §6
#   (in-place ownership cleanup — single source over the EXISTING three DBs, no restructure).
"""Phase-0 antibody: src/state/domains.py is the single ownership source, data-correct + drift-locked.

domains.py collapses the three drifting ownership sources onto one typed declaration over the EXISTING
world/forecasts/trade DBs (no physical restructure). It must equal the current registry EVERYWHERE except
the CORRECTED_FROM_REGISTRY set — the 19 tables the registry inverts (data lives elsewhere than declared),
which the in-place migration converges. This test is the ratchet: it proves domains.py == registry for
every non-inverted table (so the two cannot silently drift) AND that every correction is a real, still-open
divergence (so the list can't rot). Once a correction lands (init+registry moved to match), that table drops
out of CORRECTED_FROM_REGISTRY and this test tightens.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.state import domains

_REG = Path(__file__).resolve().parent.parent / "architecture" / "db_table_ownership.yaml"
_NON_OWNING = {"legacy_archived", "inert_experimental", "archive"}


def _registry_canonical_owners() -> dict[str, str]:
    reg = yaml.safe_load(_REG.read_text())
    owners: dict[str, str] = {}
    for t in reg["tables"]:
        if t.get("schema_class") not in _NON_OWNING:
            owners[t["name"]] = t["db"]
    return owners


def test_domains_equals_registry_except_documented_corrections() -> None:
    registry = _registry_canonical_owners()
    corrected = domains.CORRECTED_FROM_REGISTRY
    # (a) Every NON-corrected table must match the registry exactly (no silent drift).
    for name, reg_db in registry.items():
        if name in corrected:
            continue
        d = domains.owner_domain(name)
        assert d is not None, f"domains.py is missing live registry table {name!r} (drift)"
        assert d.value == reg_db, f"owner drift for {name!r}: domains={d.value} registry={reg_db}"
    # (b) domains.py must not invent tables the registry has never heard of (outside corrections).
    extra = set(domains.live_tables()) - set(registry) - corrected
    assert not extra, f"domains.py declares tables absent from the registry: {sorted(extra)}"


def test_inert_experimental_tables_are_not_domain_owners() -> None:
    registry = _registry_canonical_owners()

    for table in ("reduce_generations", "reduce_position_economics"):
        assert table not in registry
        assert domains.owner_domain(table) is None


def test_every_correction_is_a_real_open_divergence() -> None:
    registry = _registry_canonical_owners()
    for name in domains.CORRECTED_FROM_REGISTRY:
        d = domains.owner_domain(name)
        assert d is not None, f"correction {name!r} has no domains owner"
        reg_db = registry.get(name)  # None if registry has no canonical entry (the missing-canonical case)
        assert reg_db != d.value, (
            f"{name!r} is listed as corrected but domains ({d.value}) already agrees with registry "
            f"({reg_db}); remove it from CORRECTED_FROM_REGISTRY (the migration converged it)"
        )


def test_owner_domain_total_and_valid() -> None:
    for name in domains.live_tables():
        assert domains.owner_domain(name) in set(domains.Domain), name
