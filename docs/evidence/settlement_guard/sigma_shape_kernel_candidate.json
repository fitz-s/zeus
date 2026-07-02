{
  "_meta": {
    "authority": "sigma_shape_kernel_mixture_v1_mle",
    "candidate": true,
    "created": "2026-06-13T17:06:47.619008+00:00",
    "data_window": "settled-2026-06-08..2026-06-12",
    "lead_buckets": {
      "A_24h": [
        12.0,
        36.0
      ],
      "B_48h": [
        36.0,
        60.0
      ]
    },
    "method": "composite_objective(logloss + lambda*ring_calibration) over regime-aware two-normal scale mixture",
    "metric": "high",
    "min_cells": 60,
    "model": "sigma_core=max(sigma_impl*k, floor_steps*step); q_adj(bin)=(1-w)*Normal(sigma_core)+w*Normal(sigma_core*m)",
    "provenance_hash": "07973b49a2ab2b52",
    "scipy_available": true,
    "sigma_back_out": "q_mode = 2*Phi(half/sigma_impl)-1 ; sigma_impl = half/Phi^-1((q_mode+1)/2)",
    "source": "forecast_posteriors join settlement_outcomes(authority=VERIFIED), high metric, no-leak lead-bucketed",
    "source_query_hash": "40c7ba821ba53330",
    "supersedes_form": "q_adj(bin) = (1-w)*Normal(sigma_impl*k) + w*(1/n_bins)  [live; flat uniform pedestal]"
  },
  "families": {
    "C": {
      "calibration_at_fit": [
        {
          "dist": "0",
          "mean_q": 0.2141,
          "n_bins": 278,
          "ratio_realized_over_expected": 1.042,
          "realized_freq": 0.223,
          "wins": 62
        },
        {
          "dist": "1",
          "mean_q": 0.1848,
          "n_bins": 574,
          "ratio_realized_over_expected": 1.15,
          "realized_freq": 0.2125,
          "wins": 122
        },
        {
          "dist": "2",
          "mean_q": 0.1195,
          "n_bins": 553,
          "ratio_realized_over_expected": 0.998,
          "realized_freq": 0.1193,
          "wins": 66
        },
        {
          "dist": "3",
          "mean_q": 0.0585,
          "n_bins": 491,
          "ratio_realized_over_expected": 1.045,
          "realized_freq": 0.0611,
          "wins": 30
        },
        {
          "dist": ">=4",
          "mean_q": 0.0117,
          "n_bins": 840,
          "ratio_realized_over_expected": 1.423,
          "realized_freq": 0.0167,
          "wins": 14
        },
        {
          "dist": "tail",
          "mean_q": 0.0555,
          "n_bins": 608,
          "ratio_realized_over_expected": 0.296,
          "realized_freq": 0.0164,
          "wins": 10
        }
      ],
      "calibration_at_k1_w0": [
        {
          "dist": "0",
          "mean_q": 0.3812,
          "n_bins": 278,
          "ratio_realized_over_expected": 0.585,
          "realized_freq": 0.223,
          "wins": 62
        },
        {
          "dist": "1",
          "mean_q": 0.2104,
          "n_bins": 574,
          "ratio_realized_over_expected": 1.01,
          "realized_freq": 0.2125,
          "wins": 122
        },
        {
          "dist": "2",
          "mean_q": 0.0667,
          "n_bins": 553,
          "ratio_realized_over_expected": 1.788,
          "realized_freq": 0.1193,
          "wins": 66
        },
        {
          "dist": "3",
          "mean_q": 0.022,
          "n_bins": 491,
          "ratio_realized_over_expected": 2.78,
          "realized_freq": 0.0611,
          "wins": 30
        },
        {
          "dist": ">=4",
          "mean_q": 0.0042,
          "n_bins": 840,
          "ratio_realized_over_expected": 3.985,
          "realized_freq": 0.0167,
          "wins": 14
        },
        {
          "dist": "tail",
          "mean_q": 0.0429,
          "n_bins": 608,
          "ratio_realized_over_expected": 0.384,
          "realized_freq": 0.0164,
          "wins": 10
        }
      ],
      "ci": {
        "floor_steps": [
          1.8002,
          1.8002
        ],
        "k": [
          1.0,
          1.0
        ],
        "m": [
          1.0,
          6.0
        ],
        "w": [
          0.0,
          0.6
        ]
      },
      "ci_method": "profile_objective_95",
      "data_window": "settled-2026-06-08..2026-06-12",
      "fitted": true,
      "fitted_at": "2026-06-13T17:06:47.619008+00:00",
      "floor_steps": 1.8002,
      "k": 1.0,
      "lead_buckets": [
        "A_24h",
        "B_48h"
      ],
      "m": 1.0,
      "method": "mle_scipy_lbfgsb",
      "model_form": "sigma_core=max(sigma_impl*k, floor_steps*step); (1-w)*Normal(sigma_core) + w*Normal(sigma_core*m)",
      "n_cells": 304,
      "neg_log_likelihood": 892.1154,
      "objective": 1017.9978,
      "objective_form": "neg_log_likelihood + lambda * ring_calibration_penalty(squared log-ratio, dist 0..3)",
      "objective_lambda": 10.0,
      "ring_calibration_penalty": 12.5882,
      "w": 0.0
    },
    "F": {
      "calibration_at_fit": [
        {
          "dist": "0",
          "mean_q": 0.2064,
          "n_bins": 63,
          "ratio_realized_over_expected": 1.154,
          "realized_freq": 0.2381,
          "wins": 15
        },
        {
          "dist": "1",
          "mean_q": 0.1794,
          "n_bins": 132,
          "ratio_realized_over_expected": 1.014,
          "realized_freq": 0.1818,
          "wins": 24
        },
        {
          "dist": "2",
          "mean_q": 0.118,
          "n_bins": 132,
          "ratio_realized_over_expected": 1.22,
          "realized_freq": 0.1439,
          "wins": 19
        },
        {
          "dist": "3",
          "mean_q": 0.0596,
          "n_bins": 117,
          "ratio_realized_over_expected": 1.004,
          "realized_freq": 0.0598,
          "wins": 7
        },
        {
          "dist": ">=4",
          "mean_q": 0.0143,
          "n_bins": 177,
          "ratio_realized_over_expected": 1.188,
          "realized_freq": 0.0169,
          "wins": 3
        },
        {
          "dist": "tail",
          "mean_q": 0.0525,
          "n_bins": 138,
          "ratio_realized_over_expected": 0.138,
          "realized_freq": 0.0072,
          "wins": 1
        }
      ],
      "calibration_at_k1_w0": [
        {
          "dist": "0",
          "mean_q": 0.369,
          "n_bins": 63,
          "ratio_realized_over_expected": 0.645,
          "realized_freq": 0.2381,
          "wins": 15
        },
        {
          "dist": "1",
          "mean_q": 0.2083,
          "n_bins": 132,
          "ratio_realized_over_expected": 0.873,
          "realized_freq": 0.1818,
          "wins": 24
        },
        {
          "dist": "2",
          "mean_q": 0.0678,
          "n_bins": 132,
          "ratio_realized_over_expected": 2.123,
          "realized_freq": 0.1439,
          "wins": 19
        },
        {
          "dist": "3",
          "mean_q": 0.0246,
          "n_bins": 117,
          "ratio_realized_over_expected": 2.435,
          "realized_freq": 0.0598,
          "wins": 7
        },
        {
          "dist": ">=4",
          "mean_q": 0.0062,
          "n_bins": 177,
          "ratio_realized_over_expected": 2.736,
          "realized_freq": 0.0169,
          "wins": 3
        },
        {
          "dist": "tail",
          "mean_q": 0.0387,
          "n_bins": 138,
          "ratio_realized_over_expected": 0.187,
          "realized_freq": 0.0072,
          "wins": 1
        }
      ],
      "ci": {
        "floor_steps": [
          1.8037,
          1.8037
        ],
        "k": [
          1.0,
          1.0
        ],
        "m": [
          1.0,
          6.0
        ],
        "w": [
          0.0,
          0.6
        ]
      },
      "ci_method": "profile_objective_95",
      "data_window": "settled-2026-06-08..2026-06-12",
      "fitted": true,
      "fitted_at": "2026-06-13T17:06:47.619008+00:00",
      "floor_steps": 1.8037,
      "k": 1.0,
      "lead_buckets": [
        "A_24h",
        "B_48h"
      ],
      "m": 1.0,
      "method": "mle_scipy_lbfgsb",
      "model_form": "sigma_core=max(sigma_impl*k, floor_steps*step); (1-w)*Normal(sigma_core) + w*Normal(sigma_core*m)",
      "n_cells": 69,
      "neg_log_likelihood": 204.3937,
      "objective": 269.4767,
      "objective_form": "neg_log_likelihood + lambda * ring_calibration_penalty(squared log-ratio, dist 0..3)",
      "objective_lambda": 10.0,
      "ring_calibration_penalty": 6.5083,
      "w": 0.0
    }
  }
}