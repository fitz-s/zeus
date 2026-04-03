# 统计方法论：数据从哪里出错，模型在哪里说谎

---

## 第一部分：数据获取的统计陷阱

### 1.1 采样与时间对齐

#### 非同步时间戳的虚假相关

气象预报每 6 小时更新（GFS 00Z/06Z/12Z/18Z），但 Polymarket 价格在链上连续变动。将这两个时间序列"对齐"时会出错：

**线性插值（Linear Interpolation）：引入虚假平滑。** 在两次模型运行之间插值温度预报，会人为创造一个"预报在两次运行之间平滑变化"的假象。实际上预报在模型运行之间是不变的——模型不产出数据时就没有新信息。线性插值会让你的回测误以为预报在持续更新。

**前向填充（Forward Fill）：正确方法。** 在两次模型运行之间，使用上一次运行的预报值填充所有时间点。这准确反映了实际可获取的信息：在下一次模型运行完成之前，你拥有的信息就是上一次运行的结果。

**后向填充（Backward Fill）：前视偏差。** 绝对不能使用。它等价于"在模型运行之前就知道了运行结果"。

**对齐规则**：对于任意时间点 t 的分析，使用 `issue_time <= t` 的最新预报。用伪代码表达：

```
def get_forecast_at(t):
    return forecasts.filter(issue_time <= t).sort_by(issue_time, desc).first()
```

#### 事件驱动采样的选择偏差

如果你只在"市场价格显著变动时"记录预报数据，你会引入一个不对称的偏差：价格变动往往发生在预报变化大的时候，这意味着你的数据集过度代表了"模型与市场不一致"的时刻，低估了"模型与市场一致"的时刻。

后果：基于这种数据训练的校准模型会系统性地高估 edge 的大小和频率。

**正确做法**：以固定时间间隔（每 15 分钟或每小时）采样所有数据（预报 + 市场价格），无论是否有价格变动。存储成本极低（每条记录 < 1KB），但保护你免于选择偏差。

#### 前视偏差的数据库级防护

这是 Rainstorm 已经经历过的灾难——合成校准数据污染了所有参数。根源在于数据库没有在结构层面防止前视偏差。

**强制规则**：数据库中的每条记录必须有一个不可修改的 `available_at` 时间戳，表示"这条数据在什么时候对交易系统可用"。所有查询必须包含 `WHERE available_at <= decision_time` 的约束。

```sql
CREATE TABLE forecasts (
    id INTEGER PRIMARY KEY,
    issue_time TIMESTAMP NOT NULL,     -- 模型运行发起时间
    valid_time TIMESTAMP NOT NULL,     -- 预报目标时间
    available_at TIMESTAMP NOT NULL,   -- 数据对系统可用的时间
    fetch_time TIMESTAMP NOT NULL,     -- 实际从 API 获取的时间
    source TEXT NOT NULL,              -- 'ecmwf_ens', 'gfs', 'hrrr'
    data_version INTEGER DEFAULT 1,    -- 版本控制
    -- 预报值
    temperature_mean REAL,
    temperature_std REAL,
    ensemble_members TEXT,             -- JSON array, 可选
    -- 元数据
    lead_hours INTEGER,               -- valid_time - issue_time（小时）
    quality_flag INTEGER DEFAULT 0,   -- 0=正常, 1=可疑, 2=无效
    UNIQUE(source, issue_time, valid_time, data_version)
);

-- 关键约束：available_at >= issue_time + processing_delay
-- 且 available_at >= fetch_time
```

**为什么四个时间戳缺一不可**：
- `issue_time`：确定哪次模型运行产出了这个预报（用于避免混用不同运行的数据）
- `valid_time`：确定预报的目标时间（用于匹配市场结算时间）
- `available_at`：防止前视偏差（在回测中只使用决策时刻已可用的数据）
- `fetch_time`：检测数据获取延迟（如果 fetch_time - available_at 异常大，表示 API 延迟）

#### 夏令时（DST）处理

**规则：所有内部时间戳统一使用 UTC。不例外。**

DST 切换会在每年的两个夜晚创造一个 23 小时的"日"和一个 25 小时的"日"。如果你使用本地时间存储数据，这两天的时间序列对齐会出错——要么丢失一小时的数据，要么多出一小时。

NYC 市场的结算使用 Wunderground 的 KLGA 页面，其"日期"定义是本地时间的 midnight-to-midnight。因此你需要知道：

