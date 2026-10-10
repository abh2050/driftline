// Driftline: all shared Azure resources plus the container apps that exist so far.
// Deployed by `azd provision` / `azd up` (spec section 5).
targetScope = 'subscription'

@minLength(1)
@maxLength(20)
@description('azd environment name, used to name the resource group and resources.')
param environmentName string

@description('Primary region (ADR 0001: westus3 has quota for every model).')
param location string

@description('Object id of the human running azd; gets data-plane roles for local development.')
param principalId string

@allowed(['User', 'ServicePrincipal'])
param principalType string = 'User'

@description('Model deployments: [{name, model, version, format, sku, capacity}].')
param modelDeployments array

@allowed(['free', 'basic', 'standard'])
param searchSku string

@allowed(['Developer', 'BasicV2', 'StandardV2'])
param apimSku string

param budgetAmount int
param budgetContactEmails array
param budgetStartDate string
param auditRetentionDays int

@description('Comma-separated public IPs (CIDR) allowed to reach otel-collector over external ingress. Empty keeps it internal.')
param otelAllowedIps string = ''

@description('Set by azd ("true"/"false") once the otel-collector app exists.')
param otelCollectorExists string = 'false'
param patientRagExists string = 'false'
param trafficGenExists string = 'false'
param routerExists string = 'false'
param ingestExists string = 'false'
param detectorExists string = 'false'
param replayExists string = 'false'
param githubMcpExists string = 'false'
param kqlToolExists string = 'false'
param orchestratorExists string = 'false'
param sandboxRunnerExists string = 'false'

@description('"true" once the GitHub App private key is in Key Vault (HUMAN_STEPS); the secret-scoped role needs it to exist.')
param githubKeyExists string = 'false'

@description('Driftline API app registration (created by scripts/entra_app.py preprovision).')
param driftlineApiAppId string
param driftlineApiAudience string

@description('Cron for traffic-gen (config/traffic.yaml schedule).')
param trafficSchedule string = '*/5 * * * *'

var otelIps = filter(map(split(otelAllowedIps, ','), ip => trim(ip)), ip => !empty(ip))

var prefix = take(toLower(replace(environmentName, '_', '-')), 12)
var tags = { 'azd-env-name': environmentName, app: 'driftline' }
var services = [
  'patient-rag'
  'traffic-gen'
  'replay'
  'ingest'
  'detector'
  'router'
  'orchestrator'
  'sandbox-runner'
  'otel-collector'
  'kql-tool'
  'ops-api'
  'github-mcp'
  'console'
]
// One identity per agent, so APIM can tell agents apart (ADR 0006).
var agents = [
  'agent-retrieval'
  'agent-prompt'
  'agent-index'
  'agent-performance'
  'agent-validator'
  'agent-prwriter'
  'agent-explainer'
]

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module identity 'modules/identity.bicep' = {
  scope: rg
  name: 'identity'
  params: { location: location, prefix: prefix, services: concat(services, agents), tags: tags }
}

// Principal lookups by service name.
var ids = toObject(identity.outputs.identities, x => x.service)
func sp(principalId string) object => { principalId: principalId, principalType: 'ServicePrincipal' }
var me = { principalId: principalId, principalType: principalType }

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  name: 'monitoring'
  params: {
    location: location
    prefix: prefix
    tags: tags
    otlpPublishers: [sp(ids['otel-collector'].principalId)]
    workspaceReaders: [sp(ids['kql-tool'].principalId), me]
  }
}

module dcr 'modules/dcr.bicep' = {
  scope: rg
  name: 'dcr'
  params: {
    location: location
    prefix: prefix
    tags: tags
    workspaceName: monitoring.outputs.workspaceName
    dceId: monitoring.outputs.dceId
    writers: [sp(ids.router.principalId), me]
  }
}

module registry 'modules/registry.bicep' = {
  scope: rg
  name: 'registry'
  params: {
    location: location
    prefix: prefix
    tags: tags
    pullers: [for s in services: sp(ids[s].principalId)]
    pushers: [me]
  }
}

