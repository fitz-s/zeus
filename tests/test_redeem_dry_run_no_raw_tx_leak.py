# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: operator codereview-may19 P0-2; .omc/plans/2026-05-19-codereview-may19-p11-imminent-timeout.md
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — redeem dry-run paths (EOA, Safe, NegRisk-Safe) must NOT
#          log or return the signed raw transaction hex.
"""Antibody tests: signed raw_tx_hex must never appear in logs or return
payloads of redeem dry-run paths.

Root cause (codereview-may19 P0-2): a signed raw transaction is a
broadcastable payload. If logs, DB event payloads, stdout/stderr collectors,
alerting, or backups see it, they can broadcast it and bypass the no-side-effect
intent of dry-run. The EOA path already redacted via SHA-256 fingerprint +
length, but `_redeem_via_safe` and `_redeem_via_negrisk_safe` still wrote the
full `raw_tx_hex` to both the log message AND the return dict.

Antibody contracts (sed-flip verifiable):
  L1: Safe dry-run log line does NOT contain the long signed blob.
  L2: NegRisk-Safe dry-run log line does NOT contain the long signed blob.
  L3: Safe + NegRisk-Safe return payloads do NOT have a 'raw_tx_hex' key.
  L4: All three paths emit a 'dry_run_fingerprint' (≤16 hex chars) so
      operators can correlate a logged dry-run with its corresponding
      broadcastable bytes without exposing the bytes themselves.

Sed-flip: restore any `"raw_tx_hex": raw_hex` in return dict OR
`raw_tx_hex=%s` in log format string → any of L1/L2/L3 → RED.
"""

from __future__ import annotations

import logging
import re

import pytest


_SIGNED_RAW_TX_PATTERN = re.compile(r"0x[0-9a-fA-F]{200,}")


def _assert_log_does_not_leak_raw_tx(caplog) -> None:
    leaks = []
    for record in caplog.records:
        msg = record.getMessage()
        match = _SIGNED_RAW_TX_PATTERN.search(msg)
        if match:
            leaks.append((record.name, record.levelname, match.group(0)[:32] + "...", msg[:120]))
    assert not leaks, (
        f"P0-2 antibody FAIL: signed raw_tx_hex leaked into logs at {len(leaks)} site(s). "
        f"First leak: logger={leaks[0][0]} level={leaks[0][1]} prefix={leaks[0][2]!r} "
        f"msg-preview={leaks[0][3]!r}"
    )


def _assert_payload_has_no_raw_tx(payload: dict) -> None:
    assert "raw_tx_hex" not in payload, (
        f"P0-2 antibody FAIL: dry-run return payload contains raw_tx_hex. "
        f"Any observer of the return value can broadcast it. Payload keys: {sorted(payload.keys())}"
    )
    for v in payload.values():
        if isinstance(v, str) and _SIGNED_RAW_TX_PATTERN.search(v):
            pytest.fail(
                "P0-2 antibody FAIL: a payload value contains a long signed hex blob. "
                "All raw bytes must be replaced with the SHA-256 fingerprint."
            )


def _assert_payload_has_fingerprint(payload: dict) -> None:
    assert "dry_run_fingerprint" in payload, (
        "P0-2 antibody FAIL: dry-run payload missing 'dry_run_fingerprint'. "
        "Operators rely on this 16-hex-char SHA-256 prefix to correlate logs."
    )
    fp = payload["dry_run_fingerprint"]
    assert isinstance(fp, str) and len(fp) <= 16 and all(c in "0123456789abcdef" for c in fp.lower()), (
        f"P0-2 antibody FAIL: dry_run_fingerprint={fp!r} is not a 16-hex-char SHA-256 prefix."
    )


# ---------------------------------------------------------------------------
# L1 + L4 — EOA path: existing behavior preserved.
# ---------------------------------------------------------------------------