```
NYC (EST/EDT):
  EST: UTC-5（11月第一个周日到3月第二个周日）
  EDT: UTC-4（3月第二个周日到11月第一个周日）

London (GMT/BST):
  GMT: UTC+0（10月最后一个周日到3月最后一个周日）
  BST: UTC+1（3月最后一个周日到10月最后一个周日）
```

内部存储 UTC，在匹配结算日期时转换为本地时间。Python 的 `zoneinfo` 模块正确处理 DST 转换。

#### 预报时效精度衰减的量化

预报精度随 lead time 衰减的曲线不是固定的——它取决于变量、季节、地理位置、和当前天气体制。但对于交易目的，你需要一个可用的近似。

对于 ECMWF ENS 2m 温度在中纬度（NYC, London 等），典型 RMSE 衰减（以°C 为单位）：

```
lead_hours:  6    12    24    48    72    96   120   168
RMSE (°C):  0.8   1.0   1.3   1.8   2.3   2.8   3.2   4.0
```

**存储方式**：将 lead_hours 作为每条预报记录的属性存储。在校准和 edge 计算中，使用 lead_hours 来查询对应的历史 RMSE，作为不确定性估计的 floor。

```python
def min_uncertainty(lead_hours: int) -> float:
    """即使集合预报的 std 很小，不确定性也不应低于此值。"""
    # 基于 ECMWF 验证统计的保守下界
    return 0.8 + 0.02 * lead_hours  # °C
```

### 1.2 观测偏差

#### 仪器精度 vs Bin 宽度

ASOS 站点温度传感器精度为 ±0.5°C（约 ±1°F）。但 NYC Polymarket 市场的 bin 宽度通常是 2°F。

这意味着当真实温度在 53.0°F 时，仪器可能报告 52°F 到 54°F 之间的任何整数值。如果 bin 边界在 53°F，仪器误差直接决定了结算结果。

**在概率估计中显式处理测量不确定性**：

不要计算 `P(T_true > threshold)`，而是计算 `P(T_measured > threshold)`，其中：

```
T_measured = round(T_true + ε)
ε ~ N(0, σ_instrument²)
σ_instrument ≈ 0.3°C ≈ 0.5°F
```

对于预报值 T_forecast ± σ_forecast：

```
P(T_measured ∈ [a, b]) = ∫ P(round(T + ε) ∈ [a, b]) · f(T; T_forecast, σ_forecast) dT
```

实际计算中用蒙特卡罗模拟更简单：

```python
def bin_probability(forecast_mean, forecast_std, bin_low, bin_high,
                    instrument_noise=0.5, n_samples=10000):
    T_true = np.random.normal(forecast_mean, forecast_std, n_samples)
    T_measured = np.round(T_true + np.random.normal(0, instrument_noise, n_samples))
    return np.mean((T_measured >= bin_low) & (T_measured <= bin_high))
```

这比简单的 `count(ensemble_member ∈ bin) / 51` 更准确，尤其在 bin 边界附近。

#### 城市热岛效应

城市热岛效应（UHI）使得城市站点的温度系统性偏高于周边地区。对于 NWP 模型来说，KLGA 所在网格点可能包含非城市区域，导致模型预报偏低。

**量化方法**：计算"模型 bias = 模型预报 - 站点观测"在所有天数上的平均值。如果 bias 系统性为负（模型偏低），说明 UHI 效应未被模型充分捕获。

已知值：NYC KLGA 的 GFS 在夏季系统性偏低约 1-2°F（UHI + 海风效应的共同结果）。

**处理**：在线性偏差校正 `T_corrected = a * T_model + b` 中，b 项隐式吸收了 UHI 偏差。但需要按季节分别拟合——UHI 效应在夏季（强烈日照、弱风）比冬季（混合良好的大气边界层）更显著。

#### 空间插值误差

GFS 的 0.25° 网格意味着最近网格点可能距离 KLGA 站点 10-15km。ECMWF 的 0.1° 网格（IFS HRES）更精细但仍有数公里偏差。

这个 spatial mismatch 不是随机误差——它是系统性的，因为站点周边的地形和下垫面（水体、城市、植被）在模型网格尺度上被平均了。

**统计表达**：将空间插值误差视为一个额外的、与预报误差独立的噪声源：

```
σ_total² = σ_forecast² + σ_spatial² + σ_instrument²
```

σ_spatial 可以从"最近网格点预报 vs 站点观测"的历史差异中估计，通常约 0.3-0.8°C（取决于地形复杂度）。

