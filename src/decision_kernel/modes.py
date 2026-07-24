"""Certificate mode definitions."""

from __future__ import annotations

from typing import Literal, TypeAlias

CertificateMode: TypeAlias = Literal["LIVE"]

ALLOWED_MODES: frozenset[str] = frozenset({"LIVE"})