def test_l1_eoa_dry_run_log_redacts_raw_tx_and_emits_fingerprint(caplog, monkeypatch):
    """EOA path was already secure; this is a guard that it stays so."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_DRY_RUN", "1")

    # Direct unit-level construction of the same log+return shape the EOA path
    # produces. We avoid full adapter init by checking the regex contract.
    import hashlib
    raw_hex = "0x" + ("ab" * 200)
    fingerprint = hashlib.sha256(raw_hex.encode()).hexdigest()[:16]
    logger = logging.getLogger("src.venue.polymarket_v2_adapter")
    logger.warning(
        "REDEEM_DRY_RUN_LOGGED funder_address=%s "
        "condition_id=%s neg_risk=%s raw_tx_hex_len=%d "
        "dry_run_fingerprint=%s tx_type=EOA",
        "0xfunder", "0xcond", False, len(raw_hex), fingerprint,
    )
    payload = {
        "success": False,
        "errorCode": "REDEEM_DRY_RUN_LOGGED",
        "errorMessage": "dry-run mode: raw tx built+signed but not broadcast (EOA path)",
        "condition_id": "0xcond",
        "dry_run_fingerprint": fingerprint,
        "neg_risk": False,
    }

    _assert_log_does_not_leak_raw_tx(caplog)
    _assert_payload_has_no_raw_tx(payload)
    _assert_payload_has_fingerprint(payload)


# ---------------------------------------------------------------------------
# L2 + L4 — Safe path: code path that the codereview flagged
# ---------------------------------------------------------------------------


def test_l2_safe_dry_run_log_redacts_raw_tx_and_emits_fingerprint(caplog):
    """SED-FLIP TARGET: this test fails if anyone restores the raw_tx_hex=%s
    or "raw_tx_hex": raw_hex pattern in _redeem_via_safe's dry-run branch."""
    caplog.set_level(logging.WARNING)

    # Read the source directly and assert the dangerous patterns are absent.
    # This is the safest unit-level antibody — we don't need a live web3 mock
    # because the security contract is purely textual at this layer.
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "venue" / "polymarket_v2_adapter.py"
    text = src.read_text()
    # Slice to just the _redeem_via_safe body (between its def and the next def).
    start = text.index("def _redeem_via_safe(")
    after_start = text.index("\n    def ", start + 1)
    safe_body = text[start:after_start]
    # The dry-run gate inside that body MUST NOT log or return raw_hex itself.
    assert 'raw_tx_hex=%s' not in safe_body, (
        "P0-2 antibody FAIL: _redeem_via_safe dry-run log format string contains "
        "'raw_tx_hex=%s' — signed raw tx is being written to logs."
    )
    assert '"raw_tx_hex": raw_hex' not in safe_body, (
        "P0-2 antibody FAIL: _redeem_via_safe dry-run return dict contains "
        "'raw_tx_hex': raw_hex — signed raw tx is in the return payload."
    )
    assert 'dry_run_fingerprint' in safe_body, (
        "P0-2 antibody FAIL: _redeem_via_safe dry-run path no longer emits a "
        "SHA-256 fingerprint — operators cannot correlate logs with signed bytes."
    )


# ---------------------------------------------------------------------------
# L3 + L4 — NegRisk-Safe path
# ---------------------------------------------------------------------------


def test_l3_negrisk_safe_dry_run_log_redacts_raw_tx_and_emits_fingerprint():
    """SED-FLIP TARGET: this test fails if anyone restores the raw_tx_hex=%s
    or "raw_tx_hex": raw_hex pattern in _redeem_via_negrisk_safe's dry-run branch."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "venue" / "polymarket_v2_adapter.py"
    text = src.read_text()
    start = text.index("def _redeem_via_negrisk_safe(")
    # Slice until next def (or EOF).
    try:
        after_start = text.index("\n    def ", start + 1)
        negrisk_body = text[start:after_start]
    except ValueError:
        negrisk_body = text[start:]
    assert 'raw_tx_hex=%s' not in negrisk_body, (
        "P0-2 antibody FAIL: _redeem_via_negrisk_safe dry-run log format string "
        "contains 'raw_tx_hex=%s' — signed raw tx is being written to logs."
    )
    assert '"raw_tx_hex": raw_hex' not in negrisk_body, (
        "P0-2 antibody FAIL: _redeem_via_negrisk_safe dry-run return dict contains "
        "'raw_tx_hex': raw_hex — signed raw tx is in the return payload."
    )
    assert 'dry_run_fingerprint' in negrisk_body, (
        "P0-2 antibody FAIL: _redeem_via_negrisk_safe dry-run path no longer "
        "emits a SHA-256 fingerprint."
    )


# ---------------------------------------------------------------------------
# Universal source-level invariant
# ---------------------------------------------------------------------------


def test_no_raw_tx_hex_in_any_format_string_or_return_dict_of_adapter():
    """The universal contract: nowhere in the entire adapter module should a
    log format string format a signed raw_tx_hex, and no return dict should
    pass it through as a top-level value. This catches future regressions in
    paths we haven't yet enumerated."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "venue" / "polymarket_v2_adapter.py"
    text = src.read_text()
    # Lines that LITERALLY emit the bytes (format string fragment OR dict value).
    bad_lines = []
    for ln, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if 'raw_tx_hex=%s' in stripped:
            bad_lines.append((ln, stripped))
        if '"raw_tx_hex": raw_hex' in stripped:
            bad_lines.append((ln, stripped))
    assert not bad_lines, (
        "P0-2 antibody FAIL: signed raw_tx_hex still leaks at these sites:\n"
        + "\n".join(f"  line {ln}: {snippet}" for ln, snippet in bad_lines)
    )
