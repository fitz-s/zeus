# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md §6B
#   (2-DB first-principles redesign, Phase 0). Ratchet: src/state/domains.py is the intended single
#   ownership source; this proves it reproduces the current registry so the two cannot drift.
"""Phase-0 antibody: src/state/domains.py mirrors architecture/db_table_ownership.yaml exactly.

domains.py is the redesign's single ownership truth-source. Until the boot gate is repointed onto it
(§6B Phase 5), it must stay byte-equivalent to the hand-maintained YAML for every live (non-legacy)
table — otherwise the "single source" foundation is already drifting. This test is the ratchet: any
future ownership change made in ONE place (YAML or domains.py) but not the other fails CI, forcing the
single-source discipline the whole redesign depends on. It also proves the central design claim — that
the full ownership map is regenerable from one typed declaration — at zero runtime risk.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.state import domains

_REG = Path(__file__).resolve().parent.parent / "architecture" / "db_table_ownership.yaml"
_LEGACY = {"legacy_archived", "archive"}


def _registry_canonical_owners() -> dict[str, str]:
    reg = yaml.safe_load(_REG.read_text())
    owners: dict[str, str] = {}
    for t in reg["tables"]:
        if t.get("schema_class") not in _LEGACY:
            name = t["name"]
            assert name not in owners, f"registry has >1 non-legacy entry for {name}"
            owners[name] = t["db"]
    return owners


def test_current_db_reproduces_registry_owners_exactly() -> None:
    registry = _registry_canonical_owners()
    domains_map = dict(domains.CURRENT_DB)
    missing = set(registry) - set(domains_map)
    extra = set(domains_map) - set(registry)
    assert not missing, f"domains.CURRENT_DB is missing live registry tables (drift): {sorted(missing)}"
    assert not extra, f"domains.CURRENT_DB has tables not in the registry (drift): {sorted(extra)}"
    mismatched = {n: (domains_map[n], registry[n]) for n in registry if domains_map[n] != registry[n]}
    assert not mismatched, f"owner DB mismatch domains vs registry (domains, registry): {mismatched}"


def test_target_domain_is_total_and_valid() -> None:
    for name in domains.live_tables():
        d = domains.target_domain(name)
        assert d in (domains.Domain.BULK, domains.Domain.MONEY), f"{name} -> {d!r}"


def test_bulk_tables_are_live_and_subset() -> None:
    live = domains.live_tables()
    stray = domains.BULK_TABLES - live
    assert not stray, f"BULK_TABLES references non-live tables: {sorted(stray)}"
    # BULK is the forecast/observation ingest domain — its members must currently be forecast- or
    # world-owned, never trade-owned (a trade-owned table in BULK would be a classification error).
    trade_owned_in_bulk = {t for t in domains.BULK_TABLES if domains.CURRENT_DB.get(t) == "trade"}
    assert not trade_owned_in_bulk, f"trade-owned tables misclassified as BULK: {sorted(trade_owned_in_bulk)}"
