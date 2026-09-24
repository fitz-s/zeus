"""EDLI NoTradeRegretLedger."""

from __future__ import annotations

import sqlite3
import hashlib
import json
import os
import threading
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Literal, Mapping

from src.contracts.global_auction_receipt import CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
from src.events.idempotency import stable_event_id

UTC = timezone.utc
ALPHA_PROTOCOL_VERSION = "causal-brier-feedback-v1"
_ALPHA_V8_PREFIX = "market-relative-alpha-shadow-v8-causal-brier:"
# A new sequence can begin only from observations made after this process has
# installed the protocol. Existing durable slots remain valid across restarts.
_PROSPECTIVE_CAPTURE_START = datetime.now(UTC)
_FEEDBACK_SEEN_LIMIT = 128
_FEEDBACK_SEEN: OrderedDict[tuple[str, str, str, str, str], tuple[str, datetime]] = OrderedDict()
_PENDING_ACK_BY_CONNECTION: OrderedDict[int, str] = OrderedDict()
_FEEDBACK_SEEN_LOCK = threading.Lock()
_ALPHA_SELECTION_RULE = (
    "earliest_complete_global_cut_exact_global_posterior_mean_"
    "expected_growth_winner_v3"
)


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("alpha protocol clock must be timezone-aware")
    return parsed.astimezone(UTC)


