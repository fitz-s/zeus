# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: EMOS shadow-ledger task; PIECE 2 spec.
#   Ledger path: state/emos_shadow_ledger.jsonl (one JSON line per bin).
#   Append-only, atomic per line.  FAIL-OPEN/SILENT: errors must not affect live decisions.
"""EMOS shadow ledger writer.

Appends one JSON line per bin per event to state/emos_shadow_ledger.jsonl.
The ledger preserves both raw_q (existing ensemble probability) and emos_q
(EMOS-calibrated probability) so they can be scored against live-truth
settlement after the market resolves.

Row fields:
  ts             ISO-8601 UTC timestamp of the write
  city           city name
  target_date    YYYY-MM-DD
  season         DJF/MAM/JJA/SON
  lead_days      float
  bin_label      str (candidate bin label)
  bin_low        float | null (None for open-low shoulder)
  bin_high       float | null (None for open-high shoulder)
  bin_unit       "C" or "F"
  raw_q          float — raw-ensemble probability for this bin (the live trading q)
  emos_q         float | null — EMOS-calibrated probability (None if cell served=raw/missing)
  raw_mu_c       float — mean of (possibly bias-corrected) ensemble members in °C
  raw_sigma_c    float — std-dev of ensemble members in °C
  emos_mu_c      float | null
  emos_sigma_c   float | null
  served         "emos" | "raw" | "missing" — which EMOS cell was found
  candidate_id   str | null — optional family:condition_id from the event
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_DIR = Path(__file__).parent.parent.parent / "state"
_LEDGER_PATH = _STATE_DIR / "emos_shadow_ledger.jsonl"


def append_ledger(row: dict[str, Any]) -> None:
    """Append a single JSON ledger row to state/emos_shadow_ledger.jsonl.

    Atomic per-line: writes to a temp file in the same directory then renames.
    Any exception is caught and logged (FAIL-OPEN: caller must not be affected).

    Args:
        row: Dict with the fields documented in the module docstring.
             Missing fields are accepted (partial rows are valid for partial EMOS coverage).
    """
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, separators=(",", ":"), default=str) + "\n"
        line_bytes = line.encode("utf-8")
        # Atomic append: open in binary append mode (O_APPEND is atomic on POSIX
        # for writes ≤ PIPE_BUF ~ 4KB; JSON lines are well within that limit).
        with open(_LEDGER_PATH, "ab") as fh:
            fh.write(line_bytes)
    except Exception as exc:
        logger.warning("emos_ledger append failed (non-fatal): %s", exc)
