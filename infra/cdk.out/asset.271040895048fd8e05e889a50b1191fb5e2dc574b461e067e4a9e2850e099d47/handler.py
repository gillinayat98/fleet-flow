"""
FleetIQ dashboard API (Phase 4).

Serves the owner dashboard's tiles and tables from three canned, hand-validated
Athena queries (the same methodology as the Phase 2 detector — not
LLM-generated SQL). All three queries are started concurrently and polled
together, so a dashboard load costs one round of Athena latency, not three.

Response shape:
    { "driver_hours":  {"columns": [...], "rows": [[...], ...]},
      "fuel_economy":  {...},
      "anomalies":     {...} }
"""
import json
import time

import boto3

DATABASE = "fleetiq"
WORKGROUP = "fleetiq"

athena = boto3.client("athena")

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Api-Key",
}

QUERIES = {
    # Labour: shifts, hours, estimated gross pay per driver.
    "driver_hours": """
        SELECT
            p.name AS driver,
            COUNT(*) AS shifts,
            ROUND(SUM(date_diff('second',
                  from_iso8601_timestamp(t.clock_in),
                  from_iso8601_timestamp(t.clock_out)) / 3600.0), 1) AS hours,
            ROUND(SUM(date_diff('second',
                  from_iso8601_timestamp(t.clock_in),
                  from_iso8601_timestamp(t.clock_out)) / 3600.0)
                  * p.hourly_rate, 2) AS est_gross_pay
        FROM time_entries t
        JOIN profiles p ON t.employee_id = p.id
        WHERE t.clock_out IS NOT NULL
        GROUP BY p.name, p.hourly_rate
        ORDER BY hours DESC
        LIMIT 25
    """,
    # Fuel economy per unit using consecutive-fill deltas (validated Phase 2
    # methodology) — NOT shift-join arithmetic.
    "fuel_economy": """
        WITH fuel AS (
            SELECT unit_no, report_date, odometer, fuel_litres,
                   LAG(odometer) OVER (
                       PARTITION BY unit_no ORDER BY report_date, created_at
                   ) AS prev_odometer
            FROM fuel_reports
        )
        SELECT
            unit_no,
            COUNT(*) AS fills,
            ROUND(SUM(fuel_litres), 1) AS total_litres,
            ROUND(AVG(CASE
                WHEN fuel_litres > 0 AND prev_odometer IS NOT NULL
                THEN (odometer - prev_odometer) / fuel_litres
            END), 2) AS avg_km_per_litre
        FROM fuel
        GROUP BY unit_no
        ORDER BY avg_km_per_litre DESC
        LIMIT 25
    """,
    # Latest anomaly flags from the Phase 2 detector.
    "anomalies": """
        SELECT category, anomaly_type, entity, detail
        FROM anomalies
        WHERE detect_date = (SELECT MAX(detect_date) FROM anomalies)
        ORDER BY category, anomaly_type, entity
        LIMIT 50
    """,
}


def _start(sql: str) -> str:
    return athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )["QueryExecutionId"]


def _wait(qid: str) -> None:
    while True:
        info = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = info["Status"]["State"]
        if state == "SUCCEEDED":
            return
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(info["Status"].get("StateChangeReason", state))
        time.sleep(0.5)


def _results(qid: str) -> dict:
    rows = athena.get_query_results(QueryExecutionId=qid, MaxResults=60)["ResultSet"]["Rows"]
    if not rows:
        return {"columns": [], "rows": []}
    columns = [c.get("VarCharValue", "") for c in rows[0]["Data"]]
    data = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows[1:]]
    return {"columns": columns, "rows": data}


def handler(event, context):
    # Start all queries concurrently, then collect.
    running = {name: _start(sql) for name, sql in QUERIES.items()}
    payload = {}
    for name, qid in running.items():
        try:
            _wait(qid)
            payload[name] = _results(qid)
        except RuntimeError as e:
            payload[name] = {"columns": [], "rows": [], "error": str(e)}

    return {
        "statusCode": 200,
        "headers": {**CORS, "Content-Type": "application/json"},
        "body": json.dumps(payload),
    }
