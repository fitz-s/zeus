# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §4 sunset 2027-05-06; IMPLEMENTATION_PLAN Phase 0.F

"""Generative route function — Phase 0.F implementation.

Reads capabilities.yaml and invariants.yaml, cross-references changed paths,
returns a RouteCard. Token-budgeted render function covers T0..T3 tiers.

Replaces nothing yet (shadow mode only). Sunset: 2027-05-06.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
_CAPS_PATH = REPO_ROOT / "architecture" / "capabilities.yaml"
_INV_PATH = REPO_ROOT / "architecture" / "invariants.yaml"

# Reversibility severity order (higher = more severe).
_SEVERITY: dict[str, int] = {
    "WORKING": 1,
    "ARCHIVE": 2,
    "TRUTH_REWRITE": 3,
    "ON_CHAIN": 4,
}


@dataclass(frozen=True)
class RouteCard:
    """Typed route summary produced by route()."""

    capabilities: list[str]
    invariants: list[str]
    relationship_tests: list[str]
    hard_kernel_hits: list[str]
    reversibility: str  # max severity class hit
    leases: list[str]

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"RouteCard(capabilities={self.capabilities!r}, "
            f"invariants={self.invariants!r}, "
            f"reversibility={self.reversibility!r}, "
            f"leases={self.leases!r})"
        )


def _load_capabilities() -> list[dict]:
    with _CAPS_PATH.open() as f:
        return yaml.safe_load(f)["capabilities"]


def _load_invariants() -> list[dict]:
    try:
        with _INV_PATH.open() as f:
            data = yaml.safe_load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("invariants", data.get("entries", []))
    except FileNotFoundError:
        pass
    return []


def _path_matches(diff_path: str, kernel_paths: list[str]) -> bool:
    """True if diff_path matches any kernel path (exact or suffix)."""
    dp = diff_path.replace("\\", "/")
    for kp in kernel_paths:
        kp = kp.replace("\\", "/")
        if dp == kp or dp.endswith("/" + kp) or kp.endswith("/" + dp):
            return True
    return False


def _tests_for_capability(cap: dict, inv_entries: list[dict]) -> list[str]:
    """Collect relationship_tests from matching invariant entries."""
    inv_ids = cap.get("relationships", {}).get("protects_invariants", [])
    tests: list[str] = []
    for inv in inv_entries:
        if inv.get("id") in inv_ids:
            rt = inv.get("relationship_tests", [])
            if isinstance(rt, list):
                tests.extend(rt)
    return tests


def route(diff_paths: list[str], task_text: str = "") -> RouteCard:
    """Map a list of changed file paths to a RouteCard.

    Args:
        diff_paths: File paths relative to repo root (or absolute).
        task_text:  Optional free-form task description (reserved for Phase 3
                    keyword matching against original_intent).

    Returns:
        RouteCard with all capability hits merged.
    """
    caps = _load_capabilities()
    invs = _load_invariants()

    hits: list[dict] = []
    for cap in caps:
        kernel = cap.get("hard_kernel_paths", [])
        if any(_path_matches(dp, kernel) for dp in diff_paths):
            hits.append(cap)

    cap_ids = [c["id"] for c in hits]
    inv_ids = sorted({
        iid
        for c in hits
        for iid in c.get("relationships", {}).get("protects_invariants", [])
    })
    rel_tests = sorted({
        t for c in hits for t in _tests_for_capability(c, invs)
    })
    leases = sorted({c["id"] for c in hits if c.get("lease_required")})
    kernel_hits = [c["owner_module"] for c in hits]

    if hits:
        rev = max(
            hits,
            key=lambda c: _SEVERITY.get(c.get("reversibility_class", "WORKING"), 1),
        ).get("reversibility_class", "WORKING")
    else:
        rev = "WORKING"

    return RouteCard(
        capabilities=cap_ids,
        invariants=inv_ids,
        relationship_tests=rel_tests,
        hard_kernel_hits=kernel_hits,
        reversibility=rev,
        leases=leases,
    )


# ---------------------------------------------------------------------------
# Token-budgeted render
# ---------------------------------------------------------------------------

def _token_count(text: str) -> int:
    """Count tokens using tiktoken cl100k_base; fall back to char/4."""
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.encoding_for_model("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


_TIER_LIMITS = {0: 500, 1: 1000, 2: 2000, 3: 4000}


def render(card: RouteCard, tier: int = 0) -> str:
    """Render a RouteCard as a human-readable string within token budget."""
    limit = _TIER_LIMITS.get(tier, 4000)

    def _fmt_list(items: list[str], label: str, max_items: int | None = None) -> str:
        shown = items[:max_items] if max_items else items
        suffix = f" (+{len(items) - len(shown)} more)" if max_items and len(items) > len(shown) else ""
        if not shown:
            return f"{label}: (none)"
        return f"{label}: {', '.join(shown)}{suffix}"

    if tier == 0:
        caps = ", ".join(card.capabilities) or "(none)"
        rev = card.reversibility
        lease_tag = " [LEASE REQUIRED]" if card.leases else ""
        text = f"route: caps=[{caps}] rev={rev}{lease_tag}"
        while _token_count(text) > limit and len(caps) > 20:
            caps = caps[:len(caps) // 2] + "…"
            text = f"route: caps=[{caps}] rev={rev}{lease_tag}"
        return text

    lines = [
        "=== RouteCard ===",
        _fmt_list(card.capabilities, "capabilities"),
        _fmt_list(card.invariants, "invariants"),
        f"reversibility: {card.reversibility}",
        _fmt_list(card.leases, "leases"),
    ]

    if tier >= 2:
        lines.append(_fmt_list(card.hard_kernel_hits, "kernel_hits"))
        max_tests = 5 if tier == 2 else None
        lines.append(_fmt_list(card.relationship_tests, "relationship_tests", max_items=max_tests))
    elif tier == 1:
        lines.append(_fmt_list(card.hard_kernel_hits, "kernel_hits"))

    text = "\n".join(lines)
    if tier == 2 and _token_count(text) > limit:
        lines[-1] = _fmt_list(card.relationship_tests, "relationship_tests", max_items=2)
        text = "\n".join(lines)

    return text
