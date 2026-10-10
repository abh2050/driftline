// Container Apps environment (Consumption workload profile, Dapr built in) and the
// Dapr components shared by Driftline services. Logs go to Azure Monitor via OTLP, not
// the environment log sink.
param location string
param prefix string
param tags object
param serviceBusFqdn string
@description('Client id of the identity Dapr uses for Service Bus, per app scope.')
param pubsubIdentities array // [{appId, clientId}]

resource env 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: '${prefix}-cae'
  location: location
  tags: tags
  properties: {
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: { destination: 'azure-monitor' }
    zoneRedundant: false
  }
}

// One pubsub component per consuming/publishing app, each bound to that app's identity.
resource pubsub 'Microsoft.App/managedEnvironments/daprComponents@2025-01-01' = [for p in pubsubIdentities: {
  parent: env
  name: 'pubsub-${p.appId}'
  properties: {
    componentType: 'pubsub.azure.servicebus.topics'
    version: 'v1'
    metadata: [
      { name: 'namespaceName', value: serviceBusFqdn }
      { name: 'azureClientId', value: p.clientId }
      { name: 'consumerID', value: p.appId }
      { name: 'disableEntityManagement', value: 'true' }
    ]
    scopes: [p.appId]
  }
}]

@description('Dapr cron input binding that ticks the detector (config/detector.yaml schedule).')
param detectorSchedule string

resource detectorCron 'Microsoft.App/managedEnvironments/daprComponents@2025-01-01' = {
  parent: env
  name: 'detector-cron'
  properties: {
    componentType: 'bindings.cron'
    version: 'v1'
    metadata: [
      { name: 'schedule', value: detectorSchedule }
      { name: 'direction', value: 'input' }
    ]
    scopes: ['detector']
  }
}

output id string = env.id
output name string = env.name
output defaultDomain string = env.properties.defaultDomain
