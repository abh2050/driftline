// Custom tables DriftlineDecisions_CL (router decisions) and DriftlineEvals_CL (one row per
// eval metric per run, for the workbook's nightly trends) and the logs-ingestion DCR.
param location string
param prefix string
param tags object
param workspaceName string
param dceId string
@description('Principals ({principalId, principalType}) that may write decision rows.')
param writers array

resource law 'Microsoft.OperationalInsights/workspaces@2025-07-01' existing = {
  name: workspaceName
}

var columns = [
  { name: 'TimeGenerated', type: 'datetime' }
  { name: 'DecisionType', type: 'string' }
  { name: 'QuestionId', type: 'string' }
  { name: 'Client', type: 'string' }
  { name: 'Model', type: 'string' }
  { name: 'Options', type: 'dynamic' }
  { name: 'Scores', type: 'dynamic' }
  { name: 'Chosen', type: 'string' }
  { name: 'Margin', type: 'real' }
  { name: 'Escalated', type: 'boolean' }
  { name: 'LatencyMs', type: 'real' }
  { name: 'InputTokens', type: 'int' }
  { name: 'CostUsd', type: 'real' }
  { name: 'IncidentId', type: 'string' }
  { name: 'TraceId', type: 'string' }
]

resource table 'Microsoft.OperationalInsights/workspaces/tables@2025-07-01' = {
  parent: law
  name: 'DriftlineDecisions_CL'
  properties: {
    schema: {
      name: 'DriftlineDecisions_CL'
      columns: columns
    }
    retentionInDays: 30
  }
}

var evalColumns = [
  { name: 'TimeGenerated', type: 'datetime' }
  { name: 'Layer', type: 'string' }
  { name: 'RunId', type: 'string' }
  { name: 'Metric', type: 'string' }
  { name: 'Value', type: 'real' }
  { name: 'Source', type: 'string' }
  { name: 'Report', type: 'string' }
]

resource evalTable 'Microsoft.OperationalInsights/workspaces/tables@2025-07-01' = {
  parent: law
  name: 'DriftlineEvals_CL'
  properties: {
    schema: {
      name: 'DriftlineEvals_CL'
      columns: evalColumns
    }
    retentionInDays: 90
  }
}

resource dcr 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: '${prefix}-dcr-decisions'
  location: location
  tags: tags
  dependsOn: [table, evalTable]
  properties: {
    dataCollectionEndpointId: dceId
    streamDeclarations: {
      'Custom-DriftlineDecisions': { columns: columns }
      'Custom-DriftlineEvals': { columns: evalColumns }
    }
    destinations: {
      logAnalytics: [
        { workspaceResourceId: law.id, name: 'law' }
      ]
    }
    dataFlows: [
      {
        streams: ['Custom-DriftlineDecisions']
        destinations: ['law']
        transformKql: 'source'
        outputStream: 'Custom-DriftlineDecisions_CL'
      }
      {
        streams: ['Custom-DriftlineEvals']
        destinations: ['law']
        transformKql: 'source'
        outputStream: 'Custom-DriftlineEvals_CL'
      }
    ]
  }
}

var monitoringMetricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'

resource writerRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for w in writers: {
  name: guid(dcr.id, w.principalId, monitoringMetricsPublisher)
  scope: dcr
  properties: {
    principalId: w.principalId
    principalType: w.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringMetricsPublisher)
  }
}]

output dcrId string = dcr.id
output dcrImmutableId string = dcr.properties.immutableId
output streamName string = 'Custom-DriftlineDecisions'
output evalsStreamName string = 'Custom-DriftlineEvals'
