# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md T2 item 1
#   (canonical asset dedup reducer) + "Consult adjudication" BLOCKER-1.

"""Canonical-token exposure dedup reducer (T2 excision item 1).

Replaces the global quarantine gate's blast-radius with FAMILY-scoped
worst-case exposure accounting. Before summing "exposure we don't already
count via ``portfolio.total_exposure_usd``" (which sums only open Positions),
this module dedups by canonical token identity: a token represented by BOTH
an open Position AND a ChainOnlyFact is ONE exposure, not two — the
ChainOnlyFact side must not be added again on top of the Position's cost
basis already inside ``total_exposure_usd``.

Contrast with ``src.strategy.family_exclusive_dedup.
_weather_family_exposures_from_portfolio_impl``: that function SILENTLY
``continue``s (drops) rows lacking city/target_date/temperature_metric
(census finding, 2026-07-11). This reducer must never repeat that — a
ChainOnlyFact with real size and no resolvable family identity is a
DATA_DEGRADED signal (``any_family_unmapped=True``), never a silent skip of
the exposure dollar amount itself.

shares x $1 payout bound: sound only while Zeus is long-only CTF (never
shorts/borrows outcome tokens) — see src.contracts.entry_exposure_obligation
module docstring for the same documented assumption.
"""

from __future__ import annotations

import sqlite3
from typing import Optional


def _open_position_token_ids(portfolio: object) -> set[str]:
    from src.state.portfolio import _is_runtime_open_position

    ids: set[str] = set()
    for pos in getattr(portfolio, "positions", None) or ():
        if not _is_runtime_open_position(pos):
            continue
        for attr in ("token_id", "no_token_id"):
            token_id = str(getattr(pos, attr, "") or "").strip()
            if token_id:
                ids.add(token_id)
    return ids


def chain_only_worst_case_add_usd(
    conn: Optional[sqlite3.Connection], portfolio: object
) -> tuple[float, bool]:
    """Worst-case USD to ADD on top of ``portfolio.total_exposure_usd`` for
    blocking ``ChainOnlyFact`` rows on ``portfolio.chain_only_facts``, after
    canonical-token dedup against already-open Positions.

    Returns ``(worst_case_add_usd, any_family_unmapped)``:
    - ``worst_case_add_usd``: sum of ``fact.size * 1.0`` (CTF payout bound)
      for every blocking fact whose ``token_id`` is NOT already an open
      Position's token (dedup — see module docstring). A fact with no
      ``token_id`` at all cannot be deduped and is always added (fail-safe:
      never silently dropped for lack of an identity to dedup against).
    - ``any_family_unmapped``: True iff any COUNTED fact could not be mapped
      to a WeatherFamilyKey via market_events — callers must route this to
      DATA_DEGRADED (BLOCKER-1's "unmappable family identity... -> never
      silent skip"), never drop the dollar figure above.

    ``conn`` may be None (fail-soft on family lookup only — the dollar sum
    itself never depends on conn); a None conn also counts as unmapped for
    every fact considered, since no market_events lookup is possible.
    """

    open_token_ids = _open_position_token_ids(portfolio)
    chain_only_facts = list(getattr(portfolio, "chain_only_facts", None) or ())

    total_usd = 0.0
    any_unmapped = False

    family_lookup = None
    if conn is not None:
        from src.state.review_work_items import family_key_for_condition_or_token

        family_lookup = family_key_for_condition_or_token

    for fact in chain_only_facts:
        if not bool(getattr(fact, "blocks_entry", True)):
            continue
        token_id = str(getattr(fact, "token_id", "") or "").strip()
        if token_id and token_id in open_token_ids:
            # Canonical-token dedup: this token's exposure is already inside
            # portfolio.total_exposure_usd via its open Position.
            continue
        size = float(getattr(fact, "size", 0.0) or 0.0)
        total_usd += size * 1.0
        condition_id = str(getattr(fact, "condition_id", "") or "")
        family_key = (
            family_lookup(conn, condition_id=condition_id, token_id=token_id)
            if family_lookup is not None
            else None
        )
        if family_key is None:
            any_unmapped = True

    return total_usd, any_unmapped


__all__ = ["chain_only_worst_case_add_usd"]
