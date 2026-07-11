# Created: 2026-05-01
# Last reused/audited: 2026-07-11
# Authority basis: ultrareview25_remediation 2026-05-01 P1-3 +
#                  repo_review_2026-05-01 SYNTHESIS K-C +
#                  INV-23 (DEGRADED_PROJECTION must be a distinct non-VERIFIED label) +
#                  docs/rebuild excision packet T2b, 2026-07-11 (third authority-tier
#                  member renamed to DISPUTED: a per-row scoped dispute with an
#                  evidence-backed release path, not a global freeze)
"""Closed enumeration for truth-file authority labels (grammar A).

Why this exists
---------------
Before this module landed, `src/state/portfolio.py:_TRUTH_AUTHORITY_MAP` used
bare string values (`"VERIFIED"`, `"UNVERIFIED"`, `"DEGRADED_PROJECTION"`,
`"DISPUTED"`). The 2026-05-01 multi-lane review found that:

1. Producers stamped these correctly.
2. **No production consumer reads `truth["authority"]`** (verified by grep
   in the P1-3 audit at `docs/operations/repo_review_2026-05-01/
   P1_3_TRUTH_AUTHORITY_AUDIT.md` §3.2). The K-C "consumer-blind" risk is
   forward-looking, not retroactive.
3. The DB CHECK constraints across `src/state/db.py` and
   `src/state/schema/v2_schema.py` enumerate a 3-set
   (`VERIFIED, UNVERIFIED, DISPUTED` — the third member renamed 2026-07-11
   per the T2b excision packet); `DEGRADED_PROJECTION` lives
   ONLY in the JSON truth-stamp lane. Persisting it to a DB row would
   violate every CHECK constraint by design.

This module turns the producer-side string surface into a closed enum so:

- Future producer additions are deliberate type edits, not silent string
  changes.
- A relationship test (`tests/test_truth_authority_enum.py`) catches any
  future producer that emits a value not in the enum.
- New consumers can use `typing.assert_never` to get type-checker-level
  exhaustiveness without rewriting existing if/elif chains.

Scope explicitly EXCLUDES:

- Grammar B (`PortfolioState.authority` lowercase: `canonical_db` /
  `degraded` / `unverified`) — translated INTO grammar A by
  `_TRUTH_AUTHORITY_MAP`. The B vocabulary has its own consumer surface
  inside portfolio/riskguard.
- Grammar C (`ScanAuthority`: `VERIFIED` / `STALE` /
  `FETCH_FAILED_NO_CACHE` / `KEYWORD_DISCOVERY_UNVERIFIED` /
  `NEVER_FETCHED`) — `src/data/market_scanner.py:43`. Wire-collides on the
  literal `"VERIFIED"` only.
- Grammar D (`AuthorityTier`: `CHAIN` / `VENUE` / `DEGRADED`) —
  `src/state/collateral_ledger.py:27`.
- Grammar E (`DepthProofSource`: `CLOB_SWEEP` / `PASSIVE_LIMIT` /
  `UNVERIFIED`) — `src/contracts/execution_intent.py:37`. Wire-collides on
  `"UNVERIFIED"` only.
- Grammar F/G (forecast authority_tier; entry_economics_authority).
- Schema-extended dialects (`ICAO_STATION_NATIVE` for
  `observation_instants`; `RECONSTRUCTED` for `rescue_events`).
  These are real lane-specific 5th members but are NOT canonical
  truth-file authority — they live only in their respective writer
  paths' allowlists.

If you find yourself wanting to add a 5th member here, first re-read the
audit doc and confirm the new value is genuinely truth-file authority and
not one of the lane-specific schema dialects above.
"""
from __future__ import annotations

from enum import StrEnum


