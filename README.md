# Springer Capital — Referral Program Data Pipeline

Take-home test submission: a data profiling + fraud-detection pipeline
for the referral program described in the test brief. Given 7 source
CSVs, it produces a single `referral_fraud_report.csv` with a
`is_business_logic_valid` flag per referral.

## What's in this repo

| File | Purpose |
|---|---|
| `data_profiling.py` | Profiles all 7 source tables (null count, distinct count, min/max, sample values) |
| `referral_fraud_pipeline.py` | Main pipeline: load → clean → join → fraud logic → CSV report |
| `data/` | The 7 source CSVs |
| `output/` | Where both scripts write their results |
| `Dockerfile` | Containerizes the whole thing |
| `requirements.txt` | Python dependencies (pandas) |
| `data_dictionary.xlsx` | Column-by-column description of the output report, for non-technical readers |

## Running it locally (without Docker)

```bash
pip install -r requirements.txt
python data_profiling.py           # writes output/data_profiling_report.csv
python referral_fraud_pipeline.py  # writes output/referral_fraud_report.csv
```

## Running it with Docker

Build the image:

```bash
docker build -t springer-referral-pipeline .
```

Run it, mounting a local folder so the report lands **outside** the
container (this satisfies the "report should be stored outside the
container" requirement):

```bash
docker run --rm -v "$(pwd)/output:/app/output" springer-referral-pipeline
```

After it finishes, `./output/data_profiling_report.csv` and
`./output/referral_fraud_report.csv` will be on your host machine.

## Business logic: how `is_business_logic_valid` is decided

Implemented exactly per the test brief's two VALID conditions and
five INVALID conditions. Where a referral matches conditions from
both sides at once, **INVALID wins** — since this is a fraud-detection
pipeline, the safer default when signals conflict is to flag for
review rather than approve. A referral matching neither an explicit
valid nor invalid rule defaults to `False` (not confirmed valid).

## Assumptions made where the spec was ambiguous

The brief doesn't fully specify a few things. Here's what was decided,
and why — all called out again inline in the pipeline script's
docstring and comments:

1. **Timezone for `referral_at` / `updated_at` / reward-granted
   timestamps** (none of which have their own timezone column): these
   are converted using the **referrer's home-club timezone**
   (`user_logs.timezone_homeclub`), since they're actions on the
   referrer's account. `transaction_at` uses its own
   `timezone_transaction` column directly, as given.
2. **"Same month as referral creation" / "after the referral"**
   comparisons use the *local* (timezone-adjusted) timestamps, matching
   how a business user reads a calendar month.
3. **"Referrer's membership has not expired"** is checked against the
   referral's creation date, not today's date, since this is a fixed
   historical dataset (May–July 2024).
4. **Initcap** is applied to status/type/name fields but **not** to
   club or location names (`homeclub`, `transaction_location`), per
   the spec's explicit exemption — confirmed against the sample output
   in the brief, which keeps `"PERMATA HIJAU"` upper-case but shows
   `"Paid"` / `"New"` in title case.

## Data quality findings (flagged, not silently patched)

A few real gaps surfaced during profiling and joining — noted here per
the brief's "if you found any invalid business logic, that's a plus":

- **`user_referral_logs.user_referral_id` barely maps to any real
  `referral_id`.** Only 7 of 78 distinct log IDs match a referral in
  `user_referrals`, and **none** of the 10 `is_reward_granted = TRUE`
  log rows match a real referral. This means the `reward_granted_at`
  column in the report is empty for every row — not a bug in the join,
  but a genuine referential-integrity gap in the source data worth
  raising with the team that owns `user_referral_logs`.
- **3 "Lead"-sourced referrals have no matching record in
  `lead_log.csv`** (their `referee_id` doesn't correspond to any
  `lead_id`), so `referral_source_category` is null for those rows.
- **4 referrals reference a `referrer_id` that doesn't exist in
  `user_logs.csv`** at all (separate from the 13 referrals that have
  no `referrer_id` in the first place) — referrer name/phone/homeclub
  are null for these for that reason, not a join bug.

## Credentials note

This pipeline reads only local CSV files and writes only local CSV
output — no cloud storage or external credentials are used anywhere
in the code. If a future version uploads the report to cloud storage,
credentials should be supplied via environment variables at runtime
(e.g. `docker run -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=...`),
never hard-coded in the script.
