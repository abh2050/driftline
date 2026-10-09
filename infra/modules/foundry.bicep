// Foundry (AIServices) resource + project + model deployments (ADR 0001), Entra-only.
// Foundry tracing reads from the shared Application Insights resource.
param location string
param prefix string
param tags object
@description('[{name, model, version, format, sku, capacity}] from main.parameters.json')
param deployments array
param appInsightsId string
param roleAssignments array // [{principalId, principalType, role: 'aiUser'|'openAiUser'}]

var accountName = '${prefix}-ai-${uniqueString(resourceGroup().id)}'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: accountName
    disableLocalAuth: true
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: '${prefix}-project'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Driftline'
    description: 'Driftline agents, evaluations and tracing'
  }
}

// Deployments must be created one at a time on the same account.
@batchSize(1)
resource models 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [for d in deployments: {
  parent: account
  name: d.name
  sku: { name: d.sku, capacity: d.capacity }
  properties: {
    model: { name: d.model, version: d.version, format: d.format }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}]

resource appi 'Microsoft.Insights/components@2020-02-02' existing = {
  name: last(split(appInsightsId, '/'))
}

// Connect the project to Application Insights for Foundry tracing views.
resource tracing 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'appinsights'
  properties: {
    category: 'AppInsights'
    target: appInsightsId
    // Documented form for Foundry tracing. App Insights has local auth disabled, so this
    // connection string cannot be used to ingest; Foundry uses it to locate the resource.
    authType: 'ApiKey'
    credentials: { key: appi.properties.ConnectionString }
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsightsId
    }
  }
}

var roles = {
  aiUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  name: guid(account.id, a.principalId, a.role)
  scope: account
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles[a.role])
  }
}]

output name string = account.name
output endpoint string = 'https://${account.name}.services.ai.azure.com'
output projectName string = project.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
