# FleetIQ — 3-Minute Demo Script

Record screen + voice. One take per section is fine; cut between sections.
Have open in tabs beforehand: the dashboard, the repo README (architecture diagram
visible), and `infra/fleetiq_infra/datalake_stack.py`.

---

### 0:00–0:25 — The problem (talk over the README header)
> "TeamClok is a production workforce app for a trucking fleet — drivers clock in and out,
> log fuel and odometer readings. All that operational data sits in a transactional
> database that can't answer the owner's real questions: what's my cost per driver,
> which truck is burning too much fuel, is anyone's payroll misconfigured.
> FleetIQ is the analytics and AI layer I built on AWS to close that gap —
> **without changing the production app at all.**"

### 0:25–0:55 — Architecture (scroll the diagram)
> "Nightly, an extract pulls the operational tables into an S3 data lake as Parquet —
> dropping sensitive columns on the way out. A Glue crawler catalogs the schemas and
> Athena gives me serverless SQL over it. On top of that: a scheduled Lambda runs
> anomaly-detection rules, and a second Lambda uses Claude on Amazon Bedrock to turn
> plain-English questions into SQL. Everything — all nine services — is defined as
> code with the AWS CDK."

### 0:55–1:40 — The dashboard (switch to the live URL)
- Point at the **tiles**: "Thirteen drivers, six hundred hours, and — seven open anomalies."
- Scroll to the **anomaly feed**:
> "This is the part the owner cares about. The detector found three drivers with
> shifts over 16 hours — almost certainly forgot to clock out — and four shifts
> logged against a **zero hourly rate**. That's unpaid work; a real payroll bug this
> surfaced automatically."
- Point at **fuel economy**: "Fuel economy per truck, computed from consecutive
  fill-ups — the same math that would catch fuel-card fraud."

### 1:40–2:20 — Ask FleetIQ (type a question live)
Type: **"Which driver worked the most hours in total?"**
> "This box sends the question to Claude on Bedrock, which writes an Athena query
> against the live schema, runs it, and answers in plain English."
When the answer appears, **expand "Show generated SQL"**:
> "Two design choices here. First, a guard rejects anything that isn't a read-only
> SELECT — the model can never write to the lake. Second, the generated SQL is always
> shown, because LLM SQL is plausible but not guaranteed correct. The dashboard panels
> use hand-validated queries; the assistant is for exploration."

### 2:20–2:50 — The engineering (switch to the CDK stack file, scroll slowly)
> "One CDK stack defines the whole system: the lake, the catalog, three Lambdas, the
> API with a usage-plan quota that hard-caps worst-case Bedrock spend, and the
> CloudFront site. Each Lambda gets least-privilege IAM scoped to exactly the
> resources it needs. Tear-down is one command, and the whole demo has cost well
> under a dollar."

### 2:50–3:00 — Close (back to the dashboard)
> "FleetIQ: a production app's data, turned into a serverless analytics and AI
> platform — data lake, anomaly detection, natural-language analytics — all
> infrastructure as code on AWS."

---

**Tips:** keep the cursor moving with what you're saying; pre-run one /ask question
so Lambdas are warm; 720p+ recording; don't show `config.json` or the API key on screen.
