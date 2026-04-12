# 数学 / 统计升级方案

## 1. 当前数学链条的真实优点

Zeus 当前已经有几个难得的优点：

### 1.1 不是 point forecast，而是 settlement-aware distribution

当前 ENS 不是直接输出一个点温度，而是先经过 member -> instrument noise -> settlement rounding -> bin probability。
这比“回归一个高温然后 hard bucketize”要强很多，因为它直接站在 settlement semantics 上建模。

### 1.2 不是 naive calibration，而是带 lead 项的 Extended Platt

当前形式：

\[
P_{\text{cal}} = \sigma(A \cdot \operatorname{logit}(P_{\text{raw}}) + B \cdot \text{lead\_days} + C)
\]

这已经比普通 Platt 强一层，因为它承认了 lead time 对概率可信度的系统影响。

### 1.3 不是盲信 model，而是 model / market fusion

当前 posterior 近似形式：

\[
P_{\text{post}} \propto \alpha P_{\text{cal}} + (1-\alpha) P_{\text{market}}
\]

这为后续做 meta-learning 留下了很好的接口。

### 1.4 Day0 已经抓住了“observed high is a hard floor”

这是极其重要的物理约束。Day0 继续升级时，必须保留这条 contract。

---

## 2. 下一阶段最关键的统计升级

## 2.1 从 pair-row 统计，升级到 decision-group 统计

当前每个 settlement / forecast snapshot 往往生成多个 bin-level pair rows。
因此真正独立的样本单位应该是：

\[
g = (city, target\_date, forecast\_available\_at, source/model\_version)
\]

而不是单独的 `(city, target_date, range_label)` row。

### 必须改的地方

- calibration maturity gate
- bootstrap resampling unit
- cross-validation split unit
- OOS reliability / ECE / Brier 汇总口径

### 推荐的 effective sample 定义

最简单先这样：

\[
n_{\text{eff}} = \#\{g\}
\]

如果未来希望考虑 group 内不完整 bins，可再引入 group-weight：

\[
w_g = \frac{1}{n_{\text{rows in group}}}
\]

并在拟合时对每条 bin-row 使用 `sample_weight = w_g`。

---

## 2.2 从 hard fallback，升级到 partial pooling

当前 fallback 是离散跳转：

- city/cluster/season
- season-only
- global
- uncalibrated

这很实用，但统计上不够平滑。

### 推荐升级：empirical-Bayes partial pooling

把每个 bucket 的参数写成：

\[
\theta_b = (\beta_{0,b}, \beta_{1,b}, \beta_{2,b})
\]

对应：

\[
\operatorname{logit}(P_{\text{cal}}) =
\beta_{0,b}
+ \beta_{1,b}\operatorname{logit}(P_{\text{raw}})
+ \beta_{2,b}\text{lead\_days}
\]

然后向 parent bucket 收缩：

\[
\hat{\theta}^{\text{shrunk}}_b
=
\lambda_b \hat{\theta}^{\text{local}}_b
+
(1-\lambda_b)\hat{\theta}^{\text{parent}}
\]

其中：

\[
\lambda_b = \frac{n_{\text{eff},b}}{n_{\text{eff},b} + \tau}
\]

优点：

- 小样本 bucket 不会完全丢掉 local identity
- 大样本 bucket 仍然保留本地特征
- 可以比 hard fallback 更稳定地覆盖 45 城

---

## 2.3 评估从 insample Brier，升级到 blocked OOS score suite

当前至少需要以下 OOS 指标：

- Brier
- Log loss
- ECE（Expected Calibration Error）
- reliability slope / intercept
- tail-bin calibration error
- CRPS（如果你转向连续分布或有离散累积分布）

### 为什么必须 blocked OOS

天气和市场都有明显时间相关性。随机打散 split 会高估模型。

建议：

- 按时间 forward split
- 或 rolling origin evaluation
- resampling / bootstrap 也按 decision group block 做

---

## 2.4 Day0：从 heuristic seam 升级到 two-stage residual model

定义：

