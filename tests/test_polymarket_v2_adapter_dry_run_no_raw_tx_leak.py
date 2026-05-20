# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: codereview-may19.md P0-2
"""
Structural antibody: dry-run redeem paths must never expose signed raw_tx_hex.

Three dry-run branches in polymarket_v2_adapter.py:
  EOA     : redeem() L1001-1034  (ZEUS_AUTONOMOUS_REDEEM_DRY_RUN env)
  SAFE    : _redeem_via_safe() L1335-1359  (dry_run param)
  NEGRISK : _redeem_via_negrisk_safe() L1661-1683  (dry_run param)

Each branch must:
  1. Derive `_dry_run_fingerprint` via SHA-256[:16] of raw_hex
  2. Log ONLY fingerprint + length metadata (never raw_hex itself)
  3. Return dict WITHOUT "raw_tx_hex" key
  4. Exit BEFORE eth_sendRawTransaction is called

sed-flip verification: remove the `_dry_run_fingerprint = ...` line from any
branch → T1 and T4 both fail immediately.
"""

import pathlib
import re
import textwrap

import pytest

_ADAPTER_PATH = (
    pathlib.Path(__file__).parent.parent
    / "src" / "venue" / "polymarket_v2_adapter.py"
)

_SOURCE = _ADAPTER_PATH.read_text(encoding="utf-8")
_LINES = _SOURCE.splitlines()  # 0-indexed; file lines are 1-indexed


# ---------------------------------------------------------------------------
# Extract dry-run gate blocks using exact line-number anchors.
#
# Anchor discovery (grep verified 2026-05-19):
#   EOA     : "# ── Dry-run gate (EOA" at line 1001, "# ── Broadcast" at 1036
#   SAFE    : "# ── Dry-run gate"       at line 1335, "# ── Broadcast" at 1361
#   NEGRISK : "# ── Dry-run gate"       at line 1661, "# ── Broadcast" at 1685
#
# We locate the anchors dynamically so the test stays correct if surrounding
# lines shift, but assert that all anchors are found.
# ---------------------------------------------------------------------------

def _extract_block(gate_anchor: str, broadcast_anchor: str) -> list[str]:
    """
    Return lines from the first occurrence of gate_anchor up to (but not
    including) the next occurrence of broadcast_anchor that follows it.
    Raises AssertionError if either anchor is not found.
    """
    gate_idx = None
    for i, line in enumerate(_LINES):
        if gate_anchor in line:
            gate_idx = i
            break
    assert gate_idx is not None, f"Anchor not found in adapter: {gate_anchor!r}"

    broadcast_idx = None
    for i in range(gate_idx + 1, len(_LINES)):
        if broadcast_anchor in _LINES[i]:
            broadcast_idx = i
            break
    assert broadcast_idx is not None, (
        f"Broadcast anchor not found after gate at line {gate_idx+1}: {broadcast_anchor!r}"
    )
    return _LINES[gate_idx:broadcast_idx]


def _eoa_block() -> list[str]:
    return _extract_block(
        "# ── Dry-run gate (EOA path",
        "# ── Broadcast",
    )


def _safe_block() -> list[str]:
    # _redeem_via_safe starts at line 1076; its dry-run gate comment is first
    # occurrence of "# ── Dry-run gate" that is NOT the EOA one.
    # Use the safe-specific log marker to find the right gate.
    gate_anchor = "# ── Dry-run gate ────"
    gate_idx = None
    for i, line in enumerate(_LINES):
        if gate_anchor in line and "EOA" not in line:
            gate_idx = i
            break
    assert gate_idx is not None, f"SAFE gate anchor not found: {gate_anchor!r}"
    broadcast_idx = None
    for i in range(gate_idx + 1, len(_LINES)):
        if "# ── Broadcast" in _LINES[i]:
            broadcast_idx = i
            break
    assert broadcast_idx is not None
    return _LINES[gate_idx:broadcast_idx]


def _negrisk_block() -> list[str]:
    # Second occurrence of "# ── Dry-run gate ────" (without EOA label)
    gate_anchor = "# ── Dry-run gate ────"
    count = 0
    gate_idx = None
    for i, line in enumerate(_LINES):
        if gate_anchor in line and "EOA" not in line:
            count += 1
            if count == 2:
                gate_idx = i
                break
    assert gate_idx is not None, f"NEGRISK gate anchor not found (2nd occurrence): {gate_anchor!r}"
    broadcast_idx = None
    for i in range(gate_idx + 1, len(_LINES)):
        if "# ── Broadcast" in _LINES[i]:
            broadcast_idx = i
            break
    assert broadcast_idx is not None
    return _LINES[gate_idx:broadcast_idx]


