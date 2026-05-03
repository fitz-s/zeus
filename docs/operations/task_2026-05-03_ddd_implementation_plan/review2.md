. Executive adversarial verdict
0.1 结论是否可信

当前 DDD Phase 1 结论 不能进入 live sizing / entry / exit / promotion report。

它可以作为：

SHADOW_ONLY
diagnostic telemetry
report cohort label
source/time/coverage audit trigger

但不能作为：

live Kelly multiplier
live entry eligibility
live exit rule
live P&L promotion evidence
0.2 当前最强、最弱组件
组件    对抗性判断
最强    “观察面覆盖不足应进入风险控制”这个概念本身。外部现实支持：机场气象站/ASOS/AWOS 有连续和特殊观测机制，但缺测可能来自 QC、设备、观测者、供应商等非随机原因；站点元数据和迁站会改变观测意义。
最弱    §2.4 discount curve。脚本把 HIGH-window coverage shortfall 用到 HIGH+LOW winning-row Brier；非零 shortfall bins 极稀疏；没有 live executable EV；9% cap 没有足够实证基础。
最大计算风险    per_day_coverage() 只从存在的 observation_instants_v2 行里 COUNT(DISTINCT CAST(local_hour AS INTEGER))，没有 expected-slot calendar；0 行窗口/整日缺失会被从样本中消失，而不是计为 0。WU client 本身在没有 raw obs 的小时不会 emit row，这使 expected-slot left join 成为必要条件。
最大 live-money 风险    DDD 被实现成 2–9% Kelly discount，而真实失败场景需要 gate/block：station mismatch、source contract drift、current-day critical-hour outage、HIGH/LOW identity corruption。
E8 最大风险    live Platt loader 选择 active/latest verified calibrator，没有 frozen-as-of/snapshot pin；bulk refit 会直接进入 live serving。load_platt_model_v2 的 repo 事实支持这一点：它按 metric/cluster/season/data_version/input_space/authority 选 active verified，并 ORDER BY fitted_at DESC LIMIT 1。
0.3 Immediate stop / rerun / shadow verdict
DDD implementation: SHADOW_ONLY
hard floors: REQUIRES_RERUN
k=0: ACCEPT_AS_HEURISTIC_ONLY, not live-safe proof
sigma_window=90: REQUIRES_RERUN
discount curve: REJECT_FOR_LIVE
0.35 absolute kill: REQUIRES_REDEFINITION
HIGH→LOW transfer: REJECT_FOR_LIVE
E8 live serving path: REQUIRES_REDEFINITION before any live DDD/Platt audit claim
1. Faithful reconstruction of current DDD plan

本节先忠实重建，不批判。

1.1 Plan 中的 DDD 目标对象

DDD 目标是给天气市场里的 oracle/weather-data density 风险加一个折扣。核心思想：

当 settlement-relevant weather observation surface 不够密、不够稳定、
或低于 city-specific hard floor 时，模型概率/交易 sizing 需要折扣。
1.2 公式结构

从 PLAN.md 和 Phase 1 scripts 恢复出的 v1 结构是：

coverage(city, target_date)
    = directional_window_hours_observed / expected_window_hours

floor(city)
    = HARD_FLOOR_FOR_SETTLEMENT[city]

sigma(city)
    = rolling or train-window stddev of coverage

shortfall(city, date)
    = max(0, floor(city) - coverage(city, date) - sigma(city, rolling_window))

small_sample_multiplier
    = 1 + k / sqrt(N)

discount
    = curve(shortfall * small_sample_multiplier)

但 Phase 1 scripts 的实际实现更具体：

directional window = historical_peak_hour ± 3
coverage = COUNT(DISTINCT CAST(local_hour AS INTEGER)) / 7
source = 'wu_icao_history'
data_version = 'v1.wu-native'
track = HIGH only for coverage
1.3 Phase 1 experiments
Section    Script(s)    Intended result
§2.1    p2_1_hard_floor_calibration.py, p2_1b_floor_sensitivity.py, p2_1c_sigma_aware_floor.py    per-city hard floors
§2.2    p2_2_k_validation.py    validate/reject k in 1+k/sqrt(N)
§2.3    p2_3_sigma_window_acf.py    validate sigma_window, choose 90 days
§2.4    p2_4_curve_breakpoints.py    validate discount curve breakpoints
§2.5    not executed    small sample floor
§2.6    not executed    peak window radius
1.4 Current final floors