\[
R = \max_{t' \le t} \text{observed\_temp}(t')
\]

\[
Y = \max(0, S - R)
\]

其中 `S` 是最终 settlement high，`Y` 是“从当前时刻开始，今天还能再涨多少”。

### 两阶段建模

先建：

\[
\Pr(Y > 0 \mid x)
\]

再建：

\[
\mathbb{E}[Y \mid Y > 0, x]
\]

最后合成 remaining-upside distribution。

### 好处

- 保留 hard floor：`final_high >= running_max`
- 比直接估一个高温更符合 day0 physics
- 可自然吸收 diurnal / solar / freshness / spread / obs source 等特征

### 推荐特征

- local_hour
- running_max
- temp_current
- delta_rate_per_h
- obs_age_minutes
- daylight_progress
- post_peak_confidence
- ensemble remaining-tail quantiles
- ensemble spread
- city / season / cluster

---

## 2.5 Bias / uncertainty：从均值修正升级到 distributional correction

现在的 bias correction 已经有接口，但可以更进一步：

\[
Y \mid \mu_{\text{ens}}, s_{\text{ens}}
\sim
\mathcal{N}(a + b\mu_{\text{ens}}, c + d s_{\text{ens}}^2)
\]

再经过 settlement rounding 映射回 bin probabilities。

这是 EMOS 风格的最小升级版。

### 实际上你会得到什么

- 不只是 bias 更正
- 还会把 “ensemble spread 到底对应多大的真实误差” 学出来
- alpha/fusion 也会因此更稳，因为 model uncertainty 变得更真实

---

## 2.6 Selection：从“通过前筛的 edges 做 FDR”，升级到“全家族假设做 FDR”

当前最重要的不是 BH 算法本身，而是**family 定义**。

一个更合理的 family 至少应覆盖：

\[
\text{family} =
(\text{cycle}, \text{city}, \text{target\_date}, \text{mode}, \text{all tested bins}, \text{both directions})
\]

只有这样，q-value 的解释才是真正成立的。

### 最小升级

- 记录每个 cycle 中所有被测试的 hypothesis
- 对全量 tested hypotheses 做 p-value / q-value
- 然后再做 selection

### 更高级升级

如果你后续想继续扩展，可以再上：

- hierarchical FDR
- sequential alpha spending
- online FDR（如果你把 cycle 看成流式决策）

---

## 2.7 Correlation：从 heuristic matrix 升级到 shrinkage covariance

当前可以分两层：

### 层 A：settlement anomaly correlation

\[
a_{c,d} = T_{c,d} - \mathbb{E}[T_{c,\text{month}(d)}]
\]

先用 city daily anomaly 做一个稳定的气候共振矩阵。

### 层 B：forecast error correlation

\[
e_{c,d,\ell,s} = \hat{T}_{c,d,\ell,s} - T_{c,d}
\]

按 source / lead 再估一个 error correlation matrix。

最后做 shrinkage：

\[
\Sigma_{\lambda} = (1-\lambda)\hat{\Sigma} + \lambda D
\]

其中 \(D\) 是对角矩阵或 cluster prior。

---

## 2.8 Execution surprise：你其实已经有足够的数据做 edge half-life

当前 `token_price_log` 400 万+，这是一个很大的额外机会。

你可以定义：

- 当前 edge 是否在 5 / 15 / 30 分钟后仍存在
- spread / depth / price jump 对 fill quality 的影响
- 不同 strategy / entry_method 的 slippage profile

这会让 Zeus 从“会选边”升级到“会选择更好的进入时机”。

---

## 3. 先后顺序

正确顺序不是“先所有城市全上新模型”。

正确顺序应该是：

1. **P0 probability truth surface**
2. **P1 grouped calibration + blocked OOS**
3. **P2 day0 residual model**
4. **P3 bias / uncertainty EMOS**
5. **P4 selection family + correlation**
6. **P5 execution microstructure**
7. **P6 promotion / reporting**

因为如果 P0/P1 没做好，后面任何 fancy math 都会缺比较基线。
