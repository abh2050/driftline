// One user-assigned managed identity per Driftline service (spec section 10).
param location string
param prefix string
param services array
param tags object

resource ids 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = [for s in services: {
  name: '${prefix}-id-${s}'
  location: location
  tags: tags
}]

output identities array = [for (s, i) in services: {
  service: s
  id: ids[i].id
  principalId: ids[i].properties.principalId
  clientId: ids[i].properties.clientId
}]