---

## 第二部分：核心统计方法的选择

### 2.1 从 51 个集合成员到概率

#### 经验频率 vs 参数拟合

**在大多数情况下，直接使用经验频率 `count / 51` 是更好的选择。**

原因：

51 个样本不足以可靠拟合参数分布。即使假设正态分布（只有 2 个参数），参数估计的标准误差也相当大：

```
均值的 SE = σ / √51 ≈ σ / 7.14
标准差的 SE = σ / √(2×50) = σ / 10
```

对于 σ = 2°C 的典型温度集合，均值的 SE ≈ 0.28°C，标准差的 SE ≈ 0.20°C。这意味着你的参数估计本身就有约 0.3°C 的不确定性——对于 2°F bin 宽度来说这不算小。

**但经验频率有一个致命弱点：在尾部概率极度不稳定。**

当 51 个成员中只有 2 个超过阈值时，P = 2/51 ≈ 0.039。但如果用 bootstrap 估计这个比例的置信区间：95% CI 约为 [0.005, 0.135]。即你的 "4% 概率" 估计的真实值可能在 0.5% 到 13.5% 之间——对于交易来说这个不确定性是不可接受的。

#### 何时使用参数拟合

**仅在以下条件同时满足时使用正态（或偏态正态）拟合**：

1. 目标阈值在集合分布的尾部（< 10% 或 > 90% 区域）
2. 集合分布通过 Shapiro-Wilk 正态性检验（p > 0.05）
3. 集合没有明显的双峰结构

检验代码：
```python
from scipy.stats import shapiro, skewnorm

def estimate_tail_probability(ensemble_members, threshold, direction='above'):
    _, p_shapiro = shapiro(ensemble_members)
    
    if p_shapiro > 0.05:
        # 正态假设成立，使用参数估计
        mu, sigma = np.mean(ensemble_members), np.std(ensemble_members, ddof=1)
        if direction == 'above':
            return 1 - norm.cdf(threshold, mu, sigma)
        else:
            return norm.cdf(threshold, mu, sigma)
    else:
        # 正态假设不成立，检查偏态
        a, loc, scale = skewnorm.fit(ensemble_members)
        if direction == 'above':
            return 1 - skewnorm.cdf(threshold, a, loc, scale)
        else:
            return skewnorm.cdf(threshold, a, loc, scale)
```

**温度分布是否正态？** 大多数情况下大致正态，但有两个常见偏离：
- 接近冰点时偏态（冷边界效应——地面辐射冷却有下限）
- 强对流活动期间双峰（"暴风雨来/不来"两种情景）

#### 极值理论（EVT）在此场景中不适用

EVT（如 Generalized Pareto Distribution）用于估计非常稀有事件的概率（如百年一遇的洪水）。在 Polymarket 温度市场中，即使是 shoulder bins 的概率也在 1-10% 范围——这远高于 EVT 的典型应用场景（< 0.1%）。而且 EVT 需要大量的极值样本来拟合，51 个集合成员中可能只有 1-2 个极值，完全不够。

**不要使用 EVT。** 对于 1-10% 的尾部概率，偏态正态拟合 + Bootstrap 不确定性量化就足够了。

### 2.2 三种标准差的精确定义

你的系统中有三种完全不同的"不确定性"，混淆它们是致命的。

#### σ_ensemble：预报不确定性

**定义**：ECMWF ENS 51 个成员在特定时间和地点的温度标准差。

```python
sigma_ensemble = np.std(ensemble_members, ddof=1)  # 使用 ddof=1（无偏估计）
```

**含义**：模型认为的、初始条件和物理参数化不确定性导致的预报误差大小。

**已知缺陷**：ECMWF ENS 是欠离散的（underdispersive），σ_ensemble 系统性偏小约 15-20%。因此在使用时需要膨胀：

```python
sigma_adjusted = sigma_ensemble * 1.2  # 经验膨胀因子
```

**在交易中的用途**：决定 bin 概率的扩散程度。σ_ensemble 越大，概率越均匀分布在多个 bin 上；σ_ensemble 越小，概率越集中在 1-2 个 bin。

#### σ_calibration：校准误差

**定义**：校准后概率与实际结果频率之间的差异，用 RMSE 衡量。

```python
# 将历史校准概率分为 K 个 bin
# 对每个 bin 计算 (平均预测概率 - 实际发生频率)²
# RMSE = sqrt(mean of all bins)
```

**含义**：你的概率估计在历史上有多准确。

