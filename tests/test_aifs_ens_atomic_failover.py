# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator directive 2026-06-09 — the AIFS download runs several times a day
#   and must be FLAWLESS; a partial/throttled/killed retrieve must NEVER commit a corrupt
#   artifact (the byte_size mismatch that stalled live for ~10h). Relationship tests for the
#   atomic-write + mirror-failover antibody in src/data/ecmwf_aifs_ens_request.py.
"""Relationship tests: the AIFS retrieve makes partial-download corruption UNCONSTRUCTABLE.

The corruption category (Fitz #5): a throttled/killed retrieve writes a half file to the final
artifact path; RawForecastArtifactManifest.verify_artifact later aborts the materializer on the
byte_size/sha256 mismatch -> no fresh posterior -> zero trades. These tests pin the boundary so
the half-written final commit cannot happen, and a throttled mirror fails over instead of
truncating.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.data.ecmwf_aifs_ens_request import (
    build_aifs_ens_open_data_request,
    retrieve_aifs_ens_open_data_request,
)


def _req(tmp_path: Path):
    return build_aifs_ens_open_data_request(
        forecast_date="2026-06-08",
        cycle_hour=12,
        target_path=tmp_path / "aifs_ens.grib2",
        steps=(0,),
    )


def test_partial_retrieve_never_commits_final_artifact(tmp_path):
    """A client that half-writes then raises (throttle/kill) leaves NO final artifact and no
    orphan temp — so verify_artifact can never see a half-written file."""

    class HalfThenRaise:
        def __init__(self, *, source, model):
            self.source = source

        def retrieve(self, **kw):
            Path(kw["target"]).write_bytes(b"HALF")  # partial write to the temp
            raise RuntimeError("503 SlowDown mid-download")

    req = _req(tmp_path)
    with pytest.raises(RuntimeError):
        retrieve_aifs_ens_open_data_request(req, client_factory=lambda **k: HalfThenRaise(**k))
    assert not req.target_path.exists(), "atomic: a failed retrieve must not commit the final artifact"
    assert not (req.target_path.parent / (req.target_path.name + ".partial")).exists(), "temp cleaned"


def test_mirror_failover_when_first_source_throttled(tmp_path):
    """First mirror (azure) 503s; the retrieve fails over to the next mirror and commits a
    COMPLETE artifact atomically."""

    calls: list[str] = []

    class FailoverClient:
        def __init__(self, *, source, model):
            self.source = source
            calls.append(source)

        def retrieve(self, **kw):
            if self.source == "azure":
                raise RuntimeError("503 SlowDown")
            Path(kw["target"]).write_bytes(b"COMPLETE_GRIB")

    req = _req(tmp_path)  # default source = azure
    p = retrieve_aifs_ens_open_data_request(req, client_factory=lambda **k: FailoverClient(**k))
    assert p.exists() and p.read_bytes() == b"COMPLETE_GRIB", "committed from the failover mirror"
    assert calls[0] == "azure", "tries the requested source first"
    assert any(s in ("ecmwf", "aws") for s in calls[1:]), "falls over to another mirror"


def test_empty_artifact_is_not_committed(tmp_path):
    """A client that returns but leaves an empty file must NOT commit it (would be a 0-byte
    corrupt artifact)."""

    class EmptyClient:
        def __init__(self, *, source, model):
            pass

        def retrieve(self, **kw):
            Path(kw["target"]).write_bytes(b"")

    req = _req(tmp_path)
    with pytest.raises(RuntimeError):
        retrieve_aifs_ens_open_data_request(req, client_factory=lambda **k: EmptyClient(**k))
    assert not req.target_path.exists()


def test_first_source_success_commits_without_failover(tmp_path):
    """Happy path: the requested source succeeds -> committed atomically, no other mirror tried."""

    calls: list[str] = []

    class OkClient:
        def __init__(self, *, source, model):
            calls.append(source)

        def retrieve(self, **kw):
            Path(kw["target"]).write_bytes(b"GRIB_OK")

    req = _req(tmp_path)
    p = retrieve_aifs_ens_open_data_request(req, client_factory=lambda **k: OkClient(**k))
    assert p.exists() and p.read_bytes() == b"GRIB_OK"
    assert calls == ["azure"], "no failover when the first source succeeds"
