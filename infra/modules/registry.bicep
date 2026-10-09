// Container registry; every service identity gets AcrPull.
param location string
param prefix string
param tags object
param pullers array
param pushers array

resource acr 'Microsoft.ContainerRegistry/registries@2025-11-01' = {
  name: replace('${prefix}acr${uniqueString(resourceGroup().id)}', '-', '')
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
  }
}

var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var acrPush = '8311e382-0749-4cb8-b61a-304f252e45ec'

resource pullRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for p in pullers: {
  name: guid(acr.id, p.principalId, acrPull)
  scope: acr
  properties: {
    principalId: p.principalId
    principalType: p.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
  }
}]

resource pushRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for p in pushers: {
  name: guid(acr.id, p.principalId, acrPush)
  scope: acr
  properties: {
    principalId: p.principalId
    principalType: p.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPush)
  }
}]

output name string = acr.name
output loginServer string = acr.properties.loginServer