**在交易中的用途**：决定你的 edge 估计有多可信。如果 σ_calibration = 0.10（10 个百分点），那么你声称的"我认为概率是 65%"实际上意味着"概率在 55%-75% 之间"。如果市场价格是 60%，你的 edge 可能是 +5%，也可能是 -5%。

#### σ_parameter：参数不确定性

**定义**：由于训练样本有限，Platt Scaling 的参数 A 和 B 本身有估计误差。

量化方法——Bootstrap：

```python
def parameter_uncertainty(forecasts, outcomes, n_bootstrap=1000):
    """Bootstrap Platt Scaling 参数的不确定性"""
    n = len(forecasts)
    A_samples, B_samples = [], []
    
    for _ in range(n_bootstrap):
        idx = np.random.choice(n, n, replace=True)
        f_boot = forecasts[idx]
        o_boot = outcomes[idx]
        
        # 拟合 Platt Scaling
        A, B = fit_platt(f_boot, o_boot)
        A_samples.append(A)
        B_samples.append(B)
    
    return {
        'A_mean': np.mean(A_samples), 'A_std': np.std(A_samples),
        'B_mean': np.mean(B_samples), 'B_std': np.std(B_samples),
    }
```

**在交易中的用途**：Bootstrap 得到的参数分布直接给出校准概率的不确定性范围。对于每个新预报，用所有 bootstrap 参数组合计算 1000 个校准概率 → 得到校准概率的分布 → 这个分布的宽度告诉你 edge 估计有多不确定。

#### 预测区间 vs 置信区间

**预测区间（Prediction Interval）**：未来一个新观测落入的范围。包含σ_forecast + σ_instrument + σ_spatial。

**置信区间（Confidence Interval）**：参数估计值的不确定性范围。只包含σ_parameter。

**传递给交易决策层的应该是预测区间，而非置信区间。**

原因：交易决策需要知道"明天的温度可能是多少"（预测区间），而不是"我的模型参数估计得多准"（置信区间）。混用二者会导致严重低估不确定性——置信区间通常比预测区间窄得多，因为它不包含未来观测的固有随机性。

### 2.3 多源融合的相关性处理

#### 误差不独立——这至关重要

GFS、ECMWF、Open-Meteo 的预报误差高度正相关，因为：
1. 它们都从相同的全球观测网络获取初始条件
2. 它们共享一些物理参数化方案（如辐射传输）
3. Open-Meteo 直接使用 GFS 和 ECMWF 的输出

**典型的模型间误差相关系数**：
- GFS vs ECMWF：r ≈ 0.6-0.8（高相关，因为共享初始条件）
- GFS vs HRRR：r ≈ 0.4-0.6（中等相关，HRRR 有更多雷达同化）
- Open-Meteo GFS vs 原始 GFS：r ≈ 0.9+（因为 Open-Meteo 包装了 GFS）

#### 简单平均在正相关误差下低估不确定性

如果两个预报的误差方差都是 σ²，相关系数为 ρ，简单平均的误差方差是：

```
Var(average) = σ²(1 + ρ) / 2
```

当 ρ = 0（独立）时，Var = σ²/2（不确定性减半，好）。
当 ρ = 0.8 时，Var = 0.9σ²（不确定性几乎没有减少——融合两个高度相关的源等于什么都没做）。

**正确的融合不确定性**：

```python
def fused_uncertainty(sigma_sources, correlation_matrix, weights):
    """计算加权融合预报的正确不确定性"""
    # weights: 各源权重向量
    # sigma_sources: 各源的预报误差标准差
    # correlation_matrix: 源间误差相关矩阵
    
    # 构建协方差矩阵
    cov_matrix = np.outer(sigma_sources, sigma_sources) * correlation_matrix
    
    # 融合方差 = w^T @ Cov @ w
    fused_var = weights @ cov_matrix @ weights
    return np.sqrt(fused_var)
```

#### 线性意见池 vs 对数意见池

**线性意见池（Linear Opinion Pool）**：`P_fused = Σ w_i × P_i`

- 适用条件：各源的概率估计可以直接比较（相同尺度、相同含义）
- 优点：简单、直观、权重可解释
- 缺点：当某个源给出极端概率（0.01 或 0.99）时，其他源无法完全覆盖它

**对数意见池（Logarithmic Opinion Pool）**：`P_fused ∝ Π P_i^{w_i}`（归一化后）

