"""
referral_fraud_pipeline.py
---------------------------
Springer Capital take-home test — Referral program data pipeline.

Loads the 7 source tables, cleans and joins them into a single
referral_details table, converts timestamps from UTC to local time,
and flags each referral reward as valid or invalid based on the
business rules in the test spec.

ASSUMPTIONS (documented here because the spec leaves them open):

1. Timezone for events with no timezone column of their own
   (referral_at, updated_at on user_referrals; the reward-granted
   timestamp from user_referral_logs) is taken from the REFERRER's
   home club timezone (user_logs.timezone_homeclub), since these are
   actions the referrer's account triggers. paid_transactions already
   carries its own timezone_transaction column, so that one is used
   directly. If the referrer is unknown (null referrer_id) or not
   found, the timestamp is left in UTC and flagged in a helper column
   is_timezone_estimated so this is auditable, not silent.

2. "Transaction occurred after the referral was created" / "same
   month as referral creation" are evaluated using the LOCAL
   (timezone-adjusted) timestamps, since that matches how a business
   user would read a calendar month in practice.

3. "Referrer's membership has not expired" is evaluated against the
   referral's creation date (i.e. was the membership valid at the
   time the referral happened), not against today's date, since this
   is a historical dataset from 2024.

4. When a referral matches BOTH a "valid" condition and an "invalid"
   condition simultaneously, INVALID WINS. This is a fraud-detection
   pipeline, so the safer default when signals conflict is to flag it
   for review rather than wave it through.

5. A referral that matches neither an explicit valid nor an explicit
   invalid rule defaults to is_business_logic_valid = False (not
   confirmed valid), since the burden of proof for paying out a
   reward should sit with matching a valid rule.

6. String "Initcap" (title case) is applied to descriptive/status
   fields (transaction_status, transaction_type, names) but NOT to
   club/location names (homeclub, transaction_location), which the
   spec explicitly exempts and which the sample output confirms
   (e.g. "PERMATA HIJAU", "BENHIL" stay upper case).
"""

import re
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

NA_VALUES = ["null", "NULL", "None", ""]


# ---------------------------------------------------------------------------
# 1. DATA LOADING
# ---------------------------------------------------------------------------
def load_data() -> dict[str, pd.DataFrame]:
    """Load all 7 CSVs into DataFrames, parsing the literal string "null"
    (used throughout these files) as a real missing value."""
    files = {
        "lead_log": "lead_log.csv",
        "paid_transactions": "paid_transactions.csv",
        "referral_rewards": "referral_rewards.csv",
        "user_logs": "user_logs.csv",
        "user_referral_logs": "user_referral_logs.csv",
        "user_referral_statuses": "user_referral_statuses.csv",
        "user_referrals": "user_referrals.csv",
    }
    return {name: pd.read_csv(DATA_DIR / fname, na_values=NA_VALUES) for name, fname in files.items()}


# ---------------------------------------------------------------------------
# 2. DATA CLEANING
# ---------------------------------------------------------------------------
def title_case_except_clubs(series: pd.Series) -> pd.Series:
    """Apply Initcap (title case) to a string column. Caller is
    responsible for NOT calling this on club/location name columns."""
    return series.astype("string").str.strip().str.title()


