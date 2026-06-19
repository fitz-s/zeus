# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: operator BLOCKER (the_path PR review 2026-06-08) requirement #2 — if the
#   required product/request identity CANNOT be constructed for a row, the script REFUSES to
#   seed (hard error) rather than writing identity-less rows. A NULL-identity row reintroduces
#   the NULL!=NULL non-idempotency hole and is unreconstructable to its OM product. BAYES_PRECISION_FUSION_SPEC §6 F1.
"""Relationship test (backfill -> identity-construction failure boundary).

When a B0 city cannot be resolved to its requested coordinates/timezone (so the live
identity construction cannot run), the backfill MUST hard-error and write NOTHING —
never silently fall back to identity-less rows.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_live_schema(conn)
    conn.commit()
    conn.close()
    return db


def _count(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM raw_model_forecasts").fetchone()[0]
    conn.close()
    return int(n)


def test_backfill_refuses_when_city_identity_unresolvable(tmp_path) -> None:
    from scripts.backfill_bayes_precision_fusion_history_from_b0 import (
        BackfillIdentityError,
        backfill_bayes_precision_fusion_history,
    )

    db = _db(tmp_path)
    # A city name that does NOT exist in cities_by_name (no lat/lon/timezone) -> the live
    # identity construction cannot be reconstructed -> the script must REFUSE.
    b0 = {
        "Atlantis__not_a_real_city": {
            "leads": {"1": {"ecmwf_ifs": {"2026-06-01": [7.4, 2.2]}}},
        }
    }
    with pytest.raises(BackfillIdentityError):
        backfill_bayes_precision_fusion_history(b0=b0, db=db, dry_run=False)

    # And it wrote NOTHING (refuses cleanly, never partial identity-less rows).
    assert _count(db) == 0, "a refusal must not leave identity-less rows behind"
