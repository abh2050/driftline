// Entra ID sign-in for the console with Container Apps authentication (spec 5.2.1).
// No client secret: the console's managed identity is a federated credential on the Driftline
// API app (scripts/entra_app.py), named by the reserved secret below. Tokens are kept in a
// blob token store reached with the same identity, and the user's access token for the
// Driftline API is forwarded to ops-api by the console's server.
param appName string
param tenantId string
param clientId string
param audience string
param identityId string
param tokensContainerUri string

resource app 'Microsoft.App/containerApps@2025-01-01' existing = {
  name: appName
}

resource auth 'Microsoft.App/containerApps/authConfigs@2026-07-01' = {
  parent: app
  name: 'current'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: clientId
          clientSecretSettingName: 'override-use-mi-fic-assertion-client-id'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
        }
        login: {
          loginParameters: ['scope=openid profile email offline_access ${audience}/user_impersonation']
        }
        validation: {
          allowedAudiences: [clientId, audience]
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
        azureBlobStorage: {
          blobContainerUri: tokensContainerUri
          managedIdentityResourceId: identityId
        }
      }
    }
    httpSettings: { requireHttps: true }
  }
}