def clean_data(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    lead_log = tables["lead_log"].copy()
    paid_transactions = tables["paid_transactions"].copy()
    referral_rewards = tables["referral_rewards"].copy()
    user_logs = tables["user_logs"].copy()
    user_referral_logs = tables["user_referral_logs"].copy()
    user_referral_statuses = tables["user_referral_statuses"].copy()
    user_referrals = tables["user_referrals"].copy()

    # --- Parse all timestamps as UTC-aware datetimes ---
    lead_log["created_at"] = pd.to_datetime(lead_log["created_at"], utc=True)
    paid_transactions["transaction_at"] = pd.to_datetime(paid_transactions["transaction_at"], utc=True)
    referral_rewards["created_at"] = pd.to_datetime(referral_rewards["created_at"], utc=True)
    user_referral_logs["created_at"] = pd.to_datetime(user_referral_logs["created_at"], utc=True)
    user_referral_statuses["created_at"] = pd.to_datetime(user_referral_statuses["created_at"], utc=True)
    user_referrals["referral_at"] = pd.to_datetime(user_referrals["referral_at"], utc=True)
    user_referrals["updated_at"] = pd.to_datetime(user_referrals["updated_at"], utc=True)
    user_logs["membership_expired_date"] = pd.to_datetime(user_logs["membership_expired_date"], utc=True)

    # --- Booleans stored as strings ---
    user_logs["is_deleted"] = user_logs["is_deleted"].astype("string").str.lower().eq("true")
    user_referral_logs["is_reward_granted"] = (
        user_referral_logs["is_reward_granted"].astype("string").str.lower().eq("true")
    )

    # --- Dedupe reference/lookup tables (source data has repeated rows) ---
    # user_logs has multiple identical-looking log rows per user_id; keep
    # the most recent log entry (highest id) per user.
    user_logs = user_logs.sort_values("id").drop_duplicates(subset="user_id", keep="last")
    # lead_log likewise has repeated rows per lead_id (status changes over
    # time); for our purposes we only need source_category, which does not
    # vary across the duplicates, so keep the most recent entry.
    lead_log = lead_log.sort_values("id").drop_duplicates(subset="lead_id", keep="last")

    # --- Parse reward_value ("10 days" -> 10) ---
    referral_rewards["num_reward_days"] = (
        referral_rewards["reward_value"].astype("string").str.extract(r"(\d+)").astype("Int64")
    )

    # --- Initcap on descriptive fields (NOT club/location names) ---
    paid_transactions["transaction_status"] = title_case_except_clubs(paid_transactions["transaction_status"])
    paid_transactions["transaction_type"] = title_case_except_clubs(paid_transactions["transaction_type"])
    user_referral_statuses["description"] = title_case_except_clubs(user_referral_statuses["description"])
    user_logs["name"] = title_case_except_clubs(user_logs["name"])
    user_referrals["referee_name"] = title_case_except_clubs(user_referrals["referee_name"])
    # referral_source values ("User Sign Up", "Draft Transaction", "Lead")
    # are already correctly cased in source data; title() is idempotent here.
    user_referrals["referral_source"] = title_case_except_clubs(user_referrals["referral_source"])
    # NOTE: homeclub, transaction_location, preferred_location are
    # deliberately left untouched — these are club/location names, which
    # the spec exempts from Initcap.

    return {
        "lead_log": lead_log,
        "paid_transactions": paid_transactions,
        "referral_rewards": referral_rewards,
        "user_logs": user_logs,
        "user_referral_logs": user_referral_logs,
        "user_referral_statuses": user_referral_statuses,
        "user_referrals": user_referrals,
    }


# ---------------------------------------------------------------------------
# 3. DATA PROCESSING (joins, timezone adjustment, feature engineering)
# ---------------------------------------------------------------------------
def to_local(utc_series: pd.Series, tz_series: pd.Series) -> pd.Series:
    """Convert each UTC timestamp to the local time given by the matching
    IANA timezone name in tz_series. Rows with a missing/unknown timezone
    are left as UTC (their is_timezone_estimated flag records this)."""
    out = []
    for ts, tz in zip(utc_series, tz_series):
        if pd.isna(ts):
            out.append(pd.NaT)
        elif pd.isna(tz):
            out.append(ts)  # no timezone available -> leave as UTC
        else:
            out.append(ts.tz_convert(tz))
    return pd.Series(out, index=utc_series.index)


def process_data(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    lead_log = tables["lead_log"]
    paid_transactions = tables["paid_transactions"]
    referral_rewards = tables["referral_rewards"]
    user_logs = tables["user_logs"]
    user_referral_logs = tables["user_referral_logs"]
    user_referral_statuses = tables["user_referral_statuses"]
    df = tables["user_referrals"].copy()

    # --- Referral status text ---
    df = df.merge(
        user_referral_statuses[["id", "description"]].rename(
            columns={"id": "user_referral_status_id", "description": "referral_status"}
        ),
        on="user_referral_status_id",
        how="left",
    )

    # --- Reward details ---
    df = df.merge(
        referral_rewards[["id", "reward_value", "num_reward_days", "reward_type"]].rename(
            columns={"id": "referral_reward_id"}
        ),
        on="referral_reward_id",
        how="left",
    )

    # --- Referrer details (from user_logs) ---
    referrer_cols = user_logs[
        ["user_id", "name", "phone_number", "homeclub", "timezone_homeclub", "membership_expired_date", "is_deleted"]
    ].rename(
        columns={
            "user_id": "referrer_id",
            "name": "referrer_name",
            "phone_number": "referrer_phone_number",
            "homeclub": "referrer_homeclub",
            "timezone_homeclub": "referrer_timezone_homeclub",
            "membership_expired_date": "referrer_membership_expired_date",
            "is_deleted": "referrer_is_deleted",
        }
    )
    df = df.merge(referrer_cols, on="referrer_id", how="left")

    # --- Lead details, only meaningful when referral_source == "Lead" ---
    lead_cols = lead_log[["lead_id", "source_category"]].rename(
        columns={"lead_id": "referee_id", "source_category": "lead_source_category"}
    )
    df = df.merge(lead_cols, on="referee_id", how="left")

    # --- referral_source_category business rule ---
    df["referral_source_category"] = df["referral_source"].map(
        {"User Sign Up": "Online", "Draft Transaction": "Offline"}
    )
    is_lead = df["referral_source"] == "Lead"
    df.loc[is_lead, "referral_source_category"] = df.loc[is_lead, "lead_source_category"]

    # --- Transaction details ---
    txn_cols = paid_transactions.rename(columns={"transaction_id": "_txn_id_join"})
    df = df.merge(txn_cols, left_on="transaction_id", right_on="_txn_id_join", how="left")
    df = df.drop(columns=["_txn_id_join"])

    # --- Reward-granted timestamp: latest TRUE row per referral in
    #     user_referral_logs (a referral can have many log rows; we only
    #     care about the one that actually granted the reward, if any) ---
    granted = (
        user_referral_logs[user_referral_logs["is_reward_granted"]]
        .sort_values("created_at")
        .drop_duplicates(subset="user_referral_id", keep="last")[["user_referral_id", "created_at"]]
        .rename(columns={"user_referral_id": "referral_id", "created_at": "reward_granted_at_utc"})
    )
    df = df.merge(granted, on="referral_id", how="left")

    # --- Timezone adjustment ---
    # referral_at / updated_at / reward_granted_at have no timezone column
    # of their own -> use the referrer's home-club timezone (see module
    # docstring, assumption 1). transaction_at uses its own column.
    df["is_timezone_estimated"] = df["referrer_timezone_homeclub"].isna()
    df["referral_at"] = to_local(df["referral_at"], df["referrer_timezone_homeclub"])
    df["updated_at"] = to_local(df["updated_at"], df["referrer_timezone_homeclub"])
    df["reward_granted_at"] = to_local(df["reward_granted_at_utc"], df["referrer_timezone_homeclub"])
    df["transaction_at"] = to_local(df["transaction_at"], df["timezone_transaction"])

    return df


# ---------------------------------------------------------------------------
# 4. BUSINESS LOGIC — FRAUD DETECTION
# ---------------------------------------------------------------------------
def apply_business_logic(df: pd.DataFrame) -> pd.DataFrame:
    has_reward_value = df["num_reward_days"].notna() & (df["num_reward_days"] > 0)
    is_berhasil = df["referral_status"] == "Berhasil"
    is_pending_or_failed = df["referral_status"].isin(["Menunggu", "Tidak Berhasil"])
    has_transaction = df["transaction_id"].notna()
    txn_is_paid = df["transaction_status"] == "Paid"
    txn_is_new = df["transaction_type"] == "New"
    txn_after_referral = df["transaction_at"] > df["referral_at"]
    txn_before_referral = df["transaction_at"] < df["referral_at"]
    # transaction_at / referral_at are localized to different per-row
    # timezones, so pandas stores them as plain objects (not a uniform
    # datetime64 dtype) and .dt accessors aren't available -> compare
    # year/month element-wise instead.
    def _year_month(ts):
        return (ts.year, ts.month) if pd.notna(ts) else (None, None)

    txn_ym = df["transaction_at"].map(_year_month)
    ref_ym = df["referral_at"].map(_year_month)
    same_month = txn_ym == ref_ym
    membership_active = df["referrer_membership_expired_date"] >= df["referral_at"]
    referrer_not_deleted = df["referrer_is_deleted"] == False  # noqa: E712 (explicit vs NaN)
    reward_granted = df["reward_granted_at"].notna()

    # --- VALID conditions ---
    valid_1 = (
        has_reward_value
        & is_berhasil
        & has_transaction
        & txn_is_paid
        & txn_is_new
        & txn_after_referral
        & same_month
        & membership_active
        & referrer_not_deleted
        & reward_granted
    )
    valid_2 = is_pending_or_failed & ~has_reward_value

    # --- INVALID conditions ---
    invalid_1 = has_reward_value & ~is_berhasil
    invalid_2 = has_reward_value & ~has_transaction
    invalid_3 = ~has_reward_value & has_transaction & txn_is_paid & txn_after_referral
    invalid_4 = is_berhasil & ~has_reward_value
    invalid_5 = has_transaction & txn_before_referral

    df["is_business_logic_valid"] = valid_1 | valid_2
    any_invalid = invalid_1 | invalid_2 | invalid_3 | invalid_4 | invalid_5
    # Invalid wins on conflict — see module docstring, assumption 4.
    df.loc[any_invalid, "is_business_logic_valid"] = False

    return df


# ---------------------------------------------------------------------------
# 5. OUTPUT
# ---------------------------------------------------------------------------
OUTPUT_COLUMNS = [
    "referral_details_id",
    "referral_id",
    "referral_source",
    "referral_source_category",
    "referral_at",
    "referrer_id",
    "referrer_name",
    "referrer_phone_number",
    "referrer_homeclub",
    "referee_id",
    "referee_name",
    "referee_phone",
    "referral_status",
    "num_reward_days",
    "transaction_id",
    "transaction_status",
    "transaction_at",
    "transaction_location",
    "transaction_type",
    "updated_at",
    "reward_granted_at",
    "is_business_logic_valid",
]


def build_report(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("referral_at").reset_index(drop=True)
    df["referral_details_id"] = range(101, 101 + len(df))
    report = df[OUTPUT_COLUMNS].copy()
    return report


def main():
    tables = load_data()
    tables = clean_data(tables)
    df = process_data(tables)
    df = apply_business_logic(df)
    report = build_report(df)

    out_path = OUTPUT_DIR / "referral_fraud_report.csv"
    report.to_csv(out_path, index=False)

    print(f"Report rows: {len(report)} (spec expects 46)")
    print(f"Valid: {report['is_business_logic_valid'].sum()} | "
          f"Invalid: {(~report['is_business_logic_valid']).sum()}")
    print(f"Report written to {out_path}")


if __name__ == "__main__":
    main()
