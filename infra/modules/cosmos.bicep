// Cosmos DB for NoSQL, serverless, Entra-only, with the four Driftline containers.
param location string
param prefix string
param tags object
@description('[{principalId, role: "reader"|"contributor"}] data-plane assignments.')
param dataAssignments array

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: '${prefix}-cosmos-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    capabilities: [{ name: 'EnableServerless' }]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [{ locationName: location, failoverPriority: 0, isZoneRedundant: false }]
    minimalTlsVersion: 'Tls12'
  }
}

resource db 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: 'driftline'
  properties: { resource: { id: 'driftline' } }
}

var containers = [
  { name: 'windows', ttl: 1209600 }   // 14 days of one-minute windows
  { name: 'baselines', ttl: -1 }
  { name: 'incidents', ttl: -1 }
  { name: 'checkpoints', ttl: 2592000 }
]

resource cs 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = [for c in containers: {
  parent: db
  name: c.name
  properties: {
    resource: {
      id: c.name
      partitionKey: { paths: ['/pk'], kind: 'Hash', version: 2 }
      defaultTtl: c.ttl
    }
  }
}]

// Built-in data-plane role definitions.
var roleIds = {
  reader: '00000000-0000-0000-0000-000000000001'
  contributor: '00000000-0000-0000-0000-000000000002'
}

resource assignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [for a in dataAssignments: {
  parent: account
  name: guid(account.id, a.principalId, a.role)
  properties: {
    principalId: a.principalId
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${roleIds[a.role]}'
    scope: account.id
  }
}]

output endpoint string = account.properties.documentEndpoint
output name string = account.name
