// Storage: immutable `audit` container (time-based retention, append allowed) and `eval-results`.
param location string
param prefix string
param tags object
@description('Days audit blobs are immutable. Keep small in dev so azd down can clean up.')
param auditRetentionDays int
param roleAssignments array // [{principalId, principalType, role: 'contributor'|'reader', container}]

resource sa 'Microsoft.Storage/storageAccounts@2025-06-01' = {
  name: take(replace('${prefix}st${uniqueString(resourceGroup().id)}', '-', ''), 24)
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    defaultToOAuthAuthentication: true
  }
}

resource blob 'Microsoft.Storage/storageAccounts/blobServices@2025-06-01' = {
  parent: sa
  name: 'default'
}

resource audit 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01' = {
  parent: blob
  name: 'audit'
}

resource auditPolicy 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2025-06-01' = {
  parent: audit
  name: 'default'
  properties: {
    immutabilityPeriodSinceCreationInDays: auditRetentionDays
    allowProtectedAppendWrites: true
  }
}

resource evalResults 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01' = {
  parent: blob
  name: 'eval-results'
}

// Container Apps authentication token store for the console (managed identity, no SAS).
resource tokens 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01' = {
  parent: blob
  name: 'tokens'
}

resource tokenRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in filter(roleAssignments, x => x.container == 'tokens'): {
  name: guid(sa.id, 'tokens', a.principalId, a.role)
  scope: tokens
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles[a.role])
  }
}]

var roles = {
  contributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  reader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
}

resource auditRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in filter(roleAssignments, x => x.container == 'audit'): {
  name: guid(sa.id, 'audit', a.principalId, a.role)
  scope: audit
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles[a.role])
  }
}]

resource evalRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in filter(roleAssignments, x => x.container == 'eval-results'): {
  name: guid(sa.id, 'eval-results', a.principalId, a.role)
  scope: evalResults
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles[a.role])
  }
}]

output name string = sa.name
output blobEndpoint string = sa.properties.primaryEndpoints.blob
output tokensContainerUri string = '${sa.properties.primaryEndpoints.blob}tokens'
