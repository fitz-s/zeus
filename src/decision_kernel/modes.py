"""Certificate mode definitions."""

from __future__ import annotations

from typing import Literal, TypeAlias

CertificateMode: TypeAlias = Literal["LIVE", "NO_SUBMIT", "SHADOW", "REPLAY_COUNTERFACTUAL"]

LIVE_LIKE_MODES: frozenset[str] = frozenset({"LIVE", "NO_SUBMIT"})
ALLOWED_MODES: frozenset[str] = frozenset({"LIVE", "NO_SUBMIT", "SHADOW", "REPLAY_COUNTERFACTUAL"})


def is_live_like(mode: str) -> bool:
    return mode in LIVE_LIKE_MODES