- 适用条件：各源的信息是独立的（在你的场景中不成立）
- 优点：对极端概率更敏感（如果一个源认为概率是 0.01，对数池会显著拉低融合结果）
- 缺点：要求独立性假设，在你的场景中会过度压缩不确定性

**选择：线性意见池。** 理由简单——你的数据源不独立，对数池的独立性假设不成立。

#### 源失效的自动检测

当某个数据源在特定条件下系统性失效（如 GFS 在强对流天气时预报偏差暴增）：

**检测方法：滚动 CUSUM（Cumulative Sum）检验**

```python
def detect_source_failure(errors, threshold=3.0):
    """
    CUSUM 检测预报误差的均值偏移。
    当累积偏移超过阈值时，标记该源为"可疑"。
    """
    mean_error = np.mean(errors[-90:])  # 最近 90 天的基线
    cusum = 0
    for e in errors[-14:]:  # 检查最近 14 天
        cusum = max(0, cusum + (abs(e) - mean_error) - 0.5)  # 单侧 CUSUM
        if cusum > threshold:
            return True  # 该源可能已失效
    return False
```

当检测到失效时，不要完全删除该源——而是将其权重降低到最低值（如 0.05），同时增加其他源的权重。完全删除会导致当该源恢复正常时无法利用其信息。

### 2.4 贝叶斯更新框架

#### 市场价格作为先验

将市场价格 P_market 视为先验概率，气象模型给出的概率 P_model 视为似然函数的某种体现。

但这个框架有一个根本性问题：**P_market 和 P_model 不是独立的信息源。** 市场参与者中的 bot 使用与你相同的气象数据，因此 P_market 已经部分反映了 P_model 的信息。直接用贝叶斯更新会对气象信息做双重计数。

**正确的框架**：将 P_market 分解为"来自气象信号的部分"和"来自非气象信息的部分"（市场情绪、流动性效应、行为偏差）：

```
P_market = β × P_weather_signal + (1-β) × P_noise
```

你关心的 edge 来自 P_noise 这部分——即市场因为非信息原因偏离了正确概率。但你无法直接观测 β 和 P_noise。

**实用近似**：

```python
def bayesian_edge(p_model, p_market, model_confidence=0.6, market_informativeness=0.4):
    """
    线性融合模型概率和市场价格。
    model_confidence: 你相信自己模型的权重
    market_informativeness: 你认为市场价格中有多少真实信息
    """
    p_posterior = model_confidence * p_model + (1 - model_confidence) * p_market
    edge = p_posterior - p_market
    return edge, p_posterior
```

`market_informativeness` 应该随流动性动态调整：
- 高流动性市场（成交 > $200K）：market_informativeness = 0.6（市场更可信）
- 中等流动性（$50K-200K）：market_informativeness = 0.4
- 低流动性（< $50K）：market_informativeness = 0.2（市场价格基本是噪声）

#### 模型 vs 市场长期背离的诊断

当你的模型长期认为"市场价格偏低"但实际结算结果显示市场是对的：

**诊断统计量：Calibration-Arbitrage Ratio**

```python
def diagnose_disagreement(model_probs, market_probs, outcomes, window=50):
    """
    如果模型概率比市场概率的 Brier score 更低 → 模型更准
    如果市场概率比模型概率的 Brier score 更低 → 市场更准
    """
    model_brier = np.mean((model_probs[-window:] - outcomes[-window:])**2)
    market_brier = np.mean((market_probs[-window:] - outcomes[-window:])**2)
    
    if market_brier < model_brier * 0.95:
        return "MARKET_IS_RIGHT"  # 你的模型有系统问题
    elif model_brier < market_brier * 0.95:
        return "MODEL_IS_RIGHT"   # 真实 edge
    else:
        return "INDISTINGUISHABLE"  # 样本量不足以区分
```

当连续 50+ 笔交易的诊断结果是 "MARKET_IS_RIGHT"，应该暂停交易并重新审视模型。

---

## 第三部分：小样本统计陷阱

### 3.1 多重比较

#### 问题量化

如果你测试：6 个城市 × 4 个预报时效 × 3 种策略变体 = 72 个假设检验。在 α = 0.05 时，即使没有任何 edge，期望假阳性数 = 72 × 0.05 = 3.6。你几乎肯定会"发现"3-4 个"显著"的策略——全部是噪声。

#### Bonferroni 校正

将显著性水平调整为 α' = α / m，其中 m 是检验数量。对于 72 个检验，α' = 0.05/72 ≈ 0.0007。

