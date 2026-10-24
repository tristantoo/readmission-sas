"""Trim CMS DE-SynPUF Sample 1 to the columns the SAS programs read, and prove in pandas that the
trimmed files still reproduce the reference mart counts before anything is uploaded to SAS OnDemand.

The cohort rules mirror sas/02_mart.sas line for line (and healthcare-claims-analytics/sql/02_build_mart.sql).
"""
from pathlib import Path

import numpy as np
import pandas as pd

DX_COLS = [f"ICD9_DGNS_CD_{i}" for i in range(1, 11)]
CHRONIC = ["SP_ALZHDMTA", "SP_CHF", "SP_CHRNKIDN", "SP_CNCR", "SP_COPD", "SP_DEPRESSN",
           "SP_DIABETES", "SP_ISCHMCHT", "SP_OSTEOPRS", "SP_RA_OA", "SP_STRKETIA"]
# Column order is the contract with the INPUT statements in sas/01_import.sas. Change both or neither.
IP_COLS = ["DESYNPUF_ID", "CLM_ID", "SEGMENT", "CLM_ADMSN_DT", "NCH_BENE_DSCHRG_DT",
           "PRVDR_NUM", "CLM_PMT_AMT", "CLM_DRG_CD", *DX_COLS]
BENE_COLS = ["DESYNPUF_ID", "BENE_BIRTH_DT", "BENE_DEATH_DT", "BENE_SEX_IDENT_CD", "BENE_RACE_CD",
             "BENE_ESRD_IND", "BENE_HI_CVRAGE_TOT_MONS", "BENE_HMO_CVRAGE_TOT_MONS", "MEDREIMB_IP", *CHRONIC]
# upload name -> (glob in the raw dir, columns kept)
UPLOADS = {
    "inpatient.csv": ("*Inpatient_Claims*.csv", IP_COLS),
    "bene_2008.csv": ("*2008_Beneficiary_Summary*.csv", BENE_COLS),
    "bene_2009.csv": ("*2009_Beneficiary_Summary*.csv", BENE_COLS),
    "bene_2010.csv": ("*2010_Beneficiary_Summary*.csv", BENE_COLS),
}
MAX_MB = 10  # strictest per-file upload cap reported for SAS Studio; the official quota is 5 GB total
TRAIN = ("2009-01-01", "2009-12-31")
TEST = ("2010-01-01", "2010-12-01")


def find(raw: Path, pattern: str) -> Path:
    hits = sorted(Path(raw).glob(pattern))
    if len(hits) != 1:
        raise FileNotFoundError(f"expected exactly one {pattern} in {raw}, found {len(hits)}")
    return hits[0]


def trim(src: Path, dst: Path, cols: list[str]) -> int:
    """Keep every row and only `cols`, in that order. LF line endings so SAS on Linux reads clean numbers."""
    df = pd.read_csv(src, dtype=str, usecols=cols)[cols]
    dst.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dst, index=False, lineterminator="\n")
    return len(df)


def _dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="%Y%m%d")


def build_stays(ip: pd.DataFrame) -> pd.DataFrame:
    """Segment-1 claims -> stays. A claim admitted on or before the running max discharge + 1 day joins the stay."""
    c = ip[ip["SEGMENT"] == "1"].copy()
    c["admit_dt"], c["discharge_dt"] = _dt(c["CLM_ADMSN_DT"]), _dt(c["NCH_BENE_DSCHRG_DT"])
    c["clm"] = pd.to_numeric(c["CLM_ID"])
    c["paid"] = pd.to_numeric(c["CLM_PMT_AMT"]).fillna(0.0)
    c["n_dx"] = c[DX_COLS].notna().sum(axis=1)
    c = c.sort_values(["DESYNPUF_ID", "admit_dt", "discharge_dt", "clm"]).reset_index(drop=True)
    run_max = c.groupby("DESYNPUF_ID")["discharge_dt"].cummax().groupby(c["DESYNPUF_ID"]).shift()
    new = run_max.isna() | (c["admit_dt"] > run_max + pd.Timedelta(days=1))
    c["stay_seq"] = new.astype(int).groupby(c["DESYNPUF_ID"]).cumsum()
    g = c.groupby(["DESYNPUF_ID", "stay_seq"], sort=False)
    s = g.agg(admit_dt=("admit_dt", "min"), discharge_dt=("discharge_dt", "max"),
              prvdr_num=("PRVDR_NUM", "last"), paid=("paid", "sum"), n_dx=("n_dx", "max"),
              n_claims=("clm", "size")).reset_index()
    s["los_days"] = (s["discharge_dt"] - s["admit_dt"]).dt.days
    s["days_to_next_admit"] = (s.groupby("DESYNPUF_ID")["admit_dt"].shift(-1) - s["discharge_dt"]).dt.days
    pairs = s[["DESYNPUF_ID", "stay_seq", "admit_dt"]].merge(s[["DESYNPUF_ID", "discharge_dt"]], on="DESYNPUF_ID")
    hit = (pairs["discharge_dt"] < pairs["admit_dt"]) & (pairs["discharge_dt"] >= pairs["admit_dt"] - pd.Timedelta(days=365))
    prior = pairs[hit].groupby(["DESYNPUF_ID", "stay_seq"]).size().rename("prior_stays_365")
    s = s.merge(prior, on=["DESYNPUF_ID", "stay_seq"], how="left")
    s["prior_stays_365"] = s["prior_stays_365"].fillna(0).astype(int)
    return s


