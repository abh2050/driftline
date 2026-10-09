// Monthly cost budget with alerts at 80% actual and 100% forecast (spec 5.4.6).
param prefix string
param amount int
param contactEmails array
param startDate string

resource budget 'Microsoft.Consumption/budgets@2026-06-01' = {
  name: '${prefix}-budget'
  properties: {
    category: 'Cost'
    amount: amount
    timeGrain: 'Monthly'
    timePeriod: { startDate: startDate }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: contactEmails
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: contactEmails
      }
    }
  }
}

output name string = budget.name
