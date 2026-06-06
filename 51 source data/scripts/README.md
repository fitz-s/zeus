# 51 source data / scripts

## DEPRECATED: download_ecmwf_open_ens.py

`download_ecmwf_open_ens.py` was deleted on 2026-05-11 as part of the
ECMWF Open Data download structural replacement (PLAN v3, Candidate H).

**Replacement**: `src/data/ecmwf_open_data.py` now performs in-process
parallel SDK fetches via `ThreadPoolExecutor(max_workers=5)` with per-step
`.step{NNN}_{param}.grib2` file boundaries and atomic `.partial → os.replace`
resume.  The subprocess wrapper is structurally eliminated — the failure
category (monolithic serial subprocess, no boundary smaller than the whole job)
is now impossible.

Authority: `zeus/docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md`
