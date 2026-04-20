# Phase 10C Contract v2 — LOW-lane Tail Repair + HKO Semantic Injection + DT#1 SAVEPOINT

**Written**: 2026-04-19 post P10A+P10B closeout (`4248525` pushed).
**Revised**: 2026-04-19 post critic-eve cycle-1 precommit + scout — **v1 had 2 CRITICAL (C1 city type collision + C2 wrong mock target) + 3 MAJOR + scout 18-callsite enumeration**. v2 absorbs all.
**Branch**: `data-improve` @ `4248525`.
**Mode**: Gen-Verifier. critic-eve cycle 1 (fresh; inherits L17-L26).
**User ruling 2026-04-19**: P10C 先修复；HKO 用现有 `SettlementSemantics.for_city(city)` polymorphic dispatch，**不**开特殊分支。

## v1 → v2 delta

| 发现 | v1 | v2 修正 |
|---|---|---|
| **C1** `add_calibration_pair*` 已有 `city: str` 参数；改成 `City` 会破 18+ callers | "城市签名加 City kwarg" | **新增 `city_obj: City \| None = None`** 独立 kwarg；`city: str` 不变；`city_obj=None` → bare WMO (向后兼容)；`city_obj=City` → polymorphic dispatch。生产 harvester L843 传两者；测试 + 脚本 NOT migrated（fall through to bare WMO；HKO 路径只在 production 路径生效） |
| **C2** S7 mock `query_authoritative_status` 不存在 | mock 不存在的函数 | mock target = `src.state.portfolio.choose_portfolio_truth_source` 返回 `source != "canonical_db"` 的 policy；assert `state.authority in ("degraded", "unverified")` |
| **M1** S4 18+ callers 未在契约里枚举 | 隐含 | 显式列表（见 §S4 Migration Manifest）+ scope 决策：production-only 迁移 |
| **M2** S1 alias `member_maxes` 未删保留误导 | "保留 member_maxes 别名做向后兼容" | **删除别名**。统一用 `bootstrap_ctx["member_extrema"]`。新增 R-CQ.3 AST 抗体：`bootstrap_ctx["member_maxes"]` 在 monitor_refresh.py 内零 reader |
| **M3** `MetricIdentity.LOW_LOCALDAY_MIN` 未 grep 验证 | 假设存在 | grep-verified `LOW_LOCALDAY_MIN` 是模块级常量 at `src/types/metric_identity.py:85` （不是 `MetricIdentity.X` enum 访问）。Import: `from src.types.metric_identity import LOW_LOCALDAY_MIN` |
| **S5 L722/L1468** scout 报告这两个 site city 不在 scope | 全部站点统一处理 | 显式：L722 + L1468 保留 `round_fn=None` (existing fallback)；属 documented escape hatch (L24)；P10D 重构候选 |
| **SAVEPOINT precedent** | 无引用 | scout 找到 `src/data/daily_obs_append.py:549-655` 完整 SAVEPOINT 模式；S6 mirror 此 pattern |
| **S2 metric thread 必需** | 内部 gate | `_check_persistence_anomaly` 当前签名 `(conn, city_name, target_date, predicted_high)` 无 metric；caller L205 必须 thread `temperature_metric` |

## 范围 — 8 items（一次原子 commit；single executor worker；S1+S2 同 file，S3+S4 同 file，必须串行）

### S1 — LOW BLOCKER-1: monitor_refresh bootstrap_ctx LOW-aware

**Files**: `src/engine/monitor_refresh.py`

**Stash 站点**（约 L405）：
```python
# OLD:
bootstrap_ctx["member_maxes"] = extrema.maxes
# NEW (M2 fix — drop alias):
bootstrap_ctx["member_extrema"] = (
    extrema.maxes if extrema.maxes is not None else extrema.mins
)
```

**Consume 站点 L648**:
```python
if len(bootstrap_ctx["member_extrema"]) == 0:
```

**Consume 站点 L662**:
```python
MarketAnalysis(member_maxes=bootstrap_ctx["member_extrema"], ...)
```
(MarketAnalysis kwarg 名暂时保持 `member_maxes` — 改它是 P10D 命名重构的工作；P10C 只让传入的值不再是 None)

