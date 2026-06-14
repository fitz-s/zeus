# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis:
#   docs/evidence/investigation_2026-06-13/cold_bias_metadata_root.md (ROOT = per-city 9km
#     grid-cell-vs-settlement-station representativeness offset; per-city, two-sign, lead-stable;
#     correctable ONLY by a per-city de-bias, NOT a global constant).
#   docs/evidence/investigation_2026-06-13/percity_corrected_oos.md (per-city δ is the right
#     SHAPE but OVERFITS on thin live-anchor history → must be activation-guarded + EB-shrunk and
#     fit on the FULL previous_runs history; do-no-harm walk-forward gate before it applies).
#   operator law 2026-06-12 (no hardcoded constants; FITTED artifact only).
#   docs/evidence/investigation_2026-06-13/percity_debias_impl.md (this loader's design + schema).
"""Per-city anchor representativeness de-bias loader (read-only, fail-soft, activation-guarded).

Reads the FITTED artifact ``state/anchor_representativeness_debias.json`` (written ONLY by
``scripts/fit_anchor_representativeness_debias.py`` — EB-shrunk robust per-city median of the
OpenMeteo IFS9 anchor residual over the full previous_runs VERIFIED history) and returns the
per-city de-bias δ_city for a (city, metric) cell.

SAFE-ON-THIN-DATA CONTRACT — δ_city is returned ONLY when ALL hold; else None (the materializer
then leaves ``bias_shift_c = None`` → the current family-level de-bias, do no harm):
  (1) metric == 'high'. The artifact's LOW family did not pass the do-no-harm walk-forward gate
      on the current sparse LOW history (mirrors src/calibration/grid_representativeness.get_offset
      which also FAILS CLOSED on non-high). To enable LOW, the fitter must produce a LOW family
      whose walk_forward.do_no_harm is True.
  (2) the family's walk_forward.do_no_harm is True (the fit reduces OOS anchor MAE; never wired
      when it would worsen aggregate accuracy).
  (3) the city entry exists AND is ``activated`` (n >= n_min — the per-city mean's SE is small).
      Thin cities (< n_min) are NOT activated → None → family-level fallback.

SIGN: δ_city = median(anchor_c − settlement_c). The materializer applies the standard
``bias_shift_c`` contract ``corrected = raw − δ_city`` (a cold anchor, δ<0, is warmed; a hot
anchor, δ>0, is cooled), and that corrected center feeds the soft-anchor / fusion prior so the
de-bias propagates into the fused posterior μ*.

FAIL-SOFT: any error (missing/malformed artifact, family absent, non-finite δ) → None. Never
raises. Module-level cache (thread-safe), mirroring grid_representativeness.py.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from typing import Any

_LOG = logging.getLogger("zeus.anchor_representativeness_debias")

_ARTIFACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "state",
    "anchor_representativeness_debias.json",
)

_cache: dict[str, Any] | None = None
_cache_lock = threading.Lock()


def _load_table() -> dict[str, Any]:
    """Load + cache the fitted artifact (thread-safe). {} on any error."""
    global _cache
    if _cache is not None:
        return _cache
    with _cache_lock:
        if _cache is not None:
            return _cache
        try:
            with open(_ARTIFACT_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            _cache = data if isinstance(data, dict) else {}
        except Exception as exc:
            _LOG.warning("anchor_representativeness_debias: failed to load artifact: %s", exc)
            _cache = {}
        return _cache


def load_debias_table() -> dict[str, Any]:
    """Return the raw parsed artifact dict (for inspection / testing)."""
    return _load_table()


def reset_cache() -> None:
    """Drop the module cache (tests that rewrite the artifact between assertions)."""
    global _cache
    with _cache_lock:
        _cache = None


def get_city_debias_c(city: str, metric: str = "high") -> float | None:
    """Return the activated, do-no-harm-gated per-city de-bias δ_city (°C), else None.

    None means "use the current family-level de-bias" — the safe fallback. See module docstring
    for the full activation contract. FAIL-CLOSED on anything unexpected.
    """
    try:
        if str(metric).lower() != "high":
            return None  # fail closed: only the HIGH family passed the do-no-harm gate.
        table = _load_table()
        fam = (table.get("families") or {}).get("high")
        if not isinstance(fam, dict) or not fam.get("fitted"):
            return None
        # GATE (2): the family-level walk-forward must show the de-bias does no harm OOS.
        wf = fam.get("walk_forward")
        if not isinstance(wf, dict) or wf.get("do_no_harm") is not True:
            return None
        entry = (fam.get("cities") or {}).get(city)
        # GATE (3): the city must exist AND be activated (n >= n_min).
        if not isinstance(entry, dict) or not entry.get("activated", False):
            return None
        delta = float(entry.get("delta_c"))
        if not math.isfinite(delta):
            return None
        return delta
    except Exception as exc:
        _LOG.warning("anchor_representativeness_debias.get_city_debias_c(%s,%s): %s", city, metric, exc)
        return None
