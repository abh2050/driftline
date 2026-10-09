param namespaceName string
param topicName string
param subscriptions array

resource ns 'Microsoft.ServiceBus/namespaces@2026-01-01' existing = {
  name: namespaceName
}

resource topic 'Microsoft.ServiceBus/namespaces/topics@2026-01-01' existing = {
  parent: ns
  name: topicName
}

resource s 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2026-01-01' = [for sub in subscriptions: {
  parent: topic
  name: sub
  properties: {
    maxDeliveryCount: 10
    lockDuration: 'PT1M'
    deadLetteringOnMessageExpiration: true
  }
}]
