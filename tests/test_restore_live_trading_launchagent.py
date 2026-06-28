from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "restore_live_trading_launchagent.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("restore_live_trading_launchagent_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_plist(path: Path, *, label: str = "com.zeus.live-trading", module: str = "src.main") -> None:
    path.write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>\n"""
        b"""<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" """
        b""""http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n"""
        + f"""
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array>
<string>/usr/bin/python3</string><string>-m</string><string>{module}</string>
</array>
<key>WorkingDirectory</key><string>/Users/leofitz/zeus</string>
</dict></plist>
""".encode()
    )


def test_restore_launchagent_dry_run_selects_disabled_backup(tmp_path):
    mod = _load_module()
    launchagents = tmp_path / "LaunchAgents"
    launchagents.mkdir()
    older = launchagents / "com.zeus.live-trading.plist.bak_older"
    disabled = launchagents / "com.zeus.live-trading.plist.disabled_current"
    _write_plist(older)
    _write_plist(disabled)

    result = mod.restore_launchagent(launchagents_dir=launchagents)

    assert result["ok"] is True
    assert result["reason"] == "dry_run"
    assert result["selected"]["path"] == str(disabled.resolve())
    assert result["launchctl_action"] == "none"
    assert not (launchagents / "com.zeus.live-trading.plist").exists()


def test_restore_launchagent_apply_copies_active_plist_without_launchctl(tmp_path):
    mod = _load_module()
    launchagents = tmp_path / "LaunchAgents"
    launchagents.mkdir()
    source = launchagents / "com.zeus.live-trading.plist.disabled_current"
    _write_plist(source)

    result = mod.restore_launchagent(launchagents_dir=launchagents, apply=True)

    active = launchagents / "com.zeus.live-trading.plist"
    assert result["ok"] is True
    assert result["reason"] == "restored_active_launchagent"
    assert result["launchctl_action"] == "none"
    assert active.exists()
    assert active.read_bytes() == source.read_bytes()


def test_restore_launchagent_rejects_non_src_main_candidate(tmp_path):
    mod = _load_module()
    launchagents = tmp_path / "LaunchAgents"
    launchagents.mkdir()
    source = launchagents / "com.zeus.live-trading.plist.disabled_bad"
    _write_plist(source, module="src.other")

    result = mod.restore_launchagent(launchagents_dir=launchagents)

    assert result["ok"] is False
    assert result["reason"] == "no_valid_live_trading_launchagent_backup"
