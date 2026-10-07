# Dense observations and Day0 settlement probability

Status: PROPOSED mathematical specification and offline reference; no change to live probability authority.

Source baseline: `88e0e25e98a0dac6a828f1bc90ddd7673338d5f6`.
Request: `REQ-20261007-003521-e5e3a6`.
Scope: daily HIGH and LOW; exact accepted-source-row semantics, causal information,
latent inference, numerical assurance, and a code-ready integration contract.
The executable companion is [artifacts/dense_obs_theory/](../../artifacts/dense_obs_theory/README.md).

## 0. Decision and achievable guarantees

Adopt a **joint state-space model of temperature, source reports, source revisions,
and receipts**. Its target is the final accepted source-row tape. A scalar OU
residual followed by a maximum over a guessed METAR schedule is an inadequate
general formulation. A nonstationary two-scale Gaussian state, with optional
weather and sensor regimes, is a defensible nested model inside the joint law;
it is not a theorem about the actual atmosphere.

There are three distinct meanings of exactness. Semantic exactness means that
the random variable being predicted is exactly the contract's outcome. Model
exactness means integration under a specified joint probability law. Numerical
exactness means either an analytic identity, an exact finite-state sum in exact
arithmetic, or a numerical answer with a proved error enclosure. None implies
that an estimated model is the true data-generating law. The companion implements
finite-state model exactness, with floating-point arithmetic and explicitly
separate support metadata. It does not claim a certified continuous-atmosphere
solver or live empirical superiority.

The requested universal guarantee must be corrected before implementation:

| Requested property | Precise disposition |
|---|---|
| More information always improves probability | Correct Bayes conditioning weakly improves every proper score **in expectation over the new information and outcome**. Strict improvement requires positive target-relevant information. It need not improve each realized prediction. |
| No near-one mass unless settlement forces it | Incompatible with an exact informative posterior. A noisy likelihood can produce 0.999999 without logical certainty. No universal threshold separates valid confidence from misspecification. |
| Exact zero or one only when settlement forces it | Achievable as an authority rule when the stochastic model gives positive probability to every semantically compatible outcome. Structural support proofs must be independent of Monte Carlo hits and floating-point underflow. |
| Stale dense evidence instantly becomes the METAR-only posterior | Not a Bayesian identity at a finite age unless that evidence becomes conditionally irrelevant or is excluded on an explicit validity basis. OU memory decays continuously and is not exactly zero at finite time. |
| No dense input changes today's serving | Achievable by a literal dispatcher to the unchanged legacy builder. This is a compatibility guarantee, distinct from exact reduction of the new joint law to its own METAR-only marginal. |
| A/B log-loss improvement also guarantees fewer false-certainty events | False for an unqualified threshold-count definition; see section 8. |

The strongest alternative is a direct, regularized categorical or report-hazard
predictor fitted to final settlement labels and causal observation summaries.
It may outperform a large latent model on a short history. It still needs the
same row semantics, support rules, causal validation and uncertainty about
misspecification. Prefer the joint model as the **reference law** because it
makes source selection, latency and extrema auditable; select model complexity
by walk-forward predictive evidence, not by the elegance of an OU equation.

## 1. Corrections obtained by following the pinned code

The user-supplied counts and product descriptions are useful hypotheses, not a
replacement for the actual source decoder.

1. `src/data/noaa_wrh_timeseries.py:459-476,635-675` reads the source's
   `air_temp_set_1` and implements the hourly predicate as non-null sea-level
   pressure **or** station-prefixed raw METAR text. Thus hourly does not mean
   routine-only. All-data mode keeps every finite-temperature row. Its comments
   at lines 50-77 describe why recomputing the feed value from a T-group can
   disagree. Those historical counts are repository evidence, not a newly
   reproduced source audit. NOAA's own help independently confirms that the
   hourly display includes qualifying SPECI observations. [R1, E1]
2. Finland AIP GEN 3.5, June 2026, states that Finland issues half-hourly METARs
   and does not provide SPECIs. Local SPECIAL reports in the same table are
   distinct. It identifies the three EFHK temperature sensor locations but
   does not identify which sensor feeds FMI 100968 or its switching behavior.
   The current October issue's GEN 3.5 text was not retrieved. Model a policy
   that can declare zero SPECI production; do not invent Helsinki SPECI events
   from the word SPECIAL. Accepted unscheduled *source rows*, if demonstrated,
   still enter the settlement tape regardless of their origin. [E2]
3. The NOAA page labels its data preliminary and subject to quality-control
   adjustment. An issued report is not automatically an immutable settlement
   lower/upper bound. The existing carrier already distinguishes survival and
   resolver components. [E1, R3]
4. `src/events/day0_authority.py:46-53,100-140` declares unmixed, unshifted live
   semantics and rejects legacy diurnal-mixture fields. The materializer
   explicitly passes `remaining_center_bias_native=0.0` at line 1794 and stamps
   `unshifted_live_policy` at lines 7631-7635. The requested diurnal and bias
   modules are historical mechanisms, not proof of an active live adjustment.
   This proposal must not silently reactivate them. [R4]
5. `day0_exact_remaining_probability_vector` is exact for its documented
   scalar Gaussian-extreme mixture. It receives `future_extremes`, not a
   correlated vector of source-row temperatures. Exact integration of that
   scalar model is not an exact law for a sampled path maximum. The current
   callers also condition paths on a current-temperature state, partition
   variance and optionally compose a resolver law; the simplified context
   omits those pieces. [R3, R5]
6. Raw `observation_prints` belongs to WORLD in the ownership manifest, while
   hourly vectors, calibration and forecast posteriors belong to FORECAST.
   Do not relocate the raw publication ledger under a generic statement that
   all observations are in FORECAST. [R6]

The supplied 38/177 is 21.47%, not one in seven. The NOAA-era 6/43 is 13.95%,
approximately one in seven. An era boundary is a different target law; pooling
the two eras cannot establish settlement correctness after the change.

Those two numerators were recomputed from pinned per-day artifact records:
the full-grid HIGH cohort has 177 used days, 137 equal, 38 above and 2 below;
the post-2026-08-24 subset has 43 days, 36 equal, 6 above and 1 below. The
scheduled-minute control has 16/177 and 3/43 overshoots, respectively, but its
script merely filters `:20/:50`, without joining actual accepted report
versions. It cannot isolate cadence causally. On September 10, the raw dense
maximum is 18.0 at 13:10 UTC (16:10 Helsinki), and an earlier scheduled-minute
17.6 also rounds to 18 against recorded settlement 17. This supports rejecting
a dense hard floor; it does not isolate cadence as the sole cause. [R9]

The supplied 240-pair/0.283-SD cohort was not found in the inspected artifacts.
A different checked-in diagnostic has 3,229 pairs, 2,889 agreements and only
plus/minus-one discrepancies; its un-ordered same-timestamp overwrite policy
does not preserve an auditable correction choice. It must not silently replace
the supplied cohort. The raw historical FMI CSV has observation time and value
only, with no original first-receipt/revision clocks. Its arithmetic is usable
for the recorded daily comparison, but it is not a receipt-causal replay
corpus. The original settlement DB was not available to revalidate its labels.
See `artifacts/dense_obs_theory/source_evidence.json`. [R9]

## 2. The exact settlement random variable

### 2.1 Contract and source tape

Let a family contract be

$$
 C=(c,D,z,u,v,r,\mathcal B,\chi,\rho),
$$

where city/station identity is $c$, local date $D$, IANA timezone $z$,
unit $u$, accepted page/product view $v$, rounding rule $r$, integer bin
partition $\mathcal B$, row-acceptance rule $\chi$, and finalization,
correction, no-data and dispute policy $\rho$. These are versioned contract
inputs, not fit parameters. The day is the UTC half-open interval

$$
 I_D=[\operatorname{UTC}(D\ 00{:}00,z),
       \operatorname{UTC}((D+1)\ 00{:}00,z)).
$$

Construct both local midnights before converting to UTC. Adding 24 hours to the
first UTC midnight is incorrect on 23- and 25-hour local days. Offset-aware
source timestamps disambiguate repeated local hours. A row exactly at the
next midnight belongs to the next day.

