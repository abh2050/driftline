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

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module identity 'modules/identity.bicep' = {
  scope: rg
  name: 'identity'
  params: { location: location, prefix: prefix, services: services, tags: tags }
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
    ]
    roleAssignments: [
      union(sp(ids['patient-rag'].principalId), { role: 'sender' })
      union(sp(ids.replay.principalId), { role: 'sender' })
      union(sp(ids.ingest.principalId), { role: 'receiver' })
      union(sp(ids.detector.principalId), { role: 'sender' })
      union(sp(ids.orchestrator.principalId), { role: 'receiver' })
      union(sp(ids.orchestrator.principalId), { role: 'sender' })
      union(sp(ids['sandbox-runner'].principalId), { role: 'sender' })
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
  { name: 'OTEL_EXPORTER_OTLP_ENDPOINT', value: 'http://otel-collector' }
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
output SERVICE_IDENTITIES array = [for s in services: {
  service: s
  principalId: ids[s].principalId
  clientId: ids[s].clientId
}]
