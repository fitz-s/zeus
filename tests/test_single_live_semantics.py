# Created: 2026-07-22
# Last reused/audited: 2026-07-22
# Authority basis: operator-directed single-live-semantics extinction pass.
"""Relapse antibodies for dormant alternate-runtime concepts."""

from __future__ import annotations

from pathlib import Path

from scripts.check_single_live_semantics import violations


def test_gate_scans_live_and_current_surfaces(tmp_path: Path) -> None:
    for relative in (
        "src/live.py",
        "config/settings.json",
        "deploy/live.plist",
        "docs/authority/current.md",
        "docs/reference/current.md",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mode = '" + "shadow_" + "veto_only'\n", encoding="utf-8")
    assert len({item.split(":", 1)[0] for item in violations(tmp_path)}) == 5


def test_gate_rejects_resurrected_inactive_lane(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    token = "shadow_" + "veto_only"
    (source / "bad.py").write_text(f"mode = {token!r}\n", encoding="utf-8")
    assert violations(tmp_path)


def test_gate_rejects_retired_runtime_category(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    token = "telemetry_" + "only"
    (source / "bad.py").write_text(f"category = {token!r}\n", encoding="utf-8")
    assert violations(tmp_path)


def test_gate_rejects_extended_alternate_concept_variants(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "allowed.py").write_text(
        "# " + "diag" + "nostic alternate path\n"
        "# offline replay remains evidence-only\n"
        "mode = 'shadow_veto_only_extended'\n",
        encoding="utf-8",
    )
    assert violations(tmp_path)


def test_gate_ignores_historical_and_migration_preview_surfaces(tmp_path: Path) -> None:
    for relative in (
        "docs/archive/old.md",
        "docs/evidence/old.md",
        "docs/rebuild/old.md",
        "docs/operations/current/plans/migration_preview/old.md",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mode = '" + "sha" + "dow'\n", encoding="utf-8")
    assert violations(tmp_path) == []
