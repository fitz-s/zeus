"""ATTRIBUTION_DRIFT packet — BATCH 1: per-position attribution-drift detector.

Created: 2026-04-28
Last reused/audited: 2026-04-28
Authority basis: round3_verdict.md §1 #2 (R3 next packet) + ULTIMATE_PLAN.md
L305-308 (silent attribution drift detector). Per AGENTS.md L114-126:
"strategy_key is the sole governance identity for attribution, risk policy,
and performance slicing." A position whose persisted strategy_key disagrees
with what the entry-time dispatch rule WOULD assign given the same
attributes is a silent attribution drift; this detector catches it.

K1 contract (mirrors src/state/edge_observation.py):
  - Read-only: pure analysis of position_events rows; no DB writes; no caches.
  - Reads canonical surface via query_authoritative_settlement_rows + the
    metric_ready filter (db.py:3345 vs is_degraded — same measurement-vs-
    learning split as edge_observation; reconfirmed in BATCH 1 critic review).
  - History note: this packet is the follow-up to ULTIMATE_PLAN's
    "no detector exists for silent attribution drift" — the detector exists
    now; it answers "does the persisted strategy_key match what the
    entry-time _strategy_key_for rule would assign on the same attributes?"

Ground-truth dispatch rule (mirrored from src/engine/evaluator.py L420-441):
  1. discovery_mode == 'day0_capture'   → 'settlement_capture'
  2. discovery_mode == 'opening_hunt'   → 'opening_inertia'
  3. bin.is_shoulder                     → 'shoulder_sell'
  4. direction == 'buy_yes'              → 'center_buy'
  5. fallback                            → 'opening_inertia'

Known limitations (per BATCH 1 boot §1 + dispatch GO_BATCH_1 note):
  - `discovery_mode` is NOT surfaced by `_normalize_position_settlement_event`
    in the row dict. Without it, clauses 1-2 of the dispatch rule cannot be
    applied. Positions whose persisted `strategy` is `settlement_capture` OR
    `opening_inertia` (which require clause 1 or 2 OR fall through clause 5)
    are therefore not definitively classifiable from row alone — the
    detector emits `insufficient_signal` for them rather than risk a false
    `drift_detected`.
  - `bin.is_shoulder` must be inferred from the persisted `bin_label` string
    (the Bin object is not persisted). Per AGENTS.md L66 antibody warning,
    label-based shoulder inference is heuristic. The classifier conservatively
    falls back to `unknown` topology when the label format is ambiguous, and
    the detector emits `insufficient_signal` rather than risking a false
    drift verdict.
  - Result: the detector is RECALL-LIMITED (some real drifts may not be
    detected because the input data lacks discovery_mode or the bin label
    is ambiguous) but PRECISION-FAVORED (every drift it reports is a real
    label/semantics mismatch on at least one of clauses 3-5).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from src.state.db import query_authoritative_settlement_rows
from src.state.edge_observation import STRATEGY_KEYS

BinTopology = Literal["point", "finite_range", "open_shoulder", "unknown"]
DriftKind = Literal["label_matches_semantics", "drift_detected", "insufficient_signal"]


@dataclass
class AttributionSignature:
    """Per-position fingerprint used by detect_attribution_drift."""
    position_id: str
    label_strategy: str                       # the persisted strategy_key
    inferred_strategy: str | None             # what the dispatch rule yields, or None
    bin_topology: BinTopology
    direction: str                            # buy_yes / buy_no / unknown
    discovery_mode: str | None                # only present if surfaced by upstream
    bin_label: str
    is_label_inferable: bool                  # False if cannot apply enough clauses


@dataclass
class AttributionVerdict:
    """Result of detect_attribution_drift for one position."""
    kind: DriftKind
    position_id: str
    signature: AttributionSignature
    evidence: dict[str, Any] = field(default_factory=dict)


# ----- Bin-topology classifier ---------------------------------------------
# Per AGENTS.md L60-67: open_shoulder bins are unbounded ("X°F or below" /
# "X°F or above" / "X+"); point bins are single integer °C; finite_range
# bins are e.g. "50-51°F".

_SHOULDER_PATTERNS = (
    re.compile(r"\bor below\b", re.IGNORECASE),
    re.compile(r"\bor higher\b", re.IGNORECASE),
    re.compile(r"\bor above\b", re.IGNORECASE),
    re.compile(r"\bor more\b", re.IGNORECASE),
    re.compile(r"\bor less\b", re.IGNORECASE),
    re.compile(r"^\d+\s*°?[FC]?\s*\+\s*$"),         # e.g., "75°F+" or "75+"
    re.compile(r"^\s*[-]?\d+\s*°?[FC]?\s*-\s*$"),    # e.g., "-10°F-" trailing dash
    re.compile(r"^\s*<=\s*[-]?\d"),
    re.compile(r"^\s*>=\s*[-]?\d"),
)
_POINT_RE = re.compile(r"^\s*[-]?\d+\s*°?C\s*$", re.IGNORECASE)
_FINITE_RANGE_RE = re.compile(r"^\s*[-]?\d+\s*-\s*[-]?\d+\s*°?[FC]?\s*$", re.IGNORECASE)


def _classify_bin_topology(bin_label: str | None) -> BinTopology:
    """Infer bin topology from the persisted label string. Conservative —
    returns 'unknown' rather than guess when the label is ambiguous."""
    if not bin_label:
        return "unknown"
    label = bin_label.strip()
    for p in _SHOULDER_PATTERNS:
        if p.search(label):
            return "open_shoulder"
    if _POINT_RE.match(label):
        return "point"
    if _FINITE_RANGE_RE.match(label):
        return "finite_range"
    return "unknown"


# ----- Dispatch-rule re-application ----------------------------------------

def _infer_strategy_from_signature(sig: AttributionSignature) -> str | None:
    """Re-apply the entry-time _strategy_key_for dispatch rule from
    src/engine/evaluator.py:420-441. Returns None when discovery_mode is
    missing AND clauses 3-5 cannot definitively rule out clauses 1-2."""
    # Clauses 1-2: discovery_mode-based.
    if sig.discovery_mode == "day0_capture":
        return "settlement_capture"
    if sig.discovery_mode == "opening_hunt":
        return "opening_inertia"

    # If discovery_mode is missing AND the persisted label is one of the
    # discovery-mode-derived strategies, we cannot tell whether clause 1/2
    # would have fired. Defer to insufficient_signal.
    if sig.discovery_mode is None and sig.label_strategy in {"settlement_capture", "opening_inertia"}:
        return None

    # Clause 3: shoulder bin → shoulder_sell.
    if sig.bin_topology == "open_shoulder":
        return "shoulder_sell"
    if sig.bin_topology == "unknown":
        # Cannot rule out shoulder; not safe to assert center_buy/opening_inertia.
        return None

    # Clauses 4-5: direction-based fallthrough.
    if sig.direction == "buy_yes":
        return "center_buy"
    return "opening_inertia"


# ----- Per-position drift detector -----------------------------------------

def _build_signature(row: dict[str, Any]) -> AttributionSignature:
    label_strategy = str(row.get("strategy") or "")
    bin_label = str(row.get("bin_label") or row.get("range_label") or "")
    direction = str(row.get("direction") or "unknown")
    discovery_mode = row.get("discovery_mode")
    if discovery_mode is not None:
        discovery_mode = str(discovery_mode)
    sig = AttributionSignature(
        position_id=str(row.get("trade_id") or row.get("position_id") or ""),
        label_strategy=label_strategy,
        inferred_strategy=None,
        bin_topology=_classify_bin_topology(bin_label),
        direction=direction,
        discovery_mode=discovery_mode,
        bin_label=bin_label,
        is_label_inferable=False,
    )
    sig.inferred_strategy = _infer_strategy_from_signature(sig)
    sig.is_label_inferable = sig.inferred_strategy is not None
    return sig


def detect_attribution_drift(row: dict[str, Any]) -> AttributionVerdict:
    """Compare persisted strategy_key against re-applied dispatch rule.

    Returns one of:
      label_matches_semantics : persisted label == inferred label
      drift_detected          : persisted label != inferred label
      insufficient_signal     : cannot infer (missing discovery_mode +
                                ambiguous bin_label, OR unknown strategy_key)
    """
    sig = _build_signature(row)

    # Quarantine: unknown persisted strategy_key.
    if sig.label_strategy not in STRATEGY_KEYS:
        return AttributionVerdict(
            kind="insufficient_signal",
            position_id=sig.position_id,
            signature=sig,
            evidence={"reason": "label_not_in_governed_strategy_keys",
                      "label_strategy": sig.label_strategy},
        )

    if sig.inferred_strategy is None:
        return AttributionVerdict(
            kind="insufficient_signal",
            position_id=sig.position_id,
            signature=sig,
            evidence={
                "reason": "cannot_infer_strategy_from_row",
                "discovery_mode_present": sig.discovery_mode is not None,
                "bin_topology": sig.bin_topology,
                "label_strategy": sig.label_strategy,
            },
        )

    if sig.label_strategy == sig.inferred_strategy:
        return AttributionVerdict(
            kind="label_matches_semantics",
            position_id=sig.position_id,
            signature=sig,
            evidence={"inferred_strategy": sig.inferred_strategy,
                      "bin_topology": sig.bin_topology,
                      "direction": sig.direction},
        )

    return AttributionVerdict(
        kind="drift_detected",
        position_id=sig.position_id,
        signature=sig,
        evidence={
            "label_strategy": sig.label_strategy,
            "inferred_strategy": sig.inferred_strategy,
            "bin_topology": sig.bin_topology,
            "direction": sig.direction,
            "discovery_mode": sig.discovery_mode,
            "bin_label": sig.bin_label,
            "mismatch_summary": (
                f"label={sig.label_strategy!r} but evaluator dispatch rule on "
                f"persisted attributes (bin_topology={sig.bin_topology!r}, "
                f"direction={sig.direction!r}, discovery_mode={sig.discovery_mode!r}) "
                f"yields {sig.inferred_strategy!r}"
            ),
        },
    )


# ----- BATCH 1 thin-wrapper for "all positions in a window" ---------------

def detect_drifts_in_window(
    conn: sqlite3.Connection,
    window_days: int = 7,
    end_date: str | None = None,
) -> list[AttributionVerdict]:
    """Read settled rows in the window and return per-position verdicts.

    K1-compliant read path: uses query_authoritative_settlement_rows with
    not_before=window_start, applies metric_ready filter (same as
    edge_observation), then runs detect_attribution_drift on each row.
    """
    if window_days <= 0:
        raise ValueError(f"window_days must be positive; got {window_days}")
    if end_date is None:
        end = datetime.now(timezone.utc).date()
    else:
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    window_start = (end - timedelta(days=window_days)).isoformat()
    window_end = end.isoformat()

    rows = query_authoritative_settlement_rows(conn, limit=None, not_before=window_start)
    verdicts: list[AttributionVerdict] = []
    for row in rows:
        if not row.get("metric_ready"):
            continue
        settled_at = str(row.get("settled_at") or "")
        if settled_at[:10] > window_end:
            continue
        verdicts.append(detect_attribution_drift(row))
    return verdicts
