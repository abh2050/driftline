// Service Bus Standard (Entra-only) with Driftline topics and per-app subscriptions.
// Subscriptions are named after the Dapr app id that consumes them.
param location string
param prefix string
param tags object
param topics array          // [{name, subscriptions: [..]}]
param roleAssignments array // [{principalId, principalType, role: 'sender'|'receiver'|'owner'}]

resource ns 'Microsoft.ServiceBus/namespaces@2026-01-01' = {
  name: '${prefix}-sb-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard' }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
  }
}

resource t 'Microsoft.ServiceBus/namespaces/topics@2026-01-01' = [for topic in topics: {
  parent: ns
  name: topic.name
  properties: {
    defaultMessageTimeToLive: 'P2D'
  }
}]

module subs 'servicebus-subs.bicep' = [for (topic, i) in topics: {
  name: 'sb-subs-${topic.name}'
  params: {
    namespaceName: ns.name
    topicName: topic.name
    subscriptions: topic.subscriptions
  }
  dependsOn: [t]
}]

var roles = {
  sender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  receiver: '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
  owner: '090c5cfd-751d-490a-894a-3ce6f1109419'
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  name: guid(ns.id, a.principalId, a.role)
  scope: ns
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles[a.role])
  }
}]

output name string = ns.name
output fqdn string = '${ns.name}.servicebus.windows.net'
