# Created: 2026-09-27
# Last reused or audited: 2026-09-27
# Authority basis: correction design review REQ-20260925-223704 §2 (persist every
#   cut before its winner/no-winner branch; raw q before any rejection), §3
#   (complete ordered raw YES simplex + synchronized market snapshot; missing or
#   invalid quotes stored with a reason, never patched), §10 (idempotent on
#   immutable identities; the corpus never touches receipt atomicity).
"""Build, queue and persist the complete auction learning corpus.

``build_cut_corpus`` turns one evaluated cut's frozen inputs into ready rows.
It performs no I/O. ``build_unreceipted_cut`` does the same for a cut that
ended before its receipt. Either result is queued in-process per trade DB.
``flush_pending_cuts`` then writes the queue in its own short trade-DB
transaction, after the auction receipt has committed. The corpus therefore
never shares, extends or endangers the receipt transaction. A failed flush,
for example on SQLITE_FULL, loses nothing: the rows stay queued and the next
receipt retries them.

Raw q comes from the family witness with the same projection as the solver's
``family_payoff_point_q``. It does not come from a sealed correction, which
exists only for scored legs, so a candidate rejected before scoring still
carries the raw q it was rejected against. A deterministic Day0 witness gives
q only for its proved bins; an unproved bin stays NULL.

A family state is content-addressed over its topology, witness content
identities, per-bin book top and leg outcomes. A family unchanged across
consecutive cuts is stored once, and each cut links to it through
``tier0_cut_family``. The YES midpoint is reported only when YES bid and ask
both exist, are positive and are uncrossed. Otherwise a reason is stored and
no number is patched in.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping, Sequence

import zstandard

from src.solve.solver import (
    DeterministicBinPayoffWitness,
    JointOutcomeProbabilityWitness,
)
from src.state.schema.tier0_auction_corpus_schema import (
    CUT_ENCODING,
    SNAPSHOT_ENCODING,
    TOPOLOGY_ENCODING,
)

_ZSTD_LEVEL = 3
# ~100-200 KB per built cut: the queue is bounded to ~100 MB however long
# flushes fail (e.g. a full disk).
_QUEUE_LIMIT = 512
_FLUSH_LIMIT = 16


class Tier0CorpusIdentityConflict(RuntimeError):
    """An immutable cut identity already holds a different payload."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def encode_payload(raw: bytes) -> bytes:
    """zstd-3 BLOB for one canonical-JSON corpus payload."""

    return zstandard.ZstdCompressor(level=_ZSTD_LEVEL).compress(raw)


def decode_payload(blob: bytes) -> object:
    """Inverse of ``encode_payload`` for every ``*_ENCODING`` in the schema."""

    return json.loads(zstandard.ZstdDecompressor().decompress(blob))


def _id128(*parts: bytes) -> bytes:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
        digest.update(b"\x1f")
    return digest.digest()[:16]


def witness_yes_q_by_bin(witness: object) -> dict[str, float]:
    """Raw YES point probability per bin, exactly as ``family_payoff_point_q``.

    A joint witness gives every column. A deterministic Day0 witness gives only
    its proved bins; an unproved bin is absent, never zero.
    """

    if isinstance(witness, JointOutcomeProbabilityWitness):
        return dict(zip(witness.bin_ids, witness.yes_point_q.tolist()))
    if isinstance(witness, DeterministicBinPayoffWitness):
        return {bin_id: float(value) for bin_id, value in witness.exact_yes_payoffs}
    return {}


def held_payoff_q(yes_q: float, side: str) -> float:
    """``family_payoff_point_q``'s native-side projection of a YES column."""

    return yes_q if side == "YES" else 1.0 - yes_q


def candidate_raw_q(
    witnesses: Mapping[str, object],
    evaluation: object,
    yes_by_family: dict[str, dict[str, float]] | None = None,
) -> float | None:
    """Held-side raw q for one evaluated leg, from the witness it was scored on."""

    family_key = evaluation.family_key
    witness = witnesses.get(family_key)
    if (
        witness is None
        or witness.witness_identity != evaluation.probability_witness_identity
    ):
        return None
    cache = {} if yes_by_family is None else yes_by_family
    yes_by_bin = cache.get(family_key)
    if yes_by_bin is None:
        yes_by_bin = cache[family_key] = witness_yes_q_by_bin(witness)
    yes = yes_by_bin.get(evaluation.bin_id)
    if yes is None:
        return None
    return held_payoff_q(yes, evaluation.side)


