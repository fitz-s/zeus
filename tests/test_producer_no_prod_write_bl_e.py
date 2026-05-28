# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC re-audit Blocker 4 / BL-E. The full_transport producer may
#   ONLY write an isolated staging DB; writing the canonical world DB would let an
#   INSERT-OR-REPLACE STAGING row overwrite a same-PK VERIFIED row (no authority in the PK).
"""Tested invariant for BL-E: the producer refuses to write any production DB."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fit_full_transport_error_models import _refuse_prod_db


@pytest.mark.parametrize("name", ["zeus-world.db", "zeus-forecasts.db", "zeus_trades.db"])
def test_refuse_prod_db_rejects_each_production_basename(name):
    with pytest.raises(SystemExit):
        _refuse_prod_db(Path(f"/some/dir/{name}"))


def test_refuse_prod_db_allows_isolated_staging_copy(tmp_path):
    # A non-production basename is allowed (no raise).
    staging = tmp_path / "ft_staging_copy.db"
    _refuse_prod_db(staging)  # must not raise


def test_refuse_prod_db_rejects_samefile_renamed_world_db(tmp_path, monkeypatch):
    # Defense-in-depth: a renamed copy that is the SAME physical file as the canonical world
    # DB must still be refused (basename alone would miss it).
    import scripts.fit_full_transport_error_models as mod
    fake_world = tmp_path / "zeus-world.db"
    fake_world.write_text("db")
    renamed = tmp_path / "totally_innocent_name.db"
    try:
        renamed.hardlink_to(fake_world)
    except (OSError, AttributeError):
        pytest.skip("hardlink unsupported on this fs")
    # Point the producer's canonical world path at our fake.
    monkeypatch.setattr("src.state.db.ZEUS_WORLD_DB_PATH", str(fake_world), raising=False)
    with pytest.raises(SystemExit):
        _refuse_prod_db(renamed)
