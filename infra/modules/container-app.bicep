// Generic Driftline container app: its own user-assigned identity, ACR pull through that
// identity, optional Dapr sidecar and ingress. azd replaces the image on deploy; when the
// app already exists we keep its current image so `azd provision` does not roll it back.
param name string
param location string
param tags object
param environmentId string
param identityId string
param identityClientId string
param registryServer string
param exists bool
param env array = []
param targetPort int = 0
@allowed(['none', 'internal', 'external'])
param ingress string = 'none'
param transport string = 'auto'
param additionalPorts array = []
param allowedIps array = []
param daprAppId string = ''
param daprAppPort int = 0
param minReplicas int = 0
param maxReplicas int = 1
param cpu string = '0.25'
param memory string = '0.5Gi'
param probes array = []
@description('Image used until azd deploys the service image.')
param placeholderImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

module current 'fetch-image.bicep' = if (exists) {
  name: 'fetch-image-${name}'
  params: { name: name }
}

var ipRules = [for (ip, i) in allowedIps: {
  name: 'allow-${i}'
  action: 'Allow'
  ipAddressRange: ip
}]
var image = exists ? current!.outputs.image : placeholderImage

resource app 'Microsoft.App/containerApps@2025-01-01' = {
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
      activeRevisionsMode: 'Single'
      registries: [{ server: registryServer, identity: identityId }]
      ingress: ingress == 'none' ? null : {
        external: ingress == 'external'
        targetPort: targetPort
        transport: transport
        additionalPortMappings: additionalPorts
        ipSecurityRestrictions: ipRules
      }
      dapr: empty(daprAppId) ? { enabled: false } : {
        enabled: true
        appId: daprAppId
        appPort: daprAppPort == 0 ? null : daprAppPort
        appProtocol: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'main'
          image: image
          env: concat(env, [{ name: 'AZURE_CLIENT_ID', value: identityClientId }])
          resources: { cpu: json(cpu), memory: memory }
          probes: probes
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: maxReplicas }
    }
  }
}

output name string = app.name
output fqdn string = ingress == 'none' ? '' : app.properties.configuration.ingress.fqdn