def _quote(
    asset: object | None,
    sell_asset: object | None,
    epoch_captured_at: datetime | None,
) -> tuple[str | None, str | None, str | None]:
    """(best bid, best ask, captured_at when it differs from the epoch clock).

    Prices keep the venue's own decimal text (``str`` of the book Decimal).
    ``min``/``max`` rather than ``levels[0]``: the best level must not depend
    on each curve type's sort convention.
    """

    ask = bid = captured = None
    if asset is not None:
        if asset.curve.levels:
            ask = str(min(level.price for level in asset.curve.levels))
        if asset.bid_levels:
            bid = str(max(level.price for level in asset.bid_levels))
        captured = asset.captured_at_utc
    if bid is None and sell_asset is not None:
        if sell_asset.curve.levels:
            bid = str(max(level.price for level in sell_asset.curve.levels))
        captured = captured or sell_asset.captured_at_utc
    if captured is None or captured == epoch_captured_at:
        return bid, ask, None
    return bid, ask, captured.isoformat()


def _yes_mid(
    status: str | None,
    bid: str | None,
    ask: str | None,
) -> tuple[str | None, str | None]:
    """(mid, reason). Exactly one of the two is None."""

    if status is None:
        return None, "BOOK_SIDE_NOT_CAPTURED"
    if status not in {"EXECUTABLE", "NO_ASK"}:
        return None, status
    if bid is None:
        return None, "YES_BID_MISSING"
    if ask is None:
        return None, "YES_ASK_MISSING"
    b, a = Decimal(bid), Decimal(ask)
    if b <= 0 or a <= 0:
        return None, "YES_QUOTE_NON_POSITIVE"
    if b > a:
        return None, "YES_QUOTE_CROSSED"
    return format(((b + a) / 2).normalize(), "f"), None


@dataclass(frozen=True)
class FamilyRows:
    """One family's rows in one cut. Topology and state are content-addressed."""

    topology_id: bytes
    topology: tuple[object, ...]
    family_state_id: bytes
    snapshot: tuple[object, ...]
    witness_identity: bytes


@dataclass(frozen=True)
class CutCorpus:
    cut_row: tuple[object, ...]
    families: tuple[FamilyRows, ...]
    q_raw_by_candidate: Mapping[str, float]


def _topology(
    witness: object,
    context: Mapping[str, str],
    unit: str | None,
    first_seen: str,
) -> tuple[bytes, tuple[object, ...]]:
    bindings = [
        [b.bin_id, b.condition_id, b.yes_token_id, b.no_token_id]
        for b in witness.bindings
    ]
    raw = _canonical(
        {
            "family_key": str(witness.family_key),
            "family_binding_identity": str(witness.family_binding_identity),
            "topology_identity": str(witness.topology_identity),
            "resolution_identity": str(witness.resolution_identity),
            "column_order": "witness_binding_order",
            "bindings": bindings,
            "native_unit": unit,
        }
    )
    topology_id = _id128(b"tier0_topology_v1", raw)
    return topology_id, (
        topology_id,
        str(witness.family_key),
        str(context.get("city") or ""),
        str(context.get("target_date") or ""),
        str(context.get("metric") or ""),
        unit,
        len(bindings),
        TOPOLOGY_ENCODING,
        encode_payload(raw),
        first_seen,
    )


def cut_status(*, winner_candidate_id: str | None, candidate_count: int) -> str:
    if winner_candidate_id:
        return "SELECTED"
    return "NO_TRADE" if candidate_count else "NO_CANDIDATES"


def _cut_row(
    *,
    cut_id: str,
    selection_epoch_identity: str | None,
    status: str,
    reason: str | None,
    decision_iso: str,
    selection_policy_identity: str,
    full_scope_family_count: int,
    eligible_family_count: int,
    candidate_count: int,
    winner_candidate_id: str | None,
    payload: Mapping[str, object],
    cancel_source: str | None = None,
    cancel_stage: str | None = None,
) -> tuple[object, ...]:
    raw = _canonical(dict(payload))
    return (
        cut_id,
        selection_epoch_identity,
        status,
        reason,
        decision_iso,
        selection_policy_identity,
        full_scope_family_count,
        eligible_family_count,
        candidate_count,
        winner_candidate_id,
        None,
        CUT_ENCODING,
        hashlib.sha256(raw).hexdigest(),
        encode_payload(raw),
        datetime.now(timezone.utc).isoformat(),
        cancel_source,
        cancel_stage,
    )


