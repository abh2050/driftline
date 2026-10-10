// Generic Driftline Container Apps job (schedule, manual or event trigger). Jobs cannot use
// Dapr, so they talk to Service Bus with the SDK (ADR 0003).
param name string
param location string
param tags object
param environmentId string
param identityId string
param identityClientId string
param registryServer string
param exists bool
param env array = []
@allowed(['Schedule', 'Manual', 'Event'])
param triggerType string
param cronExpression string = ''
param eventScaleRules array = []
param replicaTimeoutSeconds int = 600
param cpu string = '0.25'
param memory string = '0.5Gi'
param placeholderImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

module current 'fetch-job-image.bicep' = if (exists) {
  name: 'fetch-job-image-${name}'
  params: { name: name }
}

var image = exists ? current!.outputs.image : placeholderImage

resource job 'Microsoft.App/jobs@2025-01-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': name })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: triggerType
      replicaTimeout: replicaTimeoutSeconds
      replicaRetryLimit: 0
      registries: [{ server: registryServer, identity: identityId }]
      scheduleTriggerConfig: triggerType == 'Schedule' ? {
        cronExpression: cronExpression
        parallelism: 1
        replicaCompletionCount: 1
      } : null
      manualTriggerConfig: triggerType == 'Manual' ? {
        parallelism: 1
        replicaCompletionCount: 1
      } : null
      eventTriggerConfig: triggerType == 'Event' ? {
        parallelism: 1
        replicaCompletionCount: 1
        scale: { minExecutions: 0, maxExecutions: 5, pollingInterval: 30, rules: eventScaleRules }
      } : null
    }
    template: {
      containers: [
        {
          name: 'main'
          image: image
          env: concat(env, [{ name: 'AZURE_CLIENT_ID', value: identityClientId }])
          resources: { cpu: json(cpu), memory: memory }
        }
      ]
    }
  }
}

output name string = job.name
