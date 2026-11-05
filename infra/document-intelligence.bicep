@description('Custom subdomain and resource name. Entra authentication requires a custom subdomain. Defaults to a stable name for the target resource group.')
@minLength(2)
@maxLength(64)
param name string = 'di-harness-${uniqueString(subscription().id, resourceGroup().name)}'

@description('Region for the resource.')
param location string = resourceGroup().location

@description('Tags applied to harness-owned resources.')
param tags object = {}

@description('Pricing tier. F0 is free for development: 500 pages a month, files up to 4 MB, and only the first 2 pages of a PDF are analysed. S0 is billed per page.')
@allowed([
  'F0'
  'S0'
])
param sku string

// Keyless by design: shared keys are disabled and callers authenticate with Entra. The operator
// is granted "Cognitive Services User" on this resource separately; the template grants no roles.
resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'FormRecognizer'
  sku: {
    name: sku
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

output name string = account.name
output endpoint string = account.properties.endpoint
output resourceId string = account.id
