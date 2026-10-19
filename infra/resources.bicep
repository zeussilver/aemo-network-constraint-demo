// Resources inside the resource group. Free or near-free tiers only; see
// docs/console-PLAN.md section 6 for the cost of each item.

param location string
param adminObjectId string
param adminPrincipalName string
param alertEmail string
param githubRepository string
param githubEnvironment string
@secure()
param betterAuthSecret string
param ingestAlertEnabled bool

var suffix = take(uniqueString(resourceGroup().id), 6)
var webAppName = 'siteops-console-${suffix}'
var postgresName = 'psql-siteops-${suffix}'
var databaseName = 'ops_console'
var contributorRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

// ---------------------------------------------------------------------------------------
// Monitoring
// ---------------------------------------------------------------------------------------

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-siteops-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    // Hard stop at 0.1 GB/day keeps ingestion inside the 5 GB/month free allowance.
    workspaceCapping: { dailyQuotaGb: json('0.1') }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-siteops-${suffix}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-siteops-${suffix}'
  location: 'global'
  properties: {
    groupShortName: 'siteops'
    enabled: true
    emailReceivers: [
      { name: 'owner', emailAddress: alertEmail, useCommonAlertSchema: true }
    ]
  }
}

// Fires when any ingest run failed in the last 2 hours, or when no SCADA run succeeded
// (summarize over no rows still returns one row of zeros, so silence counts as failure).
resource ingestAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'alert-ingest-siteops-${suffix}'
  location: location
  properties: {
    displayName: 'NEMWeb ingest failed or stale'
    description: 'An ops_ingest source run failed, or no successful SCADA run in the last 2 hours.'
    severity: 2
    enabled: ingestAlertEnabled
    evaluationFrequency: 'PT1H'
    windowSize: 'PT2H'
    scopes: [ appInsights.id ]
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: '''
customEvents
| where timestamp > ago(2h) and name == "ingest_run"
| extend source = tostring(customDimensions.source), status = tostring(customDimensions.status)
| summarize failedRuns = countif(status == "failed"), scadaSuccesses = countif(source == "scada" and status == "success")
| where failedRuns > 0 or scadaSuccesses == 0
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: [ actionGroup.id ] }
  }
}

// ---------------------------------------------------------------------------------------
// GitHub Actions deploy identity: OIDC federation, no stored Azure secret
// ---------------------------------------------------------------------------------------

resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-siteops-github-${suffix}'
  location: location
}

resource githubFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: deployIdentity
  name: 'github-${githubEnvironment}'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubRepository}:environment:${githubEnvironment}'
    audiences: [ 'api://AzureADTokenExchange' ]
  }
}

// Contributor on this resource group only: deploy the web app, open/close database
// firewall rules for the runner.
resource deployRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, deployIdentity.id, contributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleId)
    principalId: deployIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------------------
// PostgreSQL: Entra authentication only (password login disabled)
// ---------------------------------------------------------------------------------------

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2025-08-01' = {
  name: postgresName
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '17'
    storage: { storageSizeGB: 32, autoGrow: 'Disabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: tenant().tenantId
    }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2025-08-01' = {
  parent: postgres
  name: databaseName
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// App Service has no fixed outbound IP on the Free tier, so Azure-internal traffic is
// allowed; every login still needs a valid Entra token for a known database role.
resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2025-08-01' = {
  parent: postgres
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

module adminUser 'postgres-admin.bicep' = {
  name: 'postgres-admin-user'
  params: {
    serverName: postgres.name
    objectId: adminObjectId
    principalName: adminPrincipalName
    principalType: 'User'
  }
  dependsOn: [ database, allowAzure ]
}

// Admin operations on one server must not overlap, hence the explicit chain.
module adminDeploy 'postgres-admin.bicep' = {
  name: 'postgres-admin-deploy-identity'
  params: {
    serverName: postgres.name
    objectId: deployIdentity.properties.principalId
    principalName: deployIdentity.name
    principalType: 'ServicePrincipal'
  }
  dependsOn: [ adminUser ]
}

// ---------------------------------------------------------------------------------------
// App Service (Linux, Free F1)
// ---------------------------------------------------------------------------------------

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'asp-siteops-${suffix}'
  location: location
  kind: 'linux'
  sku: { name: 'F1', tier: 'Free' }
  properties: { reserved: true }
}

resource webApp 'Microsoft.Web/sites@2024-04-01' = {
  name: webAppName
  location: location
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    siteConfig: {
      linuxFxVersion: 'NODE|22-lts'
      appCommandLine: 'node server.js'
      alwaysOn: false
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      appSettings: [
        { name: 'AZURE_POSTGRES_HOST', value: postgres.properties.fullyQualifiedDomainName }
        { name: 'AZURE_POSTGRES_DATABASE', value: databaseName }
        // The deploy workflow creates a database role with this name for the app's
        // system-assigned identity (infra/sql/app-role.sql).
        { name: 'AZURE_POSTGRES_USER', value: webAppName }
        { name: 'BETTER_AUTH_SECRET', value: betterAuthSecret }
        { name: 'BETTER_AUTH_URL', value: 'https://${webAppName}.azurewebsites.net' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'HOSTNAME', value: '0.0.0.0' }
        { name: 'NODE_ENV', value: 'production' }
        { name: 'NEXT_TELEMETRY_DISABLED', value: '1' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'false' }
      ]
    }
  }
}

// No basic-auth publishing: deployments go through Entra (GitHub OIDC) only.
resource scmBasicAuth 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01' = {
  parent: webApp
  name: 'scm'
  properties: { allow: false }
}

resource ftpBasicAuth 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01' = {
  parent: webApp
  name: 'ftp'
  properties: { allow: false }
}

// ---------------------------------------------------------------------------------------
// Hourly ingest on Azure Functions
// ---------------------------------------------------------------------------------------

module ingestFunction 'ingest-function.bicep' = {
  name: 'ingest-function'
  params: {
    location: location
    suffix: suffix
    postgresHost: postgres.properties.fullyQualifiedDomainName
    databaseName: databaseName
    appInsightsName: appInsights.name
  }
}

output webAppName string = webApp.name
output webAppUrl string = 'https://${webApp.properties.defaultHostName}'
output webAppPrincipalId string = webApp.identity.principalId
output postgresServerName string = postgres.name
output postgresHost string = postgres.properties.fullyQualifiedDomainName
output deployIdentityName string = deployIdentity.name
output deployIdentityClientId string = deployIdentity.properties.clientId
output appInsightsName string = appInsights.name
output functionAppName string = ingestFunction.outputs.functionAppName
output functionPrincipalId string = ingestFunction.outputs.functionPrincipalId