def build_cut_corpus(
    *,
    selection_epoch_identity: str,
    reason: str | None,
    decision_at_utc: datetime,
    selection_policy_identity: str,
    full_scope_family_count: int,
    probability_witnesses: Mapping[str, object],
    ineligible_by_family: Mapping[str, str],
    excluded_by_family: Mapping[str, str],
    evaluations: Sequence[object],
    winner_candidate_id: str | None,
    book_epoch: object | None,
    family_context_by_key: Mapping[str, Mapping[str, str]],
    native_unit_by_city: Mapping[str, str],
    policy: Mapping[str, object],
) -> CutCorpus:
    """Freeze one evaluated cut's corpus rows. Pure: no I/O."""

    states = {
        (str(row[0]), str(row[1]), str(row[3])): str(row[5])
        for row in tuple(getattr(book_epoch, "asset_states", ()) or ())
    }
    assets = getattr(book_epoch, "asset_by_key", None) or {}
    sell_assets = getattr(book_epoch, "sell_asset_by_key", None) or {}
    book_at = getattr(book_epoch, "captured_at_utc", None)
    book_max_age = getattr(book_epoch, "max_age", None)
    legs_by_family: dict[str, list[tuple[object, ...]]] = {}
    q_raw_by_candidate: dict[str, float] = {}
    yes_by_family: dict[str, dict[str, float]] = {}
    for evaluation in evaluations:
        q = candidate_raw_q(probability_witnesses, evaluation, yes_by_family)
        if q is not None:
            q_raw_by_candidate[str(evaluation.candidate_id)] = q
        legs_by_family.setdefault(str(evaluation.family_key), []).append(
            (
                str(evaluation.bin_id),
                str(evaluation.side),
                str(evaluation.action),
                str(evaluation.execution_mode),
                str(evaluation.status),
                evaluation.rejection_reason,
                evaluation.q_served,
            )
        )
    decision_iso = decision_at_utc.astimezone(timezone.utc).isoformat()
    families: list[FamilyRows] = []
    for family_key in sorted(probability_witnesses):
        witness = probability_witnesses[family_key]
        context = family_context_by_key.get(family_key) or {}
        topology_id, topology = _topology(
            witness,
            context,
            native_unit_by_city.get(str(context.get("city") or "")),
            decision_iso,
        )
        bindings = tuple(witness.bindings)
        column = {binding.bin_id: index for index, binding in enumerate(bindings)}
        yes_by_bin = yes_by_family.get(family_key)
        if yes_by_bin is None:
            yes_by_bin = yes_by_family[family_key] = witness_yes_q_by_bin(witness)
        yes_q = [yes_by_bin.get(b.bin_id) for b in bindings]
        simplex_complete = bool(bindings) and all(
            q is not None for q in yes_q
        ) and math.isclose(sum(yes_q), 1.0, abs_tol=1e-9)
        book: list[list[object]] = []
        mids_complete = bool(bindings)
        for binding in bindings:
            row: list[object] = []
            for side, token in (("YES", binding.yes_token_id), ("NO", binding.no_token_id)):
                key = (family_key, binding.bin_id, side, str(token or ""))
                row.append(states.get((family_key, binding.bin_id, side)))
                row.extend(_quote(assets.get(key), sell_assets.get(key), book_at))
            mid, mid_reason = _yes_mid(row[0], row[1], row[2])
            mids_complete = mids_complete and mid_reason is None
            row.extend([mid, mid_reason])
            book.append(row)
        legs = sorted(
            (
                [column.get(leg[0], leg[0]), *leg[1:]]
                for leg in legs_by_family.get(family_key, ())
            ),
            key=lambda leg: (str(leg[0]), *leg[1:4]),
        )
        raw = _canonical(
            {
                "q_version": str(getattr(witness, "q_version", "") or ""),
                "posterior_identity_hash": str(
                    getattr(witness, "posterior_identity_hash", "") or ""
                ),
                "source_truth_identity": str(
                    getattr(witness, "source_truth_identity", "") or ""
                ),
                "raw_yes_q": yes_q,
                "book": book,
                "legs": legs,
                "family_excluded_reason": excluded_by_family.get(family_key),
            }
        )
        family_state_id = _id128(b"tier0_family_state_v1", topology_id, raw)
        families.append(
            FamilyRows(
                topology_id=topology_id,
                topology=topology,
                family_state_id=family_state_id,
                snapshot=(
                    family_state_id,
                    type(witness).__name__,
                    int(simplex_complete),
                    int(mids_complete),
                    SNAPSHOT_ENCODING,
                    encode_payload(raw),
                    decision_iso,
                ),
                witness_identity=bytes.fromhex(str(witness.witness_identity)),
            )
        )
    book_age = (
        (decision_at_utc - book_at).total_seconds() if book_at is not None else None
    )
    return CutCorpus(
        cut_row=_cut_row(
            cut_id=hashlib.sha256(
                _canonical(["tier0_cut_v1", selection_epoch_identity, decision_iso])
            ).hexdigest(),
            selection_epoch_identity=selection_epoch_identity,
            status=cut_status(
                winner_candidate_id=winner_candidate_id,
                candidate_count=len(evaluations),
            ),
            reason=reason,
            decision_iso=decision_iso,
            selection_policy_identity=selection_policy_identity,
            full_scope_family_count=full_scope_family_count,
            eligible_family_count=len(probability_witnesses),
            candidate_count=len(evaluations),
            winner_candidate_id=winner_candidate_id,
            payload={
                # Per-family state and witness identity live in tier0_cut_family.
                "ineligible_by_family": dict(sorted(ineligible_by_family.items())),
                "excluded_by_family": dict(sorted(excluded_by_family.items())),
                "book_epoch_identity": getattr(book_epoch, "witness_identity", None),
                "book_captured_at_utc": (
                    book_at.isoformat() if book_at is not None else None
                ),
                "book_max_age_seconds": (
                    book_max_age.total_seconds() if book_max_age is not None else None
                ),
                "book_age_seconds": book_age,
                "book_stale": (
                    book_age is not None
                    and book_max_age is not None
                    and book_age > book_max_age.total_seconds()
                ),
                "policy": dict(policy),
            },
        ),
        families=tuple(families),
        q_raw_by_candidate=q_raw_by_candidate,
    )


