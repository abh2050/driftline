// Azure AI Content Safety (prompt shields), Entra-only.
param location string
param prefix string
param tags object
param roleAssignments array // [{principalId, principalType}] -> Cognitive Services User

resource cs 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: '${prefix}-cs-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  kind: 'ContentSafety'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: '${prefix}-cs-${uniqueString(resourceGroup().id)}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  name: guid(cs.id, a.principalId, cognitiveServicesUser)
  scope: cs
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
  }
}]

output name string = cs.name
output endpoint string = cs.properties.endpoint