**问题**：Bonferroni 太保守了——它假设所有检验完全独立（它们不是，因为不同城市的天气有相关性）。

#### Benjamini-Hochberg（BH）FDR 控制——推荐方法

BH 方法控制的是 False Discovery Rate（在你声称显著的发现中，错误的比例），而非 Family-Wise Error Rate。对于发现导向的研究（如策略搜索），FDR 控制更合适。

```python
from scipy.stats import false_discovery_control

def fdr_filter(p_values, alpha=0.10):
    """
    Benjamini-Hochberg FDR 控制。
    返回哪些假设在 FDR < alpha 下是显著的。
    """
    # 排序 p-values
    sorted_idx = np.argsort(p_values)
    sorted_p = p_values[sorted_idx]
    m = len(p_values)
    
    # BH 阈值
    thresholds = alpha * np.arange(1, m + 1) / m
    
    # 找到最大的 k 使得 p_(k) <= threshold_k
    significant = sorted_p <= thresholds
    if significant.any():
        k = np.max(np.where(significant)) + 1
        return sorted_idx[:k]  # 前 k 个是显著的
    return np.array([])
```

FDR = 0.10 意味着你接受"在我声称有 edge 的策略中，最多有 10% 是假阳性"。对于交易系统来说这是一个合理的容忍度——因为假阳性策略只会导致零 edge（不赚不亏），而不是系统性亏损。

### 3.2 过拟合

#### 参数-样本比例法则

经验法则：**每个模型参数至少需要 10-20 个独立样本。**

Platt Scaling 有 2 个参数 → 需要至少 20-40 个样本。
Isotonic Regression 的有效参数约等于分段数（通常 5-10）→ 需要至少 50-200 个样本。

你每个城市每季度有约 90 天的数据。对于 Platt Scaling，这刚好够用。对于 Isotonic Regression，不够。

#### 时间序列交叉验证

普通 k-fold CV 随机划分数据，会将未来数据放入训练集、过去数据放入测试集——这在时间序列中造成前视偏差。

**正确方法：Expanding Window CV（时间序列 CV）**

```python
def time_series_cv(data, min_train_size=60, step=30):
    """
    训练集不断扩大，测试集始终在训练集之后。
    
    Round 1: train[0:60],  test[60:90]
    Round 2: train[0:90],  test[90:120]
    Round 3: train[0:120], test[120:150]
    ...
    """
    splits = []
    n = len(data)
    for start in range(min_train_size, n - step, step):
        train_idx = list(range(0, start))
        test_idx = list(range(start, min(start + step, n)))
        splits.append((train_idx, test_idx))
    return splits
```

**不要使用 LOO-CV 来做模型选择。** LOO-CV 在时间序列上会严重高估模型性能，因为它让模型看到了测试点前后的所有数据。LOO-CV 适合估计预测误差的期望值，但不适合估计它在新数据上的方差。

#### 有效样本量（Effective Sample Size）

相邻天的温度高度相关（自相关系数 ρ₁ 通常 0.7-0.9）。100 个连续天的温度观测，有效独立信息量远少于 100。

```
ESS ≈ n × (1 - ρ₁) / (1 + ρ₁)
```

对于 ρ₁ = 0.8，n = 100：ESS ≈ 100 × 0.2 / 1.8 ≈ 11。

**即 100 天的数据只相当于 11 个独立样本。** 这解释了为什么你的 LOO 交叉验证没有显示动态权重优于等权——你的有效样本量根本不够区分。

对于预报误差的序列（而非温度本身），自相关通常较低（ρ₁ ≈ 0.3-0.5），ESS 约为 n/2 到 n/3。但这仍然意味着你需要 2-3 倍于你直觉认为的样本量。

### 3.3 非平稳性

#### 分布漂移检测

温度分布在以下时间尺度上变化：
- 季节性（周期性，可预测）
- 年际变化（ENSO、NAO 等，部分可预测）
- 长期趋势（气候变化，约 +0.02°C/年）
- 城市化（局地，约 +0.01-0.05°C/年）

**检测方法：Page-Hinkley 检验**（滚动均值变化的快速检测）

```python
class PageHinkley:
    def __init__(self, delta=0.01, threshold=50):
        self.delta = delta
        self.threshold = threshold
        self.sum = 0
        self.min_sum = float('inf')
        self.count = 0
        self.mean = 0
        
    def update(self, value):
        self.count += 1
        self.mean += (value - self.mean) / self.count
        self.sum += value - self.mean - self.delta
        self.min_sum = min(self.min_sum, self.sum)
        
        return (self.sum - self.min_sum) > self.threshold  # True = drift detected
```

