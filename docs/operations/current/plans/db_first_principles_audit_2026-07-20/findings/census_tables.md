# DB census (raw: census_raw.jsonl)

Generated 2026-07-21 01:44:30. Objects: 799, timeouts: 0. Complete.

## trades — measured total 93.9 GiB

| object | type | GiB | pages | cells | payload GiB | unused GiB | mx_payload |
|---|---|---|---|---|---|---|---|
| executable_market_snapshots | table | 43.11 | 11300198 | 17989157 | 37.71 | 5.17 | 6662 |
| execution_feasibility_evidence | table | 19.03 | 4987613 | 30534122 | 15.75 | 3.00 | 5707 |
| decision_log | table | 7.60 | 1992068 | 190032 | 7.52 | 0.07 | 1146549 |
| idx_execution_feasibility_evidence_token_created | index | 4.25 | 1114123 | 25582518 | 2.80 | 1.36 | 119 |
| idx_execution_feasibility_evidence_token_time | index | 3.02 | 791449 | 25582518 | 2.64 | 0.30 | 112 |
| sqlite_autoindex_execution_feasibility_evidence_1 | index | 2.29 | 599240 | 25582518 | 1.92 | 0.29 | 81 |
| book_hash_transitions | table | 2.13 | 558446 | 10272682 | 1.99 | 0.06 | 256 |
| idx_snapshots_condition_captured | index | 1.95 | 510690 | 10321771 | 1.02 | 0.89 | 107 |
| idx_snapshots_no_token_captured | index | 1.64 | 430905 | 10322025 | 1.13 | 0.48 | 119 |
| idx_snapshots_yes_token_captured | index | 1.64 | 430829 | 10322021 | 1.13 | 0.48 | 119 |
| idx_snapshots_selected_token_captured | index | 1.62 | 424067 | 10321990 | 1.13 | 0.46 | 119 |
| sqlite_autoindex_book_hash_transitions_1 | index | 0.90 | 234899 | 9715765 | 0.78 | 0.09 | 121 |
| idx_book_hash_transitions_market_time | index | 0.89 | 233317 | 9715765 | 0.77 | 0.09 | 120 |
| position_events | table | 0.84 | 220436 | 397975 | 0.68 | 0.16 | 58510 |
| idx_book_hash_transitions_new_hash | index | 0.76 | 198160 | 9715765 | 0.64 | 0.08 | 72 |
| sqlite_autoindex_executable_market_snapshots_1 | index | 0.59 | 153693 | 10321769 | 0.49 | 0.06 | 241 |
| decision_certificates | table | 0.22 | 58306 | 58021 | 0.20 | 0.02 | 99830 |
| collateral_ledger_snapshots | table | 0.20 | 51576 | 160356 | 0.15 | 0.04 | 11489 |
| edli_live_order_events | table | 0.16 | 40742 | 16743 | 0.15 | 0.00 | 103804 |
| market_price_history | table | 0.13 | 34849 | 657409 | 0.13 | 0.00 | 395 |
| execution_feasibility_latest | table | 0.10 | 26596 | 60505 | 0.06 | 0.04 | 4596 |
| sqlite_autoindex_market_price_history_1 | index | 0.08 | 21445 | 622649 | 0.07 | 0.01 | 118 |
| idx_market_price_history_token_recorded | index | 0.08 | 21445 | 622649 | 0.07 | 0.01 | 118 |
| idx_market_price_history_slug_recorded | index | 0.06 | 14812 | 622649 | 0.05 | 0.01 | 90 |
| provenance_envelope_events | table | 0.05 | 13740 | 57249 | 0.04 | 0.01 | 12857 |
| token_price_log | table | 0.05 | 13626 | 217102 | 0.05 | 0.00 | 283 |
| probability_trace_fact | table | 0.05 | 12229 | 45401 | 0.04 | 0.01 | 1984 |
| token_suppression_history | table | 0.04 | 10515 | 94342 | 0.04 | 0.00 | 899 |
| executable_market_snapshot_latest | table | 0.03 | 8390 | 38808 | 0.02 | 0.01 | 918 |
| venue_order_facts | table | 0.03 | 8157 | 43976 | 0.03 | 0.00 | 12632 |
| idx_market_price_history_condition_recorded | index | 0.03 | 7313 | 622649 | 0.02 | 0.00 | 106 |
| idx_market_price_history_snapshot | index | 0.03 | 7298 | 622649 | 0.02 | 0.00 | 84 |
| idx_token_price_token | index | 0.02 | 6057 | 203512 | 0.02 | 0.00 | 105 |
| decision_certificate_edges | table | 0.02 | 5027 | 105275 | 0.02 | 0.00 | 212 |
| sqlite_autoindex_decision_certificate_edges_1 | index | 0.02 | 4076 | 100262 | 0.01 | 0.00 | 151 |
| idx_position_events_position_type_sequence | index | 0.01 | 3486 | 238360 | 0.01 | 0.00 | 102 |
| availability_fact | table | 0.01 | 3469 | 27849 | 0.01 | 0.00 | 770 |
| sqlite_autoindex_position_events_1 | index | 0.01 | 3453 | 238514 | 0.01 | 0.00 | 122 |
| sqlite_autoindex_position_events_2 | index | 0.01 | 3425 | 238514 | 0.01 | 0.00 | 122 |
| market_topology_state | table | 0.01 | 3253 | 9734 | 0.01 | 0.00 | 1925 |

