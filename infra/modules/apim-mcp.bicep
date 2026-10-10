// APIM MCP surfaces (spec 5.1.12): named values, the GitHub MCP and kql-tool MCP servers with
// their policies, and gateway logs to Log Analytics.
param apimName string
param tenantId string
param driftlineApiAppId string
param driftlineApiAudience string
param githubMcpUrl string
param kqlToolUrl string
param workspaceId string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

var namedValues = {
  'tenant-id': tenantId
  'driftline-api-app-id': driftlineApiAppId
  'driftline-api-audience': driftlineApiAudience
}

resource nv 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [for item in items(namedValues): {
  parent: apim
  name: item.key
  properties: {
    displayName: item.key
    value: item.value
    secret: false
  }
}]

var servers = [
  { name: 'github-mcp', path: 'github-mcp', url: githubMcpUrl, policy: loadTextContent('../apim/policies/github-mcp.xml'), title: 'GitHub MCP (Driftline)' }
  { name: 'kql-mcp', path: 'kql-mcp', url: kqlToolUrl, policy: loadTextContent('../apim/policies/kql-mcp.xml'), title: 'Driftline KQL tool' }
]

// Existing MCP servers are proxied through a backend resource referenced by backendId; with
// only serviceUrl, APIM answers as its own (empty) MCP server instead of passing through.
resource backends 'Microsoft.ApiManagement/service/backends@2024-05-01' = [for s in servers: {
  parent: apim
  name: '${s.name}-backend'
  properties: {
    description: s.title
    url: s.url
    protocol: 'http'
    tls: { validateCertificateChain: true, validateCertificateName: true }
  }
}]

resource apis 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = [for (s, i) in servers: {
  parent: apim
  name: s.name
  properties: {
    type: 'mcp'
    displayName: s.title
    path: s.path
    protocols: ['https']
    backendId: backends[i].name
    subscriptionRequired: false
    mcpProperties: {
      transportType: 'streamable'
    }
  }
  dependsOn: [nv]
}]

resource policies 'Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview' = [for (s, i) in servers: {
  parent: apis[i]
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: s.policy
  }
}]

resource diag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: apim
  name: 'gateway-logs'
  properties: {
    workspaceId: workspaceId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      { category: 'GatewayLogs', enabled: true }
      { category: 'GatewayMCPLogs', enabled: true }
    ]
  }
}

output githubMcpEndpoint string = '${apim.properties.gatewayUrl}/github-mcp/mcp'
output kqlMcpEndpoint string = '${apim.properties.gatewayUrl}/kql-mcp/mcp'