#### 历史数据的"保质期"

对于温度校准数据：
- **同一季节的去年数据**：有效（气候学年际变化小于预报误差）
- **不同季节的最近数据**：无效（季节性差异远大于你想检测的信号）
- **两年以上的同季节数据**：有效但需要降权（气候变化和城市化的累积效应）

**滚动窗口 vs 指数加权**：

选择：**指数加权，半衰期 = 365 天。** 理由是指数加权不需要硬截断（"180 天以前的数据无效"），而是平滑地降低旧数据的影响。一年前的数据权重为 50%，两年前为 25%——这对于捕捉缓慢的气候漂移是合理的。

```python
def exponential_weights(dates, reference_date, half_life_days=365):
    days_ago = (reference_date - dates).days
    return np.exp(-np.log(2) * days_ago / half_life_days)
```

### 3.4 置信区间的正确构建

#### 小样本下不要用正态近似

当 n < 30 时，正态近似的覆盖率严重不足。对于比例估计（如胜率），Wilson 区间比 Wald 区间（p ± z√(p(1-p)/n)）更可靠：

```python
from statsmodels.stats.proportion import proportion_confint

# Wilson 区间（推荐用于小样本比例估计）
lo, hi = proportion_confint(count=wins, nobs=total, alpha=0.05, method='wilson')
```

#### Block Bootstrap——时间序列的正确 Bootstrap

普通 Bootstrap 假设样本独立同分布（i.i.d.）。时间序列数据违反独立性假设。

**Block Bootstrap** 通过重采样连续的数据块（而非单个数据点）来保留序列相关结构：

```python
def block_bootstrap(data, block_length, n_bootstrap=1000, statistic=np.mean):
    n = len(data)
    n_blocks = int(np.ceil(n / block_length))
    results = []
    
    for _ in range(n_bootstrap):
        # 随机选择起始点
        starts = np.random.randint(0, n - block_length + 1, n_blocks)
        # 拼接数据块
        sample = np.concatenate([data[s:s+block_length] for s in starts])[:n]
        results.append(statistic(sample))
    
    return np.array(results)

# 使用方法
# block_length ≈ 1 / (1 - ρ₁)，对于 ρ₁ = 0.5，block_length ≈ 2
# 对于 ρ₁ = 0.8，block_length ≈ 5
```

**block_length 的选择**：经验法则是 `1 / (1 - ρ₁)`，其中 ρ₁ 是 lag-1 自相关。对预报误差序列（ρ₁ ≈ 0.3-0.5），block_length = 2-3。对温度序列（ρ₁ ≈ 0.7-0.9），block_length = 5-10。

#### "不确定性太大，不应下注"的阈值

当 edge 的 Bootstrap 95% CI 跨越零时（即下界 < 0，上界 > 0），你的估计"有 edge"这个判断在统计上不显著——此时不应下注。

更保守的规则：**只有当 Bootstrap 90% CI 的下界 > 2% 时才下注。** 这意味着你至少有 90% 的置信度认为 edge > 2%——扣除交易成本后仍为正。

---

## 第四部分：数据存储的统计设计

### 4.1 充分统计量

对于每条 ECMWF ENS 预报记录，完整的统计充分量需要存储：

```sql
CREATE TABLE ensemble_summary (
    id INTEGER PRIMARY KEY,
    forecast_id INTEGER REFERENCES forecasts(id),
    -- 分布参数（足以重建近似分布）
    mean REAL NOT NULL,
    std REAL NOT NULL,
    skewness REAL,           -- 通常可省略，但见下文
    kurtosis REAL,           -- 通常可省略
    -- 关键分位数（足以重建经验分布的主体）
    p10 REAL,                -- 10th percentile
    p25 REAL,                -- 25th percentile
    median REAL,
    p75 REAL,                -- 75th percentile
    p90 REAL,                -- 90th percentile
    min_value REAL,
    max_value REAL,
    -- bin 概率（直接可用于交易决策）
    bin_probabilities TEXT,  -- JSON: {"46-47": 0.02, "48-49": 0.05, ...}
    -- 双峰检测
    is_bimodal BOOLEAN DEFAULT FALSE,
    bimodal_modes TEXT       -- JSON: [{"center": 40, "weight": 0.6}, ...]
);
```

**偏度和峰度是否需要存储？**

