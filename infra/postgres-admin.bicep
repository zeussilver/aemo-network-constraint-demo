// One Microsoft Entra administrator on a PostgreSQL flexible server. A module, because the
// resource name is the principal's object ID, which for a new managed identity is only
// known once that identity exists.

param serverName string
param objectId string
param principalName string
@allowed([ 'User', 'Group', 'ServicePrincipal' ])
param principalType string

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2025-08-01' existing = {
  name: serverName
}

resource admin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2025-08-01' = {
  parent: server
  name: objectId
  properties: {
    principalType: principalType
    principalName: principalName
    tenantId: tenant().tenantId
  }
}
