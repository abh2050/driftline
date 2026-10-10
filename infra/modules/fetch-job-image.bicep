// Reads the image a Container Apps job currently runs (separate deployment, no cycle).
param name string

resource job 'Microsoft.App/jobs@2025-01-01' existing = {
  name: name
}

output image string = job.properties.template.containers[0].image