From p2_1_FINAL_per_city_floors.json:

Floor    Cities
0.35    Jakarta
0.45    Lagos
0.50    Lucknow
0.55    Shenzhen
0.85    43 WU cities, including Denver/Paris
null    Hong Kong, Istanbul, Moscow, Tel Aviv
1.5 Operator rulings
Ruling    Reconstructed meaning
Denver/Paris forced to 0.85    asymmetric-loss policy; missing one critical outage is worse than a few false positives
Lagos stays 0.45    high σ treated as infrastructure reality; avoid blocking Lagos permanently
Null no-WU cities    DDD does not apply because no WU primary surface
1.6 §2.2 current conclusion
Measure    Result
Brier all rows    weak positive signal
Brier winning rows    contradicts hypothesis; script-level fit gives near-zero/negative signal
ECE    borderline weak support
train/test stability    fails
conclusion    FAIL
recommendation    k=0 in v1
1.7 §2.3 current conclusion
Item    Result
white-noise hypothesis    fails
coverage drops    cluster
recommended σ window    90 days
Lagos/Shenzhen    high σ, σ-band can absorb anomalies
catastrophic catch    rely on §7 absolute kill at coverage <0.35
1.8 §2.4 current curve
Shortfall    Discount
0    0%
(0, 0.10)    0–2%
[0.10, 0.25)    2–5%
[0.25, 0.40)    5–8%
>=0.40    9% cap
2. Reality-object mapping table
Plan variable    Actual script/database field    Intended object    Actual measured object    Mismatch risk    Affected conclusion
daily_cov    not central in Phase 1; fallback all hours if no peak    required day observation completeness    count of existing local-hour rows    absent rows invisible; fixed denominator    all
directional_cov    COUNT(DISTINCT CAST(local_hour AS INTEGER))/7    settlement-relevant high/low critical-window availability    existing WU rows in HIGH historical peak ±3    HIGH-only; no expected slots; no authority/station filter    floors, σ, curve
hard_floor    final JSON    minimum acceptable coverage    mixture of stats + operator policy    falsifiability broken    §2.1
sigma_train    statistics.stdev(train_vals)    normal coverage volatility    volatility of observed-row coverage values only    missing zero days absent; high σ normalizes bad infra    §2.1/§2.3
sigma_window    90 days conclusion    rolling stability horizon    chosen after lag-14 ACF/probe-city analysis    extrapolation; overfit    §2.3
shortfall    max(0, floor-cov-sigma)    data-risk deficit    HIGH-window deficit with σ subtracted    σ can erase true outage; LOW uses HIGH shortfall    §2.4
N    count of winning calibration rows or per-bin rows    independent evidence count    repeated lead/bin rows; not decision-group count    inflated N    §2.2
Brier all rows    conclusion extra result    calibration error    dominated by losing bins/non-live rows    not live EV    §2.2
winning Brier    outcome=1, (1-p_raw)^2    realized winning-bin accuracy    post-outcome selected row set    no live analogue    §2.2
ECE    conclusion extra result    reliability    underpowered bin statistic    weak signal    §2.2
target_date    observation_instants_v2.target_date, calibration_pairs_v2.target_date    city-local settlement date    likely local date in obs table, but scripts trust existing field    if row bad or source drift, no catch    all
utc_timestamp    observations    physical instant    existing row identity    no expected missing slots    coverage
local_hour    CAST(local_hour AS INTEGER)    local hour slot    integer-rounded/cast existing local hour    fractional/ambiguous/DST edge lost    coverage
source    source='wu_icao_history'    settlement source    WU source only    no-WU null; fallback/source_role ignored    floors
station_id    not filtered in scripts    physical station    not used    station migration invisible    floors/σ
authority    not filtered in coverage scripts    verified observation row    ignored    unverified/fallback possible    all
data_version    v1.wu-native    training surface version    fixed string    no frozen hash; E8 reload concern    all
temperature_metric    not filtered for coverage; both metrics in §2.4 errors    HIGH/LOW identity    coverage HIGH-only; errors HIGH+LOW    category error    curve
forecast_available_at    not used in §2.2 regression    causal availability    ignored    time leakage not tested    k
fitted_at    live Platt loader uses latest    active model version time    mutable bulk-refit timestamp    no frozen serving    E8/live

Repo schema supports much richer identity fields than Phase 1 scripts used: temperature_metric, observation_field, source, timezone_name, local_timestamp, utc_timestamp, DST flags, station_id, authority, data_version, training_allowed, causality_status, and source_role exist in v2 surfaces.

