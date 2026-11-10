@description('Globally unique Cosmos DB account name. Defaults to a stable name for the target resource group.')
@minLength(3)
@maxLength(44)
param name string = 'cosmos-harness-${uniqueString(subscription().id, resourceGroup().name)}'

@description('Region for the account.')
param location string = resourceGroup().location

@description('Tags applied to harness-owned resources.')
param tags object = {}

@description('Free is for development only: one free-tier account per subscription, 1,000 RU/s and 25 GB at no cost. Serverless bills per request.')
@allowed([
  'free'
  'serverless'
])
param sku string

@description('Days before audit events expire. Set to a value that meets your retention policy.')
@minValue(1)
param auditRetentionDays int = 90

// Keyless by design: shared keys are disabled and callers authenticate with Entra roles. Data
// roles are granted separately: the built-in Data Contributor on the whole account for the
// operator, and "harness-audit-append" on the audit container for the agent identity.
resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: name
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    capabilities: sku == 'serverless' ? [
      {
        name: 'EnableServerless'
      }
    ] : []
    enableFreeTier: sku == 'free'
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
    publicNetworkAccess: 'Enabled'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: 'harness'
  properties: {
    resource: {
      id: 'harness'
    }
    options: sku == 'free' ? {
      throughput: 400
    } : {}
  }
}

// Documents are filtered by kind and run id only, so the stored payload is left out of the index.
var indexing = {
  indexingMode: 'consistent'
  includedPaths: [
    {
      path: '/*'
    }
  ]
  excludedPaths: [
    {
      path: '/payload/*'
    }
    {
      path: '/"_etag"/?'
    }
  ]
}

resource records 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'records'
  properties: {
    resource: {
      id: 'records'
      partitionKey: {
        paths: [
          '/tenantScope'
        ]
        kind: 'Hash'
        version: 2
      }
      defaultTtl: -1
      indexingPolicy: indexing
    }
  }
}

resource audit 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'audit'
  properties: {
    resource: {
      id: 'audit'
      partitionKey: {
        paths: [
          '/tenantScope'
        ]
        kind: 'Hash'
        version: 2
      }
      defaultTtl: auditRetentionDays * 86400
      uniqueKeyPolicy: {
        uniqueKeys: [
          {
            paths: [
              '/eventId'
            ]
          }
        ]
      }
      indexingPolicy: indexing
    }
  }
}

// Create and read only: no replace, upsert or delete, so a compromised agent cannot rewrite history.
resource auditAppendRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-11-15' = {
  parent: account
  name: guid(account.id, 'harness-audit-append')
  properties: {
    roleName: 'harness-audit-append'
    type: 'CustomRole'
    assignableScopes: [
      account.id
    ]
    permissions: [
      {
        dataActions: [
          'Microsoft.DocumentDB/databaseAccounts/readMetadata'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/create'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/read'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/executeQuery'
        ]
      }
    ]
  }
}

output name string = account.name
output endpoint string = account.properties.documentEndpoint
output databaseName string = database.name
output auditAppendRoleId string = auditAppendRole.id
