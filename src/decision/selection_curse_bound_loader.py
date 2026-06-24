# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/evidence/live_order_pathology/2026-06-23_selection_curse_*.md +
#   src/calibration/anchor_representativeness_debias.py loader pattern (state/<name>.json, module
#   cache, fail-soft) + config.state_path (honors ZEUS_PRIMARY_ROOT — live daemon reads the shared
#   state dir, not its own code-tree state, the zeus-live-main deploy footgun).
"""Read-only, fail-soft loader for the selection-curse bound artifact.

Reads ``state/selection_curse_bound.json`` (fit by scripts/fit_selection_curse_bound.py over the
counterfactual admission ledger) and reconstructs the pure :class:`SelectionCurseBound`. Any problem
(missing file, malformed JSON, missing field, non-monotone band) returns ``None`` — the identity
no-op signal, so a missing/bad artifact can never tighten or break the live gate. Never raises.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Optional

from src.decision.selection_curse_bound import SelectionCurseBound

_LOG = logging.getLogger("zeus.selection_curse_bound_loader")

ARTIFACT_FILENAME = "selection_curse_bound.json"

_cache: dict[str, Optional[SelectionCurseBound]] = {}
_cache_lock = threading.Lock()


def reset_cache() -> None:
    """Drop the module cache (tests / post-refit reload)."""
    with _cache_lock:
        _cache.clear()


def default_artifact_path() -> str:
    """``<RUNTIME_ROOT>/state/selection_curse_bound.json`` via config.state_path (ZEUS_PRIMARY_ROOT-aware)."""
    from src.config import state_path

    return str(state_path(ARTIFACT_FILENAME))


def _parse(data: object) -> Optional[SelectionCurseBound]:
    if not isinstance(data, dict):
        return None
    try:
        return SelectionCurseBound(
            price_knots=tuple(float(x) for x in data["price_knots"]),
            realized_lcb=tuple(float(x) for x in data["realized_lcb"]),
            n_train=int(data["n_train"]),
            armed_sides=frozenset(str(s) for s in data["armed_sides"]),
            artifact_hash=str(data.get("artifact_hash", "")),
            built_at=str(data.get("built_at", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        # Missing field, bad type, non-monotone band (SelectionCurseBound.__post_init__): fail soft.
        _LOG.warning("selection_curse_bound artifact malformed, treating as absent: %s", exc)
        return None


def load_bound(path: Optional[str] = None) -> Optional[SelectionCurseBound]:
    """Load + cache the bound. Returns None (identity no-op) on any error. Cached per resolved path."""
    resolved = path or default_artifact_path()
    if resolved in _cache:
        return _cache[resolved]
    with _cache_lock:
        if resolved in _cache:
            return _cache[resolved]
        bound: Optional[SelectionCurseBound] = None
        try:
            with open(resolved, "r", encoding="utf-8") as fh:
                bound = _parse(json.load(fh))
        except FileNotFoundError:
            bound = None
        except (OSError, json.JSONDecodeError) as exc:
            _LOG.warning("selection_curse_bound artifact unreadable, treating as absent: %s", exc)
            bound = None
        _cache[resolved] = bound
        return bound
