# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: fix/edli-stage-readiness-2026-05-31 — EDLI-mode release-gate
#   loaded_sha surface. ROOT: in EDLI modes nothing wrote state/loaded_sha.json,
#   so the gate's loaded_sha check returned missing_loaded_sha.
#
# Lifecycle: created=2026-05-31; last_reviewed=2026-05-31; last_reused=never
# Purpose: Prove _write_loaded_sha_state writes the genuine HEAD SHA and the gate
#   reads it as PASS, and that wrong/absent SHA fails the gate (cross-module relationship test).
# Reuse: Confirm _write_loaded_sha_state still exists in src.main and
#   check_live_release_gate._check_loaded_sha signature unchanged before reusing.
#
# Relationship invariant (daemon boot -> release gate, cross-module boundary):
#   _write_loaded_sha_state(boot_sha) MUST write a file whose loaded_sha is the
#   GENUINE booted HEAD, such that the gate's _check_loaded_sha(expected=HEAD)
#   returns PASS. Writing a wrong/blank SHA must NOT pass.

import json
import subprocess

from scripts.check_live_release_gate import PASS, FAIL, _check_loaded_sha  # type: ignore
from src import main as zeus_main


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def test_boot_writer_writes_real_head_and_gate_passes(tmp_path, monkeypatch):
    head = _git_head()
    out = tmp_path / "loaded_sha.json"
    monkeypatch.setattr(zeus_main, "state_path", lambda name: tmp_path / name, raising=False)
    # _write_loaded_sha_state imports state_path locally from src.config; patch there.
    import src.config as cfg
    monkeypatch.setattr(cfg, "state_path", lambda name: tmp_path / name, raising=True)

    zeus_main._write_loaded_sha_state(head)

    assert out.exists(), "boot writer must create loaded_sha.json"
    payload = json.loads(out.read_text())
    assert payload["loaded_sha"] == head, "loaded_sha must equal the genuine booted HEAD"

    # Relationship: gate reads it as PASS when expected == HEAD.
    result = _check_loaded_sha(head, out)
    assert result.status == PASS, f"gate should PASS on genuine loaded_sha: {result.detail}"


def test_gate_fails_on_mismatched_loaded_sha(tmp_path, monkeypatch):
    """A loaded_sha that does NOT match expected HEAD must FAIL — the writer
    must not be able to mint a passing file with a wrong SHA."""
    import src.config as cfg
    monkeypatch.setattr(cfg, "state_path", lambda name: tmp_path / name, raising=True)

    zeus_main._write_loaded_sha_state("deadbeef" * 5)
    out = tmp_path / "loaded_sha.json"
    result = _check_loaded_sha(_git_head(), out)
    assert result.status == FAIL, "gate must FAIL when loaded_sha != expected HEAD"


def test_missing_boot_sha_does_not_write_file(tmp_path, monkeypatch):
    """If boot SHA is unavailable (override), no file is minted — gate then reads
    missing_loaded_sha (fail-closed), never a fabricated SHA."""
    import src.config as cfg
    monkeypatch.setattr(cfg, "state_path", lambda name: tmp_path / name, raising=True)

    zeus_main._write_loaded_sha_state(None)
    assert not (tmp_path / "loaded_sha.json").exists(), "no file when boot SHA unavailable"
