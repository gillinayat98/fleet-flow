# FleetIQ — Resume Bullets & Interview Notes

Target role: **AWS Solutions Architect (early career)** — posting emphasizes AI/ML
experience, one strong language, depth in a domain (analytics), and communicating
technical solutions to customer business problems.

## Resume bullets (pick 2–3)

- Designed and built **FleetIQ**, a serverless analytics + AI platform on AWS for a
  production fleet-management app: S3 data lake (Parquet), Glue, Athena, Lambda,
  EventBridge, API Gateway, CloudFront, and Amazon Bedrock — all defined as
  infrastructure-as-code with **AWS CDK (Python)**.
- Built a **natural-language analytics assistant** with Claude on Amazon Bedrock
  (text-to-SQL over a Glue catalog) with a read-only SQL guard and SQL-transparency
  design; and a **scheduled anomaly-detection pipeline** that surfaced real payroll
  defects (unpaid shifts, missed clock-outs) in production data.
- Applied Well-Architected practices end to end: least-privilege IAM per function,
  data minimization at extract, encrypted TLS-only buckets behind CloudFront OAC,
  and hard cost guardrails (usage-plan quotas, budget alerts) — total build cost < $1.

## 30-second pitch

> "I took a production workforce app I'd built for a trucking company and asked: what
> can't the owner see? I built FleetIQ — a separate serverless analytics plane on AWS.
> A nightly extract lands operational data in an S3 lake, Athena queries it, a
> scheduled Lambda flags anomalies — it found real payroll bugs — and Claude on
> Bedrock lets the owner ask questions in plain English. The whole thing is one CDK
> stack, least-privilege IAM throughout, and it cost under a dollar to build."

## Mapping to the job requirements

| Posting asks for | FleetIQ evidence |
|---|---|
| AI/ML experience | Bedrock + Claude text-to-SQL assistant; guardrails; model right-sizing (Haiku) |
| Domain depth (analytics) | Lake → catalog → SQL; Parquet/partitioning cost mechanics; anomaly rules |
| A programming language | Python end to end (extract, 3 Lambdas, CDK) |
| Solve customer business problems | Payroll defects found automatically; fuel-fraud detection; owner dashboard |
| Communication | Case-study README, architecture diagram, Well-Architected review, demo video |

## War stories (the "tell me about a time…" answers)

1. **Glue crawler merged all tables into one.** A single crawler target + combine-schemas
   policy collapsed five tables into one "raw" table. Diagnosed via the catalog, fixed
   *in code* (per-table targets in CDK), redeployed. Lesson: IaC made the fix reviewable
   and repeatable instead of console-clicking.
2. **"Failed to fetch" that wasn't CORS.** The browser showed a CORS-looking failure; the
   real cause was an unhandled Lambda exception → bare 502 → *no CORS headers on error
   responses*. Fixed with a catch-all boundary that always returns CORS-headed JSON.
   Lesson: distinguish the symptom (browser opacity) from the fault (error path).
3. **The LLM writes plausible-but-wrong SQL.** Claude produced an ambiguous-column join
   and, separately, a methodologically wrong fuel-economy query (shift-join vs
   consecutive-fill LAG). Response: surface generated SQL for verification, degrade
   gracefully on failure, and keep validated SQL as the source of truth for dashboard
   metrics. Lesson: LLM output is a draft, not an oracle — design the trust boundary.
4. **Data-residency trade-off.** Client data should stay in Canada (PIPEDA), but no
   Canada-resident Claude inference profile exists on Bedrock. Kept the lake and all
   processing in ca-central-1; routed only model inference through the global profile;
   **documented the trade-off and made it the customer's informed choice.** Lesson:
   an SA's job is surfacing trade-offs, not hiding them.
5. **Cost guardrails as architecture.** The dashboard's API key is public by design, so
   authentication can't be the control — instead a usage-plan quota hard-caps worst-case
   Bedrock spend at ~$2.50/day. Lesson: pick the control that matches the threat.

## Likely follow-up questions

- *Why Athena and not Redshift/RDS?* — No always-on cost, data already in S3, query
  volume is sporadic; Redshift earns its keep at sustained BI concurrency, not here.
- *Why not fine-tune a model?* — Text-to-SQL over a known schema is a prompting problem;
  schema-in-context + guardrails beats fine-tuning on cost, iteration speed, and risk.
- *How would this scale to 100 fleets?* — Partition the lake by tenant, per-tenant Glue
  databases or Lake Formation permissions, Cognito for auth, move the extract to
  event-driven CDC (e.g., Supabase webhooks → Kinesis Firehose).
- *What breaks first?* — The laptop-run extract; productionize as a scheduled job with
  DLQ + alerting (already listed in the Well-Architected gaps).
- *Production auth?* — Cognito user pool on API Gateway; the demo API key is a spend
  ceiling, not auth, and I say so in the docs.