module cosmos 'modules/cosmos.bicep' = {
  scope: rg
  name: 'cosmos'
  params: {
    location: location
    prefix: prefix
    tags: tags
    dataAssignments: [
      { principalId: ids.ingest.principalId, role: 'contributor' }
      { principalId: ids.detector.principalId, role: 'contributor' }
      { principalId: ids.orchestrator.principalId, role: 'contributor' }
      { principalId: ids['ops-api'].principalId, role: 'reader' }
      { principalId: principalId, role: 'contributor' }
    ]
  }
}

module servicebus 'modules/servicebus.bicep' = {
  scope: rg
  name: 'servicebus'
  params: {
    location: location
    prefix: prefix
    tags: tags
    topics: [
      { name: 'telemetry', subscriptions: ['ingest'] }
      { name: 'incidents', subscriptions: ['orchestrator', 'ops-api'] }
      { name: 'validations', subscriptions: ['orchestrator'] }
      { name: 'approvals', subscriptions: ['orchestrator'] }
      { name: 'sandbox', subscriptions: ['sandbox-runner'] }
    ]
    roleAssignments: [
      union(sp(ids['patient-rag'].principalId), { role: 'sender' })
      union(sp(ids.replay.principalId), { role: 'sender' })
      union(sp(ids.ingest.principalId), { role: 'receiver' })
      union(sp(ids.detector.principalId), { role: 'sender' })
      union(sp(ids.orchestrator.principalId), { role: 'receiver' })
      union(sp(ids.orchestrator.principalId), { role: 'sender' })
      union(sp(ids['sandbox-runner'].principalId), { role: 'sender' })
      union(sp(ids['sandbox-runner'].principalId), { role: 'receiver' })
      union(sp(ids['ops-api'].principalId), { role: 'sender' })
      union(sp(ids['ops-api'].principalId), { role: 'receiver' })
      union(me, { role: 'owner' })
    ]
  }
}

module storage 'modules/storage.bicep' = {
  scope: rg
  name: 'storage'
  params: {
    location: location
    prefix: prefix
    tags: tags
    auditRetentionDays: auditRetentionDays
    roleAssignments: [
      union(sp(ids.orchestrator.principalId), { role: 'contributor', container: 'audit' })
      union(sp(ids['sandbox-runner'].principalId), { role: 'contributor', container: 'eval-results' })
      union(sp(ids['ops-api'].principalId), { role: 'reader', container: 'eval-results' })
      union(me, { role: 'contributor', container: 'audit' })
      union(me, { role: 'contributor', container: 'eval-results' })
    ]
  }
}

module keyvault 'modules/keyvault.bicep' = {
  scope: rg
  name: 'keyvault'
  params: {
    location: location
    prefix: prefix
    tags: tags
    roleAssignments: [union(me, { role: 'secretsOfficer' })]
    secretReaders: githubKeyExists == 'true' ? [
      union(sp(ids['github-mcp'].principalId), { secretName: 'github-app-private-key' })
    ] : []
  }
}

module search 'modules/search.bicep' = {
  scope: rg
  name: 'search'
  params: {
    location: location
    prefix: prefix
    tags: tags
    sku: searchSku
    roleAssignments: [
      union(sp(ids['patient-rag'].principalId), { role: 'reader' })
      union(sp(ids['sandbox-runner'].principalId), { role: 'contributor' })
      // creates and deletes its temporary sbx-* indexes (ADR 0007)
      union(sp(ids['sandbox-runner'].principalId), { role: 'serviceContributor' })
      union(me, { role: 'contributor' })
      union(me, { role: 'serviceContributor' })
    ]
  }
}

module contentsafety 'modules/contentsafety.bicep' = {
  scope: rg
  name: 'contentsafety'
  params: {
    location: location
    prefix: prefix
    tags: tags
    roleAssignments: [sp(ids.orchestrator.principalId), me]
  }
}