偏度（skewness）：当 |skewness| > 0.5 时，正态假设不成立，尾部概率估计会有显著偏差。应存储并在概率计算中使用（切换到偏态正态分布）。

峰度（kurtosis）：对于 51 个样本，峰度的估计误差太大（SE ≈ 0.67），不值得存储——你无法区分"真的尖峰/扁平"和"采样噪声"。

**实用选择**：存储 mean, std, skewness, 5 个分位数（p10, p25, median, p75, p90）, 和 min/max。这 10 个数字足以用偏态正态分布 + 分位数约束来重建完整的概率分布，而不需要存储 51 个原始成员值。

### 4.2 数据质量标记体系

```sql
CREATE TABLE data_quality (
    record_id INTEGER PRIMARY KEY REFERENCES forecasts(id),
    -- 源可靠性
    source_reliability REAL,  -- 0-1，基于过去 30 天该源的缺失率和异常率
    -- 异常检测
    anomaly_score REAL,       -- Isolation Forest 的异常分数，> 0.7 标记为可疑
    anomaly_type TEXT,        -- 'none', 'spike', 'flatline', 'missing', 'timezone_error'
    -- 影响力
    leverage REAL,            -- 该数据点对校准模型的 Cook's Distance
    -- 综合质量标签
    quality INTEGER DEFAULT 0 -- 0=可用, 1=可疑需人工检查, 2=确认无效不使用
);
```

**异常检测方法选择**：

- **IQR 方法**（简单但对非正态分布敏感）：适合温度数据（近似正态）
- **Grubbs 检验**（假设正态分布）：适合单个异常值检测
- **Isolation Forest**（非参数，不假设分布）：适合多变量异常检测

**推荐**：对单变量温度数据使用 IQR（简单可靠）。对多变量模式异常（如"温度不异常但该站在该时间不应报告这个值"）使用 Isolation Forest。

```python
def iqr_anomaly(value, recent_values, multiplier=2.5):
    q1, q3 = np.percentile(recent_values, [25, 75])
    iqr = q3 - q1
    lower = q1 - multiplier * iqr
    upper = q3 + multiplier * iqr
    return value < lower or value > upper
```

### 4.3 版本控制与数据溯源

#### 历史修订的处理

气象机构修订历史数据的场景：
- NOAA 的 GHCN-Daily 质量控制流程可能在发布后数天甚至数月修改观测值
- Wunderground 的 "finalized" 数据可能在几小时后被修正

**规则：永远不覆盖旧数据。** 使用版本号追踪修订：

```sql
-- 原始值
INSERT INTO observations VALUES (1, 'KLGA', '2026-03-29', 53.0, 1, '2026-03-30 02:00');
-- 修订值（新版本，保留旧版本）
INSERT INTO observations VALUES (2, 'KLGA', '2026-03-29', 54.0, 2, '2026-03-31 08:00');

-- 查询时使用最新版本
SELECT * FROM observations 
WHERE station='KLGA' AND date='2026-03-29' 
ORDER BY data_version DESC LIMIT 1;

-- 但回测时使用决策时刻可用的版本
SELECT * FROM observations 
WHERE station='KLGA' AND date='2026-03-29' 
AND available_at <= @decision_time
ORDER BY data_version DESC LIMIT 1;
```

#### 交易决策的数据溯源

每笔交易记录应包含：

```sql
CREATE TABLE trade_decisions (
    trade_id INTEGER PRIMARY KEY,
    -- 交易信息
    market_id TEXT,
    side TEXT,
    size REAL,
    price REAL,
    timestamp TIMESTAMP,
    -- 数据溯源
    forecast_ids TEXT,        -- JSON: 使用了哪些预报记录的 ID
    observation_ids TEXT,     -- JSON: 使用了哪些观测记录的 ID
    calibration_model_version TEXT,  -- 校准模型的版本哈希
    -- 决策参数
    model_probability REAL,
    market_probability REAL,
    edge REAL,
    kelly_fraction REAL,
    confidence_interval_lower REAL,
    confidence_interval_upper REAL
);
```

**重新评估（Restatement）的规则**：当校准模型被重新训练后，不要修改历史交易的 edge 计算。历史交易的评估应永远使用当时的模型版本和当时可用的数据。否则你会创造一个"如果我当时知道现在知道的"的虚假评估——这就是后视偏差。

正确做法：在新模型下运行前向（out-of-sample）验证。只有新模型在新数据上持续优于旧模型时，才切换。
