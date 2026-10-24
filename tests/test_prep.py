import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import prep

ROOT = Path(__file__).resolve().parents[1]
SAS = {p.name: p.read_text() for p in (ROOT / "sas").glob("*.sas")}
EXPECTED = pd.concat([pd.read_csv(ROOT / "expected" / f) for f in ("mart_counts.csv", "model_metrics.csv")])


def claims(rows):
    """rows: (id, clm, admit, discharge[, provider]) with dates as YYYYMMDD strings."""
    df = pd.DataFrame([dict(DESYNPUF_ID=r[0], CLM_ID=str(r[1]), SEGMENT="1", CLM_ADMSN_DT=r[2],
                            NCH_BENE_DSCHRG_DT=r[3], PRVDR_NUM=r[4] if len(r) > 4 else "P1",
                            CLM_PMT_AMT="100.00", CLM_DRG_CD="001") for r in rows])
    for c in prep.DX_COLS:
        df[c] = np.nan
    return df


def bene(ids, death=None, hi="12", hmo="0", years=(2008, 2009)):
    rows = [dict(DESYNPUF_ID=i, BENE_BIRTH_DT="19400101", BENE_DEATH_DT=(death or {}).get(i, np.nan),
                 BENE_SEX_IDENT_CD="2", BENE_RACE_CD="1", BENE_ESRD_IND="0", BENE_HI_CVRAGE_TOT_MONS=hi,
                 BENE_HMO_CVRAGE_TOT_MONS=hmo, MEDREIMB_IP="0", file_year=y) for i in ids for y in years]
    return pd.DataFrame(rows)


def test_transfer_merge_uses_running_max_plus_one_day():
    s = prep.build_stays(claims([
        ("A", 1, "20090101", "20090110", "P1"),
        ("A", 2, "20090103", "20090105", "P2"),   # nested inside claim 1: same stay
        ("A", 3, "20090111", "20090115", "P3"),   # +1 day after running max 01-10: transfer, same stay
        ("A", 4, "20090117", "20090120", "P4"),   # +2 days: new stay
        ("B", 9, "20090101", "20090102"),
    ]))
    a = s[s["DESYNPUF_ID"] == "A"]
    assert a["stay_seq"].tolist() == [1, 2]
    first = a.iloc[0]
    assert (first["n_claims"], first["prvdr_num"], first["los_days"]) == (3, "P3", 14)
    assert first["days_to_next_admit"] == 2
    assert np.isnan(a.iloc[1]["days_to_next_admit"])


def test_running_max_not_previous_claim():
    # claim 2 ends before claim 1; claim 3 is within 1 day of claim 1's discharge, not claim 2's
    s = prep.build_stays(claims([("A", 1, "20090101", "20090120"), ("A", 2, "20090102", "20090103"),
                                 ("A", 3, "20090121", "20090125")]))
    assert len(s) == 1


def test_claim_id_ties_break_numerically():
    # same dates: clm 10 sorts after clm 9 as a number, so P10 is the discharging provider
    s = prep.build_stays(claims([("A", 10, "20090101", "20090105", "P10"), ("A", 9, "20090101", "20090105", "P9")]))
    assert s["prvdr_num"].item() == "P10"


def test_prior_stays_365_window_is_strictly_before_admission():
    s = prep.build_stays(claims([("A", 1, "20080101", "20080105"), ("A", 2, "20090101", "20090105"),
                                 ("A", 3, "20090106", "20090106")]))  # 20090106 is a transfer of stay 2
    assert s["prior_stays_365"].tolist() == [0, 1]   # 2008-01-05 is 362 days before 2009-01-01
    s = prep.build_stays(claims([("A", 1, "20080101", "20080105"), ("A", 2, "20090106", "20090110")]))
    assert s["prior_stays_365"].tolist() == [0, 0]   # 367 days: outside the window


def test_index_rules_missing_values_and_attrition():
    s = prep.build_stays(claims([
        ("A", 1, "20090301", "20090305"), ("A", 2, "20090401", "20090403"),   # readmit after 27 days
        ("B", 3, "20090301", "20090305"),                                      # no later stay: not a readmit
        ("C", 4, "20090301", "20090305"),                                      # died 20090401: died_30
        ("D", 5, "20090501", "20090505"),                                      # died before discharge: out
        ("E", 6, "20100105", "20100110"),                                      # test split
        ("F", 7, "20101202", "20101205"),                                      # after Dec 1: out of window
    ]))
    b = bene(list("ABCDEF"), death={"C": "20090401", "D": "20090501"}, years=(2008, 2009))
    idx, att = prep.build_index(s, b)
    assert set(idx["DESYNPUF_ID"]) == set("ABCE")
    by = idx.set_index(["DESYNPUF_ID", "stay_seq"])
    assert by.loc[("A", 1), "readmit_30"] == 1 and by.loc[("A", 2), "readmit_30"] == 0
    assert by.loc[("B", 1), "readmit_30"] == 0
    assert by.loc[("C", 1), "died_30"] == 1 and by.loc[("B", 1), "died_30"] == 0
    assert att["attrition_train_1"] == 5 and att["attrition_train_4"] == 4
    assert att["attrition_test_1"] == 1


