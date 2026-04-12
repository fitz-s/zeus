# 代码碎片示例

下面给几个最关键的代码碎片，方便你快速判断我不是只给概念。

## 1. calibration 有效样本量

```python
from src.calibration.effective_sample_size import load_and_summarize

groups, health = load_and_summarize("zeus-shared.db")
print(health.head())
```

核心思想是把 bin-level `calibration_pairs` 先聚成：

```python
(city, target_date, forecast_available_at)
```

再做 maturity / bootstrap / OOS。

---

## 2. partial pooling calibrator

```python
from src.calibration.hierarchical_platt import fit_hierarchy

models = fit_hierarchy(
    df=train_df,
    bucket_col="bucket_key",
    parent_col="parent_bucket_key",
    raw_col="p_raw",
    lead_col="lead_days",
    y_col="outcome",
    group_col="group_id",
    tau_groups=50.0,
)
```

局部参数向父 bucket 收缩：

```python
lambda_b = n_eff / (n_eff + tau_groups)
theta_shrunk = lambda_b * theta_local + (1 - lambda_b) * theta_parent
```

---

## 3. Day0 residual

```python
with sqlite3.connect("zeus-shared.db") as conn:
    frame = build_training_frame(conn)

train, test = temporal_split(frame, split_date="2026-03-01")
models = fit_models(train)
metrics = evaluate(models, test)
```

目标定义：

```python
residual_upside = max(0.0, settlement_value - running_max)
has_upside = int(settlement_value > running_max)
```

---

## 4. family-wise FDR

```python
out = apply_familywise_fdr(hypothesis_df, q=0.10)
```

重点不是 BH 函数本身，而是 `hypothesis_df` 必须包含**全量 tested hypotheses**。

---

## 5. city correlation

```python
with sqlite3.connect("zeus-shared.db") as conn:
    matrix = load_settlement_anomalies(conn)
corr = shrink_correlation(matrix, shrinkage_lambda=0.20)
```

这个矩阵可以直接喂给 risk throttle / portfolio allocation。

---
