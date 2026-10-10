// One role assignment scoped to one Container Apps job (ops-api may start replay runs only).
param jobName string
param principalId string
param roleDefinitionId string

resource job 'Microsoft.App/jobs@2025-01-01' existing = {
  name: jobName
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(job.id, principalId, roleDefinitionId)
  scope: job
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
  }
}

output jobId string = job.id
