"""Build the four CSVs to upload to SAS OnDemand, then prove they reproduce the reference mart counts.

uv run python scripts/prepare_upload.py                  # raw files from ../healthcare-claims-analytics/data/raw
uv run python scripts/prepare_upload.py --raw D:/synpuf  # or anywhere (SYNPUF_RAW_DIR also works)
Writes data/upload/ (gitignored). Exits 1 if a file is over the size cap or any count check fails.
"""
import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import prep  # noqa: E402

DEFAULT_RAW = ROOT.parent / "healthcare-claims-analytics" / "data" / "raw"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=os.environ.get("SYNPUF_RAW_DIR", DEFAULT_RAW))
    ap.add_argument("--out", default=ROOT / "data" / "upload")
    a = ap.parse_args(argv)
    raw, out = Path(a.raw), Path(a.out)

    too_big = []
    for name, (pattern, cols) in prep.UPLOADS.items():
        n = prep.trim(prep.find(raw, pattern), out / name, cols)
        mb = (out / name).stat().st_size / 1e6
        print(f"{name:15s} {n:>8,} rows {len(cols):>3} cols {mb:6.2f} MB")
        if mb > prep.MAX_MB:
            too_big.append(name)

    res = prep.compare(prep.mart_counts(*prep.load_upload(out)), pd.read_csv(ROOT / "expected" / "mart_counts.csv"))
    fails = res[res["result"] == "FAIL"]
    print(f"\npandas on the trimmed files: {len(res) - len(fails)}/{len(res)} mart checks PASS")
    if len(fails):
        print(fails[["check", "expected", "actual"]].to_string(index=False))
    if too_big:
        print(f"over {prep.MAX_MB} MB: {too_big}")
    return 1 if len(fails) or too_big else 0


if __name__ == "__main__":
    sys.exit(main())
