# FleetIQ — Cost Analysis

Everything in FleetIQ is serverless / pay-per-use: there is **no always-on compute**
(no EC2, no RDS, no OpenSearch cluster, no NAT gateway). Idle cost is effectively zero.

> Figures below are computed from actual usage counts during the build (queries run,
> Lambda invocations, Bedrock calls) priced at published ca-central-1 / Bedrock rates,
> July 2026. Cost Explorer on this new account had not finished ingesting at write time.

## What the build + demo actually cost

| Service | Usage so far | Unit price | Cost |
|---|---|---|---|
| Glue crawler | ~6 runs × ~1–2 min | $0.44 / DPU-hour (2 DPU min) | ~$0.15 |
| Athena | ~30 queries, each scanning KBs | $5 / TB scanned | < $0.01 |
| Bedrock (Claude Haiku 4.5) | ~25 calls (2 per question) | $1 / M input + $5 / M output tokens | ~$0.10 |
| S3 (lake + site) | < 5 MB stored, light requests | $0.025 / GB-month | ~$0.00 |
| Lambda (×3 functions) | ~60 invocations, seconds each | free tier: 1M req + 400k GB-s / month | $0.00 |
| API Gateway (REST) | ~40 requests | $3.50 / M requests | ~$0.00 |
| CloudFront | one small page, few loads | free tier: 1 TB egress / month | $0.00 |
| EventBridge, Glue catalog, CDK bootstrap | — | free tier / negligible | ~$0.00 |
| **Total to date** | | | **≈ $0.30** |

## Projected monthly cost (demo left running)

Daily detector run + daily crawler refresh + occasional dashboard use:

| Item | Monthly |
|---|---|
| Glue crawler (daily) | ~$1.50 |
| Anomaly detector (daily Lambda + Athena) | ~$0.10 |
| Dashboard + assistant (light demo traffic) | ~$0.50 |
| S3 storage | ~$0.01 |
| **Total** | **≈ $2 / month** |

## Projected at production scale (one fleet, ~50 drivers, 1 year of data)

| Item | Notes | Monthly |
|---|---|---|
| S3 lake | a few GB of Parquet | < $0.25 |
| Athena | Parquet + date partitioning keep scans in MBs | ~$1–3 |
| Bedrock | ~$0.005 per question → 1,000 questions | ~$5 |
| Glue, Lambda, API GW, CloudFront | | ~$3 |
| **Total** | | **≈ $10–15 / month** |

The design choices that keep this flat: **Parquet** (columnar, compressed → Athena bills
per byte scanned) and **date partitioning** (queries touch only the partitions they need).
The same workload stored as CSV without partitioning would scan orders of magnitude more.

## Cost guardrails in place

1. **AWS Budget alert** at $20/month (email).
2. **API Gateway usage plan** — 500 requests/day hard quota + 2 rps throttle. Because the
   dashboard's API key is public by design, the quota is the *spend ceiling* on Bedrock:
   worst-case abuse ≈ 500 × $0.005 ≈ **$2.50/day**, bounded.
3. **No always-on resources** — the two classic bill-runners (OpenSearch domain, NAT
   gateway) were deliberately designed out (DynamoDB-/Athena-first, no VPC).
4. **`cdk destroy`** tears down every resource, including auto-emptying both S3 buckets —
   nothing lingers to bill after the demo.
