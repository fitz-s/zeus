# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §3 sunset 2027-05-06; IMPLEMENTATION_PLAN Phase 0.E

"""Capability and invariant-protection decorators.

Runtime no-ops: decorators record metadata into module-level registries and
return the wrapped function unchanged. The registries are consumed by the
route function (Phase 0.F+) and the diff verifier (Phase 4).
"""

from __future__ import annotations

from typing import Callable

_CAPABILITY_REGISTRY: dict[str, list[Callable]] = {}
_INVARIANT_REGISTRY: dict[str, list[Callable]] = {}


def capability(cap_id: str, *, lease: bool | None = None) -> Callable:
    """Mark a function as the writer for a capability.

    CI lint (test_capability_decorator_coverage.py) asserts every path in
    capabilities.yaml::hard_kernel_paths carries this decorator.
    The lease kwarg mirrors capabilities.yaml::lease_required for route-card
    generation; it has no runtime effect in Phase 0.
    """
    def decorator(fn: Callable) -> Callable:
        _CAPABILITY_REGISTRY.setdefault(cap_id, []).append(fn)
        fn._capability_ids = getattr(fn, '_capability_ids', []) + [cap_id]  # type: ignore[attr-defined]
        if not hasattr(fn, '_capability_id'):  # type: ignore[attr-defined]
            fn._capability_id = cap_id  # type: ignore[attr-defined]
        fn._capability_lease = lease  # type: ignore[attr-defined]
        return fn
    return decorator


def protects(*invariant_ids: str) -> Callable:
    """Mark a function as the runtime anchor of one or more invariants."""
    def decorator(fn: Callable) -> Callable:
        for inv_id in invariant_ids:
            _INVARIANT_REGISTRY.setdefault(inv_id, []).append(fn)
        fn._protects_invariants = list(invariant_ids)  # type: ignore[attr-defined]
        return fn
    return decorator


def get_capability_writers(cap_id: str) -> list[Callable]:
    """Return all functions registered under cap_id."""
    return list(_CAPABILITY_REGISTRY.get(cap_id, []))


def get_invariant_anchors(inv_id: str) -> list[Callable]:
    """Return all functions registered as anchors for inv_id."""
    return list(_INVARIANT_REGISTRY.get(inv_id, []))
