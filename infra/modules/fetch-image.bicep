// Reads the image a container app currently runs. Kept in its own deployment so the
// lookup does not form a cycle with the app being updated.
param name string

resource app 'Microsoft.App/containerApps@2025-01-01' existing = {
  name: name
}

output image string = app.properties.template.containers[0].image
