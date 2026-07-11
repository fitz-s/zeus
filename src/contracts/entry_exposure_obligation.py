# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   BLOCKER-1 (largest live-money hole).

"""EntryExposureObligation — the conservative-bound leg of BLOCKER-1's three-way law.

BLOCKER-1 (adjudication, 2026-07-11): every durable command that may have caused
venue/chain exposure must carry exactly one of:
  1. authoritative settled economics (a real fill/position row — no obligation needed), or
  2. a conservative bounded EntryExposureObligation (THIS module), or
  3. an unbounded obligation -> DATA_DEGRADED (represented as a
     ``ReviewWorkItem`` with ``unbounded=True``, reason_code
     CHAIN_ONLY_UNKNOWN_ASSET or similar — see src.contracts.review_work_item).

created ATOMICALLY on the failure path (before return), not on a later load/reconcile —
the caller that could not confirm settled economics for a command must write this fact
in the SAME transaction as the failure it is recovering from (INV-37 same-connection).

Long-only CTF bound (documented prominently per the adjudication): a Polymarket CTF
outcome token is minted 1:1 against USDC collateral and settles at $0 or $1. Zeus is
long-only (it never shorts/borrows outcome tokens), so the worst case for `shares` units
of held/pending inventory is `shares x $1`. This bound is INVALID the moment Zeus ever
carries short/borrowed exposure — revisit before trusting it if that assumption changes.

Storage: src/state/entry_exposure_obligation.py, table
``entry_exposure_obligations`` (SIBLING to ``review_work_items``, same trade DB) —
see that module's docstring for why a sibling table beats reusing the review-item table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.contracts.review_work_item import FamilyKey

_VALID_STATUSES = ("OPEN", "RESOLVED")


@dataclass(frozen=True)
class EntryExposureObligation:
    """One command's conservative (or unbounded) exposure fact.

    Exactly one row per ``command_id`` — this is a LIVE fact updated as a
    command's true economics become known (open_entry_exposure_obligation is
    an upsert-open), not an append-only event log.
    """

    command_id: str  # the durable command this obligation guards (stable identity)
    owner_domain: str  # physical DB identity, e.g. "trade"
    token_id: str = ""
    condition_id: str = ""
    # Exactly one of {(shares, cost_basis_usd) both set, unbounded=True}.
    shares: Optional[float] = None
    cost_basis_usd: Optional[float] = None
    unbounded: bool = False
    family_key: Optional[FamilyKey] = None
    status: str = "OPEN"  # OPEN | RESOLVED
    created_at: str = ""
    updated_at: str = ""
    resolved_at: Optional[str] = None

    def __post_init__(self) -> None:  # type: ignore[override]
        if not str(self.command_id).strip():
            raise ValueError("EntryExposureObligation.command_id must be non-empty")
        if not str(self.owner_domain).strip():
            raise ValueError("EntryExposureObligation.owner_domain must be non-empty")
        shares_set = self.shares is not None
        cost_set = self.cost_basis_usd is not None
        if shares_set != cost_set:
            raise ValueError(
                "EntryExposureObligation requires shares and cost_basis_usd together "
                f"or neither (shares={self.shares!r}, cost_basis_usd={self.cost_basis_usd!r})"
            )
        bounded = shares_set and cost_set
        if bounded == bool(self.unbounded):
            raise ValueError(
                "EntryExposureObligation requires exactly one of a bounded "
                f"(shares, cost_basis_usd) pair or unbounded=True (shares={self.shares!r}, "
                f"cost_basis_usd={self.cost_basis_usd!r}, unbounded={self.unbounded!r})"
            )
        if bounded and (float(self.shares) < 0.0 or float(self.cost_basis_usd) < 0.0):  # type: ignore[arg-type]
            raise ValueError("EntryExposureObligation shares/cost_basis_usd must be >= 0")
        if str(self.status) not in _VALID_STATUSES:
            raise ValueError(
                f"EntryExposureObligation.status must be one of {_VALID_STATUSES}, got {self.status!r}"
            )

    @property
    def exposure_bound_usd(self) -> Optional[float]:
        """Conservative worst-case USD exposure: ``shares`` x $1/share.

        LONG-ONLY CTF ASSUMPTION — see module docstring. Returns None when
        ``unbounded`` (the caller must route that case through DATA_DEGRADED
        via a ReviewWorkItem, never treat None here as zero exposure).
        """

        if self.unbounded or self.shares is None:
            return None
        return float(self.shares) * 1.0

    @property
    def net_cost_usd(self) -> Optional[float]:
        """Cost-side of the obligation (from cost_basis); None when unbounded."""

        return None if self.unbounded else self.cost_basis_usd


__all__ = ["EntryExposureObligation"]