def test_coverage_exclusion_and_missing_prior_year():
    s = prep.build_stays(claims([("A", 1, "20090301", "20090305"), ("B", 2, "20090301", "20090305")]))
    b = pd.concat([bene(["A"], hmo="3"), bene(["B"], years=(2009,))])  # B has no 2008 row
    idx, att = prep.build_index(s, b)
    assert idx.empty
    assert [att[f"attrition_train_{k}"] for k in (1, 2, 3, 4)] == [2, 1, 0, 0]


@pytest.mark.parametrize("n,rounding", [(9385, None), (1000, 2), (500, 3)])
def test_sas_decile_rule_reproduces_qcut(n, rounding):
    p = np.random.default_rng(n).beta(2, 25, n)
    if rounding:
        p = p.round(rounding)   # heavy ties
    try:
        q = pd.qcut(p, 10, labels=False)
    except ValueError:
        pytest.skip("qcut cannot form 10 unique edges")
    assert (prep.sas_decile(p) == q).all()


def test_trim_keeps_rows_column_order_blanks_and_lf(tmp_path):
    src = tmp_path / "raw.csv"
    full = claims([("A", 1, "20090101", "20090105")]).assign(EXTRA="x")
    full.to_csv(src, index=False)
    assert prep.trim(src, tmp_path / "out.csv", prep.IP_COLS) == 1
    raw = (tmp_path / "out.csv").read_bytes()
    assert b"\r" not in raw
    header, row = raw.decode().splitlines()
    assert header.split(",") == prep.IP_COLS
    assert row.endswith("," * 10)   # ten empty dx fields stay empty (SAS DSD reads them as blank)


def test_expected_csvs_well_formed():
    assert EXPECTED["check"].is_unique
    assert EXPECTED["check"].str.len().max() <= 32 and (EXPECTED["tol"] >= 0).all()
    e = EXPECTED.set_index("check")["expected"]
    assert (e["ip_stay_rows"], e["index_stay_rows"], e["overall_readmits"]) == (64273, 27242, 1665)
    assert (e["train_readmits"], e["train_index_stays"], e["test_readmits"], e["test_index_stays"]) == (1296, 17857, 369, 9385)
    assert e["auc"] == pytest.approx(0.6118, abs=1e-4)


def test_every_expected_check_is_computed_in_sas():
    sas = "\n".join(SAS.values())
    features = re.search(r"%let features\s*=([^;]*);", SAS["04_model.sas"]).group(1).split()
    dynamic = [r"(train|test)_(index_stays|readmits|died_30|prior_stays_365)", r"attrition_(train|test)_[1-4]",
               r"month_2010_\d\d_(index_stays|readmits|expected)", r"decile_\d_(n|mean_pred|obs_rate)",
               r"coef_(intercept|" + "|".join(features) + ")"]
    missing = [c for c in EXPECTED["check"] if f"'{c}'" not in sas and c not in SAS["04_model.sas"]
               and not any(re.fullmatch(d, c) for d in dynamic)]
    assert missing == []


def test_sas_reads_the_files_prep_writes_and_stays_short():
    for name in prep.UPLOADS:
        stem = name.removesuffix(".csv")
        assert stem in SAS["01_import.sas"] or stem.startswith("bene_")
    assert "bene_&year..csv" in SAS["01_import.sas"]
    for f in [*ROOT.glob("sas/*.sas"), *ROOT.glob("src/*.py"), *ROOT.glob("scripts/*.py"), *ROOT.glob("tests/*.py")]:
        assert len(f.read_text().splitlines()) < 500, f


@pytest.mark.skipif(not (ROOT.parent / "healthcare-claims-analytics" / "data" / "raw").exists(),
                    reason="CMS raw files not on this machine")
def test_trimmed_upload_reproduces_reference_counts(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("prepare_upload", ROOT / "scripts" / "prepare_upload.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--out", str(tmp_path)]) == 0
