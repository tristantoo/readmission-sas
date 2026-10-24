"""Write expected/*.csv from the two existing builds. Run once; the CSVs are committed.

uv run python scripts/make_expected.py
Sources (read only):
  readmission-spark/tests/reference_sqlserver.json   live SQL Server LocalDB mart counts (34/34 Spark parity)
  healthcare-claims-analytics/outputs/                Python GLM: metrics, coefficients, deciles, O/E, monthly
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT.parent
REF = DOCS / "readmission-spark" / "tests" / "reference_sqlserver.json"
CLAIMS = DOCS / "healthcare-claims-analytics" / "outputs"
# tolerance per model check: estimates from an iterative fit get a small band, pure counts get 0
TOL = {"auc": 1e-4, "brier": 1e-6, "ece": 1e-4, "cal_intercept": 1e-3, "cal_slope": 1e-3,
       "mean_pred": 1e-5, "obs_rate": 1e-9, "top_decile_capture": 1e-9}


def mart_counts() -> list[tuple]:
    ref = json.loads(REF.read_text())
    c = ref["counts"]
    rows = [("raw_claim_rows", c["raw.inpatient_claim"]), ("raw_claim_seg1_rows", c["raw.inpatient_claim.segment1"]),
            ("raw_bene_rows", c["raw.beneficiary_year"]), ("ip_stay_rows", c["mart.ip_stay"]),
            ("index_stay_rows", c["mart.index_stay"]), ("monthly_rows", c["mart.v_monthly_readmission"]),
            ("provider_rows_25plus", c["mart.v_provider_oe"])]
    for s in ref["by_split"]:
        rows += [(f"{s['split']}_{k}", s[k]) for k in ("index_stays", "readmits", "died_30", "prior_stays_365")]
    for k in ("index_stays", "readmits", "died_30", "prior_stays_365"):
        rows.append((f"overall_{k}", sum(s[k] for s in ref["by_split"])))
    rows += [(f"attrition_{a['split']}_{a['step_no']}", a["n"]) for a in ref["attrition"]]
    rows = [(k, v, 0, "sqlserver") for k, v in rows]
    m = json.loads((CLAIMS / "metrics.json").read_text())
    rows.append(("test_beneficiaries", m["n"]["test_beneficiaries"], 0, "claims_py"))
    for r in pd.read_csv(CLAIMS / "monthly_readmission.csv").itertuples():
        mm = r.month.replace("-", "_")
        rows += [(f"month_{mm}_index_stays", r.index_stays, 0, "claims_py"), (f"month_{mm}_readmits", r.readmits, 0, "claims_py")]
    return rows


def model_metrics() -> list[tuple]:
    m = json.loads((CLAIMS / "metrics.json").read_text())
    rows = [(k, m["test_metrics"]["glm"][k], t) for k, t in TOL.items() if k != "top_decile_capture"]
    rows.append(("top_decile_capture", m["top_decile_capture"]["glm"], TOL["top_decile_capture"]))
    # PROC LOGISTIC silently drops rows with any missing feature; this catches it
    rows.append(("train_complete_rows", m["n"]["train_index"], 0))
    coef = pd.read_csv(CLAIMS / "glm_coefficients.csv", index_col=0)["Coef."]
    rows += [("coef_" + ("intercept" if k == "const" else k), v, 1e-4) for k, v in coef.items()]
    for r in pd.read_csv(CLAIMS / "calibration_glm.csv").itertuples():
        rows += [(f"decile_{r.bin}_n", r.n, 0), (f"decile_{r.bin}_mean_pred", r.mean_pred, 1e-5),
                 (f"decile_{r.bin}_obs_rate", r.obs_rate, 1e-9)]
    oe = pd.read_csv(CLAIMS / "provider_oe.csv")
    rows += [("provider_observed_sum", oe["observed"].sum(), 0), ("provider_expected_sum", oe["expected"].sum(), 1e-3),
             ("provider_max_oe", oe["oe_ratio"].max(), 1e-3),
             ("provider_flag_worse", (oe["flag"] == "worse than expected").sum(), 0),
             ("provider_flag_better", (oe["flag"] == "better than expected").sum(), 0)]
    for r in pd.read_csv(CLAIMS / "monthly_readmission.csv").itertuples():
        rows.append((f"month_{r.month.replace('-', '_')}_expected", r.expected, 1e-3))
    return [(k, v, t, "claims_py") for k, v, t in rows]


def write(rows: list[tuple], path: Path) -> None:
    df = pd.DataFrame(rows, columns=["check", "expected", "tol", "source"])
    if df["check"].duplicated().any() or df["check"].str.len().max() > 32:
        sys.exit(f"{path.name}: duplicate or over-32-character check names")
    path.parent.mkdir(exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n", float_format="%.12g")
    print(f"{path.relative_to(ROOT)}: {len(df)} checks")


if __name__ == "__main__":
    write(mart_counts(), ROOT / "expected" / "mart_counts.csv")
    write(model_metrics(), ROOT / "expected" / "model_metrics.csv")
