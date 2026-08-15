"""
FleetIQ natural-language query assistant (Phase 3).

Turns a plain-English question into an answer over the fleet data lake:

    question ──► Claude (Bedrock) writes Athena SQL from the live schema
             ──► read-only guard validates it
             ──► Athena runs it in ca-central-1
             ──► Claude summarizes the rows in plain English

Dependency-free (boto3 only): Bedrock is called via the Converse API, so no
Anthropic SDK needs to be bundled. Claude is reached through a Bedrock
inference profile (global routing) — the model itself runs outside Canada, a
documented data-residency trade-off for the natural-language answer feature.
"""
import json
import os
import re
import time

import boto3

DATABASE = "fleetiq"
WORKGROUP = "fleetiq"
MODEL_ID = os.environ["MODEL_ID"]

athena = boto3.client("athena")
glue = boto3.client("glue")
bedrock = boto3.client("bedrock-runtime")

# Only a single read-only statement may ever reach Athena.
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|grant|revoke|truncate|merge|call)\b",
    re.IGNORECASE,
)


def _schema_text() -> str:
    """Describe every table in the catalog for the model."""
    tables = glue.get_tables(DatabaseName=DATABASE)["TableList"]
    lines = []
    for t in tables:
        cols = t["StorageDescriptor"]["Columns"] + t.get("PartitionKeys", [])
        col_str = ", ".join(f"{c['Name']} {c['Type']}" for c in cols)
        lines.append(f"- {t['Name']}({col_str})")
    return "\n".join(lines)


def _generate_sql(question: str, schema: str) -> str:
    system = (
        "You translate questions into a single Amazon Athena (Trino SQL) query "
        "over the tables below. Rules:\n"
        "- Output ONLY the SQL, no prose, no markdown fences.\n"
        "- A single read-only SELECT (or WITH ... SELECT). Never write data.\n"
        "- clock_in/clock_out are ISO timestamp strings; wrap with "
        "from_iso8601_timestamp() for time math.\n"
        "- report_date is a 'YYYY-MM-DD' string.\n"
        "- Join time_entries/fuel_reports/loads to profiles on employee_id = profiles.id.\n"
        "- Always add a LIMIT of at most 100.\n\n"
        f"Tables:\n{schema}"
    )
    resp = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": question}]}],
        inferenceConfig={"maxTokens": 500, "temperature": 0},
    )
    sql = resp["output"]["message"]["content"][0]["text"].strip()
    # Strip accidental code fences.
    sql = re.sub(r"^```[a-z]*\n?|\n?```$", "", sql).strip().rstrip(";")
    return sql


def _guard(sql: str) -> None:
    """Reject anything that is not a single read-only query."""
    lowered = sql.lstrip().lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValueError("only SELECT/WITH queries are allowed")
    if ";" in sql:
        raise ValueError("multiple statements are not allowed")
    if FORBIDDEN.search(sql):
        raise ValueError("query contains a forbidden (write) keyword")


def _run_athena(sql: str) -> tuple[list[str], list[list[str]]]:
    qid = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )["QueryExecutionId"]
    while True:
        info = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = info["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    if state != "SUCCEEDED":
        raise RuntimeError(info["Status"].get("StateChangeReason", "query failed"))

    rows = athena.get_query_results(QueryExecutionId=qid, MaxResults=60)["ResultSet"]["Rows"]
    if not rows:
        return [], []
    header = [c.get("VarCharValue", "") for c in rows[0]["Data"]]
    data = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows[1:]]
    return header, data


def _summarize(question: str, header: list[str], data: list[list[str]]) -> str:
    table = " | ".join(header) + "\n" + "\n".join(" | ".join(r) for r in data[:50])
    if not data:
        table = "(no rows returned)"
    system = (
        "You are a fleet operations analyst. Answer the user's question in 1-3 "
        "sentences using ONLY the query results provided. Be specific with names "
        "and numbers. If there are no rows, say so plainly."
    )
    resp = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": f"Question: {question}\n\nResults:\n{table}"}]}],
        inferenceConfig={"maxTokens": 400, "temperature": 0},
    )
    return resp["output"]["message"]["content"][0]["text"].strip()


CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Api-Key",
}


def _answer(payload: dict) -> dict:
    question = (payload or {}).get("question", "").strip()
    if not question:
        return {"error": "provide a 'question' field"}

    schema = _schema_text()
    sql = _generate_sql(question, schema)
    try:
        _guard(sql)
    except ValueError as e:
        return {"question": question, "sql": sql, "error": f"blocked: {e}"}

    header, data = _run_athena(sql)
    answer = _summarize(question, header, data)

    result = {"question": question, "sql": sql, "row_count": len(data), "answer": answer}
    print(json.dumps(result))
    return result


def handler(event, context):
    # API Gateway proxy events carry the payload as a JSON string in "body";
    # direct invokes (CLI, tests) pass the payload dict itself.
    if isinstance(event, dict) and "requestContext" in event:
        try:
            payload = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            payload = {}
        result = _answer(payload)
        return {
            "statusCode": 400 if "error" in result else 200,
            "headers": {**CORS, "Content-Type": "application/json"},
            "body": json.dumps(result),
        }
    return _answer(event)