**Antibody R-CQ.1**: runtime mock — Day0 LOW position 触发 monitor refresh，assert no NameError/TypeError，且 `bootstrap_ctx["member_extrema"]` 非 None。
**Antibody R-CQ.2**: regression — `len(bootstrap_ctx["member_extrema"])` 在 LOW path 不再 raise。
**Antibody R-CQ.3** (M2 fix): AST probe `monitor_refresh.py` — `bootstrap_ctx["member_maxes"]` 在 stash 站点之外零 reader (M2 alias-pollution defense)。

### S2 — LOW BLOCKER-2: _check_persistence_anomaly metric gate

**File**: `src/engine/monitor_refresh.py:205, 436`

**Caller 改动 L205**:
```python
discount = _check_persistence_anomaly(
    conn, city.name, target_d, float(np.mean(ens.member_maxes)),
    temperature_metric=position.temperature_metric,  # NEW
)
```

**Function 改动 L436**:
```python
def _check_persistence_anomaly(
    conn, city_name: str, target_date, predicted_high: float,
    *, temperature_metric=None,  # NEW
) -> float:
    # M2 gate: legacy settlements has no metric column; LOW lookups would
    # cross-compare against HIGH historical values. Defer to metric-aware
    # query when settlements_v2 populated (P10D).
    if temperature_metric is not None and getattr(temperature_metric, "is_low", lambda: False)():
        return 1.0  # no persistence discount for LOW
    # existing HIGH logic unchanged
    ...
```

**Antibody R-CR.1**: unit — `_check_persistence_anomaly(..., temperature_metric=LOW_LOCALDAY_MIN)` returns 1.0 without DB query.

### S3 — LOW BLOCKER-3: harvester routes LOW to v2

**File**: `src/execution/harvester.py:842`

```python
from src.types.metric_identity import LOW_LOCALDAY_MIN  # M3 fix - module-level constant

# at L842:
if getattr(position, "temperature_metric", "high") == "low":
    add_calibration_pair_v2(
        conn, ...,
        city=city.name,         # str — existing
        city_obj=city,          # NEW — for SettlementSemantics dispatch
        metric_identity=LOW_LOCALDAY_MIN,
        # spec-derived fields:
        physical_quantity=LOW_LOCALDAY_MIN.physical_quantity,
        observation_field=LOW_LOCALDAY_MIN.observation_field,
        data_version=LOW_LOCALDAY_MIN.data_version,
        training_allowed=True,
    )
else:
    add_calibration_pair(conn, ...,
        city=city.name,
        city_obj=city,          # NEW
    )
```

**Antibody R-CS.1**: mock conn captures INSERT — LOW settlement → `calibration_pairs_v2` row with `temperature_metric='low'` + `data_version='tigge_mn2t6_local_calendar_day_min_v1'`.
**Antibody R-CS.2**: pair-negative — HIGH settlement → legacy `calibration_pairs` table (back-compat).

### S4 — HKO MAJOR via additive `city_obj` kwarg (C1 fix)

**Files**: `src/calibration/store.py:65, 129`

**Signature change (additive, non-breaking)**:
```python
def add_calibration_pair(
    conn: sqlite3.Connection,
    city: str,                    # EXISTING — unchanged
    target_date: str,
    settlement_value: float,
    ...,
    *,
    city_obj: "City | None" = None,  # NEW — optional; enables HKO dispatch
) -> dict:
    ...
    # OLD: settlement_value = round_wmo_half_up_value(float(settlement_value))
    # NEW (S4 HKO fix via existing semantics):
    if city_obj is not None:
        round_fn = SettlementSemantics.for_city(city_obj).round_values
        settlement_value = round_fn([float(settlement_value)])[0]
    else:
        # back-compat: callers without city_obj (tests, scripts) get bare WMO
        settlement_value = round_wmo_half_up_value(float(settlement_value))
```

Same shape for `add_calibration_pair_v2` at L129.

**Migration manifest** (declared up-front per M1 fix):

