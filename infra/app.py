#!/usr/bin/env python3
"""CDK app entry point for FleetIQ infrastructure."""
import os

import aws_cdk as cdk

from fleetiq_infra.datalake_stack import DataLakeStack

app = cdk.App()

DataLakeStack(
    app,
    "FleetiqDataLake",
    env=cdk.Environment(
        # These are populated by the CDK CLI from your active AWS profile.
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "ca-central-1"),
    ),
    description="FleetIQ Phase 1 — S3 data lake, Glue crawler/catalog, Athena workgroup",
)

app.synth()