def _database_identity(conn: sqlite3.Connection) -> str:
    main = next(
        (str(row[2]) for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        "",
    )
    return os.path.realpath(main) if main else f":memory:{id(conn)}"


def _first_committed_feedback_seen_at(
    conn: sqlite3.Connection,
    *,
    cohort: tuple[str, str, str, str],
    feedback_hash: str,
    outer_was_active: bool,
) -> datetime | None:
    """Return a prior first-read clock, never the clock of this first read."""

    key = (_database_identity(conn), *cohort)
    with _FEEDBACK_SEEN_LOCK:
        pending = _PENDING_ACK_BY_CONNECTION.get(id(conn))
        if pending is not None:
            if outer_was_active and pending == feedback_hash:
                # This connection could be reading its own uncommitted ACK.
                return None
            if not outer_was_active:
                _PENDING_ACK_BY_CONNECTION.pop(id(conn), None)
        seen = _FEEDBACK_SEEN.get(key)
        if seen is None or seen[0] != feedback_hash:
            _FEEDBACK_SEEN[key] = (feedback_hash, datetime.now(UTC))
            _FEEDBACK_SEEN.move_to_end(key)
            if len(_FEEDBACK_SEEN) > _FEEDBACK_SEEN_LIMIT:
                _FEEDBACK_SEEN.popitem(last=False)
            return None
        _FEEDBACK_SEEN.move_to_end(key)
        return seen[1]


def _normalized_alpha_proof(proof: Mapping[str, object]) -> dict[str, object]:
    required = {
        "condition_id", "token_id", "side", "envelope_sha256", "outcome",
        "yes_token_id", "no_token_id", "payout_rows",
    }
    if set(proof) != required:
        raise ValueError("alpha proof fields missing or unexpected")
    condition = str(proof["condition_id"] or "")
    yes_token = str(proof["yes_token_id"] or "")
    no_token = str(proof["no_token_id"] or "")
    side = proof["side"]
    token = str(proof["token_id"] or "")
    outcome = proof["outcome"]
    envelope_hash = str(proof["envelope_sha256"] or "")
    if (
        not condition or not yes_token or not no_token or yes_token == no_token
        or side not in {"YES", "NO"}
        or token != (yes_token if side == "YES" else no_token)
        or type(outcome) is not int or outcome not in {0, 1}
        or len(envelope_hash) != 64
        or any(ch not in "0123456789abcdef" for ch in envelope_hash)
    ):
        raise ValueError("invalid alpha proof identity")
    source_rows = proof["payout_rows"]
    if not isinstance(source_rows, (tuple, list)) or len(source_rows) != 2:
        raise ValueError("alpha proof requires both finalized payout rows")
    required_row = {
        "id", "condition_id", "outcome_index", "payout_numerator",
        "payout_denominator", "state", "source", "block_number", "block_hash",
    }
    rows: dict[int, dict[str, object]] = {}
    for raw in source_rows:
        if not isinstance(raw, Mapping) or set(raw) != required_row:
            raise ValueError("invalid alpha payout row fields")
        index = raw["outcome_index"]
        numerator = raw["payout_numerator"]
        denominator = raw["payout_denominator"]
        block_number = raw["block_number"]
        block_hash = str(raw["block_hash"] or "")
        row_id = raw["id"]
        if (
            type(index) is not int or index not in {0, 1} or index in rows
            or type(row_id) is not int or row_id <= 0
            or raw["condition_id"] != condition
            or type(numerator) is not int or numerator < 0
            or type(denominator) is not int or denominator <= 0
            or numerator > denominator
            or raw["state"] != (
                "RESOLVED_ZERO" if numerator == 0 else "RESOLVED_NONZERO"
            )
            or raw["source"] != "chain_rpc_finalized_v1"
            or type(block_number) is not int or block_number <= 0
            or len(block_hash) != 66 or not block_hash.startswith("0x")
            or any(ch not in "0123456789abcdefABCDEF" for ch in block_hash[2:])
        ):
            raise ValueError("invalid finalized alpha payout")
        rows[index] = {key: raw[key] for key in required_row}
    if set(rows) != {0, 1}:
        raise ValueError("incomplete finalized alpha payout")
    yes, no = rows[0], rows[1]
    denominator = yes["payout_denominator"]
    if (
        yes["id"] == no["id"]
        or denominator != no["payout_denominator"]
        or sorted((yes["payout_numerator"], no["payout_numerator"]))
        != [0, denominator]
        or (yes["block_number"], yes["block_hash"])
        != (no["block_number"], no["block_hash"])
        or outcome != int(
            rows[0 if side == "YES" else 1]["payout_numerator"] == denominator
        )
    ):
        raise ValueError("alpha payout pair contradicts selected outcome")
    return {
        "condition_id": condition, "token_id": token, "side": side,
        "envelope_sha256": envelope_hash, "outcome": outcome,
        "yes_token_id": yes_token, "no_token_id": no_token,
        "payout_rows": [yes, no],
    }


def _parse_alpha_feedback(
    raw_json: str, event_id: str, decision_at: str
) -> dict[str, object]:
    try:
        feedback = json.loads(raw_json)
        expected = {
            "protocol_version", "regret_event_id", "observed_at",
            "envelope_sha256", "settlement_proof", "feedback_hash",
        }
        if not isinstance(feedback, dict) or set(feedback) != expected:
            raise ValueError("invalid alpha feedback fields")
        if (
            feedback["protocol_version"] != ALPHA_PROTOCOL_VERSION
            or feedback["regret_event_id"] != event_id
            or _utc(feedback["observed_at"]) <= _utc(decision_at)
        ):
            raise ValueError("invalid alpha feedback identity")
        normalized = _normalized_alpha_proof(feedback["settlement_proof"])
        if feedback["settlement_proof"] != normalized:
            raise ValueError("alpha feedback proof is not canonical")
        if feedback["envelope_sha256"] != normalized["envelope_sha256"]:
            raise ValueError("alpha feedback envelope hash mismatch")
        unsigned = {k: v for k, v in feedback.items() if k != "feedback_hash"}
        if _hash(_canonical_json(unsigned)) != feedback["feedback_hash"]:
            raise ValueError("alpha feedback hash mismatch")
        return feedback
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("invalid alpha feedback") from exc


def validated_alpha_protocol(
    envelope_json: str,
    *,
    event_id: str,
    regret_event_id: str,
    condition_id: str,
    token_id: str,
    direction: str,
    city: str,
    target_date: str,
    metric: str,
) -> dict[str, object]:
    """Pure verifier of the frozen v8 row identity and ordered slot receipt."""

    try:
        envelope = json.loads(envelope_json)
        protocol = envelope["alpha_protocol"]
        strategy = str(envelope["strategy_key"])
        selection = str(envelope["global_selection_revision"])
        revision = str(envelope["probability_semantics_revision"])
        side = envelope["side"]
        slot = protocol["slot"]
        previous_id = protocol["previous_event_id"]
        previous_hash = protocol["previous_feedback_hash"]
        previous_seen = protocol["previous_feedback_seen_at"]
        if (
            strategy not in {"day0_nowcast_entry", "forecast_qkernel_entry"}
            or selection != CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
            or not revision or metric not in {"high", "low"}
            or envelope["schema_version"] != 3
            or envelope["selection_rule"] != _ALPHA_SELECTION_RULE
            or envelope["decision_law_id"] != "executable_min_order_capital_gain_v2"
            or (envelope["city"], envelope["target_date"], envelope["metric"])
            != (city, target_date, metric)
            or envelope["condition_id"] != condition_id
            or envelope["token_id"] != token_id
            or side not in {"YES", "NO"} or direction != f"buy_{side.lower()}"
            or event_id != (
                f"{_ALPHA_V8_PREFIX}{strategy}:{selection}:{revision}:"
                f"{metric}:{city}:{target_date}"
            )
            or regret_event_id != stable_event_id(
                event_id, "RISK_GUARD", f"MARKET_RELATIVE_ALPHA_SHADOW:{strategy}"
            )
            or protocol["version"] != ALPHA_PROTOCOL_VERSION
            or type(slot) is not int or slot <= 0
            or any(protocol.get(key) != envelope.get(key) for key in (
                "strategy_key", "global_selection_revision",
                "probability_semantics_revision", "metric",
            ))
            or (slot == 1 and (
                previous_id is not None or previous_hash is not None
                or previous_seen is not None
            ))
            or (slot > 1 and (
                not isinstance(previous_id, str) or not previous_id
                or not isinstance(previous_hash, str) or len(previous_hash) != 64
                or not isinstance(previous_seen, str)
            ))
        ):
            raise ValueError("alpha protocol identity mismatch")
        decision_at = _utc(envelope["decision_at_utc"])
        cut_at = _utc(envelope["selection_cut_at_utc"])
        if previous_seen is not None and (
            decision_at <= _utc(previous_seen) or cut_at <= _utc(previous_seen)
        ):
            raise ValueError("alpha cut predates feedback visibility")
        return dict(envelope)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("invalid alpha protocol") from exc


def validated_alpha_feedback(
    raw_json: str,
    *,
    regret_event_id: str,
    envelope_json: str,
    condition_id: str,
    token_id: str,
    direction: str,
    city: str,
    target_date: str,
    metric: str,
    event_id: str,
) -> dict[str, object]:
    """Pure verifier shared by the feedback writer and RiskGuard reader."""

    envelope = validated_alpha_protocol(
        envelope_json, event_id=event_id, regret_event_id=regret_event_id,
        condition_id=condition_id, token_id=token_id, direction=direction,
        city=city, target_date=target_date, metric=metric,
    )
    feedback = _parse_alpha_feedback(
        raw_json, regret_event_id, str(envelope["decision_at_utc"])
    )
    proof = feedback["settlement_proof"]
    if (
        feedback["envelope_sha256"] != _hash(envelope_json)
        or proof["condition_id"] != condition_id
        or proof["token_id"] != token_id
        or proof["side"] != envelope["side"]
    ):
        raise ValueError("alpha feedback does not bind its frozen decision")
    return feedback

RejectionStage = Literal[
    "EVENT_FILTER",
    "CAUSAL_STATE",
    "SOURCE_TRUTH",
    "FORECAST_COMPLETENESS",
    "FAMILY_TOPOLOGY",
    "INFERENCE",
    "EXECUTABLE_QUOTE",
    "TRADE_SCORE",
    "FDR",
    "KELLY",
    "RISK_GUARD",
    "EXECUTOR_EXPRESSIBILITY",
    "LIVE_CAP",
    "UNKNOWN_REVIEW_REQUIRED",
]

RegretBucket = Literal[
    "MODEL_WRONG",
    "SOURCE_WRONG",
    "QUOTE_UNAVAILABLE",
    "FEE_ERASED_EDGE",
    "NO_DEPTH",
    "UNFILLABLE",
    "FDR_REJECTED",
    "KELLY_TOO_SMALL",
    "RISK_CAP",
    "SHOULDER_TAIL_BLOCK",
    "FAMILY_INCOMPLETE",
    "WOULD_HAVE_WON_BUT_UNFILLABLE",
    "WOULD_HAVE_WON_AND_FILLABLE",
    "WOULD_HAVE_LOST",
    "EXECUTABLE_GAIN_LOCKED",
    "LEAKAGE_BLOCKED",
    "UNKNOWN_REVIEW_REQUIRED",
]


@dataclass(frozen=True)
class NoTradeRegretEvent:
    event_id: str
    rejection_stage: RejectionStage
    rejection_reason: str
    regret_bucket: RegretBucket
    market_slug: str | None = None
    condition_id: str | None = None
    token_id: str | None = None
    outcome_label: str | None = None
    decision_time: str | None = None
    city: str | None = None
    target_date: str | None = None
    metric: str | None = None
    observation_time: str | None = None
    decision_seq: int | None = None
    family_id: str | None = None
    bin_label: str | None = None
    direction: str | None = None
    q_live: float | None = None
    q_lcb_5pct: float | None = None
    c_fee_adjusted: float | None = None
    c_cost_95pct: float | None = None
    p_fill_lcb: float | None = None
    trade_score: float | None = None
    native_quote_available: bool | None = None
    source_status: str | None = None
    family_complete: bool | None = None
    hypothetical_order_type: str | None = None
    hypothetical_fill_status: str | None = None
    hypothetical_fill_price: float | None = None
    causal_snapshot_id: str | None = None
    executable_snapshot_id: str | None = None
    # DecisionProvenanceEnvelope (operator law 2026-06-11): the complete decision-time provenance
    # blob (canonical JSON). None on legacy / un-enriched rejections; column stays NULL.
    envelope_json: str | None = None
    later_outcome: str | None = None
    would_have_won: bool | None = None
    would_have_filled: bool | None = None


class NoTradeRegretHindsightError(ValueError):
    pass


class NoTradeRegretLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @contextmanager
    def _alpha_write(self) -> Iterator[None]:
        # sqlite3's default connection opens an implicit transaction for an
        # INSERT, whereas a top-level SAVEPOINT would silently commit on RELEASE.
        outer_started = not self.conn.in_transaction and self.conn.isolation_level is not None
        if outer_started:
            self.conn.execute("BEGIN IMMEDIATE")
        self.conn.execute("SAVEPOINT alpha_feedback_write")
        changes_before = self.conn.total_changes
        try:
            # Acquire the single SQLite writer before reading the cohort tail.
            self.conn.execute(
                "UPDATE no_trade_regret_events SET schema_version=schema_version WHERE 0"
            )
            yield
            self.conn.execute("RELEASE SAVEPOINT alpha_feedback_write")
            if outer_started and self.conn.total_changes == changes_before:
                # A deferred cut or identical ACK did not write evidence.
                # Release only our own empty BEGIN IMMEDIATE, never a caller's
                # pre-existing transaction or a real append awaiting commit.
                self.conn.rollback()
        except BaseException:
            self.conn.execute("ROLLBACK TO SAVEPOINT alpha_feedback_write")
            self.conn.execute("RELEASE SAVEPOINT alpha_feedback_write")
            if outer_started:
                self.conn.rollback()
            raise

    def insert_idempotent(self, event: NoTradeRegretEvent) -> str | None:
        if _has_hindsight_fields(event):
            raise NoTradeRegretHindsightError(
                "live no-trade regret insert cannot include later_outcome/would_have_* fields"
            )
        regret_event_id = stable_event_id(event.event_id, event.rejection_stage, event.rejection_reason)
        if event.event_id.startswith(_ALPHA_V8_PREFIX):
            return self._insert_alpha(event, regret_event_id)
        self.conn.execute(
            """
            INSERT OR IGNORE INTO no_trade_regret_events (
                regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
                market_slug, condition_id, token_id, outcome_label,
                decision_time, city, target_date, metric, family_id, bin_label, direction,
                q_live, q_lcb_5pct, c_fee_adjusted, c_cost_95pct, p_fill_lcb, trade_score,
                native_quote_available, source_status, family_complete,
                hypothetical_order_type, hypothetical_fill_status, hypothetical_fill_price,
                causal_snapshot_id, executable_snapshot_id, envelope_json,
                later_outcome, would_have_won, would_have_filled, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                regret_event_id,
                event.event_id,
                event.rejection_stage,
                event.rejection_reason,
                event.regret_bucket,
                event.market_slug,
                event.condition_id,
                event.token_id,
                event.outcome_label,
                event.decision_time,
                event.city,
                event.target_date,
                event.metric,
                event.family_id,
                event.bin_label,
                event.direction,
                event.q_live,
                event.q_lcb_5pct,
                event.c_fee_adjusted,
                event.c_cost_95pct,
                event.p_fill_lcb,
                event.trade_score,
                None if event.native_quote_available is None else int(event.native_quote_available),
                event.source_status,
                None if event.family_complete is None else int(event.family_complete),
                event.hypothetical_order_type,
                event.hypothetical_fill_status,
                event.hypothetical_fill_price,
                event.causal_snapshot_id,
                event.executable_snapshot_id,
                event.envelope_json,
                event.later_outcome,
                None if event.would_have_won is None else int(event.would_have_won),
                None if event.would_have_filled is None else int(event.would_have_filled),
                datetime.now(UTC).isoformat(),
            ),
        )
        if _has_compatibility_natural_key(event):
            self._write_no_trade_events_compatibility(event)
        return regret_event_id

    def _insert_alpha(self, event: NoTradeRegretEvent, event_id: str) -> str | None:
        # A retry of the exact natural key never updates its frozen envelope.
        existing = self.conn.execute(
            "SELECT 1 FROM no_trade_regret_events WHERE regret_event_id=?", (event_id,)
        ).fetchone()
        if existing is not None:
            return event_id
        try:
            envelope = json.loads(str(event.envelope_json or ""))
            strategy = str(envelope["strategy_key"])
            selection = str(envelope["global_selection_revision"])
            revision = str(envelope["probability_semantics_revision"])
            metric = str(envelope["metric"])
            decision_at = _utc(envelope["decision_at_utc"])
            cut_at = _utc(envelope["selection_cut_at_utc"])
            if (
                strategy not in {"forecast_qkernel_entry", "day0_nowcast_entry"}
                or selection != CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                or not revision or metric not in {"high", "low"}
                or envelope["schema_version"] != 3
                or envelope["selection_rule"] != _ALPHA_SELECTION_RULE
                or envelope["decision_law_id"] != "executable_min_order_capital_gain_v2"
                or envelope.get("alpha_protocol") is not None
                or event.rejection_stage != "RISK_GUARD"
                or event.rejection_reason != f"MARKET_RELATIVE_ALPHA_SHADOW:{strategy}"
                or (event.city, event.target_date, event.metric)
                != (envelope.get("city"), envelope.get("target_date"), metric)
                or event.decision_time != envelope["decision_at_utc"]
                or cut_at > decision_at
                or event.event_id != (
                    f"{_ALPHA_V8_PREFIX}{strategy}:{selection}:{revision}:"
                    f"{metric}:{event.city}:{event.target_date}"
                )
            ):
                raise ValueError("invalid v8 alpha identity")
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise ValueError("invalid v8 alpha envelope") from exc

        writer_now = datetime.now(UTC)
        if (
            decision_at < _PROSPECTIVE_CAPTURE_START
            or cut_at < _PROSPECTIVE_CAPTURE_START
            or decision_at > writer_now
            or cut_at > writer_now
        ):
            return None

        cohort_prefix = f"{_ALPHA_V8_PREFIX}{strategy}:{selection}:{revision}:{metric}:"
        outer_was_active = self.conn.in_transaction
        with self._alpha_write():
            if self.conn.execute(
                "SELECT 1 FROM no_trade_regret_events WHERE regret_event_id=?", (event_id,)
            ).fetchone() is not None:
                return event_id
            prior_rows = self.conn.execute(
                "SELECT regret_event_id,event_id,envelope_json,alpha_feedback_json,"
                "condition_id,token_id,direction,city,target_date,metric "
                "FROM no_trade_regret_events WHERE rejection_stage='RISK_GUARD' "
                "AND rejection_reason=? AND event_id LIKE ?",
                (event.rejection_reason, f"{cohort_prefix}%"),
            ).fetchall()
            ordered: dict[int, tuple[str, Mapping[str, object], str, str | None]] = {}
            for (
                prior_id, prior_event, prior_json, feedback_json,
                prior_condition, prior_token, prior_direction,
                prior_city, prior_date, prior_metric,
            ) in prior_rows:
                if not str(prior_event).startswith(cohort_prefix):
                    continue
                try:
                    prior_envelope = validated_alpha_protocol(
                        str(prior_json), event_id=str(prior_event),
                        regret_event_id=str(prior_id),
                        condition_id=str(prior_condition), token_id=str(prior_token),
                        direction=str(prior_direction), city=str(prior_city),
                        target_date=str(prior_date), metric=str(prior_metric),
                    )
                    protocol = prior_envelope["alpha_protocol"]
                    slot = protocol["slot"]
                    if (
                        not isinstance(protocol, Mapping)
                        or protocol.get("version") != ALPHA_PROTOCOL_VERSION
                        or any(protocol.get(key) != value for key, value in (
                            ("strategy_key", strategy),
                            ("global_selection_revision", selection),
                            ("probability_semantics_revision", revision),
                            ("metric", metric),
                        ))
                        or type(slot) is not int or slot <= 0 or slot in ordered
                        or prior_envelope.get("decision_at_utc") is None
                        or str(prior_event) != (
                            f"{cohort_prefix}{prior_envelope['city']}:"
                            f"{prior_envelope['target_date']}"
                        )
                    ):
                        raise ValueError("invalid alpha tail")
                    ordered[slot] = (
                        str(prior_id), prior_envelope, str(prior_json), feedback_json
                    )
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    raise ValueError("invalid alpha tail") from exc
            if sorted(ordered) != list(range(1, len(ordered) + 1)):
                raise ValueError("alpha cohort has a missing slot")
            previous_id: str | None = None
            previous_hash: str | None = None
            previous_observed_at: datetime | None = None
            for slot in sorted(ordered):
                prior_id, prior_envelope, prior_json, feedback_json = ordered[slot]
                protocol = prior_envelope["alpha_protocol"]
                if (
                    protocol.get("previous_event_id") != previous_id
                    or protocol.get("previous_feedback_hash") != previous_hash
                ):
                    raise ValueError("alpha cohort feedback chain mismatch")
                if previous_observed_at is not None and _utc(
                    protocol["previous_feedback_seen_at"]
                ) < previous_observed_at:
                    raise ValueError("alpha cut predates committed feedback visibility")
                if feedback_json is None:
                    if slot != len(ordered):
                        raise ValueError("alpha cohort has an unacknowledged gap")
                    return None
                prior_event = (
                    f"{cohort_prefix}{prior_envelope['city']}:"
                    f"{prior_envelope['target_date']}"
                )
                feedback = validated_alpha_feedback(
                    str(feedback_json), regret_event_id=prior_id,
                    envelope_json=prior_json,
                    event_id=prior_event,
                    condition_id=str(prior_envelope["condition_id"]),
                    token_id=str(prior_envelope["token_id"]),
                    direction=f"buy_{str(prior_envelope['side']).lower()}",
                    city=str(prior_envelope["city"]),
                    target_date=str(prior_envelope["target_date"]),
                    metric=str(prior_envelope["metric"]),
                )
                previous_id = prior_id
                previous_hash = str(feedback["feedback_hash"])
                previous_observed_at = _utc(feedback["observed_at"])
            previous_seen_at: datetime | None = None
            if previous_observed_at is not None:
                if decision_at <= previous_observed_at or cut_at <= previous_observed_at:
                    return None
                previous_seen_at = _first_committed_feedback_seen_at(
                    self.conn,
                    cohort=(strategy, selection, revision, metric),
                    feedback_hash=str(previous_hash),
                    outer_was_active=outer_was_active,
                )
                if previous_seen_at is None:
                    return None
                if previous_seen_at < previous_observed_at:
                    raise ValueError("feedback visibility predates its ACK")
                if decision_at <= previous_seen_at or cut_at <= previous_seen_at:
                    return None
            envelope["alpha_protocol"] = {
                "version": ALPHA_PROTOCOL_VERSION,
                "strategy_key": strategy,
                "global_selection_revision": selection,
                "probability_semantics_revision": revision,
                "metric": metric,
                "slot": len(ordered) + 1,
                "previous_event_id": previous_id,
                "previous_feedback_hash": previous_hash,
                "previous_feedback_seen_at": (
                    None if previous_seen_at is None else previous_seen_at.isoformat()
                ),
            }
            self.conn.execute(
                "INSERT OR IGNORE INTO no_trade_regret_events "
                "(regret_event_id,event_id,rejection_stage,rejection_reason,regret_bucket,"
                "condition_id,token_id,decision_time,city,target_date,metric,family_id,"
                "bin_label,direction,q_live,c_fee_adjusted,native_quote_available,"
                "source_status,family_complete,hypothetical_order_type,"
                "hypothetical_fill_status,hypothetical_fill_price,causal_snapshot_id,"
                "executable_snapshot_id,envelope_json,created_at,schema_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (event_id, event.event_id, event.rejection_stage, event.rejection_reason,
                 event.regret_bucket, event.condition_id, event.token_id,
                 event.decision_time, event.city, event.target_date, event.metric,
                 event.family_id, event.bin_label, event.direction, event.q_live,
                 event.c_fee_adjusted, None if event.native_quote_available is None
                 else int(event.native_quote_available), event.source_status,
                 None if event.family_complete is None else int(event.family_complete),
                 event.hypothetical_order_type, event.hypothetical_fill_status,
                 event.hypothetical_fill_price, event.causal_snapshot_id,
                 event.executable_snapshot_id, _canonical_json(envelope),
                 datetime.now(UTC).isoformat()),
            )
        return event_id

    def acknowledge_alpha_settlement(
        self, regret_event_id: str, *, settlement_proof: Mapping[str, object]
    ) -> bool:
        """Append verified chain feedback once; the caller owns the transaction.

        SCOPE: this exact v8 cohort tail only. DRAIN: finalized payout pair
        arrives and the caller ACKs under its WORLD writer lease. RESET: a
        later decision/cut after the durable ACK may claim the next slot.
        """

        with self._alpha_write():
            row = self.conn.execute(
                "SELECT event_id,rejection_stage,rejection_reason,condition_id,"
                "token_id,direction,city,target_date,metric,envelope_json,"
                "alpha_feedback_json "
                "FROM no_trade_regret_events WHERE regret_event_id=?",
                (regret_event_id,),
            ).fetchone()
            if row is None or not str(row[0]).startswith(_ALPHA_V8_PREFIX):
                raise ValueError("alpha ACK requires a v8 protocol row")
            (event_id, stage, reason, condition, token, direction,
             city, target_date, metric, envelope_json, old) = row
            envelope = validated_alpha_protocol(
                str(envelope_json), event_id=str(event_id),
                regret_event_id=regret_event_id, condition_id=str(condition),
                token_id=str(token), direction=str(direction), city=str(city),
                target_date=str(target_date), metric=str(metric),
            )
            if (
                stage != "RISK_GUARD"
                or reason != f"MARKET_RELATIVE_ALPHA_SHADOW:{envelope['strategy_key']}"
            ):
                raise ValueError("alpha ACK row identity mismatch")
            side = str(envelope["side"])
            decision_at = _utc(envelope["decision_at_utc"])
            proof = _normalized_alpha_proof(settlement_proof)
            envelope_hash = _hash(str(envelope_json))
            if (
                proof["condition_id"] != condition
                or proof["token_id"] != token
                or proof["side"] != side
                or proof["envelope_sha256"] != envelope_hash
            ):
                raise ValueError("alpha ACK proof does not match frozen decision")
            if old is not None:
                feedback = validated_alpha_feedback(
                    str(old), regret_event_id=regret_event_id,
                    envelope_json=str(envelope_json), event_id=str(event_id),
                    condition_id=str(condition), token_id=str(token),
                    direction=str(direction), city=str(city),
                    target_date=str(target_date), metric=str(metric),
                )
                if feedback["settlement_proof"] == proof:
                    return False
                raise ValueError("conflicting alpha settlement feedback")
            observed_at = datetime.now(UTC)
            if observed_at <= decision_at:
                raise ValueError("alpha ACK precedes decision")
            body: dict[str, object] = {
                "protocol_version": ALPHA_PROTOCOL_VERSION,
                "regret_event_id": regret_event_id,
                "observed_at": observed_at.isoformat(),
                "envelope_sha256": envelope_hash,
                "settlement_proof": proof,
            }
            feedback_json = _canonical_json({
                **body, "feedback_hash": _hash(_canonical_json(body))
            })
            result = self.conn.execute(
                "UPDATE no_trade_regret_events SET alpha_feedback_json=? "
                "WHERE regret_event_id=? AND alpha_feedback_json IS NULL",
                (feedback_json, regret_event_id),
            )
            if result.rowcount != 1:
                raise ValueError("concurrent alpha ACK conflict")
            with _FEEDBACK_SEEN_LOCK:
                _PENDING_ACK_BY_CONNECTION[id(self.conn)] = _hash(_canonical_json(body))
                _PENDING_ACK_BY_CONNECTION.move_to_end(id(self.conn))
                if len(_PENDING_ACK_BY_CONNECTION) > _FEEDBACK_SEEN_LIMIT:
                    _PENDING_ACK_BY_CONNECTION.popitem(last=False)
            return True

    def enrich_after_settlement(
        self,
        *,
        event_id: str,
        rejection_stage: str,
        rejection_reason: str,
        later_outcome: str,
        would_have_won: bool,
        would_have_filled: bool,
        settlement_proof: str,
    ) -> str:
        if not settlement_proof:
            raise NoTradeRegretHindsightError("settlement_proof is required for hindsight enrichment")
        regret_event_id = stable_event_id(event_id, rejection_stage, rejection_reason)
        cur = self.conn.execute(
            """
            UPDATE no_trade_regret_events
               SET later_outcome = ?,
                   would_have_won = ?,
                   would_have_filled = ?
             WHERE regret_event_id = ?
            """,
            (
                later_outcome,
                int(would_have_won),
                int(would_have_filled),
                regret_event_id,
            ),
        )
        if cur.rowcount != 1:
            raise NoTradeRegretHindsightError("cannot enrich missing no_trade_regret_event")
        return regret_event_id

    def live_reader_rows(self) -> list[dict[str, object]]:
        rows = self.conn.execute(
            """
            SELECT regret_event_id, event_id, rejection_stage, rejection_reason,
                   market_slug, condition_id, token_id, outcome_label, created_at
            FROM no_trade_regret_events
            ORDER BY created_at, regret_event_id
            """
        ).fetchall()
        keys = [
            "regret_event_id",
            "event_id",
            "rejection_stage",
            "rejection_reason",
            "market_slug",
            "condition_id",
            "token_id",
            "outcome_label",
            "created_at",
        ]
        return [dict(zip(keys, row)) for row in rows]

    def _write_no_trade_events_compatibility(self, event: NoTradeRegretEvent) -> None:
        try:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO no_trade_events (
                    market_slug, temperature_metric, target_date, observation_time,
                    decision_seq, reason, reason_detail, strategy_key, event_source,
                    observed_at, schema_version, schema_compatibility
                ) VALUES (?, ?, ?, ?, ?, 'uncategorized', ?, 'edli_v1',
                          'edli_event', ?, 38, 'degraded')
                """,
                (
                    event.market_slug,
                    event.metric,
                    event.target_date,
                    event.observation_time,
                    event.decision_seq,
                    event.rejection_reason,
                    event.decision_time or datetime.now(UTC).isoformat(),
                ),
            )
        except sqlite3.OperationalError:
            return


def classify_fillable_bucket(*, would_have_won: bool, would_have_filled: bool) -> RegretBucket:
    if would_have_won and would_have_filled:
        return "WOULD_HAVE_WON_AND_FILLABLE"
    if would_have_won and not would_have_filled:
        return "WOULD_HAVE_WON_BUT_UNFILLABLE"
    return "WOULD_HAVE_LOST"


def _has_compatibility_natural_key(event: NoTradeRegretEvent) -> bool:
    return (
        bool(event.market_slug)
        and bool(event.metric)
        and bool(event.target_date)
        and bool(event.observation_time)
        and event.decision_seq is not None
    )


def _has_hindsight_fields(event: NoTradeRegretEvent) -> bool:
    return (
        event.later_outcome is not None
        or event.would_have_won is not None
        or event.would_have_filled is not None
    )
