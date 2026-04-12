"""Day0 residual learning example for Zeus.

Goal:
    Learn the remaining upside after the current observed running max.

Definitions:
    residual_upside = max(0, settlement_value - running_max)
    has_upside = 1[settlement_value > running_max]

This intentionally preserves the critical physical contract:
    final_high >= running_max
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
except Exception:  # pragma: no cover
    HistGradientBoostingClassifier = None
    HistGradientBoostingRegressor = None


@dataclass
class Day0ResidualModels:
    classifier: object
    regressor: object
    feature_columns: list[str]

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        X = frame[self.feature_columns].copy()
        p_has_upside = self.classifier.predict_proba(X)[:, 1]
        positive_mean = np.expm1(self.regressor.predict(X)).clip(min=0.0)
        expected_residual = p_has_upside * positive_mean

        out = frame.copy()
        out["p_has_upside"] = p_has_upside
        out["positive_mean_residual"] = positive_mean
        out["expected_residual"] = expected_residual
        return out


def build_training_frame(conn: sqlite3.Connection) -> pd.DataFrame:
    """Materialize a feature frame from current Zeus tables."""
    df = pd.read_sql_query(
        """
        WITH base AS (
          SELECT
              oi.city,
              oi.target_date,
              oi.source,
              oi.local_timestamp,
              oi.local_hour,
              oi.temp_current,
              oi.running_max,
              oi.delta_rate_per_h,
              s.settlement_value,
              CAST(substr(oi.local_timestamp, 6, 2) AS INTEGER) AS month_num,
              dpp.p_high_set AS post_peak_confidence,
              CASE
                  WHEN sd.sunrise_local IS NOT NULL
                   AND sd.sunset_local IS NOT NULL
                   AND julianday(sd.sunset_local) > julianday(sd.sunrise_local)
                   AND julianday(oi.local_timestamp) BETWEEN julianday(sd.sunrise_local) AND julianday(sd.sunset_local)
                  THEN
                    (julianday(oi.local_timestamp) - julianday(sd.sunrise_local))
                    / NULLIF(julianday(sd.sunset_local) - julianday(sd.sunrise_local), 0.0)
                  ELSE NULL
              END AS daylight_progress
          FROM observation_instants oi
          JOIN settlements s
            ON s.city = oi.city
           AND s.target_date = oi.target_date
          LEFT JOIN solar_daily sd
            ON sd.city = oi.city
           AND sd.target_date = oi.target_date
          LEFT JOIN diurnal_peak_prob dpp
            ON dpp.city = oi.city
           AND dpp.month = CAST(substr(oi.local_timestamp, 6, 2) AS INTEGER)
           AND dpp.hour = CAST(oi.local_hour AS INTEGER)
          WHERE oi.running_max IS NOT NULL
            AND s.settlement_value IS NOT NULL
        )
        SELECT *,
               MAX(0.0, settlement_value - running_max) AS residual_upside,
               CASE WHEN settlement_value > running_max THEN 1 ELSE 0 END AS has_upside
        FROM base
        """,
        conn,
    )

    # Basic numeric cleaning.
    for col in ["local_hour", "temp_current", "running_max", "delta_rate_per_h", "post_peak_confidence", "daylight_progress"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Simple missing-value handling that is safe for tree models.
    df["post_peak_confidence"] = df["post_peak_confidence"].fillna(0.5)
    df["daylight_progress"] = df["daylight_progress"].fillna(-1.0)
    df["delta_rate_per_h"] = df["delta_rate_per_h"].fillna(0.0)
    df["temp_current"] = df["temp_current"].fillna(df["running_max"])

    return df


def temporal_split(df: pd.DataFrame, split_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simple forward split by target_date."""
    train = df[df["target_date"] < split_date].copy()
    test = df[df["target_date"] >= split_date].copy()
    return train, test


def fit_models(train: pd.DataFrame) -> Day0ResidualModels:
    if HistGradientBoostingClassifier is None or HistGradientBoostingRegressor is None:
        raise RuntimeError("scikit-learn is required for Day0 residual training.")

    feature_columns = [
        "local_hour",
        "temp_current",
        "running_max",
        "delta_rate_per_h",
        "daylight_progress",
        "post_peak_confidence",
    ]

    X = train[feature_columns]
    y_cls = train["has_upside"].astype(int)
    y_reg = np.log1p(train["residual_upside"])

    classifier = HistGradientBoostingClassifier(
        max_depth=6,
        learning_rate=0.05,
        max_iter=200,
        min_samples_leaf=100,
    )
    classifier.fit(X, y_cls)

    positive_mask = train["has_upside"] == 1
    regressor = HistGradientBoostingRegressor(
        max_depth=6,
        learning_rate=0.05,
        max_iter=250,
        min_samples_leaf=100,
    )
    regressor.fit(X.loc[positive_mask], y_reg.loc[positive_mask])

    return Day0ResidualModels(
        classifier=classifier,
        regressor=regressor,
        feature_columns=feature_columns,
    )


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    return float(np.mean((y_true - y_prob) ** 2))


def evaluate(models: Day0ResidualModels, test: pd.DataFrame) -> dict[str, float]:
    pred = models.predict(test)

    out = {
        "n_test": float(len(pred)),
        "brier_has_upside": brier_score(pred["has_upside"], pred["p_has_upside"]),
        "mae_expected_residual": float(np.mean(np.abs(pred["residual_upside"] - pred["expected_residual"]))),
        "mean_residual_true": float(pred["residual_upside"].mean()),
        "mean_residual_pred": float(pred["expected_residual"].mean()),
    }

    # Useful hour-bucket sanity checks.
    late = pred[pred["local_hour"] >= 18]
    if not late.empty:
        out["late_day_brier_has_upside"] = brier_score(late["has_upside"], late["p_has_upside"])
    return out


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Train a Day0 residual model against zeus-shared.db")
    parser.add_argument("db_path", type=str)
    parser.add_argument("--split-date", default="2026-03-01")
    args = parser.parse_args()

    with sqlite3.connect(args.db_path) as conn:
        frame = build_training_frame(conn)

    train, test = temporal_split(frame, split_date=args.split_date)
    models = fit_models(train)
    metrics = evaluate(models, test)

    print(json.dumps(metrics, indent=2, sort_keys=True))
