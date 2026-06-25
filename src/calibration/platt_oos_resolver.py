# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: STAT_WAVE_REPORT_AND_PLATT_TASK_SPEC_2026-05-29.md Part 2 §2.1-§2.6 (P0/P4/P6).
#   identity-Platt is the live fail-closed DEFAULT; current/refit/clamped/shrinkage Platt are
#   CANDIDATES; a non-identity Platt reaches selection ONLY after a full-chain date-blocked OOS
#   win recorded as a PROMOTE row in platt_oos_decisions (matched by p_raw_domain_hash).
"""Runtime p_cal resolver — identity-default Platt authority + slope fuse.

This module is the live read seam that makes "an un-gated Platt affects selection"
structurally unwritable. The default mode is ``identity`` (``p_cal = p_raw``); only a
PROMOTE row in the OOS decision table, matched by ``p_raw_domain_hash``, lets a
candidate's ``(A, B, C)`` reach the selection vector. This is the same principle as
the bias_c gate one layer down: correction is a candidate, raw/identity is the
default, promotion requires OOS proof.

It reuses the existing Platt math (``logit_safe`` + ``calibrate_and_normalize``) so the
applied transform is bit-identical to what the calibration layer would compute; nothing
here re-derives the sigmoid.
"""
from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

import numpy as np

from src.calibration.platt import ExtendedPlattCalibrator, calibrate_and_normalize

# ---------------------------------------------------------------------------
# Slope fuse constants (§2.4 item 4) — capital-preservation caps, NOT stat optima.
# ---------------------------------------------------------------------------
A_MAX_FUSE: float = 2.0   # clamp cap for the A_clamped_2p0 candidate
A_REJECT_HARD: float = 2.5  # hard reject: A>2.5 needs explicit override + manual signoff


class PlattMode(enum.Enum):
    """Live Platt authority mode (§2.5 P0).

    identity : p_cal = p_raw (live fail-closed default)
    gated    : use the promoted candidate from the OOS decision table (if one matches)
    """

    IDENTITY = "identity"
    GATED = "gated"

    @classmethod
    def default(cls) -> "PlattMode":
        return cls.IDENTITY


@dataclass(frozen=True)
class PlattCandidate:
    """A Platt calibration candidate: p_cal = sigmoid(A*logit(p_raw) + B*lead_days + C)."""

    name: str
    A: float
    B: float = 0.0
    C: float = 0.0


@dataclass
class PlattDecision:
    """The resolver's verdict for one p_cal resolution.

    platt_decision  : 'identity_fallback' | 'promoted_candidate'
    platt_reason    : human/audit string (e.g. 'no_oos_full_chain_win')
    applied_candidate : the candidate name actually applied to SELECTION, or None for identity
    """

    platt_decision: str
    platt_reason: str
    applied_candidate: Optional[str] = None


class PlattDecisionLookup(Protocol):
    """Read side of the OOS decision table (P6).

    ``promoted_for`` returns the PROMOTE candidate for a p_raw_domain_hash, or None.
    """

    def promoted_for(self, p_raw_domain_hash: str) -> Optional[PlattCandidate]:
        ...


# ---------------------------------------------------------------------------
# Slope fuse helpers (§2.4 item 4)
# ---------------------------------------------------------------------------

def clamp_slope(a: float, *, cap: float = A_MAX_FUSE) -> float:
    """Clamp slope magnitude to ``cap``, preserving sign. No-op when |a| <= cap.

    A healthy A≈1.5 passes unchanged (clamp-no-op-when-sane); an over-steep slope is
    bounded to ±cap. The fuse bounds MAGNITUDE so a degenerate negative fit is also
    bounded rather than let through.
    """
    if cap <= 0:
        raise ValueError(f"cap must be > 0, got {cap!r}")
    if a > cap:
        return cap
    if a < -cap:
        return -cap
    return a


def _cap_tag(cap: float) -> str:
    """Render a cap value as a model-key-safe tag: 2.0 -> '2p0', 1.7 -> '1p7'."""
    return f"{cap:.1f}".replace(".", "p")


def make_clamped_candidate(base: PlattCandidate, *, cap: float = A_MAX_FUSE) -> PlattCandidate:
    """Return a NEW candidate with A clamped to ``cap``, B/C preserved, cap-tagged name.

    Candidate construction, not mutation: ``base`` is unchanged.
    """
    return PlattCandidate(
        name=f"{base.name}_A_clamped_{_cap_tag(cap)}",
        A=clamp_slope(base.A, cap=cap),
        B=base.B,
        C=base.C,
    )