3. Script-level forensic audit
3.1 p2_1_hard_floor_calibration.py
Reads
state/zeus-world.db
observation_instants_v2
Filters
city = ?
source = 'wu_icao_history'
data_version = 'v1.wu-native'
target_date between train/test dates
CAST(local_hour AS INTEGER) IN window_hours
Grouping
GROUP BY target_date
Coverage denominator
coverage = hrs_in_window / n_target
n_target = 7 for peak_hour ± 3
Hidden calculation branch

GROUP BY target_date only returns dates with at least one matching row. If a date has zero rows in the 7-hour window, the date is absent rather than coverage=0.

Because WU hourly ingestion skips empty buckets instead of emitting missing rows, this is not theoretical. The WU client emits one hourly observation only for buckets containing at least one raw observation and skips gaps.

Missing filters

No filter on:

authority
station_id
timezone_name
temperature_metric
observation_field
training_allowed
causality_status
source_role
provenance_json
Verdict
POSSIBLE_FIELD_MISMATCH
POSSIBLE_TIME_MISMATCH
POSSIBLE_SAMPLE_BIAS
COMPUTES_PROXY_ONLY

It computes “observed WU HIGH-window row density on dates with at least one matching row,” not “settlement-relevant expected-slot coverage.”

3.2 p2_1b_floor_sensitivity.py
Reads

Same basic coverage object.

Candidate floors
[0.35, 0.50, 0.65, 0.75, 0.85, 0.95]
Recommendation logic

Uses recommended floor grid:

0.35, 0.40, ..., 0.85

Selects a floor under train false-positive constraint if possible, but if the lowest floor still exceeds the nominal 1% FP target, it still returns 0.35.

Concrete issue

Results show:

Lagos 0.35 train_below_pct ≈ 1.64%
Jakarta 0.35 train_below_pct ≈ 1.09%

Yet both can still be recommended at low floors because 0.35 is the minimum allowed fallback.

Verdict
COMPUTES_PROXY_ONLY
POSSIBLE_POLICY_FLOOR_MASKING
3.3 p2_1c_sigma_aware_floor.py
Logic
sigma = statistics.stdev(train_values)
sigma_aware_trigger = coverage < floor - sigma
Hidden branch

For high-σ cities, the effective trigger becomes very low:

Lagos floor 0.45, σ ≈ 0.178
effective trigger ≈ 0.272

That matches the conclusion’s “catch near-total breakdown only” behavior.

Adversarial issue

High σ is not necessarily “reality to preserve.” It can be “bad infrastructure normalized into baseline.”

Verdict
COMPUTES_PROXY_ONLY
LIVE_MONEY_RISK
3.4 p2_2_k_validation.py
Reads
calibration_pairs_v2
Actual metric in script

The actual code path uses:

WHERE authority='VERIFIED'
  AND outcome = 1

and computes:

brier = mean((1 - p_raw)^2)

per city/metric bucket.

Important mismatch

The script does not refit Platt on train and test. It does not evaluate p_cal. It uses p_raw, winning rows, and row counts.

The Platt code in repo supports stronger causal bootstrap semantics, including decision_group_ids, and refuses fitting with too little sample. Phase 1 k script does not use those protections.

Header/comment mismatch

The script commentary references sample threshold around >=100, but code excludes buckets with:

if n_tr < 30 or n_te < 30:
    continue
Verdict
COMPUTES_PROXY_ONLY
POSSIBLE_LEAKAGE
POSSIBLE_SAMPLE_BIAS

It can reject this exact k regression, but cannot prove small-sample risk is gone.

3.5 p2_3_sigma_window_acf.py
Reads

Same observed-row coverage object.

Probe cities

Fixed 10-city set:

Tokyo, Singapore, Wellington, Denver, NYC,
Lagos, Shenzhen, Jakarta, Lucknow, Houston
Actual ACF

Computes ACF up to lag 14. The conclusion recommends 90 days.

Hidden branch

Lag-14 ACF cannot, by itself, prove a 90-day σ window. It can motivate a longer memory hypothesis, but 90 is policy/model choice.

Verdict
COMPUTES_PROXY_ONLY
OVERFIT_RISK
REQUIRES_RERUN
3.6 p2_4_curve_breakpoints.py
Reads
p2_1_FINAL_per_city_floors.json
observation_instants_v2
calibration_pairs_v2
Coverage side

