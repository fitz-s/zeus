# Created: 2026-04-27
# Last reused/audited: 2026-05-07
# Authority basis: docs/operations/task_2026-05-07_object_invariance_wave25/PLAN.md
# Lifecycle: created=2026-04-27; last_reviewed=2026-05-07; last_reused=2026-05-07
# Purpose: Lock R3 F2 operator-gated calibration retrain/promotion wiring.
# Reuse: Run when changing calibration retrain gates, corpus filters, frozen-replay promotion, or CONFIRMED trade-fact training seams.
"""R3 F2 tests for operator-gated calibration retrain wiring."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from pathlib import Path

import pytest

from src.calibration.retrain_trigger import (
    ARTIFACT_PATTERN,
    ENV_FLAG_NAME,
    OPERATOR_TOKEN_SECRET_ENV,
    CalibrationParams,
    CalibrationRetrainGateError,
    CorpusFilter,
    FrozenReplayResult,
    RetrainStatus,
    UnsafeCorpusFilter,
    arm,
    load_confirmed_corpus,
    status,
    trigger_retrain,
)

TEST_MODEL_KEY = (
    "high:US-Northeast:DJF:test_retrain_v1:00:tigge_mars:"
    "full:width_normalized_density"
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE venue_trade_facts (
          trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
          trade_id TEXT NOT NULL,
          venue_order_id TEXT NOT NULL,
          command_id TEXT NOT NULL,
          state TEXT NOT NULL,
          filled_size TEXT NOT NULL,
          fill_price TEXT NOT NULL,
          source TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          local_sequence INTEGER NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_json TEXT
        )
        """
    )
    return conn


def _create_platt_models_v2(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE platt_models_v2 (
          model_key TEXT PRIMARY KEY,
          temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
          cluster TEXT NOT NULL,
          season TEXT NOT NULL,
          data_version TEXT NOT NULL,
          input_space TEXT NOT NULL DEFAULT 'raw_probability',
          param_A REAL NOT NULL,
          param_B REAL NOT NULL,
          param_C REAL NOT NULL DEFAULT 0.0,
          bootstrap_params_json TEXT NOT NULL,
          n_samples INTEGER NOT NULL,
          brier_insample REAL,
          fitted_at TEXT NOT NULL,
          is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
          authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
              CHECK (authority IN ('VERIFIED','UNVERIFIED','QUARANTINED')),
          bucket_key TEXT,
          cycle TEXT NOT NULL DEFAULT '00',
          source_id TEXT NOT NULL DEFAULT 'tigge_mars',
          horizon_profile TEXT NOT NULL DEFAULT 'full',
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(temperature_metric, cluster, season, data_version, input_space, is_active, cycle, source_id, horizon_profile)
        )
        """
    )


def _seed_active_platt_model(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO platt_models_v2 (
          model_key, temperature_metric, cluster, season, data_version,
          input_space, param_A, param_B, param_C, bootstrap_params_json,
          n_samples, brier_insample, fitted_at, is_active, authority,
          cycle, source_id, horizon_profile
        ) VALUES (
          ?,
          'high', 'US-Northeast', 'DJF', 'test_retrain_v1',
          'width_normalized_density', 0.9, 0.0, 0.1, '[]',
          10, 0.20, '2026-04-26T00:00:00+00:00', 1, 'VERIFIED',
          '00', 'tigge_mars', 'full'
        )
        """,
        (TEST_MODEL_KEY,),
    )


def _identity_payload(
    *,
    cluster: str = "US-Northeast",
    season: str = "DJF",
    temperature_metric: str = "high",
    data_version: str = "test_retrain_v1",
    input_space: str = "width_normalized_density",
) -> str:
    return (
        '{"calibration_identity":'
        f'{{"cluster":"{cluster}","season":"{season}",'
        f'"temperature_metric":"{temperature_metric}",'
        f'"data_version":"{data_version}",'
        f'"input_space":"{input_space}"}}'
        "}"
    )