def _redeem_body() -> list[str]:
    """All lines of the concrete redeem() method (L745+)."""
    # Find the second 'def redeem(' (first is the protocol stub)
    count = 0
    start = None
    for i, line in enumerate(_LINES):
        if re.match(r"\s+def redeem\s*\(", line):
            count += 1
            if count == 2:
                start = i
                break
    assert start is not None
    indent = len(_LINES[start]) - len(_LINES[start].lstrip())
    body = []
    for line in _LINES[start + 1:]:
        stripped = line.lstrip()
        if stripped and len(line) - len(stripped) <= indent and re.match(r"def ", stripped):
            break
        body.append(line)
    return body


def _safe_body() -> list[str]:
    return _method_body("_redeem_via_safe")


def _negrisk_body() -> list[str]:
    return _method_body("_redeem_via_negrisk_safe")


def _method_body(method_name: str) -> list[str]:
    start = None
    for i, line in enumerate(_LINES):
        if re.match(r"\s+def " + re.escape(method_name) + r"\s*\(", line):
            start = i
            break
    assert start is not None, f"Method not found: {method_name}"
    indent = len(_LINES[start]) - len(_LINES[start].lstrip())
    body = []
    for line in _LINES[start + 1:]:
        stripped = line.lstrip()
        if stripped and len(line) - len(stripped) <= indent and re.match(r"def ", stripped):
            break
        body.append(line)
    return body


# Pre-extract dry-run blocks for all three branches (module-level for parametrize)
_EOA_BLOCK = _eoa_block()
_SAFE_BLOCK = _safe_block()
_NEGRISK_BLOCK = _negrisk_block()


# ---------------------------------------------------------------------------
# T1 — dry_run_fingerprint present; "raw_tx_hex": absent from return dict
# ---------------------------------------------------------------------------

class TestT1_FingerprintPresentNoRawTxHexInReturn:
    """Each dry-run branch must contain dry_run_fingerprint and must NOT
    expose raw_tx_hex as a dict key in the return statement."""

    @pytest.mark.parametrize("branch,block", [
        ("EOA", _EOA_BLOCK),
        ("SAFE", _SAFE_BLOCK),
        ("NEGRISK", _NEGRISK_BLOCK),
    ])
    def test_fingerprint_literal_present(self, branch, block):
        joined = "\n".join(block)
        assert "_dry_run_fingerprint" in joined, (
            f"{branch} dry-run block missing `_dry_run_fingerprint` literal"
        )

    @pytest.mark.parametrize("branch,block", [
        ("EOA", _EOA_BLOCK),
        ("SAFE", _SAFE_BLOCK),
        ("NEGRISK", _NEGRISK_BLOCK),
    ])
    def test_no_raw_tx_hex_in_return_dict(self, branch, block):
        """
        No line in the dry-run return dict should contain `"raw_tx_hex":`.
        We look inside the return { ... } block only.
        """
        in_return = False
        for line in block:
            if re.search(r"\breturn\s*\{", line):
                in_return = True
            if in_return:
                # Fail if the literal key appears
                assert '"raw_tx_hex"' not in line and "'raw_tx_hex'" not in line, (
                    f"{branch} dry-run return dict contains raw_tx_hex key: {line!r}"
                )
            if in_return and "}" in line and "return" not in line:
                in_return = False


# ---------------------------------------------------------------------------
# T2 — no log call in dry-run block passes raw_hex as a direct %s argument
# ---------------------------------------------------------------------------

class TestT2_NoRawHexInLogArgs:
    """Log format strings in dry-run blocks must not have %s paired with raw_hex."""

    @pytest.mark.parametrize("branch,block", [
        ("EOA", _EOA_BLOCK),
        ("SAFE", _SAFE_BLOCK),
        ("NEGRISK", _NEGRISK_BLOCK),
    ])
    def test_no_bare_raw_hex_log_arg(self, branch, block):
        joined = "\n".join(block)
        # Check no logging call passes `raw_hex` directly as a positional arg
        # (only raw_tx_hex_len and dry_run_fingerprint are permitted)
        # Pattern: raw_hex appears as a standalone argument (not wrapped in len() or sha256)
        forbidden = re.search(
            r"""(logger|_logger)\.(warning|info|debug|error)\s*\(.*?%s.*?,\s*raw_hex\b""",
            joined,
            re.DOTALL,
        )
        assert forbidden is None, (
            f"{branch} dry-run log call passes raw_hex directly: {forbidden.group()!r}"
        )

    @pytest.mark.parametrize("branch,block", [
        ("EOA", _EOA_BLOCK),
        ("SAFE", _SAFE_BLOCK),
        ("NEGRISK", _NEGRISK_BLOCK),
    ])
    def test_no_raw_hex_fstring_in_log(self, branch, block):
        """f-string log with raw_hex interpolated directly is also forbidden."""
        for line in block:
            if ("logger.warning" in line or "logger.info" in line
                    or "_logger.warning" in line or "_logger.info" in line):
                assert "raw_hex" not in line or "raw_tx_hex_len" in line or "dry_run_fingerprint" in line, (
                    f"{branch} log line may expose raw_hex: {line!r}"
                )


# ---------------------------------------------------------------------------
# T3 — no raw_tx_hex key anywhere in the return-dict construction lines
# ---------------------------------------------------------------------------

