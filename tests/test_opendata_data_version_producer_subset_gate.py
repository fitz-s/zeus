# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: src/contracts/ensemble_snapshot_provenance.py
#   (CANONICAL_ENSEMBLE_DATA_VERSIONS gate) + ingest_grib_to_snapshots.py
#   inline _v1 normalization (commit referenced "#38 / #362"). Antibody for
#   the 2026-05-30 DataVersionQuarantinedError on
#   data_version='ecmwf_opendata_mx2t3_local_calendar_day_max_v1'.
# Lifecycle: created=2026-06-04; last_reviewed=2026-06-05; last_reused=2026-06-05
# Purpose: Relationship antibody — the OpenData producer's data_version must be a subset of the CANONICAL_ENSEMBLE_DATA_VERSIONS gate (no _v1 quarantine drift).
# Reuse: Re-run when CANONICAL_ENSEMBLE_DATA_VERSIONS or the OpenData ingest normalization changes.
"""Relationship test: OpenData producer ⊆ ensemble_snapshots gate.

CROSS-MODULE INVARIANT under test
---------------------------------
The OpenData extractor (``../51 source data/scripts/extract_open_ens_localday.py``,
an untracked staging script OUTSIDE this git repo) still tags its payload JSON
with the legacy ``_v1`` suffix:

    data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1"   # producer
    data_version="ecmwf_opendata_mn2t3_local_calendar_day_min_v1"   # producer

while the live write gate ``CANONICAL_ENSEMBLE_DATA_VERSIONS`` only admits the
no-suffix canonical forms (the version-eradication collapse, commit 6490f9c462,
which could not reach the out-of-repo extractor):

    "ecmwf_opendata_mx2t3_local_calendar_day_max"                   # gate
    "ecmwf_opendata_mn2t3_local_calendar_day_min"                   # gate

The ONLY thing keeping the live forecast-ingest path green across this boundary
is the normalization in ``ingest_grib_to_snapshots`` that strips the trailing
``_v1`` from ``ecmwf_opendata_*`` versions before calling
``assert_data_version_allowed``. On 2026-05-30 that normalization did not yet
exist and every OpenData cycle quarantined — zero fresh ensemble_snapshots.

This test makes that quarantine CATEGORY unconstructable for the OpenData path:
it asserts that, for every canonical OpenData data_version, the producer's
``_v1``-suffixed emission, after the shared normalization, is a member of the
gate. If a future edit deletes the normalization (believing the ``_v1`` era is
over) while the out-of-repo extractor still emits ``_v1``, this test goes RED
BEFORE the live daemon silently re-quarantines.

Relationship, not function: it crosses producer-emit → normalize → gate-admit.
"""

from __future__ import annotations

import pytest

from src.contracts.ensemble_snapshot_provenance import (
    CANONICAL_ENSEMBLE_DATA_VERSIONS,
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    assert_data_version_allowed,
    normalize_opendata_data_version,
)

# Exactly what the out-of-repo extractor hardcodes today
# (extract_open_ens_localday.py:153,164). Kept as literals on purpose: this is
# the PRODUCER contract, and the test must fail if the gate stops accepting the
# normalized form OR if the normalization is removed — not silently track a
# constant rename.
_PRODUCER_EMITTED_V1 = {
    ECMWF_OPENDATA_HIGH_DATA_VERSION: "ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
    ECMWF_OPENDATA_LOW_DATA_VERSION: "ecmwf_opendata_mn2t3_local_calendar_day_min_v1",
}


@pytest.mark.parametrize(
    "canonical_no_suffix,producer_v1", list(_PRODUCER_EMITTED_V1.items())
)
def test_producer_v1_normalizes_into_gate(canonical_no_suffix, producer_v1):
    """normalize(producer ``_v1`` emit) ∈ CANONICAL_ENSEMBLE_DATA_VERSIONS.

    This is the load-bearing cross-module assertion: it ties the producer's
    actual emitted string to the gate via the shared normalizer.
    """
    # Sanity: the producer string really is the no-suffix form plus ``_v1``,
    # so the test is exercising the true producer→gate gap.
    assert producer_v1 == f"{canonical_no_suffix}_v1"

    # Pre-normalization, the raw producer emission is REFUSED by the gate.
    # (Guards against accidental gate-broadening: if someone adds ``_v1`` to
    # the allowlist, this line goes RED and forces a deliberate review.)
    assert producer_v1 not in CANONICAL_ENSEMBLE_DATA_VERSIONS

    # The shared normalizer maps it to the canonical no-suffix form...
    normalized = normalize_opendata_data_version(producer_v1)
    assert normalized == canonical_no_suffix

    # ...which the gate admits without raising.
    assert normalized in CANONICAL_ENSEMBLE_DATA_VERSIONS
    assert_data_version_allowed(normalized, context="producer_subset_gate_test")


def test_normalizer_is_idempotent_and_scoped():
    """The normalizer only strips ``_v1`` from ``ecmwf_opendata_*`` versions.

    TIGGE ``_v1`` versions are a DIFFERENT physical lineage (mx2t6 archive) and
    their ``_v1`` suffix is canonical, not cruft — the normalizer must NOT touch
    them, or it would corrupt TIGGE provenance.
    """
    # Idempotent on already-canonical OpenData.
    assert (
        normalize_opendata_data_version(ECMWF_OPENDATA_HIGH_DATA_VERSION)
        == ECMWF_OPENDATA_HIGH_DATA_VERSION
    )
    # TIGGE _v1 is canonical — must be left intact.
    tigge_v1 = "tigge_mx2t6_local_calendar_day_max_v1"
    assert normalize_opendata_data_version(tigge_v1) == tigge_v1
    # Non-opendata strings pass through untouched.
    assert normalize_opendata_data_version("something_else_v1") == "something_else_v1"
    # Empty / None-ish handled defensively.
    assert normalize_opendata_data_version("") == ""