_NO_CANDIDATE_REASONS = (
    "GLOBAL_AUCTION_NO_CURRENT_PROBABILITY_FAMILY",
    "GLOBAL_AUCTION_NO_REDUCE_ONLY_FAMILY",
    "GLOBAL_FAMILY_INELIGIBLE:",
)


def build_unreceipted_cut(
    *,
    reason: str,
    decision_at_utc: datetime,
    selection_policy_identity: str,
    economic_cut_completed: bool,
    detail: Mapping[str, object],
    cancel_source: str | None = None,
    cancel_stage: str | None = None,
) -> CutCorpus:
    """Corpus for a cut that ended before its auction receipt was written.

    Such a cut never froze a full witness/book vector, so it records the
    status, reason and decision time, and nothing is reconstructed. A
    cancelled INCOMPLETE cut also records its cancel source and stage.
    """

    if reason.startswith(_NO_CANDIDATE_REASONS):
        status = "NO_CANDIDATES"
    elif economic_cut_completed:
        status = "NO_TRADE"
    else:
        status = "INCOMPLETE"
    return CutCorpus(
        cut_row=_cut_row(
            cut_id=uuid.uuid4().hex,
            selection_epoch_identity=None,
            status=status,
            reason=reason,
            decision_iso=decision_at_utc.astimezone(timezone.utc).isoformat(),
            selection_policy_identity=selection_policy_identity,
            full_scope_family_count=0,
            eligible_family_count=0,
            candidate_count=0,
            winner_candidate_id=None,
            payload=detail,
            cancel_source=cancel_source if status == "INCOMPLETE" else None,
            cancel_stage=cancel_stage if status == "INCOMPLETE" else None,
        ),
        families=(),
        q_raw_by_candidate={},
    )


CandidateRows = tuple[tuple[object, ...], ...]


class PendingCut:
    """One queued cut. Its rows are built lazily, once, off the receipt path.

    ``build`` closes over the cut's frozen inputs and returns the corpus plus
    any winner candidate rows. ``rows()`` calls it at most once and then drops
    it, so a queued cut holds compact bytes, not witness matrices.
    """

    __slots__ = ("_build", "_rows", "decision_log_id")

    def __init__(
        self,
        build: Callable[[], tuple[CutCorpus, CandidateRows]],
        decision_log_id: int | None,
    ) -> None:
        self._build: Callable[[], tuple[CutCorpus, CandidateRows]] | None = build
        self._rows: tuple[CutCorpus, CandidateRows] | None = None
        self.decision_log_id = decision_log_id

    def rows(self) -> tuple[CutCorpus, CandidateRows]:
        if self._rows is None:
            build, self._build = self._build, None
            assert build is not None
            self._rows = build()
        return self._rows


