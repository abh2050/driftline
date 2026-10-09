// Azure AI Search, Entra RBAC only. Tier is a parameter (free in dev per the cost decision).
param location string
param prefix string
param tags object
@allowed(['free', 'basic', 'standard'])
param sku string
param roleAssignments array // [{principalId, principalType, role: 'reader'|'contributor'|'serviceContributor'}]

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: '${prefix}-search-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  sku: { name: sku }
  properties: {
    replicaCount: 1
    partitionCount: 1
    disableLocalAuth: true
    publicNetworkAccess: 'enabled'
  }
}

var roles = {
  reader: '1407120a-92aa-4202-b7e9-c0e197c71c8f'
  contributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  serviceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  name: guid(search.id, a.principalId, a.role)
  scope: search
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles[a.role])
  }
}]

output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net'
