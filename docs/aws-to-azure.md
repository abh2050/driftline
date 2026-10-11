# AWS to Azure mapping

How each Driftline building block maps to AWS, for readers who know AWS. "Notes" lists the differences that mattered here.

| Driftline need | Azure (used) | AWS equivalent | Notes |
|---|---|---|---|
| Containers and scheduled or event jobs | Container Apps (apps and jobs, KEDA rules) | ECS on Fargate, plus EventBridge Scheduler or SQS-triggered tasks; App Runner for simple apps | Container Apps jobs scale from a Service Bus subscription with a managed identity; the AWS equivalent is an SQS-triggered Lambda or ECS task |
| Sidecar pub/sub and cron | Dapr components on Container Apps | none built in; use the SDK, or ECS Service Connect | Dapr kept services free of Service Bus SDK code |
| Messaging (topics, subscriptions) | Service Bus Standard | SNS topics fanning out to SQS queues, or EventBridge | Message locks and renewal correspond to the SQS visibility timeout |
| Document store and workflow checkpoints | Cosmos DB for NoSQL, serverless | DynamoDB on-demand | Partition key `/pk`; the Agent Framework checkpoints map to a single DynamoDB table |
| Search index (vector and keyword) | Azure AI Search | OpenSearch Service (k-NN) or Bedrock Knowledge Bases | |
| Models | Microsoft Foundry (Azure OpenAI and model catalog) | Amazon Bedrock | Foundry has prompt agents and evaluations; Bedrock has Agents and Evaluations |
| Agent runtime | Microsoft Agent Framework in the orchestrator, agents registered in Foundry Agent Service | Bedrock AgentCore, or Strands Agents in ECS | |
| Prompt-injection detection and content filter | Azure AI Content Safety prompt shields, Azure OpenAI content filter | Bedrock Guardrails (prompt attack filter) | |
| API gateway with MCP | API Management (MCP server type, policies in XML) | API Gateway plus Lambda authorizers; AgentCore Gateway for MCP | The per-agent tool allowlist is an APIM policy; on AWS it would be an authorizer plus a Lambda that inspects JSON-RPC |
| Workload identity | User-assigned managed identities | IAM roles for tasks (ECS task roles) | One identity per agent maps to one IAM role per agent |
| Human sign-in | Entra ID with Container Apps authentication | Cognito user pools with ALB authentication | |
| CI federation | GitHub OIDC to an Entra app (federated credential) | GitHub OIDC to an IAM role (`AssumeRoleWithWebIdentity`) | |
| Secrets | Key Vault (RBAC) | Secrets Manager | |
| Write-once audit | Blob Storage with time-based immutability | S3 Object Lock (governance or compliance mode) | |
| Logs, traces, metrics | Log Analytics, Application Insights, Azure Monitor workspace (Prometheus), native OTLP ingestion | CloudWatch Logs, X-Ray or CloudWatch Application Signals, Amazon Managed Prometheus; the ADOT collector | Both accept OTLP from an OpenTelemetry Collector |
| Custom log table for decisions | Logs ingestion API plus DCR (`DriftlineDecisions_CL`) | CloudWatch Logs with embedded metric format, or Firehose to S3 plus Athena | |
| Dashboards and alerts | Azure Workbook, scheduled query alerts, Prometheus rule groups | CloudWatch dashboards, Logs Insights alarms, AMP alert rules | |
| Budget | Cost Management budget | AWS Budgets | |
| Infrastructure as code | Bicep with azd | CloudFormation or CDK with the SAM or Copilot CLI | `azd up` bundles provisioning, image builds and deploys |