def build_index(stays: pd.DataFrame, bene: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """PCR-style index stays for both splits, plus attrition counts keyed attrition_<split>_<step>."""
    df = stays[(stays["discharge_dt"] >= TRAIN[0]) & (stays["discharge_dt"] <= TEST[1])].copy()
    df["split"] = np.where(df["discharge_dt"] <= TRAIN[1], "train", "test")
    df["file_year"] = df["discharge_dt"].dt.year - 1
    death = bene.groupby("DESYNPUF_ID", as_index=False)["BENE_DEATH_DT"].max()  # max skips blanks, like SQL MAX
    j = df.merge(bene, on=["DESYNPUF_ID", "file_year"], how="left", indicator=True)
    j = j.drop(columns="BENE_DEATH_DT").merge(death, on="DESYNPUF_ID", how="left")
    j["death_dt"] = _dt(j["BENE_DEATH_DT"])
    has_prior = j["_merge"] == "both"
    full_ffs = (pd.to_numeric(j["BENE_HI_CVRAGE_TOT_MONS"]) == 12) & (pd.to_numeric(j["BENE_HMO_CVRAGE_TOT_MONS"]) == 0)
    alive = j["death_dt"].isna() | (j["death_dt"] > j["discharge_dt"])
    steps = [pd.Series(True, index=j.index), has_prior, has_prior & full_ffs, has_prior & full_ffs & alive]
    att = {f"attrition_{sp}_{k}": int((m & (j["split"] == sp)).sum())
           for sp in ("train", "test") for k, m in enumerate(steps, 1)}
    idx = j[steps[-1]].copy()
    idx["readmit_30"] = (idx["days_to_next_admit"] <= 30).astype(int)  # NaN <= 30 is False in pandas
    idx["died_30"] = ((idx["death_dt"] - idx["discharge_dt"]).dt.days <= 30).astype(int)
    return idx.drop(columns="_merge").reset_index(drop=True), att


def load_upload(d: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    ip = pd.read_csv(Path(d) / "inpatient.csv", dtype=str)
    bene = pd.concat([pd.read_csv(Path(d) / f"bene_{y}.csv", dtype=str).assign(file_year=y)
                      for y in (2008, 2009, 2010)], ignore_index=True)
    return ip, bene


def mart_counts(ip: pd.DataFrame, bene: pd.DataFrame) -> dict:
    """Every check in expected/mart_counts.csv, computed from the trimmed files."""
    stays = build_stays(ip)
    idx, att = build_index(stays, bene)
    out = {"raw_claim_rows": len(ip), "raw_claim_seg1_rows": int((ip["SEGMENT"] == "1").sum()),
           "raw_bene_rows": len(bene), "ip_stay_rows": len(stays), "index_stay_rows": len(idx), **att}
    for sp, g in [*idx.groupby("split"), ("overall", idx)]:
        out |= {f"{sp}_index_stays": len(g), f"{sp}_readmits": int(g["readmit_30"].sum()),
                f"{sp}_died_30": int(g["died_30"].sum()), f"{sp}_prior_stays_365": int(g["prior_stays_365"].sum())}
    test = idx[idx["split"] == "test"]
    out["test_beneficiaries"] = test["DESYNPUF_ID"].nunique()
    out["monthly_rows"] = idx.groupby(["split", idx["discharge_dt"].dt.to_period("M")]).ngroups
    out["provider_rows_25plus"] = int((test.groupby("prvdr_num").size() >= 25).sum())
    for m, g in test.groupby(test["discharge_dt"].dt.strftime("%Y_%m")):
        out[f"month_{m}_index_stays"], out[f"month_{m}_readmits"] = len(g), int(g["readmit_30"].sum())
    return {k: int(v) for k, v in out.items()}


def compare(actual: dict, expected: pd.DataFrame) -> pd.DataFrame:
    """One row per expected check: PASS if |actual - expected| <= tol. Missing actuals FAIL."""
    t = expected.copy()
    t["actual"] = t["check"].map(actual)
    t["result"] = np.where((t["actual"] - t["expected"]).abs() <= t["tol"], "PASS", "FAIL")
    return t


def sas_decile(p: np.ndarray, k: int = 10) -> np.ndarray:
    """The decile rule sas/04_model.sas uses (PROC RANK TIES=LOW, then ceil((r-1)*k/(n-1)) - 1, floored at 0).

    It reproduces pandas.qcut(p, k, labels=False), whose edges are numpy's linear quantiles, which SAS PCTLDEF
    does not offer. Tested against qcut in tests/test_prep.py.
    """
    p = np.asarray(p, float)
    r = pd.Series(p).rank(method="min").to_numpy()  # TIES=LOW
    return np.maximum(np.ceil((r - 1) * k / (len(p) - 1)) - 1, 0).astype(int)