Uses HIGH historical peak-hour ±3 coverage for city/date.

Error side

Loads winning rows from calibration_pairs_v2 for:

temperature_metric IN ('high','low')
outcome = 1
authority = 'VERIFIED'

Then uses median p_raw across rows/leads for each (target_date, metric).

Critical category error

The script applies HIGH-window coverage shortfall to LOW winning-row Brier.

This is the most concrete Phase 1 script-level wrong-object computation.

Non-independent counts

Global exact-zero bin has N=7371, far larger than independent city-date count because rows/leads/metrics inflate evidence.

Verdict
POSSIBLE_FIELD_MISMATCH
HIGH_LOW_CATEGORY_ERROR
COMPUTES_PROXY_ONLY
REJECT_FOR_LIVE
4. E8 audit correction: what I got wrong, and what is true now
4.1 Previous error

上一轮我没有读取 ZIP 内 E8_audit，所以把 E8 处理成了缺失/待查。这是错误。

4.2 What E8 actually establishes

ZIP 的 E8 synthesis establishes 三层事实：

Layer    E8 finding
L1 raw observations    observation_instants_v2 WU primary rows 47/47 WU cities，约 943k rows，2026-05-02 bulk wipe/reload
L2 calibration pairs    calibration_pairs_v2 100/102 city-metric bins，约 97.9% rows bulk-regenerated
L3 Platt models    active platt_models_v2 387/399 fit on 2026-04-29 mass-refit wave
4.3 E8 does not mean “bulk write = leakage” by itself

E8 later files are more nuanced:

pair-generation may be causal-safe
calibrator fitting may be production-intended all-settled fit
forecast_available_at / target_date fields may preserve intrinsic causality

So the correct statement is:

Bulk regeneration destroys recorded_at/imported_at/fitted_at as point-in-time evidence.
It does not automatically prove target_date/forecast_available_at leakage.
But it makes Phase 1 train/test and live-serving claims invalid unless they freeze
intrinsic availability fields, model versions, and data_version/model_key snapshots.
4.4 E8 live-serving exposure

Repo load_platt_model_v2 selects active verified Platt model by metric/cluster/season/data_version/input_space, then orders by latest fitted_at. It has no frozen-as-of argument in the observed code path.

Therefore:

future mass refit under same data_version can become live immediately
current active calibrators may reflect bulk-refit surface
Phase 1 validation cannot claim live-stable behavior without model freeze
4.5 E8 implication for DDD
DDD section    E8 effect
§2.1 floors    coverage uses current rewritten observation surface; not point-in-time proof
§2.2 k    train/test validation not clean unless Platt/features/pairs are regenerated within train split
§2.3 σ    coverage history may be content-idempotent but write-time evidence unusable
§2.4 curve    winning-row probabilities may be full-surface/current probabilities, not live-frozen probabilities
live rollout    cannot use latest active model selection as controlled evidence
4.6 E8 verdict
E8_bulk_regeneration = REVIEWED
E8_as_simple_leakage_claim = REQUIRES_PRECISION
E8_as_live_serving_freeze_failure = HIGH_SEVERITY
E8_as_reason_to_rerun Phase 1 with frozen intrinsic fields = YES
5. §2.1 hard floor adversarial audit
5.1 Statistical basis

The hard floors are not purely statistical. They are:

coverage proxy results
+ σ-aware adjustment
+ operator overrides
+ catastrophic-day narrative
+ policy defaults
5.2 Observed-row denominator failure

The script’s n_days exposes the problem. Example:

2025-07-01 to 2025-12-31 = 184 calendar days
Denver train n_days = 180
Paris train n_days = 184
Lagos train n_days = 183

If a city/date has zero matching rows in the directional window, it may be absent rather than counted as 0/7.

This contaminates:

min coverage
p05
false-positive rate
catastrophic-day count
recommended floor
sigma
5.3 Operator overrides
Override    Adversarial classification
Denver/Paris 0.85    policy, not statistical calibration
Lagos 0.45    policy/infrastructure prior; could be boiled-frog degradation
stable default 0.85    cohort heuristic
null no-WU    DDD applicability gap
5.4 Denver/Paris

Denver/Paris are especially important because the conclusion explicitly says the σ-aware script would place them near 0.60, but operator ruling forces 0.85.

That is not wrong as risk policy. It is wrong if represented as statistically proven.