_PENDING_LOCK = threading.Lock()
_PENDING: dict[str, list[PendingCut]] = {}
_OVERFLOW: dict[str, int] = {}


def queue_cut(db_key: str, cut: PendingCut) -> None:
    """Hold a cut until a flush on ``db_key`` commits it.

    SCOPE: this process's cuts. DRAIN: every global batch ends with one
    bounded flush. RESET: rows leave the queue only after their flush
    transaction committed. When the queue is full the oldest cut is dropped
    and counted; the next flush writes the count as its own INCOMPLETE row, so
    the loss is recorded rather than silent. A process exit loses the queue.
    """

    with _PENDING_LOCK:
        queue = _PENDING.setdefault(db_key, [])
        if len(queue) >= _QUEUE_LIMIT:
            del queue[0]
            _OVERFLOW[db_key] = _OVERFLOW.get(db_key, 0) + 1
        queue.append(cut)


def pending_cuts(db_key: str) -> tuple[tuple[PendingCut, ...], int]:
    """The oldest queued cuts, at most ``_FLUSH_LIMIT``, and the overflow count.

    The bound keeps each flush transaction short; a batch queues one to a few
    cuts, so live traffic drains within one flush.
    """

    with _PENDING_LOCK:
        return tuple(_PENDING.get(db_key, ())[:_FLUSH_LIMIT]), _OVERFLOW.get(db_key, 0)


def release_cuts(db_key: str, written: Sequence[PendingCut], overflow: int) -> None:
    """Drop cuts, and the overflow count, whose flush transaction committed."""

    ids = {id(cut) for cut in written}
    with _PENDING_LOCK:
        queue = _PENDING.get(db_key)
        if queue is not None:
            queue[:] = [cut for cut in queue if id(cut) not in ids]
        remaining = _OVERFLOW.get(db_key, 0) - overflow
        if remaining > 0:
            _OVERFLOW[db_key] = remaining
        else:
            _OVERFLOW.pop(db_key, None)


_CUT_COLUMNS = (
    "cut_id", "selection_epoch_identity", "status", "reason", "decision_at_utc",
    "selection_policy_identity", "full_scope_family_count", "eligible_family_count",
    "candidate_count", "winner_candidate_id", "decision_log_id",
    "payload_encoding", "payload_sha256", "payload", "created_at",
    "cancel_source", "cancel_stage",
)
_TOPOLOGY_COLUMNS = (
    "topology_id", "family_key", "city", "target_date", "metric", "native_unit",
    "bin_count", "payload_encoding", "payload", "first_seen_at_utc",
)
_SNAPSHOT_COLUMNS = (
    "family_state_id", "witness_kind", "simplex_complete",
    "market_reference_complete", "payload_encoding", "payload", "first_seen_at_utc",
)


def _sequence_ids(
    conn: sqlite3.Connection,
    *,
    table: str,
    seq: str,
    key: str,
    columns: Sequence[str],
    rows: Mapping[bytes, tuple[object, ...]],
    extra: Mapping[bytes, Sequence[object]] | None = None,
    extra_columns: Sequence[str] = (),
) -> dict[bytes, int]:
    """Insert absent content-addressed rows; return {content id: integer seq}."""

    found: dict[bytes, int] = {}
    keys = list(rows)
    for offset in range(0, len(keys), 400):
        chunk = keys[offset : offset + 400]
        marks = ",".join("?" for _ in chunk)
        found.update(
            (bytes(row[0]), int(row[1]))
            for row in conn.execute(
                f"SELECT {key}, {seq} FROM {table} WHERE {key} IN ({marks})", chunk
            )
        )
    missing = [k for k in keys if k not in found]
    if missing:
        names = ",".join((*columns, *extra_columns))
        marks = ",".join("?" for _ in (*columns, *extra_columns))
        for k in missing:
            cursor = conn.execute(
                f"INSERT INTO {table} ({names}) VALUES ({marks})",
                (*rows[k], *(extra[k] if extra else ())),
            )
            found[k] = int(cursor.lastrowid)
    return found