def slope_fuse_ok(a: float, *, override: bool = False) -> bool:
    """Hard capital-preservation fuse: |A| <= A_REJECT_HARD, unless explicit override.

    §2.4 item 4: reject any A>2.5 unless explicit override + OOS proof + manual signoff.
    The override flag is the seam where that manual decision is recorded; default
    (no override) is fail-closed.
    """
    if override:
        return True
    return abs(a) <= A_REJECT_HARD


# ---------------------------------------------------------------------------
# Candidate application — reuse the production Platt math.
# ---------------------------------------------------------------------------

def _calibrator_for(candidate: PlattCandidate) -> ExtendedPlattCalibrator:
    """Build a fitted ExtendedPlattCalibrator carrying the candidate's (A, B, C).

    We construct the calibrator directly rather than via ``fit`` so an arbitrary
    candidate (A, B, C) can be applied through the
    SAME ``predict`` / ``calibrate_and_normalize`` code path the live layer uses.
    Input space is raw_probability (predict_for_bin returns predict() in that space).
    """
    cal = ExtendedPlattCalibrator()
    cal.A = float(candidate.A)
    cal.B = float(candidate.B)
    cal.C = float(candidate.C)
    cal.fitted = True
    cal.n_samples = 0
    cal.input_space = "raw_probability"
    return cal


def apply_candidate(
    p_raw_vector: np.ndarray,
    lead_days: float,
    candidate: PlattCandidate,
    *,
    bin_widths: list[float | None] | np.ndarray | None = None,
) -> np.ndarray:
    """Apply a Platt candidate to a p_raw vector → normalized p_cal vector.

    Reuses ``calibrate_and_normalize`` so the transform + renormalization match the
    live calibration layer exactly.
    """
    cal = _calibrator_for(candidate)
    return calibrate_and_normalize(np.asarray(p_raw_vector, dtype=float), cal, lead_days, bin_widths)


def resolve_p_cal(
    p_raw_vector: np.ndarray,
    lead_days: float,
    *,
    p_raw_domain_hash: str,
    mode: PlattMode = PlattMode.IDENTITY,
    decision_lookup: Optional[PlattDecisionLookup] = None,
    bin_widths: list[float | None] | np.ndarray | None = None,
) -> tuple[np.ndarray, PlattDecision]:
    """Resolve the SELECTION p_cal vector under the identity-default OOS gate (P0).

    Returns ``(p_cal_for_selection, PlattDecision)``.

    - ``identity``: returns p_raw verbatim; never consults the decision table.
    - ``gated``   : applies the PROMOTE candidate matching ``p_raw_domain_hash`` if
                    one exists; otherwise falls back to identity (fail-closed).

    The default (no promoted decision) is ALWAYS identity: ``p_cal == p_raw``,
    ``platt_decision='identity_fallback'``, ``platt_reason='no_oos_full_chain_win'``.
    """
    p_raw = np.asarray(p_raw_vector, dtype=float)

    if mode == PlattMode.IDENTITY:
        return p_raw.copy(), PlattDecision(
            platt_decision="identity_fallback",
            platt_reason="mode_identity_live_default",
            applied_candidate=None,
        )

    # GATED
    promoted = None
    if decision_lookup is not None:
        promoted = decision_lookup.promoted_for(p_raw_domain_hash)
    if promoted is None:
        return p_raw.copy(), PlattDecision(
            platt_decision="identity_fallback",
            platt_reason="no_oos_full_chain_win",
            applied_candidate=None,
        )

    p_cal = apply_candidate(p_raw, lead_days, promoted, bin_widths=bin_widths)
    return p_cal, PlattDecision(
        platt_decision="promoted_candidate",
        platt_reason=f"oos_promoted:{promoted.name}",
        applied_candidate=promoted.name,
    )


# ---------------------------------------------------------------------------
# Promotion decision table (P6) — platt_oos_decisions
# ---------------------------------------------------------------------------
# §2.5 P6 / §2.6: a separate decision table, NOT a direct platt_models overwrite.
# A PROMOTE row carries the p_raw_domain_hash it was fit on; the live reader applies a
# candidate ONLY when a PROMOTE row matches the CURRENT p_raw domain. This prevents
# stale-Platt misuse (a candidate fit on the dirty/MC domain serving the clean/analytic
# domain). decision ∈ {PROMOTE, IDENTITY, INSUFFICIENT_N, REJECT}.

VALID_PLATT_OOS_DECISIONS: frozenset[str] = frozenset(
    {"PROMOTE", "IDENTITY", "INSUFFICIENT_N", "REJECT"}
)

