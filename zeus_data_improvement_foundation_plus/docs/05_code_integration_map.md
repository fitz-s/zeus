# 代码集成地图

下面是我建议你在 Zeus 主仓里真实落点的位置。

## 1. 概率链条

### 现有文件

- `src/state/decision_chain.py`
- `src/engine/evaluator.py`
- `src/state/ledger.py`
- `src/state/db.py`

### 要做的事

- 在 decision artifact 生成后，立刻写入 `probability_trace_fact`
- 把 vector 级别概率从 shadow-only surface 升级到 canonical surface
- 给每个 hypothesis / selection family 一个稳定 ID

---

## 2. Calibration

### 现有文件

- `src/calibration/store.py`
- `src/calibration/manager.py`
- `src/calibration/platt.py`

### 要做的事

- 新增 `calibration_decision_group`
- 所有 maturity / fallback 改看有效样本量
- partial pooling calibrator 先 shadow，再 candidate，再 active

---

## 3. Day0

### 现有文件

- `src/signal/day0_signal.py`
- `src/signal/diurnal.py`
- `src/signal/forecast_uncertainty.py`

### 要做的事

- 增加 `day0_residual_fact`
- 新 residual model 只替换 remaining-upside seam
- 保持 `observed high is a hard floor`

---

## 4. Selection / Risk

### 现有文件

- `src/strategy/market_analysis.py`
- `src/strategy/fdr_filter.py`
- `src/strategy/correlation.py`
- `src/strategy/kelly.py`

### 要做的事

- 不再只对 prefiltered positives 做 FDR
- risk correlation 改成 shrinkage estimate
- Kelly sizing 接入 family risk / city correlation throttle

---

## 5. Reporting / Replay

### 现有文件

- replay / decision logging scripts
- `shadow_signals`
- `replay_results`

### 要做的事

- 新增 `model_eval_run`, `model_eval_point`
- active/shadow/candidate 由 promotion registry 控制
- inventory 全自动 SQL 化