## forecasts — measured total 39.9 GiB

| object | type | GiB | pages | cells | payload GiB | unused GiB | mx_payload |
|---|---|---|---|---|---|---|---|
| calibration_pairs | table | 11.96 | 3134581 | 51282798 | 10.97 | 0.54 | 289 |
| sqlite_autoindex_calibration_pairs_1 | index | 6.23 | 1633264 | 48157324 | 5.11 | 0.95 | 142 |
| ensemble_snapshots | table | 3.35 | 876971 | 2085231 | 2.67 | 0.64 | 9554 |
| forecast_posteriors | table | 3.30 | 864162 | 78808 | 3.25 | 0.03 | 111391 |
| idx_calibration_pairs_refit_core | index | 2.70 | 706733 | 48157324 | 2.54 | 0.01 | 71 |
| idx_calibration_pairs_group_lookup_lead | index | 2.55 | 667717 | 48157324 | 2.38 | 0.02 | 66 |
| idx_calibration_pairs_group_lookup | index | 2.47 | 647584 | 48157324 | 2.30 | 0.02 | 57 |
| idx_calibration_pairs_decision_group | index | 2.27 | 595355 | 48157324 | 2.11 | 0.02 | 47 |
| idx_calibration_pairs_city_date_metric | index | 1.50 | 393696 | 48157324 | 1.35 | 0.01 | 36 |
| idx_calibration_pairs_bucket | index | 1.26 | 331374 | 48157324 | 1.12 | 0.01 | 38 |
| idx_calibration_bucket | index | 0.97 | 253072 | 48157324 | 0.82 | 0.00 | 24 |
| raw_model_forecasts | table | 0.25 | 64409 | 727198 | 0.21 | 0.03 | 860 |
| sqlite_autoindex_ensemble_snapshots_1 | index | 0.12 | 31008 | 1211259 | 0.11 | 0.01 | 111 |
| idx_ens_entry_lookup | index | 0.10 | 25695 | 1211259 | 0.09 | 0.00 | 169 |
| observations | table | 0.09 | 22576 | 71613 | 0.06 | 0.02 | 2004 |
| raw_forecast_artifacts | table | 0.09 | 22331 | 44086 | 0.05 | 0.04 | 4514 |
| uq_raw_model_forecasts_logical_plus_request | index | 0.08 | 20603 | 662953 | 0.07 | 0.01 | 218 |
| idx_raw_model_forecasts_endpoint_family_cycle_members | index | 0.07 | 18747 | 662953 | 0.07 | 0.00 | 139 |
| idx_ensemble_snapshots_lookup | index | 0.07 | 17525 | 1211259 | 0.06 | 0.00 | 68 |
| deterministic_forecast_anchors | table | 0.06 | 16182 | 32768 | 0.04 | 0.03 | 2500 |
| sqlite_autoindex_raw_model_forecasts_1 | index | 0.06 | 15493 | 662953 | 0.05 | 0.01 | 106 |
| source_run_coverage | table | 0.05 | 12757 | 101479 | 0.04 | 0.00 | 586 |
| idx_raw_model_forecasts_product_identity | index | 0.04 | 11760 | 662953 | 0.04 | 0.00 | 117 |
| idx_raw_model_forecasts_history_join | index | 0.04 | 10609 | 662953 | 0.04 | 0.00 | 82 |
| idx_ens_source_run | index | 0.02 | 6093 | 1211259 | 0.02 | 0.00 | 95 |
| idx_raw_model_forecasts_captured_at | index | 0.02 | 5989 | 662953 | 0.02 | 0.00 | 38 |
| sqlite_autoindex_source_run_coverage_2 | index | 0.02 | 5650 | 88756 | 0.02 | 0.00 | 255 |
| market_events | table | 0.02 | 5594 | 56163 | 0.02 | 0.00 | 443 |
| day0_hourly_vectors | table | 0.02 | 5175 | 15480 | 0.01 | 0.01 | 1524 |
| readiness_state | table | 0.02 | 5034 | 16105 | 0.01 | 0.00 | 3607 |
| settlement_outcomes | table | 0.02 | 4058 | 13477 | 0.01 | 0.00 | 3936 |
| idx_source_run_coverage_scope | index | 0.01 | 3320 | 88756 | 0.01 | 0.00 | 157 |
| settlements | table | 0.01 | 2855 | 12276 | 0.01 | 0.00 | 1227 |
| idx_raw_model_forecasts_current_family_cycle_members | index | 0.01 | 2395 | 88749 | 0.01 | 0.00 | 125 |
| sqlite_autoindex_market_events_1 | index | 0.01 | 1785 | 50586 | 0.01 | 0.00 | 127 |
| idx_forecast_posteriors_live_family_cycle | index | 0.01 | 1775 | 48893 | 0.01 | 0.00 | 148 |
| idx_forecast_posteriors_topology | index | 0.01 | 1766 | 48884 | 0.01 | 0.00 | 134 |
| idx_source_run_coverage_status | index | 0.01 | 1574 | 88756 | 0.00 | 0.00 | 67 |
| sqlite_autoindex_raw_forecast_artifacts_1 | index | 0.01 | 1333 | 22456 | 0.00 | 0.00 | 208 |
| readiness_state_legacy_no_ready_20260607T131810Z | table | 0.00 | 1289 | 5244 | 0.00 | 0.00 | 2952 |