def cut_write_units(
    conn: sqlite3.Connection,
    corpus: CutCorpus,
    *,
    decision_log_id: int | None,
    max_new_rows: int,
) -> list[Callable[[], None]]:
    """Split one cut into writes of at most ``max_new_rows`` new content rows.

    Each unit runs in its own transaction, so the bytes committed, which is
    what drives commit latency on this host, stay bounded. The
    content-addressed topology and snapshot rows go first, in chunks. The final
    unit writes the cut row and its links, and it is the only unit that makes
    the cut visible. Every unit is idempotent: a retry after a partial flush
    finds the earlier chunks already present and writes only what is missing.
    """

    families = corpus.families
    units: list[Callable[[], None]] = []
    for offset in range(0, len(families), max_new_rows):
        chunk = families[offset : offset + max_new_rows]
        units.append(lambda chunk=chunk: _write_family_content(conn, chunk))
    units.append(
        lambda: write_cut(conn, corpus, decision_log_id=decision_log_id)
    )
    return units


def _write_family_content(
    conn: sqlite3.Connection,
    families: Sequence[FamilyRows],
) -> tuple[dict[bytes, int], dict[bytes, int]]:
    topology_seq = _sequence_ids(
        conn,
        table="tier0_family_topology",
        seq="topology_seq",
        key="topology_id",
        columns=_TOPOLOGY_COLUMNS,
        rows={f.topology_id: f.topology for f in families},
    )
    state_seq = _sequence_ids(
        conn,
        table="tier0_family_snapshot",
        seq="state_seq",
        key="family_state_id",
        columns=_SNAPSHOT_COLUMNS,
        rows={f.family_state_id: f.snapshot for f in families},
        extra={f.family_state_id: (topology_seq[f.topology_id],) for f in families},
        extra_columns=("topology_seq",),
    )
    return topology_seq, state_seq


def write_cut(
    conn: sqlite3.Connection,
    corpus: CutCorpus,
    *,
    decision_log_id: int | None,
) -> None:
    """Write one cut inside the caller's open trade-DB transaction.

    Idempotent: an identical retry of a cut already written is a no-op, and a
    conflicting payload for the same cut id raises.
    """

    row = dict(zip(_CUT_COLUMNS, corpus.cut_row))
    row["decision_log_id"] = decision_log_id
    stored = conn.execute(
        "SELECT status, payload_sha256 FROM tier0_auction_cut WHERE cut_id = ?",
        (row["cut_id"],),
    ).fetchone()
    if stored is not None:
        if tuple(stored) != (row["status"], row["payload_sha256"]):
            raise Tier0CorpusIdentityConflict(
                f"TIER0_CORPUS_IDENTITY_CONFLICT:tier0_auction_cut:{row['cut_id']}"
            )
        return
    families = corpus.families
    topology_seq = _sequence_ids(
        conn,
        table="tier0_family_topology",
        seq="topology_seq",
        key="topology_id",
        columns=_TOPOLOGY_COLUMNS,
        rows={f.topology_id: f.topology for f in families},
    )
    state_seq = _sequence_ids(
        conn,
        table="tier0_family_snapshot",
        seq="state_seq",
        key="family_state_id",
        columns=_SNAPSHOT_COLUMNS,
        rows={f.family_state_id: f.snapshot for f in families},
        extra={f.family_state_id: (topology_seq[f.topology_id],) for f in families},
        extra_columns=("topology_seq",),
    )
    cursor = conn.execute(
        f"INSERT INTO tier0_auction_cut ({','.join(_CUT_COLUMNS)}) "
        f"VALUES ({','.join('?' for _ in _CUT_COLUMNS)})",
        tuple(row[column] for column in _CUT_COLUMNS),
    )
    cut_seq = int(cursor.lastrowid)
    conn.executemany(
        "INSERT INTO tier0_cut_family "
        "(cut_seq, topology_seq, state_seq, probability_witness_identity) "
        "VALUES (?,?,?,?)",
        [
            (
                cut_seq,
                topology_seq[f.topology_id],
                state_seq[f.family_state_id],
                f.witness_identity,
            )
            for f in families
        ],
    )


def overflow_cut(*, overflow: int, selection_policy_identity: str) -> CutCorpus:
    """INCOMPLETE row recording ``overflow`` cuts dropped from a full queue."""

    return build_unreceipted_cut(
        reason=f"CORPUS_QUEUE_OVERFLOW:{overflow}",
        decision_at_utc=datetime.now(timezone.utc),
        selection_policy_identity=selection_policy_identity,
        economic_cut_completed=False,
        detail={"dropped_cut_count": overflow},
    )
