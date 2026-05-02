# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: 2026-05-02 WU/Ogimet hourly timezone remediation; obs_v2 local-hour contract.
"""Static antibodies for hourly backfill/capture local-time identity.

Runtime enforcement lives in ``ObsV2Row``. These AST checks prevent future
hourly backfill or capture scripts from adding a typed writer call or direct
``observation_instants`` insert that omits local-hour/DST identity fields.
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

HOURLY_SCRIPT_PATHS = (
    "scripts/backfill_obs_v2.py",
    "scripts/fill_obs_v2_dst_gaps.py",
    "scripts/fill_obs_v2_meteostat.py",
    "scripts/hko_ingest_tick.py",
)

DIRECT_INSERT_SCRIPT_PATHS = (
    "scripts/backfill_hourly_openmeteo.py",
)

REQUIRED_OBS_V2_KEYWORDS = {
    "local_hour",
    "local_timestamp",
    "utc_timestamp",
    "is_ambiguous_local_hour",
    "is_missing_local_hour",
}

REQUIRED_DIRECT_INSERT_FIELDS = {
    "local_hour",
    "local_timestamp",
    "utc_timestamp",
    "is_ambiguous_local_hour",
    "is_missing_local_hour",
}


def _parse_script(relative_path: str) -> ast.Module:
    path = ROOT / relative_path
    return ast.parse(path.read_text(), filename=str(path))


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def test_obs_v2_row_constructors_bind_local_hour_identity_fields():
    missing_by_call: list[str] = []
    constructor_count = 0
    for relative_path in HOURLY_SCRIPT_PATHS:
        tree = _parse_script(relative_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "ObsV2Row":
                constructor_count += 1
                keywords = {kw.arg for kw in node.keywords if kw.arg is not None}
                missing = REQUIRED_OBS_V2_KEYWORDS - keywords
                if missing:
                    missing_by_call.append(
                        f"{relative_path}:{node.lineno} missing {sorted(missing)}"
                    )

    assert constructor_count > 0
    assert not missing_by_call


def test_direct_hourly_inserts_bind_local_hour_identity_fields():
    missing_by_script: list[str] = []
    insert_count = 0
    for relative_path in DIRECT_INSERT_SCRIPT_PATHS:
        tree = _parse_script(relative_path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            sql = node.value.lower()
            normalized_sql = " ".join(sql.split())
            if "insert" not in normalized_sql or "into observation_instants" not in normalized_sql:
                continue
            insert_count += 1
            missing = {field for field in REQUIRED_DIRECT_INSERT_FIELDS if field not in sql}
            if missing:
                missing_by_script.append(
                    f"{relative_path}:{node.lineno} missing {sorted(missing)}"
                )

    assert insert_count > 0
    assert not missing_by_script