Paris has a concrete repo-level source-contract caution: current source-validity documentation records that active Paris markets resolved via WU LFPB while config still used LFPG, and new Paris entries were to remain blocked pending conversion evidence. A DDD floor over a stale station surface can be dangerously confident.

5.5 Lagos

Lagos 0.45 is the clearest boiled-frog risk.

The Phase 1 conclusion treats σ≈0.178 as infrastructure reality. But a high-variance data surface may be exactly what DDD should punish, not normalize.

Adversarial classification:

Lagos 0.45 = REQUIRES_REDEFINITION

Required rerun:

station/source segmentation
change-point analysis
wet/dry season split
HIGH/LOW split
expected-slot zero-fill
source-role filter
external outage/event overlay
5.6 Null cities

Hong Kong / Istanbul / Moscow / Tel Aviv null because no WU primary surface.

Repo source routing does not mean these cities lack source truth; it means they are separate tiers. The tier resolver explicitly encodes WU, Ogimet/NOAA, and HKO native routes, and states the structural principle: settlement source is observation source, with no generic grid escape.

Therefore null is safe only if:

DDD_STATUS = NOT_APPLICABLE_FOR_WU_DDD
LIVE_STATUS = separately gated by source-tier DDD/readiness

It is unsafe if:

floor=null -> no discount -> live OK
5.7 HIGH vs LOW

§2.1 metadata says track is HIGH peak-hour ±3.

Any application to LOW is rejected.

LOW failure modes:

overnight window
cross-midnight local date
DST fall-back/spring-forward
different station reporting cadence
different market tail behavior
different calibration maturity
5.8 Catastrophic 15/15 claim

The conclusion claims 15/15 catastrophic days detected. Adversarial concerns:

The catastrophic days appear partly defined by low coverage after the fact.
Any floor ≥0.35 catches days below 0.35, making the claim partly tautological.
Dates with no matching rows may be absent and therefore not counted.
It does not prove false negatives on future critical-window outages.
5.9 Verdict
§2.1 hard floors = REQUIRES_RERUN
Denver/Paris 0.85 = ACCEPT_AS_HEURISTIC_ONLY
Lagos 0.45 = REQUIRES_REDEFINITION
stable 0.85 = ACCEPT_AS_HEURISTIC_ONLY
null no-WU = REQUIRES_REDEFINITION
HIGH→LOW use = REJECT_FOR_LIVE
6. §2.2 k multiplier adversarial audit
6.1 What the failure actually proves

It proves only:

The tested regression between winning-row Brier proxy and 1/sqrt(N)
does not support this k multiplier robustly.

It does not prove:

small sample risk is fake
N is correctly defined
live risk declines with no small-sample penalty
Brier/ECE are sufficient trading-risk metrics
6.2 Script metric mismatch

Actual script:

outcome = 1 only
error = (1 - p_raw)^2
bucket = city × temperature_metric
N = count winning rows

This is not:

Platt-calibrated live probability error
selected trade error
decision-family loss
executable EV
tail risk
6.3 E8 interaction

E8 says production active calibrators were mass-refit and live selection has no frozen snapshot. Therefore a clean k test must:

split by target_date or forecast_available_at
rebuild calibration pairs inside train only
fit Platt on train only
evaluate frozen model on test
bootstrap by decision_group_id

The Phase 1 script does not do that.

6.4 k=0 live safety

k=0 can be accepted only as:

Do not use this unsupported multiplier form in v1.

It cannot be accepted as:

No sample-size penalty needed.

Correct substitute:

small_sample_floor
minimum independent decision groups
minimum non-zero shortfall observations
metric-specific maturity gate
source/station segment maturity gate
6.5 Verdict
tested k multiplier = FAIL accepted
k=0 as formula default = ACCEPT_AS_HEURISTIC_ONLY
small-sample risk dismissal = REJECT_FOR_LIVE
live-safe k=0 = NO
7. §2.3 sigma_window adversarial audit
7.1 ACF meaning

The script computes ACF on observed-row coverage values. That measures persistence of a proxy, not necessarily:

provider outage process
station outage process
critical-hour missingness
settlement error risk
live P&L risk
7.2 Lag-14 to 90-day jump

The script computes ACF up to lag 14. The conclusion chooses 90 days.

That is a plausible engineering choice, not proven.

7.3 High σ absorption

The Lagos/Shenzhen caveat is not minor. It is structurally dangerous:

If σ is high because data infrastructure is bad,
then subtracting σ from floor makes DDD less sensitive exactly where it should be more cautious.
7.4 0.35 hard kill dependency

The plan leans on absolute kill <0.35 for catastrophes.

But a stable city can have:

daily/window coverage = 4/7 or 5/7
missing actual max/min hour
coverage > 0.35
live posterior corrupted

So 0.35 catches broad collapse, not critical-hour collapse.

7.5 Verdict
coverage clustering finding = ACCEPT_AS_HEURISTIC_ONLY
sigma_window=90 = REQUIRES_RERUN
sigma subtractive band = REQUIRES_REDEFINITION
high-σ anomaly absorption = LIVE_MONEY_RISK
live use = SHADOW_ONLY
8. §2.4 discount curve adversarial audit
8.1 Script-level category error

p2_4_curve_breakpoints.py computes shortfall from HIGH peak-hour coverage, then evaluates winning-row errors for both:

temperature_metric = high
temperature_metric = low

This is a direct HIGH→LOW mismatch.

8.2 Sparse bins

Global bins:

shortfall = 0: N = 7371
(0, 0.05): N = 9
[0.05, 0.10): N = 34
[0.10, 0.20): N = 32
[0.20, 0.30): N = 23
[0.30, 0.50): N = 6
>=0.50: N = 0

Non-zero bins are too small and not independent.

8.3 Non-monotonic evidence

The mean error progression is noisy and not cleanly monotonic. Accepting a smooth monotone curve is operator prior, not empirical calibration.

8.4 Wrong risk scale

A 2–9% discount can be meaningless under Kelly:

bad source/station/current-day failure -> correct size = 0
DDD curve says size = 91% of original

Repo Kelly sizing multiplies edge-derived fraction by multiplier and bankroll; a small multiplier discount cannot fix a corrupted posterior.

8.5 Verdict
curve directional intuition = ACCEPT_AS_HEURISTIC_ONLY
curve calibration = REQUIRES_RERUN
9% cap = REQUIRES_REDEFINITION
live Kelly use = REJECT_FOR_LIVE
9. Missing §2.5 / §2.6 audit
9.1 §2.5 small_sample_floor missing

Because k=0 is recommended, §2.5 becomes mandatory.

Without it:

no multiplier
no sample floor
sparse curve bins
operator overrides
live rollout

This is not defensible.

9.2 §2.6 peak_window radius missing

Phase 1 hard floors and curve use historical_peak_hour ± 3.

That must be validated by:

city
metric
season
station
settlement source
HIGH vs LOW
DST/cross-midnight

External station/weather reality supports this caution: ASOS/AWOS observations can be hourly and special/adverse-weather-triggered, and WU can draw current conditions from ASOS/PWS/MADIS sources, so “hour availability” and “settlement-relevant extreme capture” are not equivalent.

9.3 Verdict
§2.5 = REQUIRES_RERUN
§2.6 = REQUIRES_REDEFINITION
No Phase 2 live effect before both complete
10. Time / timezone / local-day audit
10.1 Repo ingestion is stronger than Phase 1 use

The WU client uses ZoneInfo, converts UTC hour buckets to local time, assigns target_date from local date, and stores local/DST fields. Python ZoneInfo is the standard IANA timezone support mechanism, so the ingestion design is conceptually correct if used consistently.

But Phase 1 scripts rely on existing target_date and cast local hour. They do not independently test:

DST denominator
cross-midnight LOW
ambiguous local hour
missing local hour
station timezone
expected-slot calendar
10.2 DST issue

A fixed denominator of 7 for peak±3 is not always wrong, but it is unproven on DST boundary days. The package E8 time integrity audit itself flags many 2026-03-08 sparse rows, consistent with DST-related or ingest-cutoff fragility.

10.3 LOW cross-midnight

LOW markets often need overnight windows. If coverage/floor logic is HIGH afternoon window, it is not transferable.

