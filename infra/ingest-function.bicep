// Hourly NEMWeb ingest on Azure Functions (Flex Consumption, Python). Replaces the GitHub
// Actions schedule, which never fired for this repository.
//
// No keys anywhere: the storage account rejects shared-key access, the host reaches it
// with the app's system-assigned identity, and the same identity signs in to PostgreSQL
// (the deploy workflow creates its database role, DML only).

param location string
param suffix string
param postgresHost string
param databaseName string
param appInsightsName string

var functionAppName = 'func-siteops-${suffix}'
var storageName = 'stsiteops${suffix}'
var packageContainer = 'app-package'
var storageRoles = {
  blobDataOwner: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
  queueDataContributor: '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
  tableDataContributor: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource packages 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: packageContainer
  properties: { publicAccess: 'None' }
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'asp-func-siteops-${suffix}'
  location: location
  kind: 'functionapp'
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  properties: { reserved: true }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${packageContainer}'
          authentication: { type: 'SystemAssignedIdentity' }
        }
      }
      // One 2 GB instance, scale to zero between runs, no always-ready instances.
      scaleAndConcurrency: { maximumInstanceCount: 1, instanceMemoryMB: 2048 }
      runtime: { name: 'python', version: '3.13' }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        // Identity-based host storage: account name only, no connection string or key.
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'AZURE_POSTGRES_HOST', value: postgresHost }
        { name: 'AZURE_POSTGRES_DATABASE', value: databaseName }
        { name: 'AZURE_POSTGRES_USER', value: functionAppName }
      ]
    }
  }
  dependsOn: [ packages ]
}

resource storageRoleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in items(storageRoles): {
  name: guid(storage.id, functionApp.id, role.value)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.value)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}]

resource scmBasicAuth 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01' = {
  parent: functionApp
  name: 'scm'
  properties: { allow: false }
}

resource ftpBasicAuth 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01' = {
  parent: functionApp
  name: 'ftp'
  properties: { allow: false }
}

output functionAppName string = functionApp.name
output functionPrincipalId string = functionApp.identity.principalId
