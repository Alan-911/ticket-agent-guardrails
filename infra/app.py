"""CDK app: everything the Ticket Agent needs, in one stack.

    npx aws-cdk@2 deploy        # build + deploy
    npx aws-cdk@2 destroy       # tear down (no resources left running)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

sys.path.insert(0, str(Path(__file__).parent))
from policies import TARGET, policies  # noqa: E402
from tool_schemas import TOOLS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "lambda"
# Claude Haiku 4.5 is the preferred model, but its Marketplace agreement is
# still pending on this account (BREAKLOG #6). Amazon Nova Pro needs no
# Marketplace subscription. Every control is model-independent, so swapping
# the model changes nothing about what the agent is allowed to do.
MODEL_ID = "us.amazon.nova-pro-v1:0"
MODEL_FAMILY = "amazon.nova-pro-v1:0"
ALT_MODELS = {  # also invocable per-request via InvokeHarness model override
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": "anthropic.claude-haiku-4-5-20251001-v1:0",
}


def build_bundle() -> str:
    """Copy ticket_core + handlers into one asset and vendor a recent boto3
    (the Lambda runtime's bundled boto3 may predate the harness APIs)."""
    if not (BUILD / "boto3").exists():
        BUILD.mkdir(parents=True, exist_ok=True)
        subprocess.run(["uv", "pip", "install", "--quiet", "--target", str(BUILD), "boto3",
                        "--python-version", "3.13", "--python-platform", "aarch64-manylinux_2_28"], check=True)
    shutil.rmtree(BUILD / "ticket_core", ignore_errors=True)
    shutil.copytree(ROOT / "ticket_core", BUILD / "ticket_core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for f in (ROOT / "lambdas").glob("*.py"):
        shutil.copy2(f, BUILD / f.name)
    shutil.copy2(ROOT / "mock_pace" / "pricing.json", BUILD / "pricing.json")
    return str(BUILD)


class TicketAgentStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)
        acct, region = self.account, self.region
        code = lambda_.Code.from_asset(build_bundle())

        # ---------------- data ----------------
        table = ddb.Table(self, "Table",
                          partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
                          sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
                          billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                          time_to_live_attribute="expires_at",
                          removal_policy=RemovalPolicy.DESTROY)
        table.add_global_secondary_index(index_name="gsi1",
                                         partition_key=ddb.Attribute(name="gsi1pk", type=ddb.AttributeType.STRING),
                                         sort_key=ddb.Attribute(name="gsi1sk", type=ddb.AttributeType.STRING))

        hotfolders = s3.Bucket(self, "HotFolders",
                               block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                               encryption=s3.BucketEncryption.S3_MANAGED,
                               enforce_ssl=True,
                               removal_policy=RemovalPolicy.DESTROY,
                               auto_delete_objects=True)

        approval_secret = sm.Secret(self, "ApprovalSecret",
                                    description="HMAC key for client approval tokens",
                                    generate_secret_string=sm.SecretStringGenerator(password_length=64, exclude_punctuation=True),
                                    removal_policy=RemovalPolicy.DESTROY)

        # ---------------- guardrail (R6, R7) ----------------
        guardrail = bedrock.CfnGuardrail(
            self, "Guardrail", name="ticket-agent-intake",
            blocked_input_messaging="This request can't be processed by the print shop's automated intake.",
            blocked_outputs_messaging="This response was blocked.",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(filters_config=[
                bedrock.CfnGuardrail.ContentFilterConfigProperty(type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"),
                bedrock.CfnGuardrail.ContentFilterConfigProperty(type="HATE", input_strength="HIGH", output_strength="HIGH"),
            ]),
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(topics_config=[
                bedrock.CfnGuardrail.TopicConfigProperty(
                    name="Counterfeit_Currency", type="DENY",
                    definition="Requests to print banknotes, paper currency, or realistic replicas or props of money.",
                    examples=["Print 200 replica $100 bills for our film.", "Make realistic prop money, double-sided."]),
                bedrock.CfnGuardrail.TopicConfigProperty(
                    name="Government_Identity_Documents", type="DENY",
                    definition="Requests to print or template passports, driver's licenses, national ID cards, birth certificates or other government identity documents.",
                    examples=["Print 50 driver's license templates.", "We need blank passport pages."]),
                bedrock.CfnGuardrail.TopicConfigProperty(
                    name="Counterfeit_Brand_Goods", type="DENY",
                    definition="Requests to print counterfeit, knock-off or unauthorized brand labels, tags or packaging.",
                    examples=["Print knock-off designer labels for resale.", "Replica brand packaging boxes."]),
            ]),
        )
        guardrail_version = bedrock.CfnGuardrailVersion(self, "GuardrailVersion",
                                                        guardrail_identifier=guardrail.attr_guardrail_id)

        # ---------------- tools Lambda (agent-side) ----------------
        tools_role = iam.Role(self, "ToolsRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                              description="Ticket Agent tools: read customers/sessions, write DRAFT tickets, never approvals")
        tools_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"))
        tools_role.add_to_policy(iam.PolicyStatement(
            sid="ReadOnly", actions=["dynamodb:GetItem", "dynamodb:Query"],
            resources=[table.table_arn, f"{table.table_arn}/index/gsi1"]))
        tools_role.add_to_policy(iam.PolicyStatement(
            sid="WriteDraftTicketsAuditCounterOnly", actions=["dynamodb:PutItem", "dynamodb:UpdateItem"],
            resources=[table.table_arn],
            conditions={
                "ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["TICKET#*", "AUDIT#*", "COUNTER"]},
                # Approval attributes (reviewed_by, approved_hash, approved_version) are not on this list.
                "ForAllValues:StringEquals": {"dynamodb:Attributes": [
                    "pk", "sk", "customer_id", "status", "version", "data", "gsi1pk", "gsi1sk", "n"]},
                "StringEqualsIfExists": {"dynamodb:Select": "SPECIFIC_ATTRIBUTES", "dynamodb:ReturnValues": ["NONE", "UPDATED_NEW"]},
            }))
        tools_fn = lambda_.Function(self, "ToolsFn", runtime=lambda_.Runtime.PYTHON_3_13,
                                    architecture=lambda_.Architecture.ARM_64, handler="tools_handler.handler",
                                    code=code, role=tools_role, timeout=Duration.seconds(30), memory_size=512,
                                    environment={"TABLE_NAME": table.table_name},
                                    log_retention=logs.RetentionDays.TWO_WEEKS)

        # ---------------- AgentCore Policy + Gateway ----------------
        engine = cdk.CfnResource(self, "PolicyEngine", type="AWS::BedrockAgentCore::PolicyEngine", properties={
            "Name": "ticket_agent_policies", "Description": "Cedar rules for the Ticket Agent gateway"})

        gw_role = iam.Role(self, "GatewayRole", assumed_by=iam.ServicePrincipal(
            "bedrock-agentcore.amazonaws.com",
            conditions={"StringEquals": {"aws:SourceAccount": acct}}))
        tools_fn.grant_invoke(gw_role)
        gw_role.add_to_policy(iam.PolicyStatement(
            sid="PolicyEngine",
            actions=["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions",
                     "bedrock-agentcore:GetPolicyEngine"],
            resources=[engine.get_att("PolicyEngineArn").to_string(),
                       f"arn:aws:bedrock-agentcore:{region}:{acct}:gateway/*"]))

        gateway = cdk.CfnResource(self, "Gateway", type="AWS::BedrockAgentCore::Gateway", properties={
            "Name": "ticket-agent-gateway",
            "Description": "MCP tools for the Ticket Agent. Cedar default-deny.",
            "RoleArn": gw_role.role_arn,
            "AuthorizerType": "AWS_IAM",
            "ProtocolType": "MCP",
            "ExceptionLevel": "DEBUG",
            "PolicyEngineConfiguration": {"Arn": engine.get_att("PolicyEngineArn").to_string(), "Mode": "ENFORCE"},
        })
        gateway.node.add_dependency(gw_role)
        gateway_arn = gateway.get_att("GatewayArn").to_string()

        target = cdk.CfnResource(self, "ToolsTarget", type="AWS::BedrockAgentCore::GatewayTarget", properties={
            "GatewayIdentifier": gateway.get_att("GatewayIdentifier").to_string(),
            "Name": TARGET,
            "Description": "Ticket Agent tools (Lambda)",
            "TargetConfiguration": {"Mcp": {"Lambda": {"LambdaArn": tools_fn.function_arn,
                                                       "ToolSchema": {"InlinePayload": TOOLS}}}},
            "CredentialProviderConfigurations": [{"CredentialProviderType": "GATEWAY_IAM_ROLE"}],
        })

        for name, statement in policies(gateway_arn).items():
            p = cdk.CfnResource(self, f"Policy{name.title().replace('_', '')}", type="AWS::BedrockAgentCore::Policy",
                                properties={"PolicyEngineId": engine.get_att("PolicyEngineId").to_string(),
                                            "Name": name, "Definition": {"Cedar": {"Statement": statement}},
                                            "EnforcementMode": "ACTIVE"})
            p.add_dependency(target)

        # ---------------- Harness (the agent) ----------------
        harness_role = iam.Role(self, "HarnessRole", assumed_by=iam.ServicePrincipal(
            "bedrock-agentcore.amazonaws.com",
            conditions={"StringEquals": {"aws:SourceAccount": acct},
                        "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{acct}:*"}}))
        harness_role.add_to_policy(iam.PolicyStatement(
            sid="InvokeApprovedModelsOnly", actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[arn for profile, family in {MODEL_ID: MODEL_FAMILY, **ALT_MODELS}.items()
                       for arn in (f"arn:aws:bedrock:{region}:{acct}:inference-profile/{profile}",
                                   f"arn:aws:bedrock:*::foundation-model/{family}")]))
        harness_role.add_to_policy(iam.PolicyStatement(
            sid="CallOnlyOurGateway", actions=["bedrock-agentcore:InvokeGateway"], resources=[gateway_arn]))
        harness_role.add_to_policy(iam.PolicyStatement(
            # BREAKLOG #4: the harness also sets up its runtime log group's resource policy.
            sid="Logs", actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                                 "logs:DescribeLogStreams", "logs:DescribeLogGroups",
                                 "logs:PutResourcePolicy", "logs:DescribeResourcePolicies",
                                 "xray:PutTraceSegments", "xray:PutTelemetryRecords", "cloudwatch:PutMetricData"],
            resources=["*"]))

        prompt = (ROOT / "agent" / "system_prompt.md").read_text(encoding="utf-8")
        harness = cdk.CfnResource(self, "Harness", type="AWS::BedrockAgentCore::Harness", properties={
            "HarnessName": "TicketAgent",
            "ExecutionRoleArn": harness_role.role_arn,
            "Model": {"BedrockModelConfig": {"ModelId": MODEL_ID, "MaxTokens": 2048, "Temperature": 0}},
            "SystemPrompt": [{"Text": prompt}],
            "Tools": [{"Type": "agentcore_gateway", "Name": "ticket_tools",
                       "Config": {"AgentCoreGateway": {"GatewayArn": gateway_arn, "OutboundAuth": {"AwsIam": {}}}}}],
            # Built-in shell/file tools are on by default; allow only the gateway's tools.
            "AllowedTools": ["@ticket_tools"],
            "Memory": {"Disabled": {}},
            "MaxIterations": 12,
            "MaxTokens": 2048,
            "TimeoutSeconds": 240,
        })
        harness.node.add_dependency(harness_role)
        harness.add_dependency(target)
        harness_arn = harness.get_att("Arn").to_string()

        # ---------------- intake Lambda ----------------
        intake_role = iam.Role(self, "IntakeRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        intake_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"))
        intake_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:Query"], resources=[table.table_arn, f"{table.table_arn}/index/gsi1"]))
        intake_role.add_to_policy(iam.PolicyStatement(
            sid="WriteSessionsRefusalsAudit", actions=["dynamodb:PutItem", "dynamodb:UpdateItem"], resources=[table.table_arn],
            conditions={"ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["SESSION#*", "TICKET#*", "AUDIT#*", "COUNTER"]},
                        "ForAllValues:StringEquals": {"dynamodb:Attributes": [
                            "pk", "sk", "customer_id", "status", "version", "data", "gsi1pk", "gsi1sk", "n", "expires_at"]}}))
        intake_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:ApplyGuardrail"], resources=[guardrail.attr_guardrail_arn]))
        intake_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeHarness", "bedrock-agentcore:InvokeAgentRuntime"],
            resources=[harness_arn, f"{harness_arn}/*", f"arn:aws:bedrock-agentcore:{region}:{acct}:runtime/*"]))
        intake_fn = lambda_.Function(self, "IntakeFn", runtime=lambda_.Runtime.PYTHON_3_13,
                                     architecture=lambda_.Architecture.ARM_64, handler="intake_handler.handler",
                                     code=code, role=intake_role, timeout=Duration.seconds(300), memory_size=512,
                                     environment={"TABLE_NAME": table.table_name, "HARNESS_ARN": harness_arn,
                                                  "GUARDRAIL_ID": guardrail.attr_guardrail_id,
                                                  "GUARDRAIL_VERSION": guardrail_version.attr_version},
                                     log_retention=logs.RetentionDays.TWO_WEEKS)

        # ---------------- approval + release (human side) ----------------
        approval_role = iam.Role(self, "ApprovalRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        approval_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"))
        table.grant_read_write_data(approval_role)
        approval_secret.grant_read(approval_role)
        approval_fn = lambda_.Function(self, "ApprovalFn", runtime=lambda_.Runtime.PYTHON_3_13,
                                       architecture=lambda_.Architecture.ARM_64, handler="approval_handler.handler",
                                       code=code, role=approval_role, timeout=Duration.seconds(30),
                                       environment={"TABLE_NAME": table.table_name,
                                                    "APPROVAL_SECRET_ARN": approval_secret.secret_arn},
                                       log_retention=logs.RetentionDays.TWO_WEEKS)

        release_role = iam.Role(self, "ReleaseRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        release_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"))
        table.grant_read_write_data(release_role)
        approval_secret.grant_read(release_role)
        hotfolders.grant_put(release_role)
        release_fn = lambda_.Function(self, "ReleaseFn", runtime=lambda_.Runtime.PYTHON_3_13,
                                      architecture=lambda_.Architecture.ARM_64, handler="release_handler.handler",
                                      code=code, role=release_role, timeout=Duration.seconds(30),
                                      environment={"TABLE_NAME": table.table_name,
                                                   "APPROVAL_SECRET_ARN": approval_secret.secret_arn,
                                                   "HOTFOLDER_BUCKET": hotfolders.bucket_name},
                                      log_retention=logs.RetentionDays.TWO_WEEKS)

        # R4: nobody but the release Lambda can put objects into the hot folders.
        hotfolders.add_to_resource_policy(iam.PolicyStatement(
            sid="OnlyReleaseLambdaWrites", effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
            actions=["s3:PutObject"], resources=[hotfolders.arn_for_objects("*")],
            conditions={"ArnNotEquals": {"aws:PrincipalArn": release_role.role_arn}}))

        for k, v in {"TableName": table.table_name, "HotFolderBucket": hotfolders.bucket_name,
                     "GatewayArn": gateway_arn, "HarnessArn": harness_arn,
                     "IntakeFn": intake_fn.function_name, "ApprovalFn": approval_fn.function_name,
                     "ReleaseFn": release_fn.function_name, "ToolsFn": tools_fn.function_name,
                     "GuardrailId": guardrail.attr_guardrail_id,
                     "PolicyEngineId": engine.get_att("PolicyEngineId").to_string()}.items():
            cdk.CfnOutput(self, f"Out{k}", value=v, key=k)


app = cdk.App()
TicketAgentStack(app, "TicketAgent", env=cdk.Environment(region="us-east-1"))
cdk.Tags.of(app).add("project", "ticket-agent-hackathon")
app.synth()
