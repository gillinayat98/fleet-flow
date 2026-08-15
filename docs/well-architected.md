# FleetIQ — Well-Architected Review (one page)

A self-assessment against the six pillars of the AWS Well-Architected Framework.
Each pillar lists what the design does today and one honest gap with the production fix.

## 1. Operational Excellence
- **Everything is code.** All infrastructure is a single CDK (Python) stack — reviewable,
  versioned in git, reproducible with `cdk deploy`, removable with `cdk destroy`.
- Stack outputs surface every operational endpoint (dashboard URL, API URL, function names).
- Lambdas log structured JSON summaries to CloudWatch.
- **Gap → next:** no CI/CD; production would add a pipeline (GitHub Actions → `cdk diff`
  on PR, deploy on merge) and CloudWatch alarms on Lambda errors.

## 2. Security
- **Off root from day one** — MFA on root, daily work under an IAM user.
- **Least-privilege IAM per Lambda**: each function's policy is scoped to the one Athena
  workgroup, the one Glue database, the one bucket, and (for the assistant) the one
  Bedrock inference profile. No `*` resource wildcards on data access.
- **Data minimization at extract**: session tokens / OTP columns are dropped before any
  data leaves the source database.
- S3: encryption at rest, versioning, all public access blocked, TLS-only bucket policies;
  the site bucket is reachable only through CloudFront (Origin Access Control).
- **LLM guardrail**: the assistant's generated SQL passes a read-only gate (single
  `SELECT`/`WITH`, no write keywords) before touching Athena.
- **Gap → next:** the dashboard's API key is a public demo credential (it's a spend cap,
  not auth). Production adds Cognito user auth on API Gateway and per-user rate limits.

## 3. Reliability
- Serverless managed services (S3, Athena, Lambda, API Gateway, CloudFront) carry
  multi-AZ availability by default; there are no single-instance components.
- The extract is **idempotent** — re-running a day overwrites that day's partition cleanly.
- Failure isolation: a text-to-SQL failure degrades to a helpful message (with the SQL);
  it cannot crash the API or corrupt data.
- **Gap → next:** the nightly extract runs from a laptop; production moves it to a
  scheduled Lambda/ECS task with a dead-letter queue and failure alerting.

## 4. Performance Efficiency
- **Parquet + date partitioning**: Athena reads only needed columns/partitions — demo
  queries scan single-digit KBs.
- The dashboard API starts its three Athena queries **concurrently**, so page load costs
  one round of query latency, not three.
- Claude **Haiku** (not a larger model) was chosen deliberately: text-to-SQL is a
  constrained task where the small model is fast, cheap, and sufficient.
- **Gap → next:** at higher traffic, cache `/insights` responses (API Gateway caching or
  short-TTL Lambda memoization) instead of re-querying Athena per page load.

## 5. Cost Optimization
- Zero always-on compute; idle cost ≈ $0. Full analysis in [cost-analysis.md](cost-analysis.md).
- Hard guardrails: $20 budget alert + API usage-plan quota that caps worst-case Bedrock
  spend at ~$2.50/day even if the public demo URL is abused.
- **Gap → next:** S3 lifecycle rules to expire old `ingest_date` partitions / transition
  to Infrequent Access as history accumulates.

## 6. Sustainability
- Serverless scale-to-zero means compute runs only when work exists.
- Columnar storage and partition pruning minimize data moved and processed per query.
- Right-sized model choice (Haiku) minimizes inference footprint per question.
