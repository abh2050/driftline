// Key Vault (RBAC). Holds only the GitHub App private key (spec 2.4).
param location string
param prefix string
param tags object
param roleAssignments array // [{principalId, principalType, role: 'secretsUser'|'secretsOfficer'}]

resource kv 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: take('${prefix}-kv-${uniqueString(resourceGroup().id)}', 24)
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

var roles = {
  secretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  secretsOfficer: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  name: guid(kv.id, a.principalId, a.role)
  scope: kv
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles[a.role])
  }
}]

output name string = kv.name
output uri string = kv.properties.vaultUri
