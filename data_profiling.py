"""
data_profiling.py
------------------
Profiles every source table for the Springer Capital referral program
take-home test. For each column in each CSV, this reports:
    - data type (as inferred by pandas after loading)
    - row count
    - null count / percentage populated
    - distinct value count
    - min / max value (for numeric and datetime columns)
    - a sample of up to 5 distinct values (helps spot format quirks,
      e.g. "null" stored as a literal string, or "10 days" as text)

This is a read-only diagnostic step — it does not clean or transform
anything. Its job is to surface exactly the kind of quirks that would
otherwise cause silent bugs downstream (e.g. the literal string "null"
in several tables, which pandas will NOT treat as NaN unless told to).

Output: output/data_profiling_report.csv
"""

import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Every source table. Note the actual filename is `lead_log.csv` (singular)
# even though the business spec calls the table `lead_logs`.
SOURCE_FILES = {
    "lead_log": "lead_log.csv",
    "paid_transactions": "paid_transactions.csv",
    "referral_rewards": "referral_rewards.csv",
    "user_logs": "user_logs.csv",
    "user_referral_logs": "user_referral_logs.csv",
    "user_referral_statuses": "user_referral_statuses.csv",
    "user_referrals": "user_referrals.csv",
}


def profile_table(table_name: str, df: pd.DataFrame) -> list[dict]:
    """Return one profiling row per column of df."""
    rows = []
    n_rows = len(df)
    for col in df.columns:
        series = df[col]
        null_count = int(series.isna().sum())
        pct_populated = round((n_rows - null_count) / n_rows * 100, 2) if n_rows else 0.0
        distinct_count = int(series.nunique(dropna=True))

        # min/max only make sense for numeric or datetime-like columns
        min_val, max_val = "", ""
        numeric_series = pd.to_numeric(series, errors="coerce")
        if numeric_series.notna().sum() > 0 and numeric_series.notna().sum() == series.notna().sum():
            min_val = numeric_series.min()
            max_val = numeric_series.max()

        sample_values = series.dropna().astype(str).unique()[:5]

        rows.append(
            {
                "table_name": table_name,
                "column_name": col,
                "data_type": str(series.dtype),
                "row_count": n_rows,
                "null_count": null_count,
                "percentage_populated": pct_populated,
                "distinct_value_count": distinct_count,
                "min_value": min_val,
                "max_value": max_val,
                "sample_values": " | ".join(sample_values),
            }
        )
    return rows


def main():
    all_rows = []
    for table_name, filename in SOURCE_FILES.items():
        path = DATA_DIR / filename
        # IMPORTANT: these CSVs use the literal string "null" (not an
        # empty cell) to represent missing data in several columns.
        # Without na_values, pandas would treat "null" as a normal string.
        df = pd.read_csv(path, na_values=["null", "NULL", "None", ""])
        all_rows.extend(profile_table(table_name, df))
        print(f"Profiled {table_name}: {len(df)} rows, {len(df.columns)} columns")

    report = pd.DataFrame(all_rows)
    out_path = OUTPUT_DIR / "data_profiling_report.csv"
    report.to_csv(out_path, index=False)
    print(f"\nProfiling report written to {out_path}")


if __name__ == "__main__":
    main()
