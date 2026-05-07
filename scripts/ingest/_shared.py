# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: Shared helpers for scripts/ingest/* tick scripts (logging setup,
#          connection management, exit-code pattern). Keeps each tick script
#          itself thin and uniform.
# Reuse: Whenever adding a new tick script under scripts/ingest/, prefer
#        these helpers over reinventing logging/conn-open/error-exit patterns.
# Authority basis: docs/operations/task_2026-04-26_g10_ingest_scaffold/plan.md.
"""Shared helpers for ingest-tick scripts.

Per the isolation contract enforced by `tests/test_ingest_isolation.py`,
this module imports ONLY from `src.state.db.*` (allowed). It must never
import from src.engine / src.execution / src.strategy / src.signal /
src.supervisor_api / src.control / src.observability / src.main.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Iterator

from src.state.db import get_world_connection


def setup_tick_logging(name: str) -> logging.Logger:
    """Configure stdlib logging for a tick script.

    Single-handler StreamHandler at INFO. Idempotent (safe to call twice).
    Returns the logger named `<name>`.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    return logging.getLogger(name)


@contextmanager
def world_connection() -> Iterator:
    """Context-managed world DB connection.

    Closes on exit even if the body raises. Equivalent to the try/finally
    pattern in `src/main.py` tick functions.
    """
    conn = get_world_connection(write_class="bulk")
    try:
        yield conn
    finally:
        conn.close()


def run_tick(name: str, body: callable) -> int:
    """Standard tick wrapper: setup logging, open conn, run body, log result, return exit code.

    Body receives the open connection and returns either:
    - a value to be logged (any type with str() representation)
    - None (no extra logging)

    Exceptions are caught at the top level: logged with traceback, return code 1.
    Clean completion returns 0.
    """
    logger = setup_tick_logging(name)
    try:
        with world_connection() as conn:
            result = body(conn)
        if result is not None:
            logger.info("%s: %s", name, result)
        return 0
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        return 1
