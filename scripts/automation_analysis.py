#!/usr/bin/env python3
"""automation_analysis.py — Daily diagnostic for the calibration/data automation layer.

Checks:
1. alpha_overrides: what's live, any pending recoms?
2. model_bias: stability, sample counts, discount factors
3. calibration_pairs: counts per bucket, recent accrual rate
4. platt_models: Brier insample, active count per bucket
5. cross_module_invariants: bias/sync status
6. ETL table freshness: diurnal, temp_persistence, asos_wu_offsets

Designed to run as a Zeus cron job (daily) and report to Discord.
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
ZEUS_DB = PROJECT_ROOT / "state" / "zeus.db"
sys.path.insert(0, str(PROJECT_ROOT))


def get_conn() -> sqlite3.Connection:
    from src.state.db import get_world_connection as get_connection
    return get_connection()


def fmt(emoji: str, text: str) -> str:
    return f"{emoji} {text}"


def section(title: str) -> str:
    return f"\n**{'='*40}**\n**{title}**\n"


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not _table_exists(conn, table_name):
        raise sqlite3.OperationalError(f"missing table: {table_name}")
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) == column_name for row in rows)


# ── 1. Alpha Overrides ────────────────────────────────────────────────────────
def analyze_alpha_overrides(conn: sqlite3.Connection) -> str:
    rows = conn.execute("""
        SELECT city, season, alpha, source, validated_at,
               brier_improvement, n_validation_samples
        FROM alpha_overrides
        ORDER BY city, season
    """).fetchall()

    if not rows:
        return fmt("⚠️", "`alpha_overrides` 表是空的——validate_dynamic_alpha 还没产生任何 override")

    lines = []
    for r in rows:
        src_emoji = "✅" if r["source"] == "validated_optimal" else "📋"
        brier = f" | Brier改善: {r['brier_improvement']:.4f}" if r["brier_improvement"] else ""
        n = r["n_validation_samples"] or 0
        lines.append(
            f"  {src_emoji} `{r['city']}/{r['season']}` α={r['alpha']:.3f} "
            f"({r['source']}, n={n}{brier}) @ {r['validated_at'][:10]}"
        )
    return section("alpha_overrides") + "\n".join(lines)


# ── 2. Model Bias ────────────────────────────────────────────────────────────
def analyze_model_bias(conn: sqlite3.Connection) -> str:
    rows = conn.execute("""
        SELECT city, season, source, bias, mae, n_samples, discount_factor
        FROM model_bias
        ORDER BY city, season, source
    """).fetchall()

    if not rows:
        return fmt("🔴", "`model_bias` 表是空的——bias correction 无法激活")

    lines = []
    for r in rows:
        disc = r["discount_factor"] if r["discount_factor"] else 0.7
        lines.append(
            f"  🌡️ `{r['city']}/{r['season']}` [{r['source']}] "
            f"bias={r['bias']:+.2f}° | MAE={r['mae']:.2f}° | "
            f"n={r['n_samples']} | discount={disc}"
        )
    return section("model_bias") + "\n".join(lines)


# ── 3. Calibration Pairs ────────────────────────────────────────────────────
def analyze_calibration_pairs(conn: sqlite3.Connection) -> str:
    buckets = conn.execute("""
        SELECT cluster, season, COUNT(*) as n,
               SUM(outcome) as n_pos,
               MIN(target_date) as earliest,
               MAX(target_date) as latest
        FROM calibration_pairs
        GROUP BY cluster, season
        ORDER BY n DESC
    """).fetchall()

    if not buckets:
        return fmt("🔴", "`calibration_pairs` 表是空的——Harvester 还没有产生任何 pairs")

    lines = []
    for b in buckets:
        pos_rate = b["n_pos"] / b["n"] * 100 if b["n"] > 0 else 0
        maturity = "🟢" if b["n"] >= 150 else "🟡" if b["n"] >= 50 else "🔴"
        lines.append(
            f"  {maturity} `{b['cluster']}/{b['season']}` "
            f"n={b['n']:4d} | pos_rate={pos_rate:.1f}% | "
            f"{b['earliest'][:10]} → {b['latest'][:10]}"
        )
    return section("calibration_pairs") + "\n".join(lines)


# ── 4. Platt Models ─────────────────────────────────────────────────────────
def analyze_platt_models(conn: sqlite3.Connection) -> str:
    active = conn.execute("""
        SELECT bucket_key, param_A, param_B, param_C,
               n_samples, brier_insample, fitted_at
        FROM platt_models
        WHERE is_active = 1
        ORDER BY bucket_key
    """).fetchall()

    if not active:
        return fmt("🔴", "`platt_models` 没有活跃模型——需要运行 refit_platt.py")

    lines = []
    for m in active:
        brier = f"{m['brier_insample']:.4f}" if m["brier_insample"] else "N/A"
        lines.append(
            f"  ✅ `{m['bucket_key']}` "
            f"A={m['param_A']:.3f} B={m['param_B']:.3f} C={m['param_C']:.3f} "
            f"| n={m['n_samples']} | Brier={brier} | {m['fitted_at'][:10]}"
        )
    return section("platt_models") + "\n".join(lines)


# ── 5. ETL Freshness ────────────────────────────────────────────────────────
def analyze_etl_freshness(conn: sqlite3.Connection) -> str:
    checks = [
        ("asos_wu_offsets", "ASOS-WU offset 校准数据"),
        ("diurnal_curves", "日较差曲线"),
        ("temp_persistence", "温度持续性"),
    ]
    lines = []
    for table, label in checks:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n == 0:
                lines.append(f"  🔴 `{table}` — **EMPTY**（需要 ETL 补充）")
            else:
                latest = conn.execute(
                    f"SELECT MAX(rowid) FROM {table}"
                ).fetchone()[0]
                lines.append(f"  🟢 `{table}` — {n} rows（{label}）")
        except Exception as e:
            lines.append(f"  🔴 `{table}` — 查询失败: {e}")

    return section("ETL Data Freshness") + "\n".join(lines)


# ── 6. Bias Correction Readiness ───────────────────────────────────────────
def analyze_bias_readiness(conn: sqlite3.Connection) -> str:
    try:
        from src.config import settings
        bias_enabled = settings.bias_correction_enabled
    except Exception:
        bias_enabled = False

    n_bias_rows = conn.execute(
        "SELECT COUNT(*) FROM model_bias WHERE source='ecmwf' AND n_samples >= 20"
    ).fetchone()[0]

    n_pairs_with_flag: int | None = 0
    n_pairs_status = "ok"
    try:
        n_pairs_with_flag = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs WHERE bias_corrected = 1"
        ).fetchone()[0]
    except sqlite3.OperationalError as exc:
        n_pairs_with_flag = None
        n_pairs_status = f"query_failed:{exc}"

    platt_bias_instrumented = _table_has_column(
        conn,
        "platt_models",
        "trained_with_bias_correction",
    )
    n_active_bias_models: int | None = None
    if platt_bias_instrumented:
        n_active_bias_models = conn.execute(
            "SELECT COUNT(*) FROM platt_models WHERE is_active=1 AND "
            "trained_with_bias_correction = 1"
        ).fetchone()[0]
    active_bias_model_text = (
        str(n_active_bias_models)
        if n_active_bias_models is not None
        else "unknown (schema not instrumented)"
    )

    status_lines = [
        f"  bias_correction_enabled: **{bias_enabled}**",
        f"  model_bias ECMWF rows (n≥20): **{n_bias_rows}**",
        f"  calibration_pairs bias_corrected=1: **{n_pairs_with_flag if n_pairs_with_flag is not None else 'unknown'}**"
        + ("" if n_pairs_status == "ok" else f" ({n_pairs_status})"),
        f"  platt_models trained w/ bias: **{active_bias_model_text}**",
    ]

    if n_pairs_with_flag is None:
        status = "🔴 calibration_pairs bias_corrected 查询失败——无法判断 bias readiness"
    elif not platt_bias_instrumented:
        status = "⚠️ platt_models 缺少 trained_with_bias_correction 字段——model bias readiness 未被持久化"
    elif bias_enabled and n_bias_rows > 0 and n_pairs_with_flag > 0 and n_active_bias_models and n_active_bias_models > 0:
        status = "✅ **Bias correction 可以激活**"
    elif not bias_enabled and n_bias_rows > 0 and n_pairs_with_flag > 0 and n_active_bias_models and n_active_bias_models > 0:
        status = "⚠️ bias_correction_enabled=false，但数据已就绪——可考虑开启"
    elif n_bias_rows == 0:
        status = "🔴 model_bias 样本不足，无法激活 bias correction"
    else:
        status = "⚠️ 数据未完全就绪，需要先运行 ETL + refit_platt"

    return section("Bias Correction Readiness") + "\n".join(status_lines) + "\n\n" + status


# ── 7. Recent Pair Accrual Rate ─────────────────────────────────────────────
def analyze_pair_accrual_rate(conn: sqlite3.Connection) -> str:
    """Check how many new pairs were added per day in the last 7 days."""
    try:
        recent = conn.execute("""
            SELECT DATE(target_date) as dt, COUNT(*) as n
            FROM calibration_pairs
            WHERE target_date >= DATE('now', '-7 days')
            GROUP BY dt
            ORDER BY dt
        """).fetchall()
    except Exception:
        return ""

    if not recent:
        return ""

    lines = [f"  {r['dt']}: +{r['n']} pairs" for r in recent]
    return section("Pair Accrual (last 7 days)") + "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────
def run_analysis() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report = [f"**⚡ Zeus Automation Layer Report — {ts}**"]

    conn = get_conn()

    try:
        report.append(analyze_alpha_overrides(conn))
        report.append(analyze_model_bias(conn))
        report.append(analyze_calibration_pairs(conn))
        report.append(analyze_pair_accrual_rate(conn))
        report.append(analyze_platt_models(conn))
        report.append(analyze_etl_freshness(conn))
        report.append(analyze_bias_readiness(conn))
    finally:
        conn.close()

    return "\n".join(report)


if __name__ == "__main__":
    print(run_analysis())