CREATE_PLATT_OOS_DECISIONS_DDL = """
    CREATE TABLE IF NOT EXISTS platt_oos_decisions (
        decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
        bucket_key TEXT NOT NULL,
        p_raw_domain_hash TEXT NOT NULL,
        candidate_name TEXT NOT NULL,
        param_A REAL NOT NULL,
        param_B REAL NOT NULL DEFAULT 0.0,
        param_C REAL NOT NULL DEFAULT 0.0,
        score_identity REAL,
        score_candidate REAL,
        improvement_lcb REAL,
        fdr_q REAL,
        catastrophe_flags TEXT,
        decision TEXT NOT NULL
            CHECK (decision IN ('PROMOTE', 'IDENTITY', 'INSUFFICIENT_N', 'REJECT')),
        recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bucket_key, p_raw_domain_hash, candidate_name, recorded_at)
    )
"""

# Live read index: the resolver looks up PROMOTE rows by (p_raw_domain_hash, bucket_key).
CREATE_PLATT_OOS_DECISIONS_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_platt_oos_decisions_lookup
        ON platt_oos_decisions(p_raw_domain_hash, bucket_key, decision, recorded_at)
"""


def init_platt_oos_decisions_schema(conn: sqlite3.Connection) -> None:
    """Create the platt_oos_decisions table + lookup index (idempotent).

    Standalone DDL — does NOT touch platt_models. Safe to call on any connection;
    a real domain freeze + population (P2/P3/P4) is DEFERRED per the task scope.
    """
    conn.execute(CREATE_PLATT_OOS_DECISIONS_DDL)
    conn.execute(CREATE_PLATT_OOS_DECISIONS_INDEX)


class PlattOosDecisionStore:
    """SQLite-backed implementation of the PlattDecisionLookup read seam (P6).

    Reads the most-recent PROMOTE row for a (p_raw_domain_hash[, bucket_key]). A
    candidate is returned ONLY when its row's decision is PROMOTE; IDENTITY /
    INSUFFICIENT_N / REJECT rows return None so the resolver falls back to identity.

    Single connection (INV-37-safe: no independent cross-DB handles). The caller owns
    the connection lifecycle and the bucket_key it scopes to.
    """

    def __init__(self, conn: sqlite3.Connection, *, bucket_key: Optional[str] = None) -> None:
        self._conn = conn
        self._bucket_key = bucket_key
        init_platt_oos_decisions_schema(conn)

    def record_decision(
        self,
        *,
        bucket_key: str,
        p_raw_domain_hash: str,
        candidate_name: str,
        param_A: float,
        param_B: float = 0.0,
        param_C: float = 0.0,
        decision: str,
        score_identity: Optional[float] = None,
        score_candidate: Optional[float] = None,
        improvement_lcb: Optional[float] = None,
        fdr_q: Optional[float] = None,
        catastrophe_flags: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> None:
        """Insert one decision row. decision must be a VALID_PLATT_OOS_DECISIONS value."""
        if decision not in VALID_PLATT_OOS_DECISIONS:
            raise ValueError(
                f"decision={decision!r} not in {sorted(VALID_PLATT_OOS_DECISIONS)}"
            )
        ts = recorded_at or datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO platt_oos_decisions
              (bucket_key, p_raw_domain_hash, candidate_name, param_A, param_B, param_C,
               score_identity, score_candidate, improvement_lcb, fdr_q, catastrophe_flags,
               decision, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bucket_key, p_raw_domain_hash, candidate_name,
                float(param_A), float(param_B), float(param_C),
                score_identity, score_candidate, improvement_lcb, fdr_q,
                catastrophe_flags, decision, ts,
            ),
        )

    def promoted_for(self, p_raw_domain_hash: str) -> Optional[PlattCandidate]:
        """Return the most-recent PROMOTE candidate for the domain hash, else None.

        Fail-closed: a non-PROMOTE most-recent row (IDENTITY/REJECT/INSUFFICIENT_N) yields
        None → identity. Only a PROMOTE row reaches selection.
        """
        params: list = [p_raw_domain_hash]
        bucket_clause = ""
        if self._bucket_key is not None:
            bucket_clause = "AND bucket_key = ?"
            params.append(self._bucket_key)
        row = self._conn.execute(
            f"""
            SELECT candidate_name, param_A, param_B, param_C, decision
            FROM platt_oos_decisions
            WHERE p_raw_domain_hash = ?
              {bucket_clause}
            ORDER BY recorded_at DESC, decision_id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        if row is None:
            return None
        candidate_name, a, b, c, decision = (
            row["candidate_name"], row["param_A"], row["param_B"], row["param_C"], row["decision"]
        ) if isinstance(row, sqlite3.Row) else (row[0], row[1], row[2], row[3], row[4])
        if decision != "PROMOTE":
            return None
        return PlattCandidate(name=candidate_name, A=float(a), B=float(b), C=float(c))
