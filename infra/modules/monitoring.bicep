// Log Analytics, Application Insights (Entra-only ingestion), Azure Monitor workspace,
// and the OTLP data collection endpoint + rule used by otel-collector (ADR 0003).
param location string
param prefix string
param tags object
@description('Principals ({principalId, principalType}) allowed to push OTLP data to the DCR.')
param otlpPublishers array
@description('Principals ({principalId, principalType}) that get Log Analytics Reader.')
param workspaceReaders array

resource law 'Microsoft.OperationalInsights/workspaces@2025-07-01' = {
  name: '${prefix}-law'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    features: { disableLocalAuth: true }
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-appi'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    DisableLocalAuth: true
  }
}

resource amw 'Microsoft.Monitor/accounts@2025-10-03' = {
  name: '${prefix}-amw'
  location: location
  tags: tags
}

resource dce 'Microsoft.Insights/dataCollectionEndpoints@2024-03-11' = {
  name: '${prefix}-dce'
  location: location
  tags: tags
  properties: {
    networkAcls: { publicNetworkAccess: 'Enabled' }
  }
}

var traceStreams = [
  'Microsoft-OTel-Traces-Spans'
  'Microsoft-OTel-Traces-Events'
  'Microsoft-OTel-Traces-Resources'
]

resource otlpDcr 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: '${prefix}-dcr-otlp'
  location: location
  tags: tags
  properties: {
    description: 'OTLP traces, logs and metrics sent directly by otel-collector'
    dataCollectionEndpointId: dce.id
    references: {
      applicationInsights: [
        { resourceId: appi.id, name: 'appi' }
      ]
    }
    directDataSources: {
      otelMetrics: [
        {
          streams: ['Custom-Metrics-Otel']
          enrichWithResourceAttributes: ['*']
          enrichWithReference: 'appi'
          name: 'otelMetrics'
        }
      ]
      otelLogs: [
        {
          streams: ['Microsoft-OTel-Logs']
          enrichWithResourceAttributes: ['*']
          enrichWithReference: 'appi'
          replaceResourceIdWithReference: true
          name: 'otelLogs'
        }
      ]
      otelTraces: [
        {
          streams: traceStreams
          enrichWithResourceAttributes: ['*']
          enrichWithReference: 'appi'
          replaceResourceIdWithReference: true
          name: 'otelTraces'
        }
      ]
    }
    destinations: {
      monitoringAccounts: [
        { accountResourceId: amw.id, name: 'amw' }
      ]
      logAnalytics: [
        { workspaceResourceId: law.id, name: 'law' }
      ]
    }
    dataFlows: [
      { streams: ['Custom-Metrics-Otel'], destinations: ['amw'] }
      { streams: concat(['Microsoft-OTel-Logs'], traceStreams), destinations: ['law'] }
    ]
  }
}

var monitoringMetricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'

resource otlpPublisherRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for p in otlpPublishers: {
  name: guid(otlpDcr.id, p.principalId, monitoringMetricsPublisher)
  scope: otlpDcr
  properties: {
    principalId: p.principalId
    principalType: p.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringMetricsPublisher)
  }
}]

output workspaceId string = law.id
output workspaceName string = law.name
output workspaceCustomerId string = law.properties.customerId
output appInsightsId string = appi.id
output amwId string = amw.id
output appInsightsName string = appi.name
output dceId string = dce.id
output dceLogsEndpoint string = dce.properties.logsIngestion.endpoint
output dceMetricsEndpoint string = dce.properties.metricsIngestion.endpoint
output otlpDcrId string = otlpDcr.id
output otlpDcrImmutableId string = otlpDcr.properties.immutableId

var logAnalyticsReader = '73c42c96-874c-492b-b04d-ab87d138a893'

resource readers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in workspaceReaders: {
  name: guid(law.id, r.principalId, logAnalyticsReader)
  scope: law
  properties: {
    principalId: r.principalId
    principalType: r.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', logAnalyticsReader)
  }
}]