A source-row mark contains observation/averaging interval, source publication
time, first receipt time, source and station identities, raw payload hash,
native feed temperature, raw body/T-group if present, source quality flags,
view eligibility, revision lineage, and finality. Let $\mathcal R_D^*$ be the
rows retained by $\rho$ after accepted corrections and finalization, restricted
to $I_D$ and $\chi$. If the contract's resolver is not deterministically the
final page statistic, append its specified resolution variable to $\rho$.

For a retained row $j$, define

$$
 Z_j=\text{native value used by the settlement product},\qquad K_j=R_{C}(Z_j).
$$

For a nonempty retained tape,

$$
 H=\max_{j\in\mathcal R_D^*}K_j,\qquad
 L=\min_{j\in\mathcal R_D^*}K_j.
$$

For an empty tape, the outcome is the contract's no-data outcome, e.g. the
lowest **bracket**, not a fabricated physical temperature. An unsuccessful
request or an incomplete response is not an empty finalized tape. If a void or
dispute outcome lies outside the traded bin partition, carry it as a separate
outcome until the contract maps it. Do not renormalize it away.

Rounding is monotone, so for a fixed nonempty tape,
$\max R_C(Z_j)=R_C(\max Z_j)$, with the analogous minimum identity. This
does not permit changing the tape, replacing its values, or taking a continuous
path maximum. A 13:10 dense peak between 12:50 and 13:20 reports is evidence
about the latter values, not another retained row.

### 2.2 Rounding and composed quantization

For integer half-up toward positive infinity,

$$
 R(x)=\lfloor x+\tfrac12\rfloor,\quad
 R^{-1}(k)=[k-\tfrac12,k+\tfrac12),\quad R(-1.5)=-1.
$$

This is the pinned `SettlementSemantics` convention. Python/NumPy ties-to-even
and half-away-from-zero are different. JavaScript signed negative zero denotes
the same settlement bin as zero. For a set of labels $a,\ldots,b$, the
preimage is $[a-1/2,b+1/2)$; open shoulders have infinite outer endpoints.
For floor/truncation and ceil contracts use the declared offsets and endpoint
closure, not a universal half-degree interval. [R2, E3]

If a continuous Celsius measurement is converted directly to Fahrenheit before
integer rounding, the Fahrenheit bin $k$ has Celsius preimage

$$
 [(k-1/2-32)5/9,(k+1/2-32)5/9).
$$

If the upstream instrument is first quantized to tenths or whole Celsius,
compose the maps. With upstream quantizer $Q_q$,

$$
 P(K\in A\mid X)=
 \sum_{j:\ R_F(32+1.8jq)\in A}
 P(Q_q(V)=jq\mid X).
$$

The preimage is a union of upstream quantizer cells. It is not generally the
direct-conversion interval. A body integer and a tenths T-group are different
observations. Their joint likelihood must respect that the body may be the
rounded version of the same underlying measurement; treating them as two
independent sensors doubles information. For the NOAA page, an observed
`air_temp_set_1` remains the authoritative product value even if a recomputed
T-group value differs. Raw report type alone does not prove a particular
temperature precision or final feed decoding. [R1, E4]

### 2.3 What the boundary actually means

At decision $d$, let $\mathcal F_d$ contain only admissible information
received strictly before $d$. Let $\mathcal A_d$ be retained-row facts whose
survival/acceptance is settlement-forced, and let
$\mathcal U_d(\omega)=\mathcal R_D^*(\omega)\setminus\mathcal A_d$ be the
random finally retained unresolved rows. Candidates include rows observed
before $d$ but not received, future rows, and rows affected by unresolved
corrections; rejected candidates and correction operations are not themselves
temperatures in the maximum.

For a HIGH with immutable known boundary $B_H=\max_{j\in\mathcal A_d}K_j$,

$$
 H=\max\left(B_H,\max_{j\in\mathcal U_d}K_j\right).
\tag{1}
$$

Use $-\infty$ only as an internal empty-boundary sentinel. For LOW substitute
minimum and $+\infty$. Formula (1) with only $t>d$ is incorrect whenever
reports can be delayed. A provisional current maximum is a random retained
boundary under the revision model, not $B_H$. If a single changed mark can
erase the alleged bound, no absorbing certificate exists.

Define the universal bin probabilities directly:

$$
 q_A(d)=E[1\{\operatorname{Resolve}_C(\mathcal R_D^*)\in A\}\mid\mathcal F_d].
\tag{2}
$$

For ordinary settlement define the sub-CDF
$F_H(k)=P(|\mathcal R_D^*|>0,H\le k\mid\mathcal F_d)$.
For LOW use the sub-survival
$G_L(k)=P(|\mathcal R_D^*|>0,L\ge k\mid\mathcal F_d)$. Then

$$
 q_{[a,b]}^H=F_H(b)-F_H(a-1),\qquad
 q_{[a,b]}^L=G_L(a)-G_L(b+1).
\tag{3}
$$

Add no-data/resolver outcome mass in its actual bin. Deriving LOW by negating a
HIGH implementation needs transformed endpoint closures because
$R(-x)\ne-R(x)$ at half ties. A direct LOW recursion avoids that error.

## 3. Random reports and weather-triggered SPECIs

### 3.1 General marked process

Temperature alone is insufficient to determine a SPECI process. Let $W_t$
include weather variables relevant to the local reporting policy: visibility,
ceiling, wind and wind changes, precipitation/thunderstorm state, and any
documented site-specific criteria. Let $J_t$ include last report state,
trigger memory, instrument/reporting status, sensor routing, and pending
publication/revision state. Actual FAA criteria involve many such weather
changes, with routine/special coincidence and corrections separately defined.
Do not transfer US rules to every national station. [E4]

The augmented state $X_t$, including temperature and $(W_t,J_t)$, has a
joint transition/report kernel. At a discrete evaluation or routine instant,
write