module foundry 'modules/foundry.bicep' = {
  scope: rg
  name: 'foundry'
  params: {
    location: location
    prefix: prefix
    tags: tags
    deployments: modelDeployments
    appInsightsId: monitoring.outputs.appInsightsId
    roleAssignments: [
      union(sp(ids['patient-rag'].principalId), { role: 'openAiUser' })
      union(sp(ids['sandbox-runner'].principalId), { role: 'openAiUser' })
      union(sp(ids.router.principalId), { role: 'aiUser' })
      union(sp(ids.orchestrator.principalId), { role: 'aiUser' })
      union(me, { role: 'aiUser' })
      union(me, { role: 'openAiUser' })
    ]
  }
}

module apim 'modules/apim.bicep' = {
  scope: rg
  name: 'apim'
  params: {
    location: location
    prefix: prefix
    tags: tags
    publisherEmail: first(budgetContactEmails)
    sku: apimSku
  }
}

module budget 'modules/budget.bicep' = {
  scope: rg
  name: 'budget'
  params: {
    prefix: prefix
    amount: budgetAmount
    contactEmails: budgetContactEmails
    startDate: budgetStartDate
  }
}

module acaEnv 'modules/aca-env.bicep' = {
  scope: rg
  name: 'aca-env'
  params: {
    location: location
    prefix: prefix
    tags: tags
    serviceBusFqdn: servicebus.outputs.fqdn
    detectorSchedule: '@every 1m'
    pubsubIdentities: [for s in ['patient-rag', 'ingest', 'detector', 'orchestrator', 'ops-api']: {
      appId: s
      clientId: ids[s].clientId
    }]
  }
}

// otel-collector is deployed first (spec 11.3); other services are added in later phases.
module otelCollector 'modules/container-app.bicep' = {
  scope: rg
  name: 'app-otel-collector'
  params: {
    name: 'otel-collector'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids['otel-collector'].id
    identityClientId: ids['otel-collector'].clientId
    registryServer: registry.outputs.loginServer
    exists: otelCollectorExists == 'true'
    placeholderImage: 'otel/opentelemetry-collector-contrib:0.162.0'
    ingress: empty(otelIps) ? 'internal' : 'external'
    allowedIps: otelIps
    targetPort: 4318
    transport: 'http'
    additionalPorts: [{ external: false, targetPort: 4317, exposedPort: 4317 }]
    minReplicas: 1
    maxReplicas: 1
    cpu: '0.5'
    memory: '1Gi'
    env: [
      { name: 'DCE_LOGS_ENDPOINT', value: monitoring.outputs.dceLogsEndpoint }
      { name: 'DCE_METRICS_ENDPOINT', value: monitoring.outputs.dceMetricsEndpoint }
      { name: 'OTLP_DCR_IMMUTABLE_ID', value: monitoring.outputs.otlpDcrImmutableId }
    ]
  }
}

