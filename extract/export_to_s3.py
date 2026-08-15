"""
FleetIQ — Supabase → S3 extract (Phase 1).

Pulls a snapshot of each operational table from Supabase and writes it to the
S3 data lake as Parquet, partitioned by ingestion date:

    s3://<bucket>/raw/<table>/ingest_date=YYYY-MM-DD/<table>.parquet

Design notes
------------
* Data minimization: sensitive columns (session tokens, OTP fields, ...) are
  dropped before anything leaves Supabase — the lake only sees analytics data.
* Full-snapshot per run, partitioned by date, so Athena can query the latest
  snapshot or look back over time. Simple and idempotent for Phase 1.
* Uses the service_role key (bypasses RLS) purely for a read-only export.
"""

import os
import sys
import datetime as dt

import boto3
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# Tables to export. Order does not matter (independent snapshots).
TABLES = [
    "profiles",
    "time_entries",
    "fuel_reports",
    "truck_changes",
    "loads",
]

# Columns dropped from any table before export (defense-in-depth against
# leaking secrets into the analytics lake). Matched case-insensitively.
SENSITIVE_COLUMNS = {
    "session_token",
    "otp_failed_attempts",
    "otp_locked_until",
    "otp_secret",
    "password",
    "password_hash",
}

PAGE_SIZE = 1000  # Supabase caps rows per request; paginate in chunks.


def get_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or value.startswith("your-") or "YOUR-" in value:
        sys.exit(f"[fatal] environment variable {name} is not set — see .env.example")
    return value


def fetch_table(client, table: str) -> pd.DataFrame:
    """Page through an entire table and return it as a DataFrame."""
    rows = []
    start = 0
    while True:
        end = start + PAGE_SIZE - 1
        resp = client.table(table).select("*").range(start, end).execute()
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    df = pd.DataFrame(rows)
    # Drop sensitive columns if present.
    drop = [c for c in df.columns if c.lower() in SENSITIVE_COLUMNS]
    if drop:
        df = df.drop(columns=drop)
        print(f"    dropped sensitive columns: {', '.join(drop)}")
    return df


def main() -> None:
    supabase_url = get_env("SUPABASE_URL")
    service_key = get_env("SUPABASE_SERVICE_ROLE_KEY")
    bucket = get_env("S3_BUCKET")
    region = os.environ.get("AWS_REGION", "ca-central-1")

    client = create_client(supabase_url, service_key)
    s3 = boto3.client("s3", region_name=region)

    ingest_date = dt.date.today().isoformat()
    local_dir = os.path.join(os.path.dirname(__file__), "..", "out", ingest_date)
    os.makedirs(local_dir, exist_ok=True)

    total_rows = 0
    for table in TABLES:
        print(f"[extract] {table}")
        df = fetch_table(client, table)
        rows = len(df)
        total_rows += rows

        if rows == 0:
            print(f"    (empty — skipping upload)")
            continue

        local_path = os.path.join(local_dir, f"{table}.parquet")
        df.to_parquet(local_path, engine="pyarrow", index=False)

        key = f"raw/{table}/ingest_date={ingest_date}/{table}.parquet"
        s3.upload_file(local_path, bucket, key)
        print(f"    {rows} rows -> s3://{bucket}/{key}")

    print(f"\n[done] exported {total_rows} rows across {len(TABLES)} tables "
          f"for ingest_date={ingest_date}")


if __name__ == "__main__":
    main()