| Caller | Migrate now? | Reason |
|---|---|---|
| `src/execution/harvester.py:842` | YES (production) | HKO settlement value correctness |
| `scripts/rebuild_calibration_pairs_canonical.py:331` | NO (P10D) | Script — operational sweep, not live path |
| `scripts/rebuild_calibration_pairs_v2.py:286` | NO (P10D) | Same |
| `tests/test_calibration_manager.py` (~9 sites) | NO | Tests assert behavior; HKO not in test fixture |
| `tests/test_calibration_bins_canonical.py` (~4 sites) | NO | Same |
| `tests/test_phase4_foundation.py:32,67` | NO | Same |
| `tests/test_phase4_rebuild.py:42,195` | NO | Same |
| `tests/test_pnl_flow_and_audit.py:3106` | NO | Same |
| `tests/test_data_rebuild_relationships.py:453` | NO | Same |

**Why production-only**: The `city_obj` is `None`-default. Tests that pass strings + don't include HKO continue to work (bare WMO is correct for WU cities). Only the production HKO path needs the dispatch — and harvester is that path. P10D may sweep scripts/tests blanket.

**Antibody R-CT.1**: end-to-end mock — HKO city → harvester writes `settlement_value` truncated (oracle_truncate), not WMO half-up.
**Antibody R-CT.2**: pair-negative — WU city → WMO half-up via SettlementSemantics dispatch.
**Antibody R-CT.3**: AST — `add_calibration_pair*` signatures contain `city_obj: City | None = None` kwarg.

### S5 — HKO MINOR replay paths (5 sites with city + 2 documented escape-hatches)

**File**: `src/engine/replay.py`

**Sites with `city` in scope** (inject):
- L1410 (`_sweep_candidate_through_bins`)
- L1682 (`run_wu_settlement_sweep` no-forecast)
- L1734 (`run_wu_settlement_sweep` forecast)
- L1947 (trade history lane)

```python
round_fn = SettlementSemantics.for_city(city).round_values if city else None
outcome = derive_outcome_from_settlement_value(value, bin, unit, round_fn=round_fn)
```

**Sites WITHOUT `city`** (documented escape hatch per L24):
- L722 `_probability_vector_from_values` — math-only path; `round_fn=None` → bare WMO acceptable
- L1468 bare return aggregation — same

**Antibody R-CU.1**: AST allowlist-scoped — the 4 sites with city pass `round_fn`; L722 + L1468 explicitly OK to lack it (test asserts both states correctly).

### S6 — DT#1 per-candidate SAVEPOINT

**File**: `src/engine/cycle_runtime.py:1115-1124, 1131-1138`

**Precedent**: `src/data/daily_obs_append.py:549-655` — full SAVEPOINT pattern. Mirror it.

```python
sp_name = f"sp_candidate_{d.decision_id}"
conn.execute(f"SAVEPOINT {sp_name}")
try:
    log_trade_entry(conn, pos)
    _dual_write_canonical_entry_if_available(conn, pos, decision_id=d.decision_id)
    log_execution_report(conn, pos, result, decision_id=d.decision_id)
    conn.execute(f"RELEASE SAVEPOINT {sp_name}")
except Exception:
    conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
    conn.execute(f"RELEASE SAVEPOINT {sp_name}")
    raise
```

**Antibody R-CV.1**: AST — SAVEPOINT/RELEASE/ROLLBACK pattern in `execute_discovery_phase` body.
**Antibody R-CV.2**: runtime — monkeypatch `log_execution_report` to raise → assert `log_trade_entry` row rolled back (no orphan position).

### S7 — INV-20 antibody activation (C2 fix — correct mock target)

**File**: `tests/test_dual_track_law_stubs.py:584-586`

```python
def test_load_portfolio_degrades_gracefully_on_authority_loss(monkeypatch):
    """INV-20 / DT#6: load_portfolio must NOT raise on auth-loss."""
    from src.state import portfolio as portfolio_module
    from src.state.portfolio_loader_policy import (
        choose_portfolio_truth_source as _orig,
    )

    def _degraded_policy(_status):
        # Simulate auth-loss — return a non-canonical truth source policy
        from src.state.portfolio_loader_policy import LoaderPolicy  # adapt to actual class
        return LoaderPolicy(source="degraded", ...)  # exact shape per actual API

    monkeypatch.setattr(
        portfolio_module, "choose_portfolio_truth_source", _degraded_policy
    )

    state = portfolio_module.load_portfolio()
    assert state.authority in ("degraded", "unverified"), (
        f"INV-20: load_portfolio must degrade not raise on auth-loss. "
        f"Got authority={state.authority!r}"
    )
```