// Settings every Driftline service needs: identity of the API, telemetry endpoint.
var commonEnv = [
  { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
  { name: 'AZURE_ENV_NAME', value: environmentName }
  { name: 'DRIFTLINE_API_APP_ID', value: driftlineApiAppId }
  { name: 'DRIFTLINE_API_AUDIENCE', value: driftlineApiAudience }
  // The collector's short app name does not resolve in the environment (ADR 0006), so
  // services use its internal FQDN over HTTPS.
  { name: 'OTEL_EXPORTER_OTLP_ENDPOINT', value: 'https://${otelCollector.outputs.fqdn}' }
]

module patientRag 'modules/container-app.bicep' = {
  scope: rg
  name: 'app-patient-rag'
  params: {
    name: 'patient-rag'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids['patient-rag'].id
    identityClientId: ids['patient-rag'].clientId
    registryServer: registry.outputs.loginServer
    exists: patientRagExists == 'true'
    ingress: 'internal'
    targetPort: 8000
    transport: 'http'
    daprAppId: 'patient-rag'
    daprAppPort: 8000
    // Fault and index state live in memory, so run exactly one replica.
    minReplicas: 1
    maxReplicas: 1
    cpu: '1.0'
    memory: '2Gi'
    env: concat(commonEnv, [
      { name: 'FOUNDRY_ENDPOINT', value: foundry.outputs.endpoint }
      { name: 'SEARCH_ENDPOINT', value: search.outputs.endpoint }
      { name: 'PUBSUB_NAME', value: 'pubsub-patient-rag' }
      // F7 routes chat through the rate-limited APIM gateway (runtime state chat_route)
      { name: 'CHAT_GATEWAY_URL', value: '${apim.outputs.gatewayUrl}/chat-gateway' }
    ])
  }
}

module trafficGen 'modules/container-job.bicep' = {
  scope: rg
  name: 'job-traffic-gen'
  params: {
    name: 'traffic-gen'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids['traffic-gen'].id
    identityClientId: ids['traffic-gen'].clientId
    registryServer: registry.outputs.loginServer
    exists: trafficGenExists == 'true'
    triggerType: 'Schedule'
    cronExpression: trafficSchedule
    replicaTimeoutSeconds: 280
    env: concat(commonEnv, [
      { name: 'PATIENT_RAG_URL', value: 'http://patient-rag' }
    ])
  }
}

module router 'modules/container-app.bicep' = {
  scope: rg
  name: 'app-router'
  params: {
    name: 'router'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids.router.id
    identityClientId: ids.router.clientId
    registryServer: registry.outputs.loginServer
    exists: routerExists == 'true'
    ingress: 'internal'
    targetPort: 8000
    transport: 'http'
    minReplicas: 1
    maxReplicas: 3
    cpu: '0.5'
    memory: '1Gi'
    env: concat(commonEnv, [
      { name: 'FOUNDRY_ENDPOINT', value: foundry.outputs.endpoint }
      { name: 'DCE_LOGS_ENDPOINT', value: monitoring.outputs.dceLogsEndpoint }
      { name: 'DECISIONS_DCR_IMMUTABLE_ID', value: dcr.outputs.dcrImmutableId }
      { name: 'DECISIONS_STREAM', value: dcr.outputs.streamName }
    ])
  }
}

module ingest 'modules/container-app.bicep' = {
  scope: rg
  name: 'app-ingest'
  params: {
    name: 'ingest'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids.ingest.id
    identityClientId: ids.ingest.clientId
    registryServer: registry.outputs.loginServer
    exists: ingestExists == 'true'
    ingress: 'internal'
    targetPort: 8000
    transport: 'http'
    daprAppId: 'ingest'
    daprAppPort: 8000
    // Open windows live in memory until their minute closes, so one replica.
    minReplicas: 1
    maxReplicas: 1
    cpu: '0.5'
    memory: '1Gi'
    env: concat(commonEnv, [
      { name: 'COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
      { name: 'PUBSUB_NAME', value: 'pubsub-ingest' }
    ])
  }
}

module detector 'modules/container-app.bicep' = {
  scope: rg
  name: 'app-detector'
  params: {
    name: 'detector'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids.detector.id
    identityClientId: ids.detector.clientId
    registryServer: registry.outputs.loginServer
    exists: detectorExists == 'true'
    ingress: 'internal'
    targetPort: 8000
    transport: 'http'
    daprAppId: 'detector'
    daprAppPort: 8000
    minReplicas: 1
    maxReplicas: 1
    cpu: '0.5'
    memory: '1Gi'
    env: concat(commonEnv, [
      { name: 'COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
      { name: 'PUBSUB_NAME', value: 'pubsub-detector' }
      { name: 'ROUTER_URL', value: 'http://router' }
      { name: 'PATIENT_RAG_URL', value: 'http://patient-rag' }
    ])
  }
}

module replay 'modules/container-job.bicep' = {
  scope: rg
  name: 'job-replay'
  params: {
    name: 'replay'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids.replay.id
    identityClientId: ids.replay.clientId
    registryServer: registry.outputs.loginServer
    exists: replayExists == 'true'
    triggerType: 'Manual'
    replicaTimeoutSeconds: 3600
    cpu: '0.5'
    memory: '1Gi'
    env: concat(commonEnv, [
      { name: 'SERVICEBUS_FQDN', value: servicebus.outputs.fqdn }
      { name: 'REPLAY_PLAN', value: 'clean:48' }
      { name: 'REPLAY_SPEED', value: '1440' }
    ])
  }
}

// The MCP backends sit behind APIM: external ingress restricted to APIM's public IP, and
// each verifies APIM's managed-identity token (role mcp.gateway).
module githubMcp 'modules/container-app.bicep' = {
  scope: rg
  name: 'app-github-mcp'
  params: {
    name: 'github-mcp'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids['github-mcp'].id
    identityClientId: ids['github-mcp'].clientId
    registryServer: registry.outputs.loginServer
    exists: githubMcpExists == 'true'
    ingress: 'external'
    allowedIps: ['${apim.outputs.publicIp}/32']
    targetPort: 8000
    transport: 'http'
    minReplicas: 1
    maxReplicas: 1
    cpu: '0.5'
    memory: '1Gi'
    env: concat(commonEnv, [
      { name: 'KEYVAULT_URI', value: keyvault.outputs.uri }
      { name: 'MCP_UPSTREAM', value: 'http://127.0.0.1:8082' }
    ])
    sidecars: [
      {
        name: 'github-mcp-server'
        image: 'ghcr.io/github/github-mcp-server:v2.0.2'
        args: ['http']
        env: [
          { name: 'GITHUB_TOOLSETS', value: 'repos,pull_requests' }
        ]
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
      }
    ]
  }
}

module kqlTool 'modules/container-app.bicep' = {
  scope: rg
  name: 'app-kql-tool'
  params: {
    name: 'kql-tool'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids['kql-tool'].id
    identityClientId: ids['kql-tool'].clientId
    registryServer: registry.outputs.loginServer
    exists: kqlToolExists == 'true'
    ingress: 'external'
    allowedIps: ['${apim.outputs.publicIp}/32']
    targetPort: 8000
    transport: 'http'
    minReplicas: 0
    maxReplicas: 1
    env: concat(commonEnv, [
      { name: 'LOG_ANALYTICS_WORKSPACE_ID', value: monitoring.outputs.workspaceCustomerId }
    ])
  }
}

module apimMcp 'modules/apim-mcp.bicep' = {
  scope: rg
  name: 'apim-mcp'
  params: {
    apimName: apim.outputs.name
    tenantId: tenant().tenantId
    driftlineApiAppId: driftlineApiAppId
    driftlineApiAudience: driftlineApiAudience
    githubMcpUrl: 'https://${githubMcp.outputs.fqdn}/mcp'
    kqlToolUrl: 'https://${kqlTool.outputs.fqdn}/mcp'
    workspaceId: monitoring.outputs.workspaceId
  }
}

// F7: an APIM pass-through to Foundry with a deliberately low rate limit. patient-rag uses it
// only while fault F7 sets its runtime chat_route to 'gateway', and keeps its own token.
module apimChat 'modules/apim-chat.bicep' = {
  scope: rg
  name: 'apim-chat'
  params: {
    apimName: apim.outputs.name
    tenantId: tenant().tenantId
    foundryEndpoint: foundry.outputs.endpoint
  }
}

// Validates candidate fixes (spec 5.1.8): one execution per message on the sandbox topic.
module sandboxRunner 'modules/container-job.bicep' = {
  scope: rg
  name: 'job-sandbox-runner'
  params: {
    name: 'sandbox-runner'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids['sandbox-runner'].id
    identityClientId: ids['sandbox-runner'].clientId
    registryServer: registry.outputs.loginServer
    exists: sandboxRunnerExists == 'true'
    triggerType: 'Event'
    eventScaleRules: [
      {
        name: 'sandbox-requests'
        type: 'azure-servicebus'
        metadata: {
          namespace: servicebus.outputs.name
          topicName: 'sandbox'
          subscriptionName: 'sandbox-runner'
          messageCount: '1'
        }
        identity: ids['sandbox-runner'].id
      }
    ]
    replicaTimeoutSeconds: 2700
    cpu: '2.0'
    memory: '4Gi'
    env: concat(commonEnv, [
      { name: 'FOUNDRY_ENDPOINT', value: foundry.outputs.endpoint }
      { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundry.outputs.projectEndpoint }
      { name: 'SEARCH_ENDPOINT', value: search.outputs.endpoint }
      { name: 'SERVICEBUS_FQDN', value: servicebus.outputs.fqdn }
      { name: 'STORAGE_BLOB_ENDPOINT', value: storage.outputs.blobEndpoint }
    ])
  }
}

module orchestrator 'modules/container-app.bicep' = {
  scope: rg
  name: 'app-orchestrator'
  params: {
    name: 'orchestrator'
    location: location
    tags: tags
    environmentId: acaEnv.outputs.id
    identityId: ids.orchestrator.id
    identityClientId: ids.orchestrator.clientId
    extraIdentityIds: [for a in agents: ids[a].id]
    registryServer: registry.outputs.loginServer
    exists: orchestratorExists == 'true'
    ingress: 'internal'
    targetPort: 8000
    transport: 'http'
    daprAppId: 'orchestrator'
    daprAppPort: 8000
    minReplicas: 1
    maxReplicas: 1
    cpu: '1.0'
    memory: '2Gi'
    env: concat(commonEnv, [
      { name: 'FOUNDRY_ENDPOINT', value: foundry.outputs.endpoint }
      { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundry.outputs.projectEndpoint }
      { name: 'COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
      { name: 'STORAGE_BLOB_ENDPOINT', value: storage.outputs.blobEndpoint }
      { name: 'CONTENT_SAFETY_ENDPOINT', value: contentsafety.outputs.endpoint }
      { name: 'PUBSUB_NAME', value: 'pubsub-orchestrator' }
      { name: 'ROUTER_URL', value: 'http://router' }
      { name: 'PATIENT_RAG_URL', value: 'http://patient-rag' }
      { name: 'GITHUB_MCP_URL', value: apimMcp.outputs.githubMcpEndpoint }
      { name: 'KQL_MCP_URL', value: apimMcp.outputs.kqlMcpEndpoint }
      { name: 'AGENT_IDENTITIES', value: string(toObject(agents, a => a, a => ids[a].clientId)) }
      { name: 'APPINSIGHTS_RESOURCE_ID', value: monitoring.outputs.appInsightsId }
    ])
  }
}

output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer
output AZURE_CONTAINER_ENVIRONMENT_NAME string = acaEnv.outputs.name
output FOUNDRY_ENDPOINT string = foundry.outputs.endpoint
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output SEARCH_ENDPOINT string = search.outputs.endpoint
output COSMOS_ENDPOINT string = cosmos.outputs.endpoint
output SERVICEBUS_FQDN string = servicebus.outputs.fqdn
output STORAGE_BLOB_ENDPOINT string = storage.outputs.blobEndpoint
output KEYVAULT_URI string = keyvault.outputs.uri
output CONTENT_SAFETY_ENDPOINT string = contentsafety.outputs.endpoint
output LOG_ANALYTICS_WORKSPACE_ID string = monitoring.outputs.workspaceCustomerId
output APPINSIGHTS_RESOURCE_ID string = monitoring.outputs.appInsightsId
output DCE_LOGS_ENDPOINT string = monitoring.outputs.dceLogsEndpoint
output DECISIONS_DCR_IMMUTABLE_ID string = dcr.outputs.dcrImmutableId
output DECISIONS_STREAM string = dcr.outputs.streamName
output OTEL_COLLECTOR_FQDN string = otelCollector.outputs.fqdn
output PATIENT_RAG_FQDN string = patientRag.outputs.fqdn
output APIM_NAME string = apim.outputs.name
// Service identities, consumed by scripts/entra_app.py (postprovision) for app role grants.
output APIM_PRINCIPAL_ID string = apim.outputs.principalId
output APIM_GATEWAY_URL string = apim.outputs.gatewayUrl
output GITHUB_MCP_URL string = apimMcp.outputs.githubMcpEndpoint
output KQL_MCP_URL string = apimMcp.outputs.kqlMcpEndpoint
output SERVICE_IDENTITIES array = [for s in concat(services, agents): {
  service: s
  principalId: ids[s].principalId
  clientId: ids[s].clientId
}]
