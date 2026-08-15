"""
FleetIQ — Athena query runner.

Runs a SQL file against the FleetIQ Glue database via the `fleetiq` Athena
workgroup, waits for it to finish, and prints the results as a table.

Usage:
    python query/run_query.py query/examples/driver_hours.sql
"""
import sys
import time

import boto3

REGION = "ca-central-1"
DATABASE = "fleetiq"
WORKGROUP = "fleetiq"


def run(sql: str) -> None:
    athena = boto3.client("athena", region_name=REGION)

    qid = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )["QueryExecutionId"]

    # Poll until the query reaches a terminal state.
    while True:
        info = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = info["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)

    if state != "SUCCEEDED":
        reason = info["Status"].get("StateChangeReason", "unknown error")
        sys.exit(f"[query {state}] {reason}")

    # Report how much data Athena scanned — the thing you pay for.
    scanned = info["Statistics"].get("DataScannedInBytes", 0)
    print(f"(scanned {scanned/1024:.1f} KB)\n")

    # Fetch and pretty-print the rows.
    result = athena.get_query_results(QueryExecutionId=qid)
    rows = result["ResultSet"]["Rows"]
    table = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows]
    if not table:
        print("(no rows)")
        return

    widths = [max(len(r[i]) for r in table) for i in range(len(table[0]))]
    for idx, r in enumerate(table):
        line = "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r))
        print(line)
        if idx == 0:  # underline the header
            print("  ".join("-" * w for w in widths))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python query/run_query.py <path-to-sql-file>")
    with open(sys.argv[1]) as f:
        run(f.read())
