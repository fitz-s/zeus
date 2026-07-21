"""Antibody for the W2 manifest-rot gate (scripts/ops/audit_manifest_rot.py).

Fixture-only: a manifest labeling tables droppable + a fixture DB, proving the gate flags a
droppable-labeled table that actually has rows and does NOT flag a genuinely-absent one.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "audit_manifest_rot.py"
_spec = importlib.util.spec_from_file_location("audit_manifest_rot", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

MANIFEST = """\
tables:
  - name: live_but_archived
    db: trade
    schema_class: legacy_archived
    notes: "Ghost. Drop after 2026-08-09."
  - name: genuinely_absent
    db: trade
    schema_class: legacy_archived
    notes: "residual drift"
  - name: healthy_current
    db: trade
    schema_class: trade_class
    notes: "the live one"
  - name: empty_archived
    db: trade
    schema_class: legacy_archived
    notes: "ghost, empty"
"""


def _fixture(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    db = state / "zeus_trades.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE live_but_archived (id INTEGER)")
    c.execute("INSERT INTO live_but_archived VALUES (1)")   # has rows -> rot
    c.execute("CREATE TABLE healthy_current (id INTEGER)")
    c.execute("CREATE TABLE empty_archived (id INTEGER)")   # exists but empty, no writer -> not rot
    # genuinely_absent: not created
    c.commit(); c.close()
    man = tmp_path / "manifest.yaml"; man.write_text(MANIFEST)
    return man, state


def test_flags_live_but_archived_not_absent_or_empty(tmp_path, monkeypatch):
    man, state = _fixture(tmp_path)
    # no live writers in this fixture repo -> flag purely on has_rows
    monkeypatch.setattr(mod, "_has_writer", lambda name: False)
    rot = mod.audit(man, state)
    names = {r["name"] for r in rot}
    assert "live_but_archived" in names          # labeled droppable + has rows
    assert "genuinely_absent" not in names        # absent -> safe
    assert "healthy_current" not in names          # not droppable-labeled
    assert "empty_archived" not in names           # exists but empty + no writer -> safe


def test_live_writer_alone_flags(tmp_path, monkeypatch):
    man, state = _fixture(tmp_path)
    # empty_archived gains a live writer -> must be flagged even with no rows
    monkeypatch.setattr(mod, "_has_writer", lambda name: name == "empty_archived")
    rot = mod.audit(man, state)
    names = {r["name"] for r in rot}
    assert "empty_archived" in names
    assert next(r for r in rot if r["name"] == "empty_archived")["live_writer"] is True


def test_healthy_manifest_has_no_rot(tmp_path, monkeypatch):
    state = tmp_path / "state"; state.mkdir()
    (state / "zeus_trades.db")  # no db needed
    man = tmp_path / "m.yaml"
    man.write_text("tables:\n  - name: t\n    db: trade\n    schema_class: trade_class\n    notes: live\n")
    monkeypatch.setattr(mod, "_has_writer", lambda name: True)  # even with a writer, not droppable-labeled
    assert mod.audit(man, state) == []