$$
 K_i(dx',d\zeta\mid x)
 =P(X_{t_i}\in dx',\text{row mark }\zeta\mid X_{t_{i-1}}=x).
$$

The mark may be no row, a retained integer, an unaccepted product, or a
revision operation. This kernel permits dependence of reporting on weather
and state transitions. For a simple conditional-independent mark kernel it
factors as $P_i(dx'\mid x)g_i(d\zeta\mid x')$; this is a modeling restriction,
not a universal fact. Enlarge the state when trigger history matters.

For a HIGH threshold $k$, define the allowed mark factor
$a_k(\zeta)=1$ for no retained row or a retained integer at most $k$, and
zero for a violating retained row. The backward survival recursion is

$$
 u_i(x)=\int a_k(\zeta)u_{i+1}(x')K_i(dx',d\zeta\mid x),\qquad u_{N+1}=1.
\tag{4}
$$

If rows can be revised away, the state must retain enough tape history to
apply the revision before testing the final maximum; killing on a merely
provisional violation would be wrong. A finalized retained-mark kernel or a
joint tape/extremum state handles this. Section 6 gives the forward version.

### 3.2 Continuous event formulation and the Cox special case

For continuous stochastic events with rate measure $\nu_t(x,d\zeta)$ and
post-event state $\Psi(x,\zeta)$, the killed generator is

$$
 \mathcal L_t^{(k)}u(x)=\mathcal L_t^0u(x)
 +\int[a_k(\zeta)u(\Psi(x,\zeta))-u(x)]\nu_t(x,d\zeta).
\tag{5}
$$

Solve $\partial_tu+\mathcal L_t^{(k)}u=0$, terminal value one, with routine
report kernels at the known schedule. A deterministic weather-trigger boundary
requires its boundary/reset kernel, e.g.
$u(x_{guard^-})=\int a_k(\zeta)u(\Psi(x,\zeta))G(d\zeta\mid x)$.
Such trigger times may be hitting times, not events with a finite density
$\lambda_tdt$. A compensator may have predictable atoms or singular parts.
Actual sampling, averaging, hysteresis and reset policies prevent the
unphysical infinite recrossing problem of a diffusion with a naive threshold
trigger. Learn unknown policy effects without replacing documented rules by
an invented temperature threshold.

Only under a conditionally Poisson/Cox process, conditional on an exogenous
latent path and independently drawn marks, may one simplify to

$$
 E\left[\prod_{i\in S_{routine}}g_{k,i}(X_{t_i})
 \exp\{-\int\lambda(t,X)[1-g_k(t,X)]dt\}\mid\mathcal F_d\right],
\tag{6}
$$

with the appropriate past-unresolved contribution and availability factors.
Here $g_k$ is the probability that a mark is accepted and nonviolating, or
is unaccepted. Equation (6) is not valid for an arbitrary self/history-dependent
reporting intensity after ignoring its effect on future state/report history.

Estimate a stochastic rate, where that model is appropriate, with a causal
point-process likelihood $\sum_j\log\lambda(t_j)-\int\lambda(t)dt$, including
marks, known exposure/outages, schedules and receipt censoring. For policy
triggers estimate the joint weather transition and any unknown selection/reset
parameters with the full report likelihood. A rate estimated from temperature
alone cannot identify unobserved weather triggers. Production needs the
versioned policy, raw row types and available meteorological covariates; a
counts-only SPECI history cannot prove exact specification.

Conditioning a Gaussian temperature process on an endogenous realized report
schedule generally makes its selected temperatures non-Gaussian. Therefore
one cannot estimate a schedule from weather, freeze it, and then use an
unconditional Gaussian orthant probability as if sampling were exogenous.

## 4. Latent temperature and observation model

### 4.1 A nested, identifiable two-scale model

Use the following model as a candidate family, with the one-scale OU as a
strict submodel. Conditional on forecast member/component $M=m$, regime
path and parameters,

$$
 T_t=f_m(t)+\mu_c(h_t,w_t)+U_t+V_t,
\quad dU_t=-a_s(h_t,w_t)U_tdt+\ell_s(h_t,w_t)dB_t^s,
\quad dV_t=-a_f(h_t,w_t)V_tdt+\ell_f(h_t,w_t)dB_t^f.
\tag{7}
$$

The slow component represents forecast-path error that persists across several
reports; the fast component describes fluctuations between reports. Correlated
innovations are allowed through a positive-semidefinite diffusion matrix.
Use cyclic local-hour functions plus season, lead and available weather
covariates. Do not assume stationary residual variance across noon and night.
The coefficient functions are fitted, with partial pooling by station and
documented product family. Structural reporting rules are supplied metadata,
not arbitrary city-specific tuning constants.

In vector notation $dZ_t=A_tZ_tdt+L_tdB_t$, the exact linear transition is

$$
 Z_t\mid Z_s\sim N(\Phi(t,s)Z_s,Q(s,t)),\quad
 Q(s,t)=\int_s^t\Phi(t,v)L_vL_v^T\Phi(t,v)^Tdv.
\tag{8}
$$

For a constant scalar OU with stationary variance $s^2$,
$\Phi=e^{-\Delta/\tau}$ and $Q=s^2(1-e^{-2\Delta/\tau})$.
Piecewise-constant coefficients give exact interval transitions for that
declared model. Continuous coefficient quadrature adds a numerical error
obligation. If $W_t$ depends on $T_t$, conditioning on weather does not
automatically retain Gaussianity; solve the augmented joint model. A finite
regime chain with conditional-linear dynamics is an explicit approximation to
that relationship, not a proof of Gaussian atmosphere.

An ensemble is a mixture model for uncertainty, not independent observations
of a single exact answer. With common pre-decision parameter posterior,

$$
 w_m(d)=\frac{w_m^0p(\mathcal E_d\mid m)}
 {\sum_lw_l^0p(\mathcal E_d\mid l)},\quad
 q_A(d)=\sum_m\int q_A(d\mid m,\theta)p(m,\theta\mid\mathcal E_d)d\theta.
\tag{9}
$$

All evidence terms used in the member likelihood must be the same ones used
in that member's posterior. Equal weights after unequal component likelihoods
are not Bayes conditioning. Conversely, assigning a discrete 'true member'
interpretation is itself a mixture-model assumption; a calibrated distribution
over forecast-path errors can be better when members are strongly redundant.

Forecast paths are themselves information. If a forecast run has assimilated
station measurements, the correct factorization is
$p(x\mid F)\,p(\mathcal E_d\mid x,F)$, not automatically
$p(x\mid F)\,p(\mathcal E_d\mid x)$. Shared measurement error can remain
dependent on the forecast after conditioning on true temperature. The limiting
case $F=Y$ makes the problem explicit: observing the already-known $Y$
again supplies no information, while multiplying an independent sensor
likelihood generally sharpens the posterior incorrectly. All likelihoods in
this document are conditional on the admitted forecast information; omitting
that argument is notation, not a conditional-independence assertion. Preserve
assimilation lineage where available, or fit and validate the joint conditional
error model. On a forecast-run change, condition on the new information or
reparameterize and replay one coherent model; do not silently reset residuals
and reuse old observations as independent evidence.

The total-variance identity is
$\operatorname{Var}(T)=E_m\operatorname{Var}(T\mid m)
+\operatorname{Var}_m E(T\mid m)$. Do not add ensemble spread again inside
every component. The live scalar law
$\sigma_{pred}^2=within^2+ens\_center\_delta^2+between^2$ constrains its
existing target. It does not specify a covariance function, and cannot identify
OU persistence. A final-extreme variance cannot simply be assigned as each
minute's process variance. The existing carrier already subtracts component
spread and instrument terms in a variance partition. [R5]

### 4.2 Define what each instrument measures

The latent physical temperature and the reporting instrument are not identical
by definition. Write the measurement for channel $c$ as

$$
 V_{c,t}=\mathcal A_{c,t}[T]+b_{c,J_t}(t)+e_{c,t},\qquad
 \mathcal A_{c,t}[T]=\int a_{c,t}(v)T_vdv,
\tag{10}
$$

where the averaging kernel integrates to one and carries its actual support.
A point observation is a delta-kernel special case. The user's stated FMI
one-minute average on a ten-minute publication grid is not automatically
$T_t$. Overlapping averages and shared sensor errors are correlated. Either
augment the state with the required averaging/sensor components, or calculate
their joint Gaussian covariance integrals. Do not count a nominal cadence as
the averaging duration. Exact FMI kernel and sensor routing remain metadata
requirements, not facts established by geographic proximity.

Set the authority-sensor bias to zero by **definition of the temperature
reference**, or anchor it with an independent calibration. Otherwise replacing
$T$ by $T+\delta$ and all biases by $b-\delta$ leaves the likelihood
unchanged. Dense bias is then relative to that anchor. Separate constant
station offsets, time-varying bias and random noise; a sensor-regime model
$J_t$ can represent distinct runway instruments if data support it.

For an integer METAR observation $K=k$ that exactly rounds its reporting
measurement, its likelihood is

$$
 \ell_M(x)=1\{\mathcal A_M[T]+b_M\in[k-1/2,k+1/2)\}.
\tag{11}
$$

If the reporting measurement has noise relative to the latent state, replace
the indicator by the difference of its conditional CDF at those endpoints.
'Exact integer' means the reported code is known exactly; it does not prove
the instrument has zero physical error. A received source-row value is an
exact fact about that version of the product, with correction uncertainty
represented separately.

For a continuous dense observation use a normal likelihood, or a fitted
full-support mixture such as

$$
 e_c\sim(1-\epsilon_c)N(0,\sigma_c^2)
+\epsilon_ct_{\nu_c}(0,s_{out,c}).
\tag{12}
$$

For tenths-quantized dense data $Y=y$, the likelihood is the integral of
(12) over $[y-0.05,y+0.05)$ after subtracting $\mathcal A_c[T]+b_c$.
Do not use both rounding variance and this quantized likelihood for the same
measurement. Fit all mixture parameters and compare against the simpler
Gaussian model; the eleven supplied flips do not by themselves establish
heavy tails or three-sensor switching. Include a shared-error component if
the channels derive from the same instrument, rather than multiplying
conditionally dependent observation likelihoods.

### 4.3 What 0.283 does and does not establish

For authority and dense sensor values $T_M,T_D$,

$$
 K-Y=[R(T_M)-T_M]+(T_M-T_D)-b-e.
\tag{13}
$$

Its variance contains all cross-covariances. The reference SD $1/\sqrt{12}
=0.288675$ is a uniform unit-cell rounding model, not a universal physical
noise floor. Nonuniform fractional temperatures, quantization, averaging and
correlated errors invalidate subtracting $1/12$ from the observed variance.
The supplied 0.283 is consistent with close correspondence; it does not
identify $b\approx0$ and $\sigma_e\approx0$. The 229/240 agreement and
eight near-half misses are useful evidence for a small discrepancy model,
with uncertainty clustered by station-day and weather episode.

For independent continuous dense noise and an authority-anchored scalar
temperature, a same-instant pair contributes

$$
 p_\theta(K=k,Y=y)=\int_{k-1/2}^{k+1/2}
 p_\theta(t)\,\varphi_{\sigma_e}(y-t-b)dt.
\tag{14}
$$

For quantized dense values replace the density by its cell integral. Estimate
the latent law and channel parameters jointly across the time series, not by
regressing integer midpoints against dense values. The lack of simultaneous
Amsterdam pairs is handled by the transition law between :20/:25/:30 etc.;
one must estimate process variability from the full irregular series. No
nearest-neighbor copy or invented simultaneous timestamp is justified.

For the Gaussian-prior special case $T\sim N(\mu,v)$, continuous dense noise
variance $s_e^2$, define
$m_y=\mu+v(y-b-\mu)/(v+s_e^2)$ and
$v_y=vs_e^2/(v+s_e^2)$. Then (14) has the closed form

$$
 p(k,y)=\varphi_{\sqrt{v+s_e^2}}(y-\mu-b)
 \left[\Phi\left(\frac{k+1/2-m_y}{\sqrt{v_y}}\right)
 -\Phi\left(\frac{k-1/2-m_y}{\sqrt{v_y}}\right)\right].
\tag{14a}
$$

This is the exact pair likelihood used by the companion parameter-recovery
experiment. After earlier interval observations, the prior is not generally
Gaussian; then integrate this conditional calculation over its actual mixture
or use the full rectangle/smoother likelihood. The pair experiment by itself
does not identify a multi-regime temporal model.

### 4.4 Walk-forward estimation contract

At fit cutoff $c<d$, maximize or integrate the full marginal likelihood

$$
 p_\theta(\mathcal E_{<c})=\int p_\theta(x_0)
 \prod_i K_{\theta,i}(dx_i,d\zeta_i\mid x_{i-1})
 \prod_{j:\ receipt_j<c}\ell_{\theta,j}(x,\zeta)
 \ell_{\theta,\text{receipt history}}\,dx.
\tag{15}
$$

The state and report factors carry actual irregular elapsed time. Parameter
uncertainty can be integrated through posterior draws; plug-in maximum
likelihood is an approximation whose uncertainty must be measured. An EM
implementation alternates a censored smoother for sufficient statistics with
updates of bias, noise, transition coefficients and mixture/reporting
parameters. Preserve an identifiable reference and order slow/fast time
scales to avoid label switching. Correlated errors and a persistent bias can
be weakly identifiable from short data; report posterior/profile intervals,
and prefer the nested simpler model when evidence cannot separate them.

Every training record must bind its original forecast run, assimilation lineage
or modeled conditional dependence, and actual first possession time. Do not use
reanalysis, later corrected values, or hindsight
forecast availability as though known at the historical decision. Final
settlement labels are admissible only when their resolution was received
before the fit cutoff. Source eras remain distinct targets; related physical
temperature history can inform a common process only through correctly
versioned observation models. Tune hyperparameters/model order on inner
chronological folds and report outer held-out folds. Training and test days,
not repeated decisions on the same outcome, are the minimum clustering units
for uncertainty; use multi-day blocks or weather episodes when serial
dependence persists. Fitted per-city coefficients use a shared estimation procedure;
they are not manually selected time scales or confidence caps.

## 5. Receipt-time causality and out-of-order observations

Represent each evidence item by observation support, issued/publication time,
first usable receipt, immutable revision identity and ledger sequence. The
decision information is generated by items with `receipt < decision`, together
with the actually observed availability/polling history. A documented sequence
tie-break can refine equal timestamp order; an unrecorded observation-time
comparison cannot. Forecast runs and fit artifacts obey the same possession
principle. A later correction creates a later evidence item; it cannot
rewrite an earlier decision certificate.

Receipt latency belongs in two places. First it controls whether an observed
value is admissible. Second, when receipt absence is observed and informative,
it contributes a censoring likelihood. For a potential report at $t$ with
conditional latency CDF $G_\theta$, a not-yet-received but existing report
contributes $1-G_\theta(d-t\mid x,\zeta,transport)$. An arriving report
contributes its latency density/mass. If source/transport availability is
unknown, integrate that state; do not equate no local receipt to no report.
The supplied bimodal AWC and FMI medians do not define the distributions or
their state dependence. Fit from original observation/first-receipt pairs,
including unresolved right-censoring and outages, before using their shape.

If latency/missingness is conditionally independent of temperatures and marks
given observed transport covariates, some receipt factors cancel between
numerator and denominator. This is an explicit missing-at-random assumption.
If storms change publication or transport delay, they do not cancel. Dense
availability can itself be informative; dropping absent values without that
factor is then misspecified.

At $d$, retain the joint posterior of current state and unresolved past
settlement contribution. Filtering only $r_d$ loses the latter. A late dense
reading may inform an earlier unreceived settlement row. Assimilate a newly
received item at its measurement time by exact out-of-sequence smoothing or
deterministic replay from an earlier checkpoint, then propagate forward. Sort
the admitted set by observation support after freezing membership by receipt;
sorting all eventual observations first leaks future knowledge. A stale
checkpoint is not permission to ignore an admissible delayed report.

Deduplicate by physical observation and source revision lineage. AWC and NOAA
may carry the same measurement; equality of values is not proof of independence,
and different receipts are not two independent sensor readings. Conversely,
equal timestamps do not prove two different channels share a sensor. Keep
channel likelihood and source authority separate.

## 6. Exact inference and bounded numerical error

### 6.1 Filtering under censored observations

For a transition density $p_i(x_i\mid x_{i-1})$, the exact filtering recursion
is

$$
 \pi_i^-(x_i)=\int p_i(x_i\mid x_{i-1})\pi_{i-1}(x_{i-1})dx_{i-1},\qquad
 \pi_i(x_i)=\frac{\ell_i(x_i)\pi_i^-(x_i)}{\int\ell_i(x)\pi_i^-(x)dx}.
\tag{16}
$$

Use the augmented state/kernel when marks, shared sensor errors and past
unresolved rows matter. This is Bayes' rule plus the Markov property. Kalman
filtering is exact only when its distributions and observation model are
linear Gaussian. [E5]

For one scalar normal $X\sim N(\mu,s^2)$ restricted to $[l,u)$, let
$a=(l-\mu)/s$, $b=(u-\mu)/s$, and $Z=\Phi(b)-\Phi(a)$. Its moments are

$$
 E[X\mid l\le X<u]=\mu+s\frac{\phi(a)-\phi(b)}Z,
$$

$$
 \operatorname{Var}(X\mid l\le X<u)=s^2\left[1+
 \frac{a\phi(a)-b\phi(b)}Z-
 \left(\frac{\phi(a)-\phi(b)}Z\right)^2\right].
\tag{17}
$$

These are exact moments. Replacing the truncated distribution by a Gaussian
with those moments is not an exact update. It reinstates mass outside the
observed interval and changes future threshold tails. Even after Gaussian
propagation, the predictive law is generally a selection-normal distribution,
not a normal determined by two moments. Moment matching can be an explicitly
benchmarked approximation, not the settlement-exact reference.

### 6.2 Fixed exogenous schedule: Gaussian rectangle probability

For known model parameters and component, with a fixed exogenous schedule and
linear observations, collect all relevant latent report measurements into
$V\sim N(\mu,\Sigma)$. Conditioning on exact continuous Gaussian dense
readings gives another multivariate normal. Let $C_M$ be the rectangular
constraints from already received METAR measurements; let $C_k$ require all
unresolved retained measurements to decode to integers at most $k$. Then

$$
 P(H\le k\mid Y_{dense},C_M)=
 1\{B_H\le k\}\frac{
  P(V\in C_M\cap C_k\mid Y_{dense})}
 {P(V\in C_M\mid Y_{dense})}.
\tag{18}
$$

With multiple quantizers, $C_k$ is a union of rectangles or an equivalent
monotone preimage. Quantized dense measurements contribute further rectangle
constraints instead of Gaussian point conditioning. Unknown bias, regime,
schedule, revision and parameter components are mixed with their posterior
weights **outside the appropriately conditioned calculation**, using common
evidence denominators. Equation (18) cannot assume Gaussianity after
conditioning on an endogenous reporting policy; the full joint model is then
required. Pending past rows belong in $C_k$ just as future rows do.

For a scalar Markov Gaussian chain, a lower-dimensional integral recursion is
often preferable. With direct C-unit half-up and fixed retained instants,

$$
 \alpha_1(x)=\pi_1(x)1\{x<k+1/2\},\quad
 \alpha_i(x)=1\{x<k+1/2\}\int p_i(x\mid z)\alpha_{i-1}(z)dz,
\quad F_H(k)=\int\alpha_N(x)dx.
\tag{19}
$$

Insert received-observation likelihoods and normalize by the evidence-only
recursion; include the initial boundary and no-data terms as in section 2.
For LOW use $1\{x\ge k-1/2\}$ and compute survival. In a two-scale model
the Markov state is $(U,V)$, not their scalar sum: projecting to the sum
usually loses the Markov property. A vector-state recursion or the full
Gaussian rectangle handles this.

### 6.3 General source-tape recursion and finite-state oracle

For the general joint law maintain an unnormalized measure
$\alpha_i(x,e,r)$, where $e$ is the retained running extreme or empty
sentinel and $r$ is any unresolved revision/report-history state. A mark
operation maps $(e,r)$ through $F_C(e,r,\zeta)$. The exact forward update is

$$
 \alpha_i(dx',de',dr')=
 \int\alpha_{i-1}(dx,de,dr)K_i(dx',d\zeta\mid x,r)
 \ell_i(x',\zeta,r')\,
 1\{(e',r')=F_C(e,r,\zeta)\}.
\tag{20}
$$

At finalization apply the contract's terminal resolution mapping and divide
each outcome mass by the total evidence mass. If a deleted earlier maximum
can reveal a second-largest value, storing only the provisional maximum is
insufficient; retain the necessary tape/order-statistic information in $r$.
This finite-state recursion is exact for a declared finite state and mark
model. The companion implements the irrevocable-final-mark special case;
provisional revision histories must first be represented in an augmented
model, not passed as immutable `Evidence` marks.

The reference tracks three different objects: log mass, Boolean model support,
and semantic support/finality evidence. Dense likelihoods with full support
may change log masses but cannot remove a possible hidden path. The Boolean
recursion is an independent audit against numerical underflow. A finite toy
temperature grid can omit real-world possibilities, so model support must
never be promoted to settlement authority. Log complements are retained when
ordinary floating-point probabilities would round to one.

### 6.4 Which numerical method is adequate?

| Method | Exact object | Required qualification |
|---|---|---|
| Gaussian moment matching | First two moments of a single truncated update | No general bound on extreme-bin probabilities; reject as the reference truth. |
| Particle filtering/smoothing | Converges to specified posterior under appropriate assumptions | Finite particles can miss rare bins. Zero hits do not imply impossibility. Parameter and particle uncertainty are different. |
| Gaussian rectangle integration / minimax tilting | Exact integral representation; exact-law sampling is possible | Computed integral is still numerical. Report error estimates/enclosures, conditioning denominator and rare-tail diagnostics. [E6] |
| Finite-state HMM | Exact sums for the declared chain and mark kernels | A chain obtained by temperature gridding is an approximation to a diffusion, not exact merely because its recursion is exact. |
| Verified interval/quadrature recursion | Enclosed probabilities for the declared continuous model | Bound transition/likelihood quadrature, tail truncation and normalization; propagate bounds through every stage. |

A temperature grid is not automatically a lumpable Markov partition. Transition
probabilities computed at cell centers do not equal those from every state in
the cell. The companion's OU cell builder is deliberately labeled an
approximation. Halving grid spacing and observing stability is a useful
diagnostic, not a proof that the residual error is small.

A route to deterministic enclosures is to split cells at every censoring and
quantizer boundary, bound each Gaussian transition cell integral over all
starting states in its cell, and bound likelihoods with outward-rounded
arithmetic. Infinite tails need analytic bounds; they cannot be deleted and
renormalized. Propagate nonnegative lower/upper unnormalized masses. If an
outcome numerator is in $[n_-,n_+]$ and common evidence denominator in
$[z_-,z_+]$ with $z_->0$,

$$
 q_A\in[\max(0,n_-/z_+),\min(1,n_+/z_-)].
\tag{21}
$$

Tiny evidence probability can amplify small unconditional errors. A tail
bound for the prior chain alone does not certify the posterior after a rare
censored observation. If the conditioning denominator cannot be bounded away
from zero, refine the calculation or return a numerically uncertified/error
result. The companion
does not implement these continuous enclosures, and does not claim that
SciPy's error estimate is an outward-rounded mathematical certificate.

For randomized exact predictive draws, Hoeffding gives simultaneous absolute
bin error at most $\varepsilon$ with probability at least $1-\delta$ if
$n\ge\log(2K/\delta)/(2\varepsilon^2)$. This requires independent exact-law
draws, not arbitrary dependent resampled particles; it is an absolute-error
statement and can be useless for very small tail probabilities. Relative/log
probability assurance in rare tails needs importance sampling or analytic
tail integration with appropriate bounds. Never use a Monte Carlo zero or a
tiny estimated standard error as a semantic fact.

### 6.5 Cost and reuse

With $M$ components, $N$ time nodes, $J$ augmented states, $B$ extreme
levels, and $C$ mark alternatives, the factorized dense-matrix reference recursion costs
$O(MN[J^2B+JBC])$. A streamed implementation needs $O(JB)$ working state
per component, excluding stored kernels and evidence. The executable reference
materializes a broadcast transition array and therefore uses an additional
$O(J^2B)$ temporary; it is an audit oracle, not a memory-optimized serving
implementation. A completely general joint state-transition/mark kernel
costs $O(MNJ^2BC)$ without additional structure. Sparse transitions reduce
the $J^2$ term. Threshold
recursions for $K$ bin edges instead cost roughly $O(MNKJ^2)$. Sensor/weather
regimes multiply state dimension; a two-dimensional temperature grid with
$j$ cells per axis has $J=j^2$, which is expensive without structure.

A dense Gaussian rectangle method requires a factorization generally costing
$O(n^3)$, then numerical integration work depending on dimension, rarity and
requested error; there is no universal milliseconds-per-family guarantee.
Cache transition matrices, common dense/METAR filtering and factorization
across bin edges. HIGH and LOW can reuse the physical latent posterior but
must retain distinct terminal functionals and certificates. The companion's
measured synthetic runtime is evidence only for its tiny finite-state model,
not a production capacity claim.

## 7. Proper-score improvement and its limits

Let $Y$ be the final settlement bin, $\mathcal F$ the METAR/source/forecast
information at a fixed decision, and $\mathcal D$ additional admissible dense
information. Suppose both predictors are the exact conditional probabilities
under the **same correct joint law**, including its parameter uncertainty:

$$
 p_A(y)=P(Y=y\mid\mathcal F),\qquad
 p_B(y)=P(Y=y\mid\mathcal F,\mathcal D).
$$

For log loss $\ell(p,Y)=-\log p(Y)$, direct conditioning gives

$$
 E[\ell(p_A,Y)-\ell(p_B,Y)\mid\mathcal F]
 =E[D_{KL}(p_B\Vert p_A)\mid\mathcal F]\ge0.
\tag{22}
$$

Averaging over $\mathcal F$ gives the conventional scalar conditional mutual
information $I(Y;\mathcal D\mid\mathcal F)$. The equality assumes the usual integrability or an extended-value formulation
that avoids subtracting infinities. Improvement is strict exactly when the
conditional distribution changes with positive probability. After seeing a
particular dense reading, the true posterior $p_B$ also minimizes expected
proper loss conditional on that expanded information. Neither statement says
that the realized outcome will reward every update, or that posterior entropy
falls for every possible reading.

For Brier loss $\|e_Y-p\|^2$, the improvement is
$E\|p_B-p_A\|^2\ge0$. More generally, conditional use of the true distribution
minimizes any proper loss, and generalized entropy is concave; conditional
Jensen proves weak improvement. Strictness needs a strictly proper score and
target-relevant information. [E7]

For fitted, potentially misspecified forecasts $q_A,q_B$, an exact
decomposition under the true distribution is

$$
 E[\ell(q_A,Y)-\ell(q_B,Y)]
 =I(Y;\mathcal D\mid\mathcal F)
 +E[D_{KL}(p_A\Vert q_A)]-E[D_{KL}(p_B\Vert q_B)].
\tag{23}
$$

Thus dense information can make predictions worse when its added
misspecification/estimation error exceeds its information benefit. Wrong
averaging, bias drift, sensor switching, duplicate evidence, underestimated
noise, ignored special-report selection, latency selection, wrong source era,
wrong finality, and numeric tail loss are concrete mechanisms. Separate models
fit on different data or with different regularization do not automatically
satisfy (22); neither does comparing the new exact model with legacy A0.

No finite observational study proves that future misspecification is absent.
The smallest honest guarantee is exact semantic mapping, causal inference,
qualified numerical computation, and a conditional score theorem under stated
specification. Real-data superiority must remain an empirical, revisable claim.

## 8. Support, certainty and validation

### 8.1 Semantic zero and one

Let $\Omega_d$ be all final source tapes/resolver outcomes consistent with
the contract and immutable received facts, and let
$\mathcal S_d=\{\operatorname{Resolve}_C(\omega):\omega\in\Omega_d\}$.
Then semantic impossibility and certainty are

$$
 A\cap\mathcal S_d=\varnothing\ \Rightarrow\ q_A=0,
 \qquad \mathcal S_d\subseteq A\ \Rightarrow\ q_A=1.
\tag{24}
$$

To make the converses valid for a categorical outcome, require the model to
assign positive posterior probability to **each** outcome in $\mathcal S_d$.
Full-support measurement errors and future innovations are useful ingredients;
they do not repair an omitted source product or a truncated model state space.
Supported source-value lattices, not just the observed boundary, may restrict
the set. Report semantic and model support separately.

Positivity is a condition on the **joint** likelihood conditional on each
compatible settlement completion. Positive marginal dense variance is
insufficient if a fitted shared-sensor covariance is singular: perfect error
correlation can still force a deterministic relation. A zero-noise boundary
fit or an exact sensor alias needs authoritative identity evidence before it
may exclude outcomes. Otherwise retain a nondegenerate model domain and
parameter uncertainty; an arbitrary fitted-noise floor is not a proof.

Under normal source-based resolution with no remaining separate void/dispute
outcome, for an immutable HIGH bound $B$, any bin entirely below $B$ is impossible;
an upper shoulder containing every integer at least $B$ is certain. The point
bin containing $B$ is not certain while a larger retained report remains
possible. Reverse these relations for LOW. If the day and all accepted row
versions are finally closed, the realized outcome is forced. A proven
station-dark resolution can also force its lowest bracket. Local midnight
alone does not prove report/revision finality.

Numerical probabilities require support-aware representation. Store `log_q`
and `log_1_minus_q`, model support and a separately verified semantic
certificate. A displayed float of 0 or 1 caused by underflow/rounding does not
authorize deterministic settlement actions. If a downstream numeric interface
cannot represent an uncertified tail, it needs a typed uncertainty/error
result, not a confidence haircut or an invented probability floor.

### 8.2 Why the proposed false-certainty gate is not a theorem

Choose a threshold $\tau=.99$. A baseline predicting .5 on two outcomes never
produces an above-threshold mistake. A correctly calibrated informative signal
can predict .995 on either outcome and be wrong with probability .005. It has
much better expected log loss and a positive count of above-threshold errors.
Thus `B false-certainty count <= A count` is a separate deployment preference,
not a consequence of proper scoring, and can reject a correct useful model.

Define at least two metrics separately: (i) probability assigned against an
immutable semantic support fact (must be exactly zero), and (ii) outcome error
among forecasts of confidence at least a predeclared threshold. For (ii), also
report coverage, forecast-expected errors $\sum(1-q_{selected})$, actual errors
and a clustered calibration interval. A lone realized rare miss is not proof
of an invalid probability. Choosing thresholds after seeing errors biases the
diagnostic. Calibration constraints do not justify clipping the forecast.

### 8.3 Required offline tests and real-data ship evidence

The reference tests establish model-level identities and expose failure modes:

* Compare the dynamic program with complete hidden-path/mark enumeration on
  a tiny chain, including both HIGH and LOW and no-report mass.
* A dense between-report peak changes probabilities but cannot become a
  settlement boundary; full-support dense evidence leaves model support intact.
* Include a past unreceived report, a future receipt, a receipt exactly at the
  decision, delayed out-of-sequence evidence, duplicates and conflicts.
* Check negative half ties, native F feed values versus body/T-group proxies,
  hourly eligible specials, finite ranges/open shoulders and local-day DST.
* Recover bias/noise/latent-distribution parameters from interval-censored
  synthetic pairs; measure uncertainty across independent seeds before
  interpreting one fit as proof of identifiability for all settings.
* Prove a finite example's expected log-score improvement by exact enumeration,
  then compare held-out simulated days under the same generating model. Add
  misspecified-bias and wrong-clock counterexamples.
* Show exact marginal reduction when dense factors are absent, preserve the
  legacy callback result unchanged in compatibility mode, and distinguish
  continuously decaying information from a finite-age reset.

The production replay must freeze one vector for every city/date/metric and
decision occasion independently of whether it traded. Compare A0 (pinned
serving), A (new model without decision-time dense values), and B (the same
new model with dense values) on the identical receipt-frozen occasions. A and
B must share the fitted prior/parameter state for the information comparison;
otherwise label the comparison as a full-model change. Final labels must be
authoritative and era-matched. Report paired log loss, Brier score and ranked
probability score by hour/lead, station, unit, source era, row/receipt mode,
distance to rounding boundaries, weather regime and channel coverage. Avoid
pooling NOAA-all, NOAA-hourly, WU and other products for causal claims.

For discrete-bin calibration use randomized PIT

$$
 U=F_Y(Y^-)+V\,P(Y),\qquad V\sim U(0,1)
\tag{25}
$$

with independent, reproducibly seeded randomization. Under the correct
conditional categorical forecast it is uniform; repeated forecasts of the same
final outcome are dependent, so ordinary iid PIT significance tests are
inappropriate. Use city-day/multi-day block calibration intervals and inspect
conditional PIT, residual serial dependence, report-arrival time-rescaling
where continuous intensities apply, and receipt-survival calibration. Random
PIT on coarse open shoulders tests the bin forecast, not the distribution
within a shoulder.

Choose model/tuning folds before outcome inspection, track all tested models,
and reserve an untouched chronological confirmation period. A negative paired
mean with a block interval excluding zero is evidence for that scope, not an
eternal guarantee. The synthetic reference is not a substitute for this gate.

## 9. Missing data, stale data and drift

Under the same joint model, if no dense information has been observed and
missingness is conditionally uninformative, its integrated likelihood is one:

$$
 \int p(Y_{dense}\mid X)dY_{dense}=1.
$$

Deleting those factors gives exactly the METAR-only marginal. If dense
missingness is weather-dependent and its absence was observed, retain its
likelihood; exact reduction then need not hold. This is an unavoidable
qualification of the requested fallback.

An old admitted dense reading is still information. For a scalar OU its
predictive mean contribution decays with $e^{-\Delta/\tau}$, and does not
become identically zero at a finite age. Exact equality to the METAR-only
posterior at time $d$ requires
$Y_{dense,past}\perp Y_{settlement}\mid\mathcal F_d$, or an explicit decision
to discard that information. The latter is a robust operational policy,
not the exact conditional law. A misspecification concern may motivate it,
but it cannot retain theorem (22) as an unconditional guarantee.

Represent drift within the model, for example as a bias state
$db_c=\sqrt{q_{b,c}}dB_t$, optionally with a hidden sensor/change regime and
learned transition law. Bias uncertainty increases predictive observation
variance and weakens transfer of old bias calibration to new readings. This
does not necessarily erase the information an already assimilated reading
provided about a persistent temperature state. Estimate
drift variance and switching hazards walk-forward; a change is inferred from
predictive residual likelihoods, not a manually fixed age timer. Persistent
errors near quantization boundaries, residual mean changes and a likelihood
preference for an alternate regime are diagnostic evidence. A sequential
likelihood-ratio/e-process with an explicit null can provide false-alarm
control under that null; it cannot guarantee immediate detection of arbitrary
unknown drift. Three runway sensors do not imply three identifiable regimes.

Proven identity, unit, clock or payload corruption makes an observation
inadmissible. Recompute from the admissible ledger after excluding it and
preserve the earlier certificate as historical evidence. Missing optional
dense data must never block an otherwise qualified baseline from serving.
Fetching, fitting and asynchronous dense ingestion stay off the serving path.
There is no new cap, stale-age gate or ad hoc probability mixture in this
proposal.

## 10. One implementation across cities

Use one event/measurement pipeline with versioned station/product metadata,
arbitrary observation times and learned channel parameters. National cadence
does not select different inference code. For a Gaussian conditional state,
one dense reading at $g$ reduces uncertainty at a settlement instant $s$ by

$$
 \operatorname{Var}(T_s\mid Y_g,\mathcal F)=V_s-
 \frac{\operatorname{Cov}(T_s,Y_g\mid\mathcal F)^2}
 {\operatorname{Var}(Y_g\mid\mathcal F)}.
\tag{26}
$$

For an illustrative stationary OU with independent dense noise and independent
uncertain bias variance $V_b$, the variance reduction is
$s^4e^{-2|s-g|/\tau}/(s^2+\sigma_e^2+V_b)$. These simplifying assumptions
are not required by the general covariance formula. Conditional on previous
observations, use the conditional covariance, not the unconditional expression.
For averaged sensors integrate covariance over their two averaging kernels.

For multiple dense readings replace the scalar term by
$C_{sY}V_Y^{-1}C_{Ys}$. Under common same-sensor noise models, closely spaced
readings often provide redundant information, and a common uncertain bias
cannot be averaged away as independent error. Correlation alone does not prove
diminishing marginal information: complementary measurements can cancel a
shared nuisance and produce synergy. The joint covariance determines the gain. Temperature-state
variance reduction is not identical to bin information gain: a reading only
helps the settlement target when it changes probabilities of unresolved
accepted rows crossing relevant thresholds. Formula (22) is the exact target
information criterion.

| Candidate in supplied context | Expected opportunity, conditional on verification |
|---|---|
| Helsinki FMI, ten-minute grid | Close alignment with :20/:50 reports may refine within-bin state and forecast upcoming/late reports; published Finnish policy does not support a generic SPECI rate. |
| Amsterdam KNMI, ten-minute grid against :25/:55 | No exact coincidences are needed. Benefit depends on five-minute covariance, averaging and bias; nearest-time equality is invalid. |
| Munich DWD | A cold offset can be learned only with an identifiable reference and stable or modeled drift. Untreated bias can reverse the score gain. |
| Warsaw hourly | Fewer new observations and potentially greater lag; still useful when they add information not already in the settlement stream. |
| Tokyo JMA, ten-minute; Singapore NEA, one-minute; Toronto ECCC SWOB | Potentially useful where station identity, product mapping, latency and covariance are verified. Faster cadence alone is insufficient, and the supplied cadence/sensor claims were not independently remeasured here. |
| No dense channel | Same new-model METAR-only marginal; in compatibility mode the unchanged existing serving callback. |

Latency changes which observations are available and how far their measurement
times are from relevant source rows. For performance planning, integrate
information gain over the receipt distribution; do not multiply the posterior
by a heuristic latency confidence factor. After every relevant row and its
finality are known, further dense temperatures cannot improve an already
forced settlement bin. Before then, even a reading after the last routine
instant can inform pending earlier rows or revisions.

## 11. Integration specification for Zeus

### 11.1 Typed input and output

The future production implementation should accept an immutable
`DenseSettlementRequest` with the following fields; a generic city name and
latest temperature are insufficient.

| Input | Required contents |
|---|---|
| Family semantics | city/station, date, metric, IANA timezone, native unit, source era, page view, accepted-row predicate version, decode/rounding version, full bin partition, no-data and finalization policy |
| Decision snapshot | UTC cutoff and tie-break sequence, WORLD and FORECAST read high-water marks, admitted evidence IDs/hashes, snapshot provenance |
| Forecast paths | coherent component paths on their original time coordinates, source-run IDs and possession clocks, assimilation lineage or specified forecast/observation conditional dependence, mixture prior, complete relevant support including pending past reports |
| Model artifact | parameter posterior/version, fit cutoff, training receipt/label cutoff, transition/averaging/sensor/reporting/receipt model specifications and support assumptions |
| Observations | time support, original native value/quantization, channel and station, first receipt, revision identity, quality status, shared-measurement lineage |
| Settlement facts | separately typed provisional rows and immutable surviving rows; unresolved row/revision state; no-data/finality evidence |
| Numerical contract | algorithm/version, requested error criterion, tail/support representation and reproducible seed when required |

Return `q`, `log_q`, `log_complement`, semantic support/certificates,
model-support diagnostics, error bounds or explicitly uncertified numerical
status, member/parameter weights, admitted evidence digest, source/fit/model
versions, and content identity. A terminal-bin draw and a parameter-posterior
probability-vector draw are different objects. Existing `samples` consumers
must receive their documented probability-vector uncertainty object; a one-hot
sample of final weather is not a confidence interval for q.

Clock is economically relevant in the new model: no-arrival likelihoods,
remaining-report kernels and diffusion propagation can change while the last
received values remain identical. The existing builder intentionally excludes
`decision_time_utc` and `probability_cutoff_utc` from its legacy economic hash.
Retain that rule only for legacy replay. A new certificate must bind the
effective time-dependent state/kernel/no-arrival witness, or the cutoff itself;
two requests are equivalent only when their resulting mathematical inputs are
equivalent. Hashing a timestamp need not reseed unrelated Monte Carlo noise:
separate deterministic evidence identity from stable random-number streams.

### 11.2 Function-by-function disposition

| Pinned surface | Proposed change / retained behavior |
|---|---|
| `day0_hourly_vectors.day0_exact_remaining_probability_vector` | Preserve as the legacy scalar-extreme integrator. Add a separately named path/report probability operator; never pass a vector of path instants as if each were an alternative whole-day extreme. |
| `day0_hourly_vectors.build_day0_remaining_probability_carrier` | Add an optional typed joint-model request or a new typed dispatcher with a distinct operator/semantics revision. If absent/inadmissible, invoke the original builder unchanged, including q, samples and identity. |
| `replacement_forecast_materializer._day0_remaining_center_delta_c` | Retain for legacy. The new law evaluates forecast paths at modeled retained-row instants and integrates the unresolved tape; it does not use the continuous-window extreme shrink as an extra correction. |
| `_day0_noaa_future_vector_members` / `_day0_noaa_carrier_future_members` | Expose original coherent paths and source variance provenance to the new constructor instead of collapsing all time structure to member extrema. Preserve current-state evidence as observations in one joint model, not a second independent shift. |
| `_day0_noaa_preliminary_carrier` | Supply receipt-frozen row, revision and model evidence to the new constructor. Resolve source-product survival inside the joint tape model; retain existing resolver composition only as a separately specified, conditioned resolver layer, without using the same evidence twice. |
| `day0_fast_obs.build_fast_station_residual_likelihood` | Keep its AWC/source-product residual purpose and replay. Add a dense observation-likelihood producer with explicit averaging, bias, correlated error and receipt fields. Do not reuse a seven-day empirical residual histogram as OU process variance or dense hard-bound authority. |
| `contracts.settlement_semantics` | Retain rounding primitives and native-unit checks. Add or compose a typed report/product selection and upstream-quantization specification without changing the existing rounding law. |
| `calibration.day0_diurnal_residual` | Historical comparison only under current unmixed policy. Any learned diurnal shape belongs in the new latent/transition prior and its fit, not an independently applied mixture over its finished q. |
| `calibration.day0_remaining_bias` | Historical comparison only under current unshifted policy. Bias parameters of the new model are estimated inside that model; do not apply this legacy shift a second time. |
| `signal.day0_low_distribution` | Retain as a max/min-sample legacy helper. New LOW probabilities use the same joint process and direct minimum/LOW threshold recursion, with their own labels and certificates. |
| `events.day0_authority` and canonical posterior replay | A future activation needs a distinct semantics revision and typed payload validation; old certificates are never restamped. This PR does not activate that revision. |

The root `AGENTS.md` probability law explicitly excludes a fitted residual-sigma
fallback from live authority. A statistically fitted state-space law is a
different proposed probability regime; its future activation must explicitly
reconcile that law and all reader/replay contracts. Merely placing this theory
under `docs/authority/` does not change current runtime authority.

### 11.3 Compatibility, ordering and rollback

There are two separate compatibility assertions. Within the new model, absent
uninformative dense factors integrate to one, producing A exactly. Across
versions, A0 is returned by a literal legacy callback when the optional dense
request is absent or explicitly unusable. The second assertion does not imply
A equals A0. It also cannot promise that B beats A0 solely from theorem (22).

For inference, freeze one read-consistent evidence snapshot with immutable row
IDs and receipt cutoffs, perform pure computation outside write locks, and
commit the resulting certificate only after its identity and authority are
validated. WORLD owns raw `observation_prints`; FORECAST owns model artifacts,
vectors and posteriors; TRADE remains untouched. Respect existing sanctioned
cross-database access paths; do not write two canonical databases in independent
transactions or mutate historical evidence to make a replay match. A new
receipt racing the calculation creates a later decision, not a partial update
to the old certificate. Repeated retries of the same immutable input are
idempotent.

This PR contains documentation and an offline oracle, so rollback is removal
of those additions. A later production migration first establishes raw source
semantics/receipt provenance and the continuous-model numerical certificate,
then runs the predeclared historical A0/A/B replay, then changes the canonical
operator and every reproduction path together. Optional channel absence never
blocks qualified baseline serving. All live deployment, order submission,
runtime scheduling and trade databases are outside this PR. Specifically,
`src/execution/exit_lifecycle.py`, `src/runtime/reactor_wake.py`,
`src/engine/event_reactor_adapter.py`, and `src/engine/global_batch_runtime.py`
are not edited.

The reference is local and deterministic apart from seeded simulation. It
does not fetch weather, evaluate source text as code, read secrets, or connect
to live databases. Production ingestion must validate station/unit/time and
length/range schemas before likelihood construction; source payloads are data.
Evidence hashes prevent accidental input substitution but do not establish
source authenticity or authorize a new statistical regime.

## 12. Terminal disposition and smallest remaining evidence

The mathematics, code-ready data/algorithm contracts and finite-state oracle
are deliverables of this PR. They establish semantic design and synthetic
correctness for declared models. The following are concrete production
residuals, not claims of completed real-world validation:

| Residual | Smallest completing evidence/action |
|---|---|
| Exact city product and finality | Preserve contract text, accepted source-row payloads and revision/finality lineage for each enabled era/view; test the exact acceptance/decoder against finalized outcomes. |
| Helsinki unscheduled-row policy | Obtain one source-authenticated accepted nonroutine row and identify its origin, or retain the documented no-SPECI policy and separately verified additional-row policy. |
| Dense averaging/sensor mapping | Capture authority and dense measurement-kernel metadata, original paired rows/receipts and station/sensor identities; fit relative bias/noise jointly, retaining identification uncertainty. |
| Forecast/observation dependence | Recover assimilation lineage or fit the joint likelihood conditional on each admitted forecast run; verify that forecast refreshes do not assimilate the same measurement twice. |
| Real-data benefit | Run receipt-frozen A0/A/B over authoritative labels with the predeclared blocked walk-forward analysis; the currently running external backtest result was not available through the offline bridge. |
| Continuous numerical assurance | Implement the stated rectangle/augmented recursion with certified tail/normalization error, or explicitly qualify a stochastic error contract; finite-grid agreement alone is insufficient. |
| Safe activation | Reconcile the new probability regime and immutable replay payload with current authority, prove legacy callback equality, then activate through the existing canonical path. No production activation is part of this theory PR. |

No choice of fitted parameters can turn an unknown accepted-row or correction
policy into settlement-exact truth. Conversely, the absence of those metadata
does not prevent completion and testing of the exact conditional mathematics
and reference implementation supplied here.

## Sources and evidence boundaries

Repository sources below were read at the pinned baseline; runtime activation,
live databases and external A/B results were not inspected. File comments that
report historical measurements are evidence of the recorded study, not an
independent rerun of its raw-data lineage. The companion evidence file records
the arithmetic verified from checked-in FMI audit artifacts.

* [R1: NOAA product decoder](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/src/data/noaa_wrh_timeseries.py), especially lines 50-77, 459-476 and 635-675.
* [R2: SettlementSemantics](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/src/contracts/settlement_semantics.py), lines 77-147, 173-312.
* [R3: Day0 hourly vectors and carrier](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/src/data/day0_hourly_vectors.py), lines 1113-1276, 1306-1905.
* [R4: Day0 authority](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/src/events/day0_authority.py), lines 46-53 and 100-140; [root AGENTS](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/AGENTS.md).
* [R5: Materializer](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/src/data/replacement_forecast_materializer.py), lines 1393-1523, 1650-2076 and 7600-7788.
* [R6: Database ownership](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/architecture/db_table_ownership.yaml), `observation_prints` at line 1117.
* [R7: Fast source residual](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/src/data/day0_fast_obs.py), lines 630-908; [diurnal residual](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/src/calibration/day0_diurnal_residual.py); [remaining bias](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/src/calibration/day0_remaining_bias.py); [LOW distribution](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/src/signal/day0_low_distribution.py).
* [R8: Settlement reference](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/docs/reference/zeus_market_settlement_reference.md), read as reference, with code controlling the pinned implementation.
* [R9: Pinned dense-observation audit](https://github.com/fitz-s/zeus/tree/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/artifacts/fast_obs_audit/daily_extreme_agreement), especially `SUMMARY.md`, both FMI per-candidate JSONs, `subsample_at_resolver_instants.py`, `_instant_offset_vs_metar.json` and `raw/fmi_efhk.csv.gz`; [FMI ingestion](https://github.com/fitz-s/zeus/blob/88e0e25e98a0dac6a828f1bc90ddd7673338d5f6/src/data/fmi_airport_temperature.py) verifies station/unit and records local fetch receipt but does not verify an averaging-duration kernel.
* [E1: NOAA timeseries help](https://www.weather.gov/wrh/timeseries?site=EFHK), preliminary-data notice and hourly/SPECI display explanation, accessed 2026-10-07.
* [E2: Finland AIP GEN 3.5, June 2026](https://www.ais.fi/eaip/003-2026_2026_06_11/eAIP/EF-GEN%203.5-en-GB.html), section 3 and EFHK row. October issue-specific GEN 3.5 content not retrieved.
* [E3: ECMAScript Math.round](https://tc39.es/ecma262/multipage/numbers-and-dates.html#sec-math.round), positive-infinity tie convention; pinned implementation independently inspected.
* [E4: FAA JO 7900.5E](https://www.faa.gov/documentLibrary/media/Order/Order_JO_7900.5E.pdf), sections 3.6-3.11 and Table 3-1. This establishes documented US criteria, not every country's policy or the current final NOAA decoder.
* [E5: Särkkä, Bayesian Filtering and Smoothing (2013)](https://users.aalto.fi/~ssarkka/pub/cup_book_online_20131111.pdf), Theorem 4.1 and exact/approximate filtering distinctions.
* [E6: Botev, The Normal Law Under Linear Restrictions (2016 preprint)](https://arxiv.org/pdf/1603.04166), exact-law truncated-normal sampling versus numerical integral estimation.
* [E7: Gneiting and Raftery (2007)](https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf), proper scores and entropy/divergence. Equations (22)-(23) are derived here by conditional expectation.
* [E8: FMI observation guidance](https://en.ilmatieteenlaitos.fi/guidance-to-observations), separate daily-product windows. Do not substitute a national daily maximum/minimum product for the local-calendar-day market tape.

Load-bearing assumptions are explicit throughout: correct contract/source
mapping, admissible first-receipt clocks, support-faithful model for semantic
0/1 equivalence, appropriately augmented reporting/revision state, and correct
joint specification for the proper-score theorem. Actual sensor identity,
averaging kernel, stochastic trigger/latency parameters and live predictive
performance require the residual evidence above.