## world — measured total 83.8 GiB

| object | type | GiB | pages | cells | payload GiB | unused GiB | mx_payload |
|---|---|---|---|---|---|---|---|
| opportunity_events | table | 30.00 | 7864431 | 24737537 | 25.34 | 4.33 | 167967 |
| no_trade_regret_events | table | 11.23 | 2944341 | 1002301 | 10.96 | 0.22 | 68668 |
| execution_feasibility_evidence | table | 10.83 | 2838431 | 15770351 | 8.76 | 1.89 | 5484 |
| decision_certificates | table | 3.13 | 821325 | 2093719 | 2.42 | 0.69 | 118985 |
| idx_opportunity_events_pending_order | index | 2.63 | 688828 | 17896630 | 2.35 | 0.20 | 149 |
| idx_opportunity_events_channel_token | index | 2.37 | 621048 | 17896731 | 2.10 | 0.19 | 133 |
| opportunity_event_processing | table | 2.34 | 613592 | 11638924 | 2.18 | 0.06 | 1254 |
| selection_hypothesis_fact | table | 1.91 | 501546 | 1013287 | 1.21 | 0.68 | 2839 |
| sqlite_autoindex_opportunity_events_1 | index | 1.60 | 419000 | 17896731 | 1.34 | 0.20 | 81 |
| sqlite_autoindex_opportunity_events_2 | index | 1.60 | 418818 | 17896731 | 1.36 | 0.18 | 82 |
| observation_instants | table | 1.57 | 412108 | 3279049 | 1.43 | 0.11 | 1041 |
| idx_opportunity_event_processing_pending_retry_floor | index | 1.56 | 407875 | 11027136 | 1.43 | 0.07 | 175 |
| idx_execution_feasibility_evidence_token_time | index | 1.47 | 384397 | 12975290 | 1.33 | 0.09 | 112 |
| sqlite_autoindex_opportunity_event_processing_1 | index | 1.19 | 310783 | 11027049 | 0.99 | 0.15 | 97 |
| sqlite_autoindex_execution_feasibility_evidence_1 | index | 1.16 | 303657 | 12975290 | 0.97 | 0.14 | 81 |
| idx_opportunity_events_fsr_target_date | index | 1.04 | 272797 | 17896731 | 0.90 | 0.09 | 74 |
| idx_opportunity_events_day0_family_extreme | index | 1.03 | 270890 | 17896731 | 0.96 | 0.02 | 99 |
| idx_opportunity_events_type_available | index | 1.01 | 264792 | 17896731 | 0.87 | 0.08 | 63 |
| idx_opportunity_event_processing_status | index | 0.75 | 197553 | 11027132 | 0.65 | 0.07 | 67 |
| decision_certificate_edges | table | 0.67 | 175757 | 3681137 | 0.62 | 0.02 | 212 |
| observation_revisions | table | 0.58 | 152020 | 303283 | 0.40 | 0.18 | 3710 |
| sqlite_autoindex_decision_certificate_edges_1 | index | 0.52 | 137022 | 3505845 | 0.44 | 0.06 | 151 |
| sqlite_autoindex_decision_certificates_3 | index | 0.37 | 97287 | 1346426 | 0.30 | 0.06 | 524 |
| idx_decision_certificates_semantic | index | 0.37 | 97287 | 1346426 | 0.30 | 0.06 | 524 |
| idx_decision_certificate_edges_parent | index | 0.29 | 74950 | 3505845 | 0.23 | 0.04 | 71 |
| edli_no_submit_receipts | table | 0.26 | 66989 | 125797 | 0.21 | 0.04 | 135462 |
| sqlite_autoindex_observation_instants_1 | index | 0.17 | 44872 | 2868040 | 0.16 | 0.00 | 70 |
| edli_live_order_events | table | 0.17 | 43738 | 24534 | 0.16 | 0.01 | 122221 |
| decision_compile_failures | table | 0.16 | 42888 | 550751 | 0.15 | 0.01 | 1314 |
| sqlite_autoindex_no_trade_regret_events_2 | index | 0.16 | 42273 | 706959 | 0.13 | 0.03 | 1153 |
| idx_observation_instants_city_ts | index | 0.15 | 38040 | 2868041 | 0.13 | 0.00 | 56 |
| sqlite_autoindex_decision_certificates_2 | index | 0.11 | 28509 | 1346426 | 0.09 | 0.02 | 71 |
| idx_decision_certificates_hash | index | 0.11 | 28509 | 1346426 | 0.09 | 0.02 | 71 |
| forecasts | table | 0.10 | 25505 | 299801 | 0.09 | 0.01 | 388 |
| probability_trace_fact | table | 0.09 | 24790 | 53201 | 0.06 | 0.04 | 2689 |
| hourly_observations | table | 0.09 | 24182 | 1850391 | 0.08 | 0.00 | 60 |
| sqlite_autoindex_decision_certificates_1 | index | 0.09 | 22306 | 1346426 | 0.07 | 0.01 | 65 |
| sqlite_autoindex_hourly_observations_1 | index | 0.08 | 21732 | 1826278 | 0.08 | 0.00 | 53 |
| data_coverage | table | 0.08 | 19792 | 711100 | 0.07 | 0.00 | 149 |
| idx_decision_compile_failures_event | index | 0.06 | 16423 | 507994 | 0.05 | 0.01 | 113 |