class TruthAuthority(StrEnum):
    """Closed enum for truth-file authority labels (grammar A).

    StrEnum is intentional: members ARE strings (subclass of `str`), so:

    - JSON serialization round-trips: `json.dumps(TruthAuthority.VERIFIED)`
      → `'"VERIFIED"'` (Python ≥3.11 stdlib).
    - Set membership against bare-string sets works either direction:
      `TruthAuthority.VERIFIED in {"VERIFIED", ...}` and the inverse both
      hold.
    - sqlite3 parameter binding via `str()` adaptation works.
    - f-string interpolation produces the bare value, not the qualified
      class name (`f"{TruthAuthority.VERIFIED}"` → `"VERIFIED"`).

    All four members are serialization-stable string labels. Adding,
    removing, or renaming any member is a backwards-incompatible change to
    every consumer that has stored the prior label on disk or in a DB row.
    """

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    DISPUTED = "DISPUTED"  # renamed 2026-07-11, T2b excision packet (name only; semantics unchanged)
    DEGRADED_PROJECTION = "DEGRADED_PROJECTION"


# ---------------------------------------------------------------------------
# Predicate helpers (named binary queries over the closed enum)
# ---------------------------------------------------------------------------
# Why these exist:
#   The 2026-05-01 P1-3 audit found 10 production sites that reduce authority
#   to an implicit two-value boolean (`!= "VERIFIED"`, `not in {"VERIFIED",
#   "UNVERIFIED", "DISPUTED"}`, etc.). Refactoring those 10 call sites
#   inline is high blast-radius (5 of 10 are wrong-grammar — ScanAuthority,
#   not TruthAuthority — per audit §3.4). Instead, we expose two NAMED
#   predicates so future consumers reach for the right semantic without
#   either (a) re-deriving the boolean each call site or (b) copy-paste
#   drift across 10 places.
#
# When DEGRADED_PROJECTION graduates from "JSON-stamp-only" to "actual
# runtime gate input" (audit §7 counter-recommendation), new consumers
# should call `is_authoritative()` / `requires_human_review()` directly
# instead of inventing a third equality check.


def is_authoritative(a: TruthAuthority) -> bool:
    """Return True iff `a` is a fully canonical, gate-passable authority.

    Today only `VERIFIED` qualifies. `DEGRADED_PROJECTION` does NOT — it
    attests "we cannot prove a breach but cannot disprove either" (per
    INV-23 + the DATA_DEGRADED design semantics in
    `src/riskguard/risk_level.py:17`). New consumers gating on "is this
    authority good enough to trade on" should call this predicate rather
    than hand-rolling `a == "VERIFIED"`.

    Type-only: refuses bare strings to force the call site to construct
    a `TruthAuthority` first (which itself rejects unknown values).
    """
    if not isinstance(a, TruthAuthority):
        raise TypeError(
            f"is_authoritative requires a TruthAuthority instance; got "
            f"{type(a).__name__}. Coerce via `TruthAuthority(value)` so "
            f"the closed-enum guard catches typos before the predicate "
            f"runs."
        )
    return a is TruthAuthority.VERIFIED


def requires_human_review(a: TruthAuthority) -> bool:
    """Return True iff `a` represents a state that should not auto-progress.

    `DISPUTED` and `DEGRADED_PROJECTION` both fall into this bucket:
      - DISPUTED: explicit operator-flagged held-state per INV-23 (renamed
        2026-07-11, T2b excision packet; semantics unchanged).
      - DEGRADED_PROJECTION: producer admitted "we cannot prove this row
        is canonical-DB sourced" (INV-23 distinct non-VERIFIED label).

    `UNVERIFIED` does NOT — it is the fail-closed default that means
    "no claim made yet"; a fresh `build_truth_metadata()` row is
    UNVERIFIED but doesn't need human attention until it survives an
    upgrade cycle. New consumers gating on "should I page a human"
    should call this rather than `a in {"DISPUTED", "DEGRADED_PROJECTION"}`.

    Type-only: refuses bare strings (same rationale as `is_authoritative`).
    """
    if not isinstance(a, TruthAuthority):
        raise TypeError(
            f"requires_human_review requires a TruthAuthority instance; got "
            f"{type(a).__name__}. Coerce via `TruthAuthority(value)`."
        )
    return a in {TruthAuthority.DISPUTED, TruthAuthority.DEGRADED_PROJECTION}


__all__ = ["TruthAuthority", "is_authoritative", "requires_human_review"]
