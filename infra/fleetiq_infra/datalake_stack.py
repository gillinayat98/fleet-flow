"""
FleetIQ infrastructure — the analytics data lake + anomaly detection.

Phase 1 (data lake):
  1. An S3 bucket        — the data lake where Parquet snapshots land.
  2. A Glue database     — the Data Catalog namespace for our tables.
  3. A Glue crawler      — scans S3 and registers table schemas + partitions.
  4. An Athena workgroup — where we run SQL, with a fixed results location.

Phase 2 (anomaly detection):
  5. A Lambda function   — runs the anomaly SQL and writes flags back to S3.
  6. An EventBridge rule — triggers the detector on a daily schedule.

Phase 3 (natural-language assistant):
  7. A Lambda function   — Claude (Bedrock) turns questions into Athena SQL.

Phase 4 (owner dashboard):
  8. A dashboard API     — API Gateway (key + daily quota) over both Lambdas.
  9. A static site       — S3 + CloudFront serving the dashboard page.
"""
import secrets
from pathlib import Path

from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    Duration,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
    aws_glue as glue,
    aws_iam as iam,
    aws_athena as athena,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_apigateway as apigateway,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
)
from constructs import Construct


def _demo_api_key() -> str:
    """Load (or create once) the demo API key from a gitignored local file.

    The key ships to the browser in config.json, so it is not a secret — it
    exists to bind requests to a usage plan with a hard daily quota, capping
    worst-case Bedrock spend. Production auth would be Cognito.
    """
    key_file = Path(__file__).resolve().parent.parent / ".fleetiq-api-key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_urlsafe(24)
    key_file.write_text(key)
    return key

GLUE_DATABASE = "fleetiq"
CRAWLER_NAME = "fleetiq-raw-crawler"
ATHENA_WORKGROUP = "fleetiq"

# Bedrock inference profile for the NL assistant (global routing — the only
# profile that serves Claude Haiku 4.5 when called from ca-central-1).
BEDROCK_MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_FOUNDATION_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"

# One crawler target per source table so each becomes its own catalog table
# (rather than being merged into a single wide table). Must match the folder
# names the extract writes under raw/.
SOURCE_TABLES = ["profiles", "time_entries", "fuel_reports", "truck_changes", "loads"]


class DataLakeStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ------------------------------------------------------------------
        # 1. S3 data lake
        # ------------------------------------------------------------------
        # Bucket names are globally unique, so we suffix with account+region.
        # Secure by default: encrypted, versioned, no public access, TLS-only.
        # DESTROY + auto_delete_objects means `cdk destroy` cleans up fully —
        # important so a demo project never keeps billing after teardown.
        bucket = s3.Bucket(
            self,
            "DataLake",
            bucket_name=f"fleetiq-datalake-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ------------------------------------------------------------------
        # 2. Glue Data Catalog database
        # ------------------------------------------------------------------
        # A namespace that will hold one table per source table (time_entries,
        # fuel_reports, ...). Athena reads schemas from here.
        database = glue.CfnDatabase(
            self,
            "GlueDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=GLUE_DATABASE,
                description="FleetIQ raw fleet data catalog",
            ),
        )

        # ------------------------------------------------------------------
        # 3. Glue crawler (+ its IAM role)
        # ------------------------------------------------------------------
        # The crawler needs permission to run as a Glue service role and to
        # read the data lake. grant_read scopes S3 access to just this bucket
        # (least privilege) rather than all of S3.
        crawler_role = iam.Role(
            self,
            "CrawlerRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSGlueServiceRole"
                )
            ],
        )
        bucket.grant_read(crawler_role)

        crawler = glue.CfnCrawler(
            self,
            "RawCrawler",
            name=CRAWLER_NAME,
            role=crawler_role.role_arn,
            database_name=GLUE_DATABASE,
            # One target per table folder → one catalog table each, with
            # ingest_date auto-detected as the partition key. The anomalies
            # prefix (written by the Phase 2 Lambda) is crawled too so flags
            # become a queryable table.
            targets=glue.CfnCrawler.TargetsProperty(
                s3_targets=[
                    glue.CfnCrawler.S3TargetProperty(
                        path=f"s3://{bucket.bucket_name}/raw/{table}/"
                    )
                    for table in SOURCE_TABLES
                ]
                + [
                    glue.CfnCrawler.S3TargetProperty(
                        path=f"s3://{bucket.bucket_name}/anomalies/"
                    )
                ]
            ),
            # Keep the catalog in sync with S3 on each run.
            schema_change_policy=glue.CfnCrawler.SchemaChangePolicyProperty(
                update_behavior="UPDATE_IN_DATABASE",
                delete_behavior="LOG",
            ),
        )
        # The database must exist before the crawler references it.
        crawler.add_dependency(database)

        # ------------------------------------------------------------------
        # 4. Athena workgroup
        # ------------------------------------------------------------------
        # A workgroup isolates our queries and pins where results are written.
        # enforce_work_group_configuration = users can't override these.
        athena.CfnWorkGroup(
            self,
            "WorkGroup",
            name=ATHENA_WORKGROUP,
            recursive_delete_option=True,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True,
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{bucket.bucket_name}/athena-results/",
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_S3"
                    ),
                ),
            ),
        )

        # ------------------------------------------------------------------
        # 5. Anomaly detector Lambda (Phase 2)
        # ------------------------------------------------------------------
        # boto3-only function (no bundling needed). Pushes all computation into
        # Athena SQL and writes normalized flags back to s3://.../anomalies/.
        detector = lambda_.Function(
            self,
            "AnomalyDetector",
            function_name="fleetiq-anomaly-detector",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda/anomaly_detector"),
            timeout=Duration.seconds(120),
            memory_size=256,
            environment={"DATA_BUCKET": bucket.bucket_name},
        )

        # Least-privilege permissions:
        #  - read the raw data + write the anomalies output (S3)
        #  - run queries in our Athena workgroup
        #  - read table metadata from the Glue catalog (Athena needs this)
        bucket.grant_read_write(detector)
        detector.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:GetWorkGroup",
                ],
                resources=[
                    f"arn:aws:athena:{self.region}:{self.account}:workgroup/{ATHENA_WORKGROUP}"
                ],
            )
        )
        detector.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:GetPartition",
                    "glue:GetPartitions",
                ],
                resources=[
                    f"arn:aws:glue:{self.region}:{self.account}:catalog",
                    f"arn:aws:glue:{self.region}:{self.account}:database/{GLUE_DATABASE}",
                    f"arn:aws:glue:{self.region}:{self.account}:table/{GLUE_DATABASE}/*",
                ],
            )
        )

        # ------------------------------------------------------------------
        # 6. Daily schedule (EventBridge)
        # ------------------------------------------------------------------
        # In production this would run just after the nightly ingest; a daily
        # rate keeps the demo simple. The detector can also be invoked manually.
        events.Rule(
            self,
            "DailyDetection",
            schedule=events.Schedule.rate(Duration.days(1)),
            targets=[targets.LambdaFunction(detector)],
        )

        # ------------------------------------------------------------------
        # 7. Natural-language query assistant Lambda (Phase 3)
        # ------------------------------------------------------------------
        # Claude (via Bedrock) turns a question into Athena SQL, runs it, and
        # summarizes the result. boto3-only; Bedrock reached via Converse API.
        assistant = lambda_.Function(
            self,
            "NlQuery",
            function_name="fleetiq-nl-query",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda/nl_query"),
            timeout=Duration.seconds(120),
            memory_size=256,
            environment={"MODEL_ID": BEDROCK_MODEL_ID},
        )

        # Same data-plane access as the detector: read/write S3, run Athena,
        # read the Glue catalog.
        bucket.grant_read_write(assistant)
        assistant.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:GetWorkGroup",
                ],
                resources=[
                    f"arn:aws:athena:{self.region}:{self.account}:workgroup/{ATHENA_WORKGROUP}"
                ],
            )
        )
        assistant.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:GetPartition",
                    "glue:GetPartitions",
                ],
                resources=[
                    f"arn:aws:glue:{self.region}:{self.account}:catalog",
                    f"arn:aws:glue:{self.region}:{self.account}:database/{GLUE_DATABASE}",
                    f"arn:aws:glue:{self.region}:{self.account}:table/{GLUE_DATABASE}/*",
                ],
            )
        )
        # Invoke Claude through the global inference profile. The profile can
        # route to any region, so the foundation-model resource is region-wild.
        assistant.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{BEDROCK_MODEL_ID}",
                    f"arn:aws:bedrock:*::foundation-model/{BEDROCK_FOUNDATION_MODEL}",
                ],
            )
        )

        # ------------------------------------------------------------------
        # 8. Dashboard API (Phase 4)
        # ------------------------------------------------------------------
        # Canned-query Lambda for the dashboard tiles (validated SQL only —
        # the same methodology as the anomaly detector, not LLM-generated).
        dashboard_api = lambda_.Function(
            self,
            "DashboardApi",
            function_name="fleetiq-dashboard-api",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda/dashboard_api"),
            timeout=Duration.seconds(60),
            memory_size=256,
        )
        bucket.grant_read_write(dashboard_api)
        dashboard_api.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:GetWorkGroup",
                ],
                resources=[
                    f"arn:aws:athena:{self.region}:{self.account}:workgroup/{ATHENA_WORKGROUP}"
                ],
            )
        )
        dashboard_api.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:GetPartition",
                    "glue:GetPartitions",
                ],
                resources=[
                    f"arn:aws:glue:{self.region}:{self.account}:catalog",
                    f"arn:aws:glue:{self.region}:{self.account}:database/{GLUE_DATABASE}",
                    f"arn:aws:glue:{self.region}:{self.account}:table/{GLUE_DATABASE}/*",
                ],
            )
        )

        # REST API fronting both Lambdas. The API key is intentionally
        # public (shipped in config.json); its job is to bind traffic to the
        # usage plan below, whose daily quota is the Bedrock spend ceiling.
        api = apigateway.RestApi(
            self,
            "DashboardRestApi",
            rest_api_name="fleetiq-dashboard",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Content-Type", "X-Api-Key"],
            ),
        )
        api.root.add_resource("insights").add_method(
            "GET",
            apigateway.LambdaIntegration(dashboard_api),
            api_key_required=True,
        )
        api.root.add_resource("ask").add_method(
            "POST",
            apigateway.LambdaIntegration(assistant),
            api_key_required=True,
        )

        demo_key_value = _demo_api_key()
        api_key = api.add_api_key("DemoApiKey", value=demo_key_value)
        plan = api.add_usage_plan(
            "DemoUsagePlan",
            name="fleetiq-demo",
            # Hard cost guardrail: at most 500 requests/day, 2 rps sustained.
            throttle=apigateway.ThrottleSettings(rate_limit=2, burst_limit=5),
            quota=apigateway.QuotaSettings(limit=500, period=apigateway.Period.DAY),
        )
        plan.add_api_key(api_key)
        plan.add_api_stage(stage=api.deployment_stage)

        # ------------------------------------------------------------------
        # 9. Dashboard static site (Phase 4)
        # ------------------------------------------------------------------
        # Private bucket + CloudFront (Origin Access Control). config.json is
        # generated at deploy time so the page knows the API URL and demo key.
        site_bucket = s3.Bucket(
            self,
            "DashboardSite",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        distribution = cloudfront.Distribution(
            self,
            "DashboardDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            default_root_object="index.html",
        )
        s3_deployment.BucketDeployment(
            self,
            "DeployDashboard",
            sources=[
                s3_deployment.Source.asset("../web"),
                s3_deployment.Source.json_data(
                    "config.json",
                    {"apiUrl": api.url, "apiKey": demo_key_value},
                ),
            ],
            destination_bucket=site_bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

        # ------------------------------------------------------------------
        # Outputs — printed after deploy; copy the bucket name into .env.
        # ------------------------------------------------------------------
        CfnOutput(self, "DataLakeBucket", value=bucket.bucket_name)
        CfnOutput(self, "GlueDatabaseName", value=GLUE_DATABASE)
        CfnOutput(self, "CrawlerName", value=CRAWLER_NAME)
        CfnOutput(self, "AthenaWorkgroup", value=ATHENA_WORKGROUP)
        CfnOutput(self, "AnomalyDetectorFn", value=detector.function_name)
        CfnOutput(self, "NlQueryFn", value=assistant.function_name)
        CfnOutput(self, "DashboardUrl", value=f"https://{distribution.domain_name}")
        CfnOutput(self, "ApiUrl", value=api.url)
