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
output APIM_NAME string = apim.outputs.name
// Service identities, consumed by scripts/entra_app.py (postprovision) for app role grants.
output SERVICE_IDENTITIES array = [for s in services: {
  service: s
  principalId: ids[s].principalId
  clientId: ids[s].clientId
}]