10.4 Verdict
city-local target_date in ingestion = likely structurally sound
Phase 1 time semantics = insufficiently tested
LOW/DST/cross-midnight = REQUIRES_RERUN
11. Overfitting / leakage / multiple-testing audit
Failure path    How it enters    Threatened conclusion    Severity    Mitigation
operator overrides after seeing outputs    Denver/Paris/Lagos    §2.1    9    label policy vs empirical
expected-slot absence    missing zero windows vanish    §2.1/2.3/2.4    10    expected calendar LEFT JOIN
HIGH coverage used for LOW errors    script §2.4    curve    10    metric-specific coverage
lag-14 ACF → 90d    model choice after observation    σ    7    forward validation
sparse curve bins    nonzero bins N 6–34    curve    9    bootstrap/min N
row-count N inflation    leads/bins/metrics repeated    k/curve    8    decision_group bootstrap
E8 bulk refit active    latest active model live    all calibration/live    9    freeze model/data_version
no train-only Platt    p_raw/current data    k    8    causal refit/replay
station migration    Paris/LFPB class risk    floors    10    source-contract segment
null no-WU    DDD bypass    live    8    separate source-tier DDD
high σ absorption    Lagos/Shenzhen    σ/floors    9    robust outage-state model
0.35 kill too blunt    misses critical hours    live    9    directional hard gate
Brier/ECE as EV proxy    no fill/cost/P&L    curve/k    8    executable EV replay
12. Live-trading translation audit
12.1 Current live path facts

Repo facts:

Evaluator creates EdgeDecision with size_usd, p_raw, p_cal, p_market, kelly_multiplier_used, etc.
Kelly sizing computes position size from posterior edge and execution price.
Oracle penalty is already a metric-keyed sizing modifier/blacklist, separate from DDD.
Platt live load path selects active verified latest model with no DDD-style freeze.
12.2 Correct DDD layer
Layer    Current readiness
report label    acceptable
shadow signal    acceptable
Kelly discount    not ready
posterior adjustment    not ready
entry gate    only for separately validated source/current-day failures
exit gate    not ready
promotion evidence    not ready
12.3 What DDD would fail to prevent
station mismatch with high coverage
wrong source with complete rows
LOW overnight outage while HIGH window complete
critical 3-hour max/min outage with coverage >0.35
bulk refit changing calibration live
Platt already internalized coverage regime
source revision after historical validation
12.4 What DDD could wrongly suppress
stable city harmless off-peak missing row
low-infrastructure city where source is noisy but settlement still recoverable
city whose Platt calibration already accounts for historical row sparsity
12.5 Verdict
DDD must be readiness/gate first, discount second.
Current curve-as-Kelly-discount = REJECT_FOR_LIVE.
13. Hidden branch register
Hidden branch    Calculation consequence    Threatened conclusion    Live consequence    Detection test    Required rerun    Verdict
observed rows only    zero-row days absent    all coverage    false pass    expected-slot calendar    P2    CALCULATION_RISK
COUNT(DISTINCT CAST(local_hour AS INTEGER))    duplicate/fractional/DST collapse    floors/σ    false coverage    hour-slot audit    P2    TIME_SEMANTICS_RISK
fixed denominator 7    DST/ambiguous local hours ignored    floors    false pass/halt    DST cases    P2    TIME_SEMANTICS_RISK
HIGH coverage used for LOW    wrong physical object    curve/live    LOW mis-sizing    metric split    P3    REJECT_FOR_LIVE
no station_id filter    migration hidden    floors    wrong station trust    station timeline    P4    LIVE_MONEY_RISK
no authority/source_role filter    fallback/unverified rows possible    floors    false trust    provenance audit    P1/P4    CALCULATION_RISK
no expected missing rows    WU skipped gaps invisible    all    false pass    LEFT JOIN expected slots    P2    CALCULATION_RISK
Denver/Paris override    policy as proof    floors    false evidence    floor_source field    P5    OVERFIT_RISK
Lagos low floor    degraded baseline normalized    floors/σ    trades through bad infra    change-point    P4/P5    LIVE_MONEY_RISK
null no-WU    no DDD protection    live    silent exposure    null semantics test    P4    REQUIRES_REDEFINITION
lag14 → 90d    unsupported extrapolation    σ    over-smooth    forward replay    P5    OVERFIT_RISK
σ subtracts anomaly    high σ hides outages    σ    false pass    MAD/HMM compare    P5    LIVE_MONEY_RISK
0.35 kill only    misses critical-hour outage    live    bad trade    synthetic outage replay    P3/P6    LIVE_MONEY_RISK
sparse nonzero bins    arbitrary curve    curve    wrong discount    bootstrap CI    P5    REJECT_FOR_LIVE
9% cap    too small    curve    bad trades remain    EV sensitivity    P6    REJECT_FOR_LIVE
winning Brier post-outcome    no live analogue    k    false risk claim    ex-ante selected replay    P6    LEAKAGE_RISK
p_raw not p_cal    wrong metric    k/curve    wrong inference    train-only Platt    P6    CALCULATION_RISK
row N not independent N    sample inflated    k/curve    overconfidence    decision_group bootstrap    P5    CALCULATION_RISK
E8 bulk import timestamps    recorded_at unusable    leakage audit    false causality    intrinsic field audit    E8 rerun    LEAKAGE_RISK
live Platt latest selection    future refit goes live    live    calibration drift    frozen model test    P7    LIVE_MONEY_RISK
Paris station drift    high coverage wrong station    Paris floor    wrong live entry    source monitor    P4    LIVE_MONEY_RISK
HKO/VHHH category error    wrong station    HK/null    wrong truth    HKO-only test    P4    LIVE_MONEY_RISK

