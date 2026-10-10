// F7 (spec fault list): a chat completions pass-through to Foundry with a low rate limit.
// Callers keep their own Entra token for Foundry (audience https://ai.azure.com); APIM checks
// it is from this tenant, rate-limits, and forwards it unchanged.
param apimName string
param tenantId string
param foundryEndpoint string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'chat-gateway'
  properties: {
    displayName: 'Chat gateway (F7)'
    path: 'chat-gateway'
    protocols: ['https']
    serviceUrl: foundryEndpoint
    subscriptionRequired: false
  }
}

resource op 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'post-any'
  properties: {
    displayName: 'POST'
    method: 'POST'
    urlTemplate: '/*'
  }
}

resource policy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace(loadTextContent('../apim/policies/chat-gateway.xml'), '{tenant-id}', tenantId)
  }
}
