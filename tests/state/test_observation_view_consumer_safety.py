# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: src/state/schema/v2_schema.py:640-669 (view design + v0 default)
#                  docs/operations/task_2026-05-16_deep_alignment_audit/FIX_PLAN.md §PR-M'
"""Antibody: runtime code must NOT read observation_instants_current before the view is activated.

Background
----------
The VIEW ``observation_instants_current`` was designed as an atomic cutover
indirection over ``observation_instants_v2``. It JOINs ``zeus_meta`` on
``observation_data_version = m.value``. The default value inserted by
``init_schema`` is ``'v0'`` (v2_schema.py:660), which matches ZERO rows in
``observation_instants_v2`` (no rows carry ``data_version='v0'``).

Consequence: any runtime code that reads ``observation_instants_current``
before the operator has run the activation UPDATE will see ``cov=0`` for
every city, silently halting all DDD-gated trade decisions.

Activation prerequisite:
  UPDATE zeus_meta SET value='v1.wu-native' WHERE key='observation_data_version';
This is an operator-mediated step tracked as a separate ops migration.

ANTIBODY CONTRACT
-----------------
No runtime file in src/engine/ or src/execution/ may read
``observation_instants_current`` while this antibody exists.

When the operator migration lands (and view activation is confirmed),
this antibody should be removed or converted to a positive assertion
that the VIEW is used. Until then it guards the pre-activation safety.

REGRESSION INJECTION PROOF
---------------------------
sed -i '' 's/FROM observation_instants_v2/FROM observation_instants_current/'
  src/engine/ddd_wiring.py
-> test_no_runtime_reads_from_inactive_view fails immediately.
Restore: sed -i '' 's/FROM observation_instants_current/FROM observation_instants_v2/'
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent

# Runtime dirs where view reads would halt DDD decisions or execution logic
_RUNTIME_DIRS = [
    "src/engine",
    "src/execution",
    "src/control",
    "src/riskguard",
]

# The view name that must not be read from runtime code while inactive
_INACTIVE_VIEW = "observation_instants_current"

# The view's definition file is allowed to reference its own name
_ALLOWED_DEFINITION_FILE = "src/state/schema/v2_schema.py"

# Test files are exempt (they exercise the view directly for antibody purposes)
_ALLOWED_PREFIXES = ("tests/",)


class TestObservationViewConsumerSafety:
    """Guard: no runtime reader of observation_instants_current before view activation."""

    def test_no_runtime_reads_from_inactive_view(self):
        """No file in the runtime dirs reads observation_instants_current.

        The VIEW returns zero rows until an operator UPDATE flips
        zeus_meta.observation_data_version from 'v0' to 'v1.wu-native'.
        Routing runtime coverage checks through the view pre-activation
        silently sets cov=0 for every city, halting all DDD decisions.

        REGRESSION INJECTION:
          sed 's/FROM observation_instants_v2/FROM observation_instants_current/'
            src/engine/ddd_wiring.py
          -> this test fails immediately.
        """
        pattern = re.compile(
            r"\bFROM\s+observation_instants_current\b"
            r"|\bJOIN\s+observation_instants_current\b",
            re.IGNORECASE,
        )
        violations: list[str] = []

        for dir_str in _RUNTIME_DIRS:
            runtime_dir = _REPO_ROOT / dir_str
            if not runtime_dir.exists():
                continue
            for py_file in sorted(runtime_dir.rglob("*.py")):
                rel = str(py_file.relative_to(_REPO_ROOT))
                if rel == _ALLOWED_DEFINITION_FILE:
                    continue
                if any(rel.startswith(p) for p in _ALLOWED_PREFIXES):
                    continue
                text = py_file.read_text(encoding="utf-8")
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if pattern.search(line):
                        violations.append(f"{rel}:{lineno}: {line.strip()}")

        assert not violations, (
            "CONSUMER-SAFETY VIOLATION: runtime code reads observation_instants_current "
            "while the VIEW is INACTIVE (zeus_meta.observation_data_version='v0' by default "
            "— returns zero rows). This would cause cov=0 for every city, silently halting "
            "all DDD-gated trade decisions.\n\n"
            "Violations found:\n" + "\n".join(f"  {v}" for v in violations) + "\n\n"
            "To fix: restore direct reads from observation_instants_v2 until the operator "
            "activation migration runs (UPDATE zeus_meta SET value='v1.wu-native' WHERE "
            "key='observation_data_version') and a downstream consumer audit is complete."
        )

    def test_view_is_inactive_by_default_in_schema(self):
        """Confirm the schema's default observation_data_version is 'v0' (view inactive).

        This test will need to be removed or inverted when the activation
        migration runs. Its presence is a canary: if it starts failing,
        it means the meta was flipped but this antibody was not updated.
        """
        schema_file = _REPO_ROOT / "src" / "state" / "schema" / "v2_schema.py"
        text = schema_file.read_text(encoding="utf-8")

        # The INSERT that seeds zeus_meta with the default value
        assert "observation_data_version', 'v0'" in text, (
            "Expected zeus_meta default for observation_data_version to be 'v0'. "
            "If this changed, the view may now be active — remove or update this "
            "antibody and the consumer-safety test above."
        )