Hong Kong-specific caution is not theoretical: the writer explicitly rejects Hong Kong WU/VHHH-style rows and documents the HKO-vs-VHHH distance/category risk.

14. Revised validation plan
P0 — reproduce Phase 1 exactly
Goal:
Reproduce every Phase 1 output from the package and record hashes.

Required:
- package SHA256
- script SHA256
- DB path/hash
- git SHA
- command log
- output diffs

Blocker:
Any unreproduced JSON/MD becomes REVIEW_REQUIRED.
P1 — schema/field/time-object audit
Goal:
Map every variable to table.column and derived SQL.

Must verify:
- source
- station_id
- authority
- data_version
- temperature_metric
- observation_field
- training_allowed
- causality_status
- target_date/local_timestamp/utc_timestamp
P2 — expected-slot local-date rerun
Goal:
Replace observed-row counting with expected-slot LEFT JOIN.

Coverage object:
expected city × date × metric × source × station × local_hour slots
LEFT JOIN observed rows

Output:
zero-row days counted as 0
DST cases explicit
P3 — HIGH/LOW/peak-window rerun
Goal:
Separate high and low.

For each city/metric/season:
- actual extreme-hour distribution
- radius sensitivity
- missed-extreme-hour risk
- cross-midnight LOW handling
P4 — station/source segmentation
Segment by:
city
metric
source
station_id
source_role
data_version
source contract version/date range
P5 — robust statistics
Replace:
mean/stdev only

With:
MAD
Huber
EWMA
change-point
bootstrap CI
decision_group bootstrap
minimum effective N
P6 — executable EV replay
Compare:
baseline
DDD report-only
DDD Kelly discount
DDD gate
source/current-day gate

Metrics:
tail loss
missed EV
filled executable EV
false halt
false trade
P7 — shadow-only implementation guard
Tests must prove:
DDD does not change size_usd
DDD does not change should_trade
DDD unavailable does not default to OK silently
HIGH/LOW/source/station/data_version required
live activation requires P0-P6 closeout
15. Final component classifications
Component    Current conclusion    Adversarial verdict    Required action    Live eligibility
DDD concept    plausible    SHADOW_ONLY    redefine target object    no live effect
directional coverage    accepted    REQUIRES_REDEFINITION    expected-slot rerun    no
hard floors    pass with overrides    REQUIRES_RERUN    local/metric/source rerun    no
Denver/Paris 0.85    operator forced    ACCEPT_AS_HEURISTIC_ONLY    label as policy    no
Lagos 0.45    preserve infra reality    REQUIRES_REDEFINITION    regime segmentation    no
null cities    DDD not apply    REQUIRES_REDEFINITION    source-tier DDD/gate    no
k=0    fail multiplier    ACCEPT_AS_HEURISTIC_ONLY    add maturity gates    shadow only
small-sample risk    downplayed    REQUIRES_REDEFINITION    §2.5    no
sigma_window=90    partial pass    REQUIRES_RERUN    robust/forward validation    no
σ subtractive band    accepted    REQUIRES_REDEFINITION    outage-state model    no
0.35 kill    safety backstop    REQUIRES_RERUN    critical-window gate test    no
discount curve    directional pass    REJECT_FOR_LIVE    executable EV rerun    no
9% cap    accepted    REQUIRES_REDEFINITION    empirical basis/remove    no
E8 pipeline    previously mishandled    REVIEWED_WITH_HIGH_RISK    freeze live model path    no
live Platt latest selection    current repo behavior    LIVE_MONEY_RISK    model snapshot/frozen-as-of    no
report label    not enough    SHADOW_ONLY    safe diagnostic    yes, diagnostic
live sizing    intended    REJECT_FOR_LIVE    wait for P0-P7    no
