# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   (GPT-5.6 Pro deep review, adopted target shape) BLOCKER-1/BLOCKER-3, critic I-1.

"""ReviewWorkItem — the owner-local review/retry/operator-resolution protocol.

Quarantine excision (docs/rebuild/quarantine_excision_2026-07-11.md) replaces every
scattered "mint a quarantine state, exclude forever, wait for a human" mechanism with
ONE typed protocol: a schema/reason-vocabulary/scheduler/operator-contract shape
instantiated in EACH fact's OWNING physical DB (trade first; forecasts/world follow the
same shape later — see src/state/schema/review_work_items_schema.py). Typed domain
facts (ChainOnlyFact, SettlementDispute, CertificateRevocation, EntryExposureObligation,
source blocks, ingestion rejections) remain the AUTHORITIES; a ReviewWorkItem never
replaces one. It schedules re-observation/retry/operator resolution and is rebuildable
from the facts it references — deleting every ReviewWorkItem row must never lose truth,
only lose retry-cadence bookkeeping.

Eligibility is permanent (no terminal exclusion state — the disease this excision
removes); execution FREQUENCY and ACTIVE CARDINALITY are what get bounded (per-cycle
budgets, priority ordering, CAS resolution so a stale authority_revision can never
resolve a live item).

K0/K3 layering note: this module is K0_frozen_kernel (architecture/zones.yaml). The
live family-exclusive-dedup gate's ``WeatherFamilyKey``
(src/strategy/family_exclusive_dedup.py) is K3_extension; K0 must not import K3 (BI-04
direction: extension code consumes contracts, not the reverse). ``FamilyKey`` below is a
structural mirror (identical field names/order/equality) so a ``ReviewWorkItem`` can
carry a family identity without inverting that dependency. src/state/review_work_items.py
(K2_runtime) converts 1:1 between the two when interoperating with the live gate.

INV-37: nothing here opens a connection; storage lives in
src/state/review_work_items.py / src/state/schema/review_work_items_schema.py.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ReviewReasonCode(str, Enum):
    """Reason vocabulary for a ReviewWorkItem. EXTENSIBLE: new members may be
    added as later excision packets (T2/T4/T5/T2b/DIQ) replace more scattered
    quarantine mechanisms with this protocol. Unlike ``WorkItemStatus`` (a
    genuinely closed lifecycle, DB-CHECK-enforced), this vocabulary is NOT
    CHECK-constrained in the schema — a SQLite CHECK cannot be ALTERed, and a
    closed CHECK here would recreate exactly the table-rebuild churn this
    excision is removing elsewhere (see T1's dropped disposition CHECK).
    Validation of known members happens in Python, at construction time.
    """

    # T4 (fill_tracker): venue payload missing fill size/price/economics.
    MISSING_FILL_ECONOMICS = "MISSING_FILL_ECONOMICS"
    # T4 (fill_tracker): venue payload missing authority fields needed to
    # trust the fill (e.g. no matching order/trade linkage).
    MISSING_FILL_AUTHORITY = "MISSING_FILL_AUTHORITY"
    # T4/general: a LOCAL ledger/canonical write failed; venue/chain truth is
    # NOT in question — a local bug must not relabel venue truth.
    LOCAL_WRITE_FAILURE = "LOCAL_WRITE_FAILURE"
    # T5 (critic I-1/I-2): a venue-confirmed fill conflicts with a chain
    # snapshot that shows absence — REAL exposure, disputed evidence.
    CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT = "CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT"
    # T5 (critic I-2): terminal-restore path recovered exposure that needs
    # operator/re-observation review before it is trusted as final.
    TERMINAL_RESTORE_EXPOSURE = "TERMINAL_RESTORE_EXPOSURE"
    # T2 (chain-only unknown asset): venue token inventory with no matching
    # local intent — family-scoped entry block + worst-case exposure.
    CHAIN_ONLY_UNKNOWN_ASSET = "CHAIN_ONLY_UNKNOWN_ASSET"
    # DIQ (decision_integrity revocation replacement): a certificate's
    # validity has been revoked; pre-submit / recovery gates must refuse it.
    CERTIFICATE_REVOKED = "CERTIFICATE_REVOKED"
    # T4 (fill_tracker, BLOCKER-3): a pending-entry order timed out with no
    # definitive venue (CLOB) classification, and the ChainObservationEnvelope
    # available at the call site does not qualify as a confirmed-absence vote
    # (missing / stale / not post-command-watermarked). Chain is the arbiter —
    # this position stays pending_tracked, never force-voided on ambiguity.
    TIMEOUT_ABSENCE_UNCONFIRMED = "TIMEOUT_ABSENCE_UNCONFIRMED"


# Reason codes whose open work items represent REAL or UNKNOWN exposure on a
# whole weather outcome family (T2 target form: family-scoped, not
# condition-scoped, because sibling temperature bins are not independent —
# see src.strategy.family_exclusive_dedup module docstring). Consulted by
# src.state.review_work_items.blocked_family_keys. Reason codes NOT in this
# set (e.g. LOCAL_WRITE_FAILURE, CERTIFICATE_REVOKED) are real review debt but
# do not by themselves imply unknown family exposure.
FAMILY_BLOCKING_REASON_CODES: frozenset[ReviewReasonCode] = frozenset(
    {
        ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
        ReviewReasonCode.CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT,
        ReviewReasonCode.TERMINAL_RESTORE_EXPOSURE,
        ReviewReasonCode.TIMEOUT_ABSENCE_UNCONFIRMED,
    }
)


class WorkItemStatus(str, Enum):
    """Closed lifecycle (DB-CHECK-enforced — unlike ReviewReasonCode above).

    OPEN       — eligible for due-work scheduling / family-block consult.
    RESOLVED   — an operator or an automated resolver closed it with
                 evidence, CAS-guarded on authority_revision.
    SUPERSEDED — a newer authority_revision made this row moot (the fact it
                 tracked changed underneath it); never resolved with evidence.
    """

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class FamilyKey:
    """Structural mirror of ``WeatherFamilyKey`` (see module docstring: K0
    must not import K3). Two instances with the same field values compare
    equal to each other's tuple form; src/state/review_work_items.py converts
    to/from the live ``WeatherFamilyKey`` type by field-for-field copy.
    """

    city: str
    target_date: str
    temperature_metric: str
    market_family_id: str = ""


@dataclass(frozen=True)
class ReviewWorkItem:
    """One owner-local review/retry/operator-resolution record.

    Physically owner-local (adjudication): a ReviewWorkItem for a trade-DB
    fact lives in the trade DB's own ``review_work_items`` table, written in
    the SAME transaction as the fact it tracks (INV-37 same-connection
    write). Cross-DB visibility is a read-only union across owning DBs, never
    a cross-DB write.
    """

    work_id: str
    owner_domain: str  # physical DB identity, e.g. "trade" | "world" | "forecasts"
    owner_table: str  # subject's owning table, e.g. "position_current"
    subject_id: str  # stable identity within owner_table (e.g. position_id)
    reason_code: ReviewReasonCode
    authority_revision: int
    evidence_refs: tuple[str, ...]
    evidence_hash: str
    first_seen_at: str  # ISO-8601 UTC
    last_seen_at: str  # ISO-8601 UTC
    family_key: Optional[FamilyKey] = None
    # Exactly one of {exposure_bound_usd is not None, unbounded is True}.
    # A bounded item carries a conservative worst-case dollar figure (e.g.
    # copied from an EntryExposureObligation); unbounded means no usable
    # size/cost figure exists yet — BLOCKER-1's "unbounded -> DATA_DEGRADED"
    # leg, never silently treated as zero exposure.
    exposure_bound_usd: Optional[float] = None
    unbounded: bool = False
    attempt_count: int = 0
    next_attempt_at: str = ""  # ISO-8601 UTC; due-work scheduler input
    priority: int = 100  # lower sorts first; see due_work() ORDER BY
    last_error_class: str = ""
    last_error_detail: str = ""
    status: WorkItemStatus = WorkItemStatus.OPEN
    resolver_identity: str = ""
    resolution_evidence: str = ""
    resolved_at: Optional[str] = None

    def __post_init__(self) -> None:  # type: ignore[override]
        if not str(self.work_id).strip():
            raise ValueError("ReviewWorkItem.work_id must be non-empty")
        if not str(self.owner_domain).strip():
            raise ValueError("ReviewWorkItem.owner_domain must be non-empty")
        if not str(self.owner_table).strip():
            raise ValueError("ReviewWorkItem.owner_table must be non-empty")
        if not str(self.subject_id).strip():
            raise ValueError("ReviewWorkItem.subject_id must be non-empty")
        if not isinstance(self.reason_code, ReviewReasonCode):
            raise ValueError(
                f"ReviewWorkItem.reason_code must be a ReviewReasonCode, got {self.reason_code!r}"
            )
        if int(self.authority_revision) < 0:
            raise ValueError("ReviewWorkItem.authority_revision must be >= 0")
        bounded = self.exposure_bound_usd is not None
        if bounded == bool(self.unbounded):
            raise ValueError(
                "ReviewWorkItem requires exactly one of exposure_bound_usd or "
                f"unbounded=True (exposure_bound_usd={self.exposure_bound_usd!r}, "
                f"unbounded={self.unbounded!r})"
            )
        if bounded and float(self.exposure_bound_usd) < 0.0:  # type: ignore[arg-type]
            raise ValueError("ReviewWorkItem.exposure_bound_usd must be >= 0")
        if int(self.attempt_count) < 0:
            raise ValueError("ReviewWorkItem.attempt_count must be >= 0")
        if not isinstance(self.status, WorkItemStatus):
            raise ValueError(
                f"ReviewWorkItem.status must be a WorkItemStatus, got {self.status!r}"
            )


def evidence_hash_for(evidence_refs: tuple[str, ...]) -> str:
    """Deterministic sha256 over sorted evidence refs (convenience helper;
    callers may also supply their own evidence_hash — this is never enforced).
    """

    joined = "|".join(sorted(str(ref) for ref in evidence_refs))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResolveWorkItemRequest:
    """Operator resolution command CONTRACT (shape only — no supervisor_api
    wiring in this packet; T6 binds this to a live operator command as the
    replacement release valve for the retired quarantine-ack machinery).

    CAS-guarded by construction: a resolve request names the
    authority_revision it believes is live; src.state.review_work_items.
    resolve_work_item refuses when the live row has moved on (stale revision).
    """

    work_id: str
    authority_revision: int
    resolver_identity: str
    resolution_evidence: str
    evidence_refs: tuple[str, ...] = ()
    requested_at: str = ""

    def __post_init__(self) -> None:  # type: ignore[override]
        if not str(self.work_id).strip():
            raise ValueError("ResolveWorkItemRequest.work_id must be non-empty")
        if int(self.authority_revision) < 0:
            raise ValueError("ResolveWorkItemRequest.authority_revision must be >= 0")
        if not str(self.resolver_identity).strip():
            raise ValueError("ResolveWorkItemRequest.resolver_identity must be non-empty")
        if not str(self.resolution_evidence).strip():
            raise ValueError("ResolveWorkItemRequest.resolution_evidence must be non-empty")


__all__ = [
    "ReviewReasonCode",
    "FAMILY_BLOCKING_REASON_CODES",
    "WorkItemStatus",
    "FamilyKey",
    "ReviewWorkItem",
    "evidence_hash_for",
    "ResolveWorkItemRequest",
]
