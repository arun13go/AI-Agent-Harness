@description('Globally unique Azure AI Search service name. Defaults to a stable name for the target resource group.')
@minLength(2)
@maxLength(60)
param name string = 'srch-harness-${uniqueString(subscription().id, resourceGroup().name)}'

@description('Region for the search service.')
param location string = resourceGroup().location

@description('Tags applied to harness-owned resources.')
param tags object = {}

@description('Search SKU. Free is for development only: one per subscription, 50 MB, no SLA.')
@allowed([
  'free'
  'basic'
])
param sku string

@description('Semantic ranker plan. "free" allows about 1,000 semantic queries a month at no cost; "disabled" turns it off.')
@allowed([
  'disabled'
  'free'
])
param semanticSearch string = 'disabled'

// Keyless by design: shared keys are disabled and callers authenticate with Entra roles. Role
// assignments are granted separately to the agent identity (read) and to the operator (write).
resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'Default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: true
    semanticSearch: semanticSearch
  }
}

output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net'
output resourceId string = search.id
