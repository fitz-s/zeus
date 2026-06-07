# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# Authority basis: PR_SPEC.md §2 FIX-2a (re-enable tiny live caps to bound blast radius).
"""FIX-2a antibody: the tiny live notional + daily-order caps must be ENABLED in
config so the live-canary blast radius stays bounded.

Per src/events/live_cap.py:cap_explicitly_disabled, the literal JSON ``false`` is
the explicit-disable sentinel: ``false`` UNCAPS, ``true`` (or any non-false value)
leaves the cap ON. Live-money config must therefore carry ``true`` for both caps.
Removing the production config fix (reverting to ``false``) re-uncaps and fails this
test, so it is a real antibody, not a smoke test.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.events.live_cap import cap_explicitly_disabled


def test_tiny_live_caps_are_enabled_in_config() -> None:
    edli = json.loads(Path("config/settings.json").read_text())["edli_v1"]

    assert edli["tiny_live_notional_cap_enabled"] is True
    assert edli["tiny_live_daily_order_cap_enabled"] is True


def test_tiny_live_caps_config_is_not_the_explicit_uncap_sentinel() -> None:
    edli = json.loads(Path("config/settings.json").read_text())["edli_v1"]

    # cap_explicitly_disabled(False) -> True would UNCAP; the live config must
    # never hand the explicit-disable sentinel to the cap reservation path.
    assert cap_explicitly_disabled(edli["tiny_live_notional_cap_enabled"]) is False
    assert cap_explicitly_disabled(edli["tiny_live_daily_order_cap_enabled"]) is False
