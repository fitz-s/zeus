# 目标架构与落地顺序

## 总体原则

这一轮改进不以“多加几个表、多加几个 feature”为目标，而以**统一 learning truth + 统一 promotion truth** 为目标。

## Phase 0：Probability Trace & Provenance（立即开始）

### 目标

把 `p_raw / p_cal / p_market / p_posterior / alpha / selected_method / family_id` 全部进入统一 fact surface。

### 交付物

- 新表 `probability_trace_fact`
- 新表 `selection_hypothesis_fact`
- canonical completeness view
- nightly audit

### 代码落点

- `src/state/decision_chain.py`
- `src/engine/evaluator.py`
- `src/state/ledger.py`
- `src/state/chronicler.py`

### 验收标准

- 新决策的 `p_raw_json / p_cal_json / p_market_json / p_posterior_json` 非空率 >= 99%
- `trade_decisions.p_calibrated` 不再全空
- 每笔 trade 能回溯到唯一的 probability trace

---

## Phase 1：Calibration Truth v2

### 目标

把 calibration 从 row-count world 升级到 decision-group world。

### 交付物

- `calibration_decision_group`
- grouped bootstrap
- blocked OOS eval pipeline
- bucket health dashboard（按有效样本量）

### 代码落点

- `src/calibration/store.py`
- `src/calibration/manager.py`
- `src/calibration/platt.py`
- 新增 `src/calibration/effective_sample_size.py`
- 新增 `src/calibration/hierarchical_platt.py`

### 验收标准

- maturity gate 只看 `decision_groups`
- OOS metrics 每个 active bucket 都可追踪
- 所有 active bucket 都有 provenance：训练窗、测试窗、版本号

---

## Phase 2：Day0 Residual v2

### 目标

把 day0 从 heuristic seam 升级成可学习的 residual engine。

### 交付物

- `day0_residual_fact`
- train / shadow / active 三态
- hour-bucket performance dashboard
- seam adapter（只替换 remaining-upside，不破坏 hard floor）

### 代码落点

- `src/signal/day0_signal.py`
- `src/signal/diurnal.py`
- `src/signal/forecast_uncertainty.py`
- 新增 `src/day0/day0_residual_learning.py`

### 验收标准

- 与当前 day0 baseline 对比，OOS Brier / log loss 改善
- 尾部高温 overshoot 误差下降
- stale / late-day 幻觉 edge 减少

---

## Phase 3：Bias / Uncertainty v2

### 目标

把 bias correction 升级为 distributional correction。

### 交付物

- source × lead × city/cluster 的 mean/variance mapping
- CRPS / coverage dashboard
- seam config 版本化

### 代码落点

- `src/signal/ensemble_signal.py`
- `src/signal/forecast_uncertainty.py`
- `model_bias` / `forecast_skill` 相关脚本

### 验收标准

- lead-wise calibration slope 更接近 1
- tail coverage 更稳定
- posterior fusion alpha 的波动减小

---

## Phase 4：Selection / Risk v2

### 目标

让 statistical significance 和 actual trading selection 对齐。

### 交付物

- full-family FDR
- shrinkage correlation matrix
- portfolio risk budget
- strategy-level threshold adaptation

### 代码落点

- `src/strategy/market_analysis.py`
- `src/strategy/fdr_filter.py`
- `src/strategy/correlation.py`
- `src/strategy/kelly.py`
- 新增 `src/strategy/selection_family.py`
- 新增 `src/strategy/error_correlation.py`

### 验收标准

- 每个 cycle 有完整 family record
- q-value 真正对应 tested family
- 大相关城市/同簇暴露有可解释 risk throttle

---

## Phase 5：Replay / Promotion v2

### 目标

把 shadow / replay / promotion 接成真实闭环。

### 交付物

- `model_eval_run`
- `model_eval_point`
- `promotion_registry`
- replay comparison notebook / report

### 代码落点

- replay pipeline
- nightly refit scripts
- inventory generator

### 验收标准

- 所有新模型先 shadow，再 candidate，再 active
- promotion 基于同一套 OOS rules
- inventory 由 DB truth 自动生成，不依赖手工 sheet

---

## 推荐实施顺序（你现在最应该做）

### 第一优先级（本周）

1. `probability_trace_fact`
2. `calibration_decision_group`
3. `trade_decisions / opportunity_fact` 概率链补齐
4. grouped OOS eval

### 第二优先级（下一个迭代）

1. day0 residual training dataset
2. partial pooling calibrator
3. family-wise FDR

### 第三优先级

1. EMOS / uncertainty
2. shrinkage correlation
3. execution microstructure

---

## 为什么不建议先“全城拓展 + 再说”

因为如果先把 45 城全部扩出来，但 maturity、OOS、selection family、probability trace 都没统一，
你只是在把一个尚未完全可比的系统复制到更大覆盖面。
