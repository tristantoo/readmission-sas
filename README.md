# readmission-sas

A SAS port of the 30-day hospital readmission pipeline in `Documents/healthcare-claims-analytics` (SQL Server mart
plus Python GLM) on CMS DE-SynPUF Sample 1. DATA steps and PROC SQL build the mart, PROC FREQ and PROC MEANS
profile it, PROC LOGISTIC fits on 2009 discharges and scores 2010, and a final step compares 129 SAS results to the
reference builds and prints PASS or FAIL for each.

> **Status: the SAS code is UNRUN.** SAS is not installed on the machine this was written on. It has never been
> executed, so nothing here claims parity yet. The first run on SAS OnDemand for Academics is the parity test.

> **Data caveat (CMS):** DE-SynPUF is synthetic and has "very limited inferential research value." The numbers
> show the method working; they are not findings about Medicare patients or hospitals.

## What is verified now, and what is not

| Claim | Status | Evidence |
|---|---|---|
| The four trimmed upload files reproduce all 52 reference mart counts | Verified in pandas | `uv run python scripts/prepare_upload.py` prints 52/52 PASS; `tests/test_prep.py` reruns it |
| The feature definitions and the decile rule used in `sas/04_model.sas` reproduce the reference GLM | Checked once in Python during development (statsmodels on the trimmed files: max coefficient difference 9e-17, AUC 0.61176; decile rule identical to `pandas.qcut`) | The decile rule is in the test suite; the statsmodels check is not, to keep this project's dependencies to pandas |
| The SAS programs run and match the reference | **Not verified** | Needs a run on OnDemand |

## Run it on SAS OnDemand for Academics

1. On your machine, build the upload files (needs the CMS raw CSVs; by default it reads
   `../healthcare-claims-analytics/data/raw`, or pass `--raw <dir>`):

   ```powershell
   uv sync
   uv run python scripts/prepare_upload.py
   ```

   Expect four files in `data/upload/` (7.4 to 7.9 MB each) and `52/52 mart checks PASS`. Stop if it says FAIL.
2. Sign in at https://welcome.oda.sas.com with your own account and open **SAS Studio**.
3. In the left pane, **Server Files and Folders** > **Files (Home)**: create a folder `readmission-sas`, and inside it
   three folders: `sas`, `data`, `expected`.
4. Upload (right-click a folder > **Upload files**):
   - the 7 files in this repo's `sas/` into `readmission-sas/sas`
   - the 4 files in `data/upload/` into `readmission-sas/data`
   - the 2 files in `expected/` into `readmission-sas/expected`
5. Open `readmission-sas/sas/run_all.sas` and click **Run**. If the log says a path does not exist, right-click the
   `readmission-sas` folder > **Properties**, copy its location (`/home/u.../readmission-sas`) into the `%let root =`
   line at the top of `run_all.sas`, and run again.
6. Read the verdict. The log ends with `NOTE: PARITY PASS: all 129 checks match` or
   `ERROR: PARITY FAIL: N of 129 checks differ`. The last table in the Results tab is titled
   `Parity: N of 129 checks PASS`, with any FAIL rows first.
7. Download `readmission-sas/out/parity_results.csv`, `calibration.png` and `roc.png` into a `results/` folder in
   this repo and commit them. Update the status line above with the date and the verdict.

If anything FAILs or errors, do not edit `expected/`. Save the log and the FAIL rows.

**Upload limits.** The official OnDemand guide gives 5 GB of storage per user. Forum reports mention per-file upload
caps of 1 GB, and 10 MB in older SAS Studio versions, which are not confirmed by an official page. The trimmed
files stay under 10 MB each so either cap is fine; `prepare_upload.py` exits 1 if one grows past it. Sources:
[SAS guide to uploading local data](https://support.sas.com/content/dam/SAS/support/en/products-solutions/ondemand/uploading-accessing-local-data.pdf),
[SAS Communities thread on upload limits](https://communities.sas.com/t5/SAS-Studio/SAS-on-Demand-Uploading-Data/m-p/397370).

## Local checks

```powershell
uv run pytest                                # 14 tests: cohort rules, decile rule, trim, expected CSVs, SAS coverage
uv run python scripts/make_expected.py       # only to regenerate expected/ from the two source repos
```

## Layout

```
sas/run_all.sas            sets &root and runs 01 to 06 in order
sas/01_import.sas          DATA step INFILE of the four CSVs with explicit types and date informats
sas/02_mart.sas            segment-1 claims, transfer merge (RETAIN), stays, look-ahead MERGE, PROC SQL prior stays,
                           index stays, attrition, readmission flag and features
sas/03_profile.sas         PROC PRINT / FREQ / MEANS on the mart
sas/04_model.sas           PROC LOGISTIC train and SCORE, rank AUC, deciles, Brier, ECE, calibration
                           intercept and slope, ROC and PROC SGPLOT reliability plot
sas/05_measure.sas         hospital O/E with exact Poisson intervals (CINV), monthly observed vs expected
sas/06_parity.sas          reads expected/, builds the actual table, PASS/FAIL per check, exports parity_results.csv
expected/mart_counts.csv   52 counts from the live SQL Server LocalDB mart (readmission-spark reference), tol 0
expected/model_metrics.csv 77 values from the Python GLM outputs (coefficients, metrics, deciles, O/E, monthly)
src/prep.py                trims the raw files; pandas mirror of the cohort rules; decile rule
scripts/                   prepare_upload.py, make_expected.py
```

## Measure definition

PCR-style, adapted; **not a certified HEDIS rate**. Claims admitted no more than 1 day after the running maximum
discharge are one stay (direct transfers). Index stays: discharged 2009-01-01 to 2010-12-01, 12 months of Part A and
no HMO months in the prior calendar year, alive at discharge. Readmission: any admission 2 to 30 days after
discharge. Not implemented: the planned-readmission exclusion and the outlier-member rule.

## Authorship

Built with AI coding assistance (Claude Code), from my scope and my two earlier builds of this pipeline. The SAS
run on OnDemand is done in my own account.
