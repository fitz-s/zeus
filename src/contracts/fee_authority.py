# Lifecycle: created=2026-06-12; last_reviewed=2026-08-25; last_reused=2026-08-25
# Purpose: single authority for the taker fee fraction used in EV math — realized-fill
#   reconciliation evidence first, venue fee SCHEDULE as the conservative fallback.
# Reuse: incident 2026-06-12 — the CLOB metadata `base_fee` (the venue's fee-schedule
#   CAP, 1000 bps) was consumed as the ACTUAL fee, taxing every EV calculation with a
#   10% phantom fee while 12/12 realized fills carried trade-level fee_rate_bps=0 and
#   cost_basis reconciled to price*shares exactly. Data-provenance error class: a
#   schedule parameter is not a realized price. The artifact state/fee_reconciliation.json
#   is written ONLY by scripts/reconcile_realized_fees.py from venue_order_facts
#   trade-level fee fields.
# Last reused/audited: 2026-08-25
# Authority basis: docs/authority/statistical_calibration_authority_2026-06-12 Task 2.3
#   ("reconcile historical fills against realized P&L"; fee model must be fitted from
#   history, never assumed) + operator no-unfitted-hardcode law.
# RECURRENCE 2026-07-12 -> 2026-08-24: evidence went stale (nobody reran the reconciler)
#   and this authority silently reverted to the phantom schedule fee for ~6 weeks — the
#   fallback DIRECTION was correct (fail-conservative) but its SILENCE let the recurrence
#   go unnoticed. scripts/reconcile_realized_fees.py now runs daily from
#   src/ingest/post_trade_capital_daemon.py so evidence age stays ~1d; the staleness branch
#   below now also logs a WARNING once per staleness episode (module-level _stale_warned)
#   so a future recurrence is visible in daemon logs instead of silent.
"""Taker-fee fraction authority: realized evidence over schedule metadata.

``resolve_taker_fee_fraction(schedule_fraction)`` returns ``(fraction, source)``:

* When ``state/fee_reconciliation.json`` exists, is licensed (``n_fills`` at or
  above ``MIN_FILLS_TO_LICENSE``), and its observation window is not stale, the
  OBSERVED fraction is used — conservatively the MAX realized fee fraction seen
  across fills (all zero today -> 0.0). A future venue fee switch-on shows up in
  the next reconciliation run and raises the fraction automatically.
* Otherwise the venue SCHEDULE fraction passed by the caller is used unchanged
  (fail-conservative: overstate fees, never understate without evidence).

The artifact read is cached per (path, mtime) so the hot decision path costs a
stat() call, not a JSON parse.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ARTIFACT_PATH = Path(__file__).resolve().parents[2] / "state" / "fee_reconciliation.json"

# License rule: the realized fee is a deterministic venue parameter per fill (the
# venue reports fee_rate_bps on the trade itself), not a statistical estimate of a
# noisy quantity — a handful of exact observations identifies the regime. 10 fills
# guards against a single odd fill class while staying reachable.
MIN_FILLS_TO_LICENSE = 10

# Evidence staleness: the venue can flip its fee switch. Evidence older than this
# many days degrades back to the schedule fraction (loudly, via source string).
MAX_EVIDENCE_AGE_DAYS = 30.0

_cache: dict[str, object] = {"mtime": None, "artifact": None}

# Set while evidence is stale; cleared the moment fresh evidence resolves again. Gates the
# staleness WARNING below to once per episode instead of once per call (this path is on the
# hot EV-decision loop).
_stale_warned = False


def _load_artifact() -> dict | None:
    try:
        mtime = os.stat(ARTIFACT_PATH).st_mtime
    except OSError:
        return None
    if _cache["mtime"] == mtime and _cache["artifact"] is not None:
        return _cache["artifact"]  # type: ignore[return-value]
    try:
        artifact = json.loads(ARTIFACT_PATH.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    _cache["mtime"] = mtime
    _cache["artifact"] = artifact
    return artifact


def resolve_taker_fee_fraction(schedule_fraction: float) -> tuple[float, str]:
    """(fraction, source) — realized-fill evidence first, schedule fallback.

    ``schedule_fraction`` is the fraction derived from the venue fee SCHEDULE
    metadata (fee_rate_fraction_from_details). It is the ceiling, not the price.
    """
    global _stale_warned
    schedule = float(schedule_fraction)
    artifact = _load_artifact()
    if not isinstance(artifact, dict):
        return schedule, "schedule_no_reconciliation_artifact"
    try:
        n = int(artifact.get("n_fills") or 0)
        observed = float(artifact.get("observed_max_fee_fraction", schedule))
        fitted_at = str(artifact.get("fitted_at") or "")
    except (TypeError, ValueError):
        return schedule, "schedule_artifact_unparseable"
    if n < MIN_FILLS_TO_LICENSE:
        return schedule, f"schedule_insufficient_fills_n={n}"
    try:
        age_days_now = (__import__("time").time() - os.path.getmtime(ARTIFACT_PATH)) / 86400.0
    except OSError:
        age_days_now = float("inf")
    if age_days_now > MAX_EVIDENCE_AGE_DAYS:
        if not _stale_warned:
            logger.warning(
                "fee_authority: realized-fee evidence is stale (age_days=%.0f > "
                "MAX_EVIDENCE_AGE_DAYS=%.0f; fitted_at=%s) -- falling back to the venue "
                "schedule fee %.6f until scripts/reconcile_realized_fees.py is rerun. "
                "Logged once per staleness episode.",
                age_days_now, MAX_EVIDENCE_AGE_DAYS, fitted_at[:10], schedule,
            )
            _stale_warned = True
        return schedule, f"schedule_evidence_stale_age_days={age_days_now:.0f}"
    _stale_warned = False
    # Conservative direction is built in: observed is the MAX realized fraction.
    # Never EXCEED the schedule (the schedule is the venue's own cap).
    fraction = min(max(observed, 0.0), schedule) if schedule > 0 else max(observed, 0.0)
    return fraction, f"realized_fills_n={n}_fitted={fitted_at[:10]}"
