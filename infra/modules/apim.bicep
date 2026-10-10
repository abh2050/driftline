// API Management, Developer tier: the lowest-cost tier that supports MCP servers.
// APIs, MCP servers and policies are added in Phase 6 (infra/apim/policies).
param location string
param prefix string
param tags object
param publisherEmail string
@allowed(['Developer', 'BasicV2', 'StandardV2'])
param sku string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: '${prefix}-apim-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  sku: { name: sku, capacity: 1 }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: publisherEmail
    publisherName: 'Driftline'
  }
}

output name string = apim.name
output gatewayUrl string = apim.properties.gatewayUrl
output publicIp string = apim.properties.publicIPAddresses[0]
output principalId string = apim.identity.principalId
