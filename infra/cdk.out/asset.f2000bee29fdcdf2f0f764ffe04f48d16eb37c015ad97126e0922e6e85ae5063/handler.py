"""
FleetIQ anomaly detector (Phase 2).

Runs the fuel and shift/payroll anomaly rules against the Glue catalog via
Athena, normalizes every hit into a common record shape, and writes them back
to the data lake as newline-delimited JSON:

    s3://<bucket>/anomalies/detect_date=YYYY-MM-DD/anomalies.jsonl

That output is itself crawlable, so anomalies become a queryable table that
later phases (NL assistant, dashboard) can read. The Lambda stays dependency-
free (boto3 only) by pushing all computation into Athena SQL.
"""
import datetime
import json
import os
import time

import boto3

DATABASE = "fleetiq"
WORKGROUP = "fleetiq"
BUCKET = os.environ["DATA_BUCKET"]

athena = boto3.client("athena")
s3 = boto3.client("s3")

FUEL_SQL = """
WITH fuel AS (
    SELECT unit_no, report_date, odometer, fuel_litres,
           LAG(odometer) OVER (PARTITION BY unit_no ORDER BY report_date, created_at) AS prev_odometer
    FROM fuel_reports
),
eff AS (
    SELECT unit_no, report_date, odometer, fuel_litres,
           (odometer - prev_odometer) AS distance_km,
           CASE WHEN fuel_litres > 0 AND prev_odometer IS NOT NULL
                THEN ROUND((odometer - prev_odometer) / fuel_litres, 2) END AS km_per_litre
    FROM fuel
)
SELECT unit_no, report_date, odometer, fuel_litres, distance_km, km_per_litre,
    CASE
        WHEN fuel_litres <= 0   THEN 'zero_or_missing_litres'
        WHEN distance_km < 0    THEN 'odometer_backwards'
        WHEN km_per_litre < 1.0 THEN 'implausible_low_economy'
        WHEN km_per_litre > 6.0 THEN 'implausible_high_economy'
    END AS anomaly
FROM eff
WHERE fuel_litres <= 0 OR distance_km < 0 OR km_per_litre < 1.0 OR km_per_litre > 6.0
"""

SHIFT_SQL = """
WITH shifts AS (
    SELECT p.name AS driver, t.unit_no, t.odometer_in, t.odometer_out, p.hourly_rate,
           date_diff('second', from_iso8601_timestamp(t.clock_in),
                     from_iso8601_timestamp(t.clock_out)) / 3600.0 AS hours
    FROM time_entries t JOIN profiles p ON t.employee_id = p.id
    WHERE t.clock_out IS NOT NULL
)
SELECT driver, unit_no, ROUND(hours, 1) AS hours, hourly_rate, odometer_in, odometer_out,
    CASE
        WHEN hours <= 0 THEN 'nonpositive_duration'
        WHEN hours > 16 THEN 'excessive_shift'
        WHEN odometer_out IS NOT NULL AND odometer_in IS NOT NULL
             AND odometer_out < odometer_in THEN 'odometer_backwards'
        WHEN hourly_rate IS NULL OR hourly_rate = 0 THEN 'missing_hourly_rate'
    END AS anomaly
FROM shifts
WHERE hours <= 0 OR hours > 16
   OR (odometer_out IS NOT NULL AND odometer_in IS NOT NULL AND odometer_out < odometer_in)
   OR hourly_rate IS NULL OR hourly_rate = 0
"""


def _run_query(sql: str) -> list[dict]:
    """Execute an Athena query and return its rows as a list of dicts."""
    qid = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )["QueryExecutionId"]

    while True:
        state = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    if state != "SUCCEEDED":
        raise RuntimeError(f"Athena query {state}")

    rows = []
    paginator = athena.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=qid):
        rows.extend(page["ResultSet"]["Rows"])
    if not rows:
        return []

    header = [c.get("VarCharValue") for c in rows[0]["Data"]]
    return [
        {header[i]: c.get("VarCharValue") for i, c in enumerate(r["Data"])}
        for r in rows[1:]
    ]


def handler(event, context):
    detect_date = datetime.date.today().isoformat()
    records = []

    # Note: detect_date is NOT stored in the record body — it is supplied by
    # the S3 partition path (detect_date=YYYY-MM-DD), so duplicating it here
    # would create a column/partition name clash in the Glue catalog.
    for r in _run_query(FUEL_SQL):
        records.append({
            "category": "fuel",
            "entity": r.get("unit_no"),
            "anomaly_type": r.get("anomaly"),
            "detail": f"km/L={r.get('km_per_litre')}, distance={r.get('distance_km')}, litres={r.get('fuel_litres')}",
        })

    for r in _run_query(SHIFT_SQL):
        records.append({
            "category": "shift",
            "entity": r.get("driver"),
            "anomaly_type": r.get("anomaly"),
            "detail": f"hours={r.get('hours')}, rate={r.get('hourly_rate')}, unit={r.get('unit_no')}",
        })

    body = "\n".join(json.dumps(x) for x in records)
    key = f"anomalies/detect_date={detect_date}/anomalies.jsonl"
    s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode("utf-8"))

    summary = {
        "detect_date": detect_date,
        "total": len(records),
        "fuel": sum(1 for x in records if x["category"] == "fuel"),
        "shift": sum(1 for x in records if x["category"] == "shift"),
        "output": f"s3://{BUCKET}/{key}",
    }
    print(json.dumps(summary))
    return summary