class TestT3_NoRawTxHexInReturnConstruction:
    """The return dict in each dry-run branch must not include raw_tx_hex key
    even as a substring in dict-construction lines."""

    @pytest.mark.parametrize("branch,block", [
        ("EOA", _EOA_BLOCK),
        ("SAFE", _SAFE_BLOCK),
        ("NEGRISK", _NEGRISK_BLOCK),
    ])
    def test_raw_tx_hex_absent_from_return_block(self, branch, block):
        in_return = False
        brace_depth = 0
        for line in block:
            if re.search(r"\breturn\s*\{", line):
                in_return = True
            if in_return:
                brace_depth += line.count("{") - line.count("}")
                assert "raw_tx_hex" not in line, (
                    f"{branch} return-dict construction contains raw_tx_hex: {line!r}"
                )
                if brace_depth <= 0 and in_return:
                    in_return = False


# ---------------------------------------------------------------------------
# T4 — REGRESSION GUARD: SHA-256 derivation line present in each branch
# ---------------------------------------------------------------------------

class TestT4_SHA256DerivationPresent:
    """Each dry-run branch must contain the exact SHA-256 fingerprint derivation.
    If this line is removed, the branch loses its security redaction."""

    SHA256_PATTERN = re.compile(
        r"_dry_run_fingerprint\s*=\s*_hashlib\.sha256\(raw_hex\.encode\(\)\)\.hexdigest\(\)\[:16\]"
    )

    @pytest.mark.parametrize("branch,block", [
        ("EOA", _EOA_BLOCK),
        ("SAFE", _SAFE_BLOCK),
        ("NEGRISK", _NEGRISK_BLOCK),
    ])
    def test_sha256_derivation_line_present(self, branch, block):
        joined = "\n".join(block)
        assert self.SHA256_PATTERN.search(joined), (
            f"{branch} dry-run block missing SHA-256 fingerprint derivation line. "
            "This is a REGRESSION: raw_tx_hex is no longer redacted."
        )


# ---------------------------------------------------------------------------
# T5 — Invocation: broadcast must not fire during dry-run (EOA branch)
# ---------------------------------------------------------------------------

class TestT5_BroadcastNotCalledDuringDryRun:
    """
    Source-text guard: the EOA dry-run block must contain a `return` statement
    BEFORE the line that calls eth_sendRawTransaction.

    We verify the structural ordering: the `return {` inside `if _eoa_dry_run:`
    appears BEFORE the `eth_sendRawTransaction` broadcast call in the same function.

    This is a static structural test — no fixture required.
    """

    def test_eoa_return_before_broadcast(self):
        body = _redeem_body()
        return_in_dry_run_idx = None
        broadcast_idx = None
        in_dry_run = False
        for i, line in enumerate(body):
            if "_eoa_dry_run" in line or "if _eoa_dry_run:" in line:
                in_dry_run = True
            if in_dry_run and re.search(r"\breturn\s*\{", line):
                return_in_dry_run_idx = i
                in_dry_run = False  # once we find the return, gate is done
            if '"eth_sendRawTransaction"' in line and return_in_dry_run_idx is not None:
                broadcast_idx = i
                break

        assert return_in_dry_run_idx is not None, (
            "EOA dry-run block: could not find return statement inside `if _eoa_dry_run:` block"
        )
        assert broadcast_idx is not None, (
            "EOA path: could not find eth_sendRawTransaction call after dry-run block"
        )
        assert return_in_dry_run_idx < broadcast_idx, (
            "EOA dry-run return must appear BEFORE eth_sendRawTransaction broadcast call. "
            f"return at body line {return_in_dry_run_idx}, broadcast at {broadcast_idx}"
        )

    @pytest.mark.parametrize("branch,func_body_fn,gate_marker", [
        ("SAFE", _safe_body, "if dry_run:"),
        ("NEGRISK", _negrisk_body, "if dry_run:"),
    ])
    def test_safe_return_before_broadcast(self, branch, func_body_fn, gate_marker):
        body = func_body_fn()
        return_in_dry_run_idx = None
        broadcast_idx = None
        in_dry_run = False
        for i, line in enumerate(body):
            if gate_marker in line:
                in_dry_run = True
            if in_dry_run and re.search(r"\breturn\s*\{", line):
                return_in_dry_run_idx = i
                in_dry_run = False
            if '"eth_sendRawTransaction"' in line and return_in_dry_run_idx is not None:
                broadcast_idx = i
                break

        assert return_in_dry_run_idx is not None, (
            f"{branch} dry-run block: could not find return inside `{gate_marker}` block"
        )
        assert broadcast_idx is not None, (
            f"{branch}: could not find eth_sendRawTransaction after dry-run block"
        )
        assert return_in_dry_run_idx < broadcast_idx, (
            f"{branch} dry-run return must appear BEFORE eth_sendRawTransaction. "
            f"return at body line {return_in_dry_run_idx}, broadcast at {broadcast_idx}"
        )
