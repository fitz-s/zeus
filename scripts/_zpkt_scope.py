# Lifecycle: created=2026-04-25; last_reviewed=2026-04-26; last_reused=2026-04-26
# Purpose: Load packet scope sidecars and classify paths for packet-runtime and hook checks.
# Reuse: Update when scope.yaml location, active-packet pointer semantics, or scope glob behavior changes.
"""Packet scope load + match helpers shared by ``zpkt`` and the pre-commit hook.

Authoritative answer for: *given a staged file path, is it in scope for the
currently active packet?*

This module is intentionally tiny and dependency-free (only stdlib + PyYAML)
because the pre-commit hook imports it on every commit attempt. It MUST stay
fast (<50 ms cold) and MUST NOT take a network call.

Scope semantics
---------------
A packet's ``scope.yaml`` declares three glob lists:

* ``in_scope``         -- explicit allow list. Matching files are admitted.
* ``allow_companions`` -- registry/mesh files that may be touched as side
                          effects. Admitted but tagged so reports can
                          distinguish primary work from bookkeeping.
* ``out_of_scope``     -- explicit deny list. Matching files always violate,
                          even if they also match ``in_scope`` (forbidden-wins).

Files matching nothing are reported as ``unscoped``. The hook treats unscoped
files the same as ``out_of_scope`` for reporting purposes but with a different
remedy hint (likely the agent forgot to widen scope).

The matcher uses ``fnmatch.fnmatchcase`` with one Zeus-flavoured extension:
``**`` is rewritten to ``*`` for compatibility with our existing manifests
which already use that idiom.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterable, Sequence

import yaml


SCOPE_FILENAME = "scope.yaml"
ACTIVE_PACKET_FILE = "state/active_packet.txt"
PACKET_ROOT = "docs/operations"


class ScopeError(Exception):
    """Raised when a scope.yaml is malformed or missing required fields."""


@dataclass(frozen=True)
class PacketScope:
    """In-memory representation of a packet's scope.yaml."""

    packet: str
    in_scope: tuple[str, ...]
    allow_companions: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    raw: dict = field(default_factory=dict, compare=False)

    @classmethod
    def load(cls, path: Path) -> "PacketScope":
        if not path.is_file():
            raise ScopeError(f"scope.yaml not found at {path}")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ScopeError(f"scope.yaml at {path} is not valid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ScopeError(f"scope.yaml at {path} must be a mapping")
        packet = data.get("packet")
        if not isinstance(packet, str) or not packet.strip():
            raise ScopeError(f"scope.yaml at {path} missing 'packet'")
        in_scope = _coerce_list(data.get("in_scope"), "in_scope", path)
        if not in_scope:
            raise ScopeError(f"scope.yaml at {path} has empty 'in_scope'")
        return cls(
            packet=packet.strip(),
            in_scope=tuple(in_scope),
            allow_companions=tuple(_coerce_list(data.get("allow_companions"), "allow_companions", path)),
            out_of_scope=tuple(_coerce_list(data.get("out_of_scope"), "out_of_scope", path)),
            raw=data,
        )


def _coerce_list(value, field_name: str, path: Path) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ScopeError(f"scope.yaml at {path} field '{field_name}' must be a list")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ScopeError(
                f"scope.yaml at {path} field '{field_name}' entries must be non-empty strings"
            )
        out.append(item.strip())
    return out


@dataclass(frozen=True)
class Classification:
    """Result of classifying a single staged path against a scope."""

    path: str
    bucket: str  # "in_scope" | "companion" | "out_of_scope" | "unscoped"
    matched_pattern: str | None  # the glob that matched (None for unscoped)


def _normalize(path: str) -> str:
    """Apply the same path normalization the topology kernel uses."""
    p = path.strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def _glob_match(path: str, patterns: Sequence[str]) -> str | None:
    norm = _normalize(path)
    for pat in patterns:
        # Treat `**` as `*` so manifests stay portable across matchers.
        flat = pat.replace("**", "*")
        if fnmatchcase(norm, flat) or fnmatchcase(norm, pat):
            return pat
    return None


def classify(scope: PacketScope, path: str) -> Classification:
    """Classify a path against a scope. Forbidden-wins ordering."""
    norm = _normalize(path)
    forbidden = _glob_match(norm, scope.out_of_scope)
    if forbidden is not None:
        return Classification(path=norm, bucket="out_of_scope", matched_pattern=forbidden)
    in_scope = _glob_match(norm, scope.in_scope)
    if in_scope is not None:
        return Classification(path=norm, bucket="in_scope", matched_pattern=in_scope)
    companion = _glob_match(norm, scope.allow_companions)
    if companion is not None:
        return Classification(path=norm, bucket="companion", matched_pattern=companion)
    return Classification(path=norm, bucket="unscoped", matched_pattern=None)


def classify_many(scope: PacketScope, paths: Iterable[str]) -> list[Classification]:
    return [classify(scope, p) for p in paths]


def find_active_packet(repo_root: Path) -> str | None:
    """Read state/active_packet.txt; return packet path under docs/operations."""
    pointer = repo_root / ACTIVE_PACKET_FILE
    if not pointer.is_file():
        return None
    raw = pointer.read_text(encoding="utf-8").strip()
    return raw or None


def scope_path_for(repo_root: Path, packet: str) -> Path:
    return repo_root / PACKET_ROOT / packet / SCOPE_FILENAME


def load_active_scope(repo_root: Path) -> PacketScope | None:
    """Convenience: read pointer, then load scope. Returns None if no packet."""
    packet = find_active_packet(repo_root)
    if packet is None:
        return None
    return PacketScope.load(scope_path_for(repo_root, packet))


__all__ = [
    "Classification",
    "PacketScope",
    "ScopeError",
    "classify",
    "classify_many",
    "find_active_packet",
    "load_active_scope",
    "scope_path_for",
    "SCOPE_FILENAME",
    "ACTIVE_PACKET_FILE",
    "PACKET_ROOT",
]