def _insert_trade(
    conn: sqlite3.Connection,
    state: str,
    seq: int,
    *,
    raw_payload_json: str | None = None,
    filled_size: str = "1",
    fill_price: str = "0.42",
) -> None:
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
          trade_id, venue_order_id, command_id, state, filled_size, fill_price,
          source, observed_at, local_sequence, raw_payload_hash, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, 'CHAIN', ?, ?, ?, ?)
        """,
        (
            f"trade-{seq}",
            f"order-{seq}",
            f"cmd-{seq}",
            state,
            filled_size,
            fill_price,
            f"2026-04-27T00:0{seq}:00+00:00",
            seq,
            f"hash-{seq}",
            raw_payload_json if raw_payload_json is not None else _identity_payload(),
        ),
    )


def _decision_artifact(root: Path) -> Path:
    path = root / "docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/calibration_retrain_decision_2026-04-27.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("operator approved calibration retrain test\n")
    return path


def _params() -> CalibrationParams:
    return CalibrationParams(A=1.1, B=-0.02, C=0.3, bootstrap_params=((1.1, -0.02, 0.3),), n_samples=15, fit_loss_metric=0.12)


def _filter() -> CorpusFilter:
    return CorpusFilter(cluster="US-Northeast", season="DJF", data_version="test_retrain_v1")


def _token(secret: str = "unit-calibration-secret", operator_id: str = "operator", nonce: str = "nonce-123456") -> str:
    message = f"v1.{operator_id}.{nonce}"
    signature = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{message}.{signature}"


def _armed_env(secret: str = "unit-calibration-secret") -> dict[str, str]:
    return {ENV_FLAG_NAME: "1", OPERATOR_TOKEN_SECRET_ENV: secret}


def test_retrain_disabled_by_default(tmp_path):
    assert status(root=tmp_path, environ={}) == RetrainStatus.DISABLED
    _decision_artifact(tmp_path)
    assert status(root=tmp_path, environ={}) == RetrainStatus.DISABLED
    assert status(root=tmp_path, environ={ENV_FLAG_NAME: "1"}) == RetrainStatus.ARMED


def test_arm_requires_operator_token_AND_evidence_path(tmp_path):
    artifact = _decision_artifact(tmp_path)
    with pytest.raises(CalibrationRetrainGateError, match="operator_token"):
        arm("", artifact, root=tmp_path, environ=_armed_env())
    with pytest.raises(CalibrationRetrainGateError, match=ENV_FLAG_NAME):
        arm(_token(), artifact, root=tmp_path, environ={})
    with pytest.raises(CalibrationRetrainGateError, match=OPERATOR_TOKEN_SECRET_ENV):
        arm(_token(), artifact, root=tmp_path, environ={ENV_FLAG_NAME: "1"})
    with pytest.raises(CalibrationRetrainGateError, match="signed v1"):
        arm("token", artifact, root=tmp_path, environ=_armed_env())
    with pytest.raises(CalibrationRetrainGateError, match="signature mismatch"):
        arm(_token(secret="wrong-secret"), artifact, root=tmp_path, environ=_armed_env())
    with pytest.raises(CalibrationRetrainGateError, match="does not exist"):
        arm(_token(), "missing.md", root=tmp_path, environ=_armed_env())
    wrong_path = tmp_path / "operator_decisions" / "calibration_retrain_decision_2026-04-27.md"
    wrong_path.parent.mkdir()
    wrong_path.write_text("wrong route\n")
    with pytest.raises(CalibrationRetrainGateError, match="must match"):
        arm(_token(), wrong_path, root=tmp_path, environ=_armed_env())

    armed = arm(_token(), artifact, root=tmp_path, environ=_armed_env())

    assert len(armed.operator_token_hash) == 64
    assert armed.evidence_path.endswith("calibration_retrain_decision_2026-04-27.md")


def test_arm_then_trigger_consumes_confirmed_trades_only(tmp_path):
    artifact = _decision_artifact(tmp_path)
    conn = _conn()
    for seq, state in enumerate(["MATCHED", "MINED", "CONFIRMED"], start=1):
        _insert_trade(conn, state, seq)
    seen_counts: list[int] = []

    def replay(corpus_filter, params, rows):
        seen_counts.append(len(rows))
        assert {row["state"] for row in rows} == {"CONFIRMED"}
        return FrozenReplayResult(True, "replay-hash")

    result = trigger_retrain(
        conn,
        _filter(),
        params=_params(),
        operator_token=_token(),
        evidence_path=artifact,
        frozen_replay_runner=replay,
        root=tmp_path,
        environ=_armed_env(),
        promote_writer=lambda **kwargs: None,
    )

    assert result.confirmed_trade_count == 1
    assert seen_counts == [1]

    with pytest.raises(UnsafeCorpusFilter, match="CONFIRMED"):
        CorpusFilter(cluster="US-Northeast", season="DJF", states=("MATCHED",))


def test_confirmed_corpus_is_filtered_by_calibration_identity(tmp_path):
    conn = _conn()
    _insert_trade(conn, "CONFIRMED", 1)
    _insert_trade(
        conn,
        "CONFIRMED",
        2,
        raw_payload_json=_identity_payload(cluster="US-West"),
    )
    _insert_trade(
        conn,
        "CONFIRMED",
        3,
        raw_payload_json=_identity_payload(temperature_metric="low"),
    )

    rows = load_confirmed_corpus(conn, _filter())

    assert [row["trade_id"] for row in rows] == ["trade-1"]


def test_confirmed_corpus_missing_identity_fails_closed():
    conn = _conn()
    _insert_trade(conn, "CONFIRMED", 1, raw_payload_json="{}")

    with pytest.raises(UnsafeCorpusFilter, match="missing calibration identity"):
        load_confirmed_corpus(conn, _filter())


def test_confirmed_corpus_missing_fill_economics_fails_closed():
    conn = _conn()
    _insert_trade(conn, "CONFIRMED", 1, filled_size="0")

    with pytest.raises(UnsafeCorpusFilter, match="positive finite fill economics"):
        load_confirmed_corpus(conn, _filter())


def test_frozen_replay_failure_blocks_promotion(tmp_path):
    artifact = _decision_artifact(tmp_path)
    conn = _conn()
    _insert_trade(conn, "CONFIRMED", 1)
    promoted: list[object] = []

    result = trigger_retrain(
        conn,
        _filter(),
        params=_params(),
        operator_token=_token(),
        evidence_path=artifact,
        frozen_replay_runner=lambda corpus_filter, params, rows: FrozenReplayResult(False, "drift-hash", "drift"),
        root=tmp_path,
        environ=_armed_env(),
        promote_writer=lambda **kwargs: promoted.append(kwargs),
    )

    assert result.status == RetrainStatus.COMPLETE_DRIFT_DETECTED
    assert result.promoted is False
    assert promoted == []
    row = conn.execute("SELECT frozen_replay_status, promoted_at FROM calibration_params_versions").fetchone()
    assert dict(row) == {"frozen_replay_status": "FAIL", "promoted_at": None}


def test_frozen_replay_pass_promotes_new_version(tmp_path):
    artifact = _decision_artifact(tmp_path)
    conn = _conn()
    _insert_trade(conn, "CONFIRMED", 1)
    calls: list[dict] = []

    result = trigger_retrain(
        conn,
        _filter(),
        params=_params(),
        operator_token=_token(),
        evidence_path=artifact,
        frozen_replay_runner=lambda corpus_filter, params, rows: FrozenReplayResult(True, "pass-hash"),
        root=tmp_path,
        environ=_armed_env(),
        promote_writer=lambda **kwargs: calls.append(kwargs),
    )

    assert result.status == RetrainStatus.COMPLETE_REPLAYED
    assert result.promoted is True
    assert calls[0]["metric_identity"].temperature_metric == "high"
    assert calls[0]["authority"] == "VERIFIED"
    row = conn.execute("SELECT frozen_replay_status, promoted_at, confirmed_trade_count FROM calibration_params_versions").fetchone()
    assert row["frozen_replay_status"] == "PASS"
    assert row["promoted_at"] is not None
    assert row["confirmed_trade_count"] == 1


def test_frozen_replay_pass_replaces_existing_live_platt_row(tmp_path):
    artifact = _decision_artifact(tmp_path)
    conn = _conn()
    _create_platt_models_v2(conn)
    _seed_active_platt_model(conn)
    _insert_trade(conn, "CONFIRMED", 1)

    result = trigger_retrain(
        conn,
        _filter(),
        params=_params(),
        operator_token=_token(),
        evidence_path=artifact,
        frozen_replay_runner=lambda corpus_filter, params, rows: FrozenReplayResult(True, "pass-hash"),
        root=tmp_path,
        environ=_armed_env(),
    )

    assert result.status == RetrainStatus.COMPLETE_REPLAYED
    live_rows = conn.execute(
        """
        SELECT param_A, param_B, param_C, n_samples, authority
          FROM platt_models_v2
         WHERE model_key = ?
        """,
        (TEST_MODEL_KEY,),
    ).fetchall()
    assert len(live_rows) == 1
    assert live_rows[0]["param_A"] == pytest.approx(1.1)
    assert live_rows[0]["param_B"] == pytest.approx(-0.02)
    assert live_rows[0]["param_C"] == pytest.approx(0.3)
    assert live_rows[0]["n_samples"] == 15
    assert live_rows[0]["authority"] == "VERIFIED"
    audit = conn.execute(
        "SELECT frozen_replay_status, promoted_at FROM calibration_params_versions"
    ).fetchone()
    assert audit["frozen_replay_status"] == "PASS"
    assert audit["promoted_at"] is not None


def test_promotion_atomic_no_mid_swap_visible(tmp_path):
    artifact = _decision_artifact(tmp_path)
    conn = _conn()
    _insert_trade(conn, "CONFIRMED", 1)

    def broken_writer(**kwargs):
        raise RuntimeError("writer failed")

    with pytest.raises(RuntimeError, match="writer failed"):
        trigger_retrain(
            conn,
            _filter(),
            params=_params(),
            operator_token=_token(),
            evidence_path=artifact,
            frozen_replay_runner=lambda corpus_filter, params, rows: FrozenReplayResult(True, "pass-hash"),
            root=tmp_path,
            environ=_armed_env(),
            promote_writer=broken_writer,
        )

    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'calibration_params_versions'"
    ).fetchone()
    count = 0
    if table_exists:
        count = conn.execute("SELECT COUNT(*) FROM calibration_params_versions").fetchone()[0]
    assert count == 0


def test_retired_versions_retained_for_audit(tmp_path):
    artifact = _decision_artifact(tmp_path)
    conn = _conn()
    _insert_trade(conn, "CONFIRMED", 1)

    for idx in range(2):
        trigger_retrain(
            conn,
            _filter(),
            params=CalibrationParams(A=1.0 + idx, B=0.0, C=0.1, bootstrap_params=((1.0 + idx, 0.0, 0.1),), n_samples=15),
            operator_token=_token(),
            evidence_path=artifact,
            frozen_replay_runner=lambda corpus_filter, params, rows: FrozenReplayResult(True, f"pass-hash-{idx}"),
            root=tmp_path,
            environ=_armed_env(),
            promote_writer=lambda **kwargs: None,
        )

    rows = conn.execute(
        "SELECT retired_at, promoted_at FROM calibration_params_versions ORDER BY version_id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["retired_at"] is not None
    assert rows[1]["retired_at"] is None
    assert rows[1]["promoted_at"] is not None