**Antibody R-CW.1**: activated unit — see above. Surgical-revert: change `load_portfolio` to raise on degraded → test fails (was previously skipped).

### S8 — CSV doc flip + S9 antibody guard

**File**: `docs/to-do-list/zeus_bug100_reassessment_table.csv`

10 行翻 → RESOLVED + 1 SEMANTICS_CHANGED → RESOLVED：
- B041, B043, B045, B049, B051, B059, B061, B062, B074, B097

**Antibody R-CX.1** (eve "no antibody for S8" 修正): pytest 解析 CSV，assert 这 10 行 status=RESOLVED 且 fix_commit 字段非空 (防 future drift)。

## 硬约束

- 不导入 TIGGE / 不写 v2 表（除 S3 LOW calibration_pairs_v2 路由）
- **不开 HKO 特殊分支**（用户原则）
- 不动 `_TRUTH_AUTHORITY_MAP` / `kelly_size` 严格化 / monitor_refresh L677 broad except
- 不重构 `ensemble_signal.py:279 self.member_maxes` 命名（P10D）
- 不修 `evaluator.py:1751 members_json` for LOW（P10D）
- 不修 causality_status DB→router wire（P10D）
- 不迁移 18 个 `add_calibration_pair*` 测试/脚本 callers（P10D blanket）
- Golden Window intact

## 验收

**回归基线**: 144 failed / 1905 passed / 93 skipped (eve 重测 P10B 后实际值)
- delta failed ≤ 0
- delta passed ≥ 12 抗体 + 1 (INV-20 unskip 从 skipped → passed)
- delta skipped: -1 (INV-20)

**R-letter 命名空间**: R-CQ 起 → R-CQ.1/2/3 + R-CR.1 + R-CS.1/2 + R-CT.1/2/3 + R-CU.1 + R-CV.1/2 + R-CW.1 + R-CX.1 = 13 抗体

## 范围外（推迟到 P10D）

- monitor_refresh L677 broad except 收窄
- `ensemble_signal.py self.member_maxes` 命名重构
- `evaluator.py members_json for LOW` 字段语义
- causality_status DB→router wire
- `add_calibration_pair*` 18 callers blanket migrate
- L722 + L1468 helper refactor for round_fn
- R10 Kelly 严格 ExecutionPrice
- R12 H7 144 failures triage
- R13 `_TRUTH_AUTHORITY_MAP` 语义
- INV-13 yaml 登记 + cycle_runner.py:226 escape hatch
- INV-16 causality_status enforcement (3 FAIL tests)
- NC-12 高低混合 Platt 抗体激活
- 4 个 ghost / semgrep continue-on-error 升级
- B055 / B099 architect packets
- Workspace cleanup (gitignore + xlsx un-track + archive)

## 顺序

1. team-lead 写契约 ← 本文件 (v2)
2. ✓ scout returned
3. ✓ critic-eve cycle-1 precommit returned
4. **单 executor worker 串行 S1-S8**（S1+S2 同 monitor_refresh.py；S3+S4 同 calibration/harvester；必须同 worker）
5. team-lead disk-verify + 全量回归（**wide-review-before-push** per L22 — 必须 diff failure list against post-P10B baseline 144/1905/93）
6. critic-eve cycle-1 wide review (fresh spawn with onboarding doc)
7. ITERATE 修 / PASS → commit + push

## 协调

- L20: 所有 file:line citation 已 grep-gated 2026-04-19 (含 v2 修正)
- L21: S7 真 ACTIVATE (pytest.skip L586 confirmed)
- L22: executor 不得自动 commit（dave 退休 legacy）
- L24: S5 L722/L1468 是 documented escape hatch (math-only / aggregation)
- L25: M3 修正 — `LOW_LOCALDAY_MIN` 是模块常量不是 enum，import path 修正
