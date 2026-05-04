"""Oracle penalty multiplier for Kelly sizing — evidence-grade.

Loader for ``data/oracle_error_rates.json`` (path resolved through
``src.state.paths.oracle_error_rates_path``). Returns ``OracleInfo``
objects carrying the full Beta-binomial evidence record (counts,
posterior, age) so downstream Kelly sizing can degrade gracefully on
weak/stale/missing signal instead of silently substituting OK.

Pre-A3 behavior (the rescue patch this module replaces)
-------------------------------------------------------
PR #40 (2026-05-02) removed the evaluator-side fail-closed gate to keep
the daemon alive when ``oracle_error_rates.json`` was missing at startup.
The mitigation was ``_DEFAULT_OK = OracleInfo(0.0, OracleStatus.OK, 1.0)``
returned for any (city, metric) not in the cache — i.e., **missing oracle
data was indistinguishable from "this city has 0% error"**. Bug review
Finding A flagged this as the canonical "missing ≠ OK" failure: a city
absent from the file gets full Kelly with no penalty, even though we have
zero evidence the oracle is reliable for it.

A3 closes Finding A by introducing 9 statuses + Beta-binomial posterior
(see ``src/strategy/oracle_estimator.py``). MISSING now resolves to a
multiplier of 0.5 (= Beta(1,1) prior posterior_mean at N=0; this is math,
not a tuning knob — see PLAN.md §5 + D-2).

Classification (computed at ``get_oracle_info`` time, not at load):

    artifact age > 7 days                  → STALE        (mult 0.7)
    n == 0  (record absent or counts=0)    → MISSING      (mult 0.5)
    n < 10                                 → INSUFFICIENT_SAMPLE (mult max(0.5, 1-p95))
    posterior_upper_95 > 0.10              → BLACKLIST    (mult 0.0)
    posterior_upper_95 > 0.05              → CAUTION      (mult min(0.97, 1-p95))
    p95 ≤ 0.05  and  m == 0                → OK           (mult 1.0)
    p95 ≤ 0.05  and  m  > 0                → INCIDENTAL   (mult 1.0)

Other statuses set by load/reload paths:

    metric == "low"                        → METRIC_UNSUPPORTED (mult 0.0)
    JSON parse error during reload         → MALFORMED          (mult prev × 0.7)

LOW track is METRIC_UNSUPPORTED until a LOW oracle snapshot bridge ships
(PLAN.md D-3) — bridge today only measures ``temperature_metric='high'``.

Reload error handling
---------------------
``reload()`` continues to be non-fatal. On JSON parse error or read error,
the previous cache is retained but each entry's status is rewritten to
MALFORMED with multiplier ``prev × 0.7`` (so a malformed write degrades
trade sizing without a hard daemon crash). The evaluator calls reload
each cycle; PR #40 removed the fail-closed evaluator gate, so a parse
error here cannot halt live trading.

Backward compat
---------------
The legacy NamedTuple had attributes ``error_rate``, ``status``,
``penalty_multiplier``. The new ``OracleInfo`` dataclass keeps those
three readable via the same names — ``error_rate`` is now a derived
alias of ``posterior_mean``. Existing evaluator.py callsites
(``oracle.status``, ``oracle.error_rate``, ``oracle.penalty_multiplier``)
continue to work unchanged.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.state.paths import oracle_error_rates_path
from src.strategy.oracle_estimator import (
    classify as _estimator_classify,
    evidence_quality as _estimator_evidence_quality,
    posterior_mean as _posterior_mean,
    posterior_upper_95 as _posterior_upper_95,
)
from src.strategy.oracle_status import OracleStatus

logger = logging.getLogger(__name__)

# Re-export for callers that imported from oracle_penalty pre-A3
# (e.g. ``from src.strategy.oracle_penalty import OracleStatus``).
__all__ = [
    "OracleInfo",
    "OracleStatus",
    "get_oracle_info",
    "is_blacklisted",
    "reload",
]


# ── policy-table multipliers (PLAN.md §A3) ──────────────────────────── #
# Constants, not a function — auditable. Order in this dict mirrors the
# 9-status definition order; readers can grep one place to see the live
# Kelly policy without chasing function arguments.
_BASE_MULTIPLIER: dict[OracleStatus, float] = {
    OracleStatus.OK:                  1.0,
    OracleStatus.INCIDENTAL:          1.0,
    OracleStatus.BLACKLIST:           0.0,
    OracleStatus.MISSING:             0.5,
    OracleStatus.STALE:               0.7,
    OracleStatus.METRIC_UNSUPPORTED:  0.0,
    # CAUTION, MALFORMED, INSUFFICIENT_SAMPLE depend on extra inputs;
    # see _resolve_multiplier.
}

_MALFORMED_DEGRADE_FACTOR: float = 0.7


def _resolve_multiplier(
    status: OracleStatus,
    *,
    posterior_upper_95: float,
    prev_multiplier: float = 1.0,
) -> float:
    """Map a status + supporting evidence to a Kelly multiplier.

    CAUTION / INSUFFICIENT_SAMPLE depend on ``posterior_upper_95`` so
    that a 5.1%-upper city gets a milder penalty than a 9.9%-upper one.
    MALFORMED depends on the previous good multiplier so a single bad
    write degrades sizing instead of zeroing it.
    """
    base = _BASE_MULTIPLIER.get(status)
    if base is not None:
        return base
    if status == OracleStatus.CAUTION:
        # Linear penalty floor at 0.97 so the tightest-CAUTION city still
        # carries a visible Kelly haircut (avoids the 0.999 case looking
        # indistinguishable from OK in logs).
        return min(0.97, 1.0 - posterior_upper_95)
    if status == OracleStatus.INSUFFICIENT_SAMPLE:
        # Floor at 0.5 so a sparse-sample city is never stingier than
        # MISSING — INSUFFICIENT_SAMPLE has SOME evidence but not enough
        # to commit; MISSING has NONE.
        return max(0.5, 1.0 - posterior_upper_95)
    if status == OracleStatus.MALFORMED:
        return prev_multiplier * _MALFORMED_DEGRADE_FACTOR
    # Unknown status — fail-closed.
    return 0.0


# ── OracleInfo dataclass ────────────────────────────────────────────── #


@dataclass(frozen=True)
class OracleInfo:
    """Evidence-grade oracle record for a (city, metric) pair.

    Frozen so callers cannot mutate cached entries.

    Backward compat: ``error_rate`` is now a property aliasing
    ``posterior_mean``. Existing readers (``evaluator.py:2918``,
    ``evaluator.py:3006``) keep working without modification.
    """
    city: str
    metric: str
    source_role: str
    status: OracleStatus
    n: int
    mismatches: int
    posterior_mean: float
    posterior_upper_95: float
    last_observed_date: Optional[str]
    artifact_age_hours: Optional[float]
    evidence_quality: str
    penalty_multiplier: float
    block_reason: Optional[str] = None
    # Internal: not part of the dataclass equality contract; used only
    # so MALFORMED carry-over knows what the prior multiplier was.
    _prev_multiplier: float = field(default=1.0, repr=False, compare=False)

    @property
    def error_rate(self) -> float:
        """Backward-compat alias: pre-A3 callers read ``error_rate``;
        the canonical name post-A3 is ``posterior_mean``. Both refer to
        the same quantity (Bayes-corrected mean of the error rate)."""
        return self.posterior_mean


# ── load + cache ────────────────────────────────────────────────────── #
# Cache stores RAW per-(city, metric) records (status determined at
# load-time for stable statuses, or recomputed via _info_from_record on
# get_oracle_info to pick up artifact-age changes between cycles).

_RawRecord = dict
_cache: Optional[dict[tuple[str, str], _RawRecord]] = None
_cache_status: OracleStatus = OracleStatus.MISSING  # global cache state
_cache_artifact_mtime: Optional[float] = None
_prev_multiplier_cache: dict[tuple[str, str], float] = {}


def _empty_info(
    city: str,
    metric: str,
    *,
    status: OracleStatus,
    block_reason: Optional[str] = None,
    prev_multiplier: float = 1.0,
) -> OracleInfo:
    """Build an info record with zero counts and no posterior — used for
    MISSING / METRIC_UNSUPPORTED / MALFORMED where we have no evidence."""
    mult = _resolve_multiplier(
        status,
        posterior_upper_95=0.95,  # uniform prior 95% upper; only used for floor calc
        prev_multiplier=prev_multiplier,
    )
    return OracleInfo(
        city=city,
        metric=metric,
        source_role="",
        status=status,
        n=0,
        mismatches=0,
        posterior_mean=_posterior_mean(0, 0),  # = 0.5
        posterior_upper_95=0.95,
        last_observed_date=None,
        artifact_age_hours=None,
        evidence_quality=_estimator_evidence_quality(0),
        penalty_multiplier=mult,
        block_reason=block_reason,
        _prev_multiplier=prev_multiplier,
    )


def _info_from_record(
    city: str,
    metric: str,
    record: _RawRecord,
    *,
    artifact_age_hours: Optional[float],
) -> OracleInfo:
    """Build a full OracleInfo from a raw JSON record + artifact age."""
    n = int(record.get("n", record.get("snapshot_comparisons", 0)) or 0)
    mismatches = int(record.get("mismatches", record.get("snapshot_mismatch", 0)) or 0)
    last_observed = record.get("last_observed_date")
    source_role = record.get("source_role", "")

    # Defensive: clamp m to [0, n] so a bad bridge write doesn't crash
    # the estimator. Log + tag MALFORMED instead.
    if mismatches < 0 or mismatches > n:
        return _empty_info(
            city, metric,
            status=OracleStatus.MALFORMED,
            block_reason=f"counts out of range: m={mismatches}, n={n}",
            prev_multiplier=_prev_multiplier_cache.get((city, metric), 1.0),
        )

    status = _estimator_classify(mismatches, n, artifact_age_hours=artifact_age_hours)
    p_mean = _posterior_mean(mismatches, n)
    p95 = _posterior_upper_95(mismatches, n)
    mult = _resolve_multiplier(
        status,
        posterior_upper_95=p95,
        prev_multiplier=_prev_multiplier_cache.get((city, metric), 1.0),
    )
    block_reason: Optional[str] = None
    if status == OracleStatus.BLACKLIST:
        block_reason = (
            f"posterior_upper_95={p95:.3f} > 0.10 (n={n}, m={mismatches})"
        )
    elif status == OracleStatus.STALE:
        block_reason = f"artifact age {artifact_age_hours:.1f}h > 7d threshold"

    return OracleInfo(
        city=city,
        metric=metric,
        source_role=source_role,
        status=status,
        n=n,
        mismatches=mismatches,
        posterior_mean=p_mean,
        posterior_upper_95=p95,
        last_observed_date=last_observed,
        artifact_age_hours=artifact_age_hours,
        evidence_quality=_estimator_evidence_quality(n),
        penalty_multiplier=mult,
        block_reason=block_reason,
        _prev_multiplier=mult,
    )


def _load() -> tuple[dict[tuple[str, str], _RawRecord], Optional[float]]:
    """Load raw oracle records from disk + return artifact mtime in epoch seconds.

    Supports two JSON shapes:
      Nested (current): ``{city: {high: {n, mismatches, oracle_error_rate, ...}, low: {...}}}``
      Legacy flat:      ``{city: {oracle_error_rate: N, ...}}``

    Legacy flat is loaded as ``(city, "high")`` only; the n/mismatches
    keys are absent in legacy files, so the resulting record will
    classify as MISSING (n=0) until the next bridge run writes the new
    schema. This is intentional — pre-A3 files lack the evidence to
    support a posterior, and treating them as MISSING (mult 0.5) is
    safer than continuing the silent-OK behavior.
    """
    oracle_file = oracle_error_rates_path()
    if not oracle_file.exists():
        logger.warning(
            "oracle_error_rates.json not found at %s — all entries → MISSING",
            oracle_file,
        )
        return {}, None

    artifact_mtime: Optional[float] = None
    try:
        artifact_mtime = oracle_file.stat().st_mtime
    except OSError:
        artifact_mtime = None

    with open(oracle_file) as f:
        raw = json.load(f)

    result: dict[tuple[str, str], _RawRecord] = {}
    for city, data in raw.items():
        if not isinstance(data, dict):
            continue
        if "high" in data or "low" in data:
            for metric in ("high", "low"):
                metric_data = data.get(metric)
                if isinstance(metric_data, dict) and _record_is_loadable(metric_data):
                    result[(city, metric)] = metric_data
        else:
            # Legacy flat — treat as (city, "high") with no n/m fields.
            if _record_is_loadable(data):
                result[(city, "high")] = data
    return result, artifact_mtime


def _record_is_loadable(record: _RawRecord) -> bool:
    """Reject records with non-coercible n / mismatches — keeps the cache
    clean of garbage so downstream classify() never sees bad types.

    A record may legitimately omit n/mismatches (legacy flat shape, or a
    pre-A3 file). Those are loadable; ``_info_from_record`` will route
    them to MISSING because ``int(record.get("n", 0)) == 0``.
    """
    for key in ("n", "snapshot_comparisons", "mismatches", "snapshot_mismatch"):
        if key in record:
            try:
                int(record[key])
            except (TypeError, ValueError):
                return False
    return True


def reload() -> None:
    """Force reload of oracle error rates (e.g. after bridge script runs).

    Non-fatal on error. JSON parse errors / read errors are caught: the
    cache state is set to MALFORMED so subsequent ``get_oracle_info``
    calls return MALFORMED records (with multiplier degraded from the
    last known good value). The evaluator calls this every cycle; a
    raise here would halt every candidate evaluation, the failure mode
    PR #40 was opened to remove. Oracle is a sizing modifier, not a
    truth gate.
    """
    global _cache, _cache_status, _cache_artifact_mtime
    try:
        new_cache, new_mtime = _load()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            "oracle_penalty reload failed (%s: %s); cache marked MALFORMED",
            type(exc).__name__,
            exc,
        )
        _cache_status = OracleStatus.MALFORMED
        if _cache is None:
            _cache = {}
        return
    _cache = new_cache
    _cache_artifact_mtime = new_mtime
    _cache_status = OracleStatus.OK if new_cache else OracleStatus.MISSING
    blacklisted = sum(
        1
        for (c, m), r in new_cache.items()
        # Coarse pre-classification for the log line; full classify
        # happens at get_oracle_info time.
        if int(r.get("n", r.get("snapshot_comparisons", 0)) or 0) >= 10
        and _posterior_upper_95(
            int(r.get("mismatches", r.get("snapshot_mismatch", 0)) or 0),
            int(r.get("n", r.get("snapshot_comparisons", 0)) or 0),
        )
        > 0.10
    )
    logger.info(
        "oracle_penalty reloaded: %d records, %d blacklisted",
        len(new_cache),
        blacklisted,
    )


def _artifact_age_hours() -> Optional[float]:
    """Hours since oracle_error_rates.json was last written; None if
    missing or mtime unreadable."""
    if _cache_artifact_mtime is None:
        return None
    now = datetime.now(timezone.utc).timestamp()
    return max(0.0, (now - _cache_artifact_mtime) / 3600.0)


def get_oracle_info(city_name: str, temperature_metric: str = "high") -> OracleInfo:
    """Return evidence-grade oracle info for a (city, metric) pair.

    Order of resolution:

    1. ``temperature_metric == "low"``  → METRIC_UNSUPPORTED (mult 0)
       (PLAN.md D-3: bridge measures HIGH only; LOW oracle bridge is its
       own future PR.)
    2. Cache empty / load failed         → MISSING (mult 0.5) or MALFORMED
                                           depending on global cache state.
    3. (city, metric) absent from cache  → MISSING (mult 0.5)
    4. Otherwise classify via ``oracle_estimator`` and return full record.
    """
    global _cache
    if _cache is None:
        reload()

    if temperature_metric == "low":
        return _empty_info(
            city_name,
            temperature_metric,
            status=OracleStatus.METRIC_UNSUPPORTED,
            block_reason="LOW oracle bridge not yet shipped (PLAN.md D-3)",
        )

    if _cache_status == OracleStatus.MALFORMED:
        return _empty_info(
            city_name,
            temperature_metric,
            status=OracleStatus.MALFORMED,
            block_reason="oracle_error_rates.json malformed; degraded carry-over",
            prev_multiplier=_prev_multiplier_cache.get(
                (city_name, temperature_metric), 1.0
            ),
        )

    record = (_cache or {}).get((city_name, temperature_metric))
    if record is None:
        return _empty_info(
            city_name,
            temperature_metric,
            status=OracleStatus.MISSING,
            block_reason="city/metric absent from oracle_error_rates.json",
        )

    info = _info_from_record(
        city_name,
        temperature_metric,
        record,
        artifact_age_hours=_artifact_age_hours(),
    )
    # Track last good multiplier so MALFORMED degrade-carry uses the
    # most recent stable value instead of always restarting at 1.0.
    if info.status not in (OracleStatus.MALFORMED, OracleStatus.MISSING):
        _prev_multiplier_cache[(city_name, temperature_metric)] = info.penalty_multiplier
    return info


def is_blacklisted(city_name: str, temperature_metric: str = "high") -> bool:
    """Convenience predicate kept for backward compat — evaluator.py
    historically called this. New code should read
    ``get_oracle_info(...).status == OracleStatus.BLACKLIST`` directly
    so block_reason / posterior values are also visible."""
    return get_oracle_info(city_name, temperature_metric).status == OracleStatus.BLACKLIST


# Test helper: clear all module-level state so a test that mutates the
# environment between runs gets a fresh module state. NOT public API;
# only test fixtures should call this.
def _reset_for_test() -> None:
    global _cache, _cache_status, _cache_artifact_mtime, _prev_multiplier_cache
    _cache = None
    _cache_status = OracleStatus.MISSING
    _cache_artifact_mtime = None
    _prev_multiplier_cache = {}
