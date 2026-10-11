// Alerts (spec 8.3): router p95 latency SLO, escalation rate and otel-collector drop rate, all
// to one email action group. The budget alert lives in budget.bicep.
param location string
param prefix string
param tags object
param workspaceId string
param amwId string
param emails array
@description('Router p95 decision latency SLO in milliseconds (model latency, not the client).')
param routerP95SloMs int = 8000
@description('Escalation rate above which the router is probably miscalibrated or starved.')
param escalationRateMax string = '0.25'
@description('Share of spans the collector failed to export.')
param dropRateMax string = '0.01'

resource group 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${prefix}-alerts'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'driftline'
    enabled: true
    emailReceivers: [for (e, i) in emails: {
      name: 'email-${i}'
      emailAddress: e
      useCommonAlertSchema: true
    }]
  }
}

var logAlerts = [
  {
    name: 'router-p95-latency'
    description: 'Router p95 decision latency above the ${routerP95SloMs} ms SLO over the last hour.'
    severity: 2
    window: 'PT1H'
    query: 'DriftlineDecisions_CL | where Client != "rules" | summarize p95 = percentile(LatencyMs, 95), n = count() | where n >= 5 and p95 > ${routerP95SloMs}'
  }
  {
    name: 'escalation-rate'
    description: 'More than ${escalationRateMax} of router decisions escalated over 6 hours.'
    severity: 3
    window: 'PT6H'
    query: 'DriftlineDecisions_CL | summarize escalated = countif(Escalated), n = count() | where n >= 10 and todouble(escalated) / n > ${escalationRateMax}'
  }
]

resource rules 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = [for a in logAlerts: {
  name: '${prefix}-${a.name}'
  location: location
  tags: tags
  properties: {
    displayName: 'Driftline: ${a.name}'
    description: a.description
    severity: a.severity
    enabled: true
    scopes: [workspaceId]
    evaluationFrequency: 'PT15M'
    windowSize: a.window
    criteria: {
      allOf: [
        {
          query: a.query
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    autoMitigate: true
    actions: { actionGroups: [group.id] }
  }
}]

// The collector scrapes its own metrics (prometheus/self) into the Azure Monitor workspace.
resource collector 'Microsoft.AlertsManagement/prometheusRuleGroups@2023-03-01' = {
  name: '${prefix}-otel-collector'
  location: location
  tags: tags
  properties: {
    description: 'otel-collector export health'
    scopes: [amwId]
    enabled: true
    interval: 'PT5M'
    rules: [
      {
        alert: 'OtelCollectorDropRate'
        expression: 'sum(rate(otelcol_exporter_send_failed_spans_total[15m])) / clamp_min(sum(rate(otelcol_exporter_sent_spans_total[15m])) + sum(rate(otelcol_exporter_send_failed_spans_total[15m])), 1e-9) > ${dropRateMax}'
        for: 'PT10M'
        severity: 2
        annotations: {
          description: 'otel-collector failed to export more than ${dropRateMax} of spans for 10 minutes.'
        }
        actions: [{ actionGroupId: group.id }]
        resolveConfiguration: { autoResolved: true, timeToResolve: 'PT10M' }
      }
    ]
  }
}
