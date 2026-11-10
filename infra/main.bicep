targetScope = 'subscription'

@description('Local azd environment name used to tag only harness-owned resources.')
@minLength(1)
@maxLength(32)
param environmentName string

@description('Region for the dedicated development registry.')
param location string

@description('Existing resource group containing the selected Foundry project.')
param resourceGroupName string

@description('Existing Foundry account. This template does not create or modify it.')
param foundryAccountName string

@description('Existing Foundry project. This template does not create or modify it.')
param foundryProjectName string

@description('Existing model deployment to reuse without reprovisioning.')
param modelDeploymentName string

@sealed()
type RegistrationConfig = {
  identityName: string
  image: string
  exclusiveDeploymentOwner: bool
  allowInitialExposure: bool
}

@description('Opt-in candidate registration using an existing approved identity. Null leaves the azd path unchanged.')
param registrationConfig RegistrationConfig?

@description('Opt-in Azure AI Search service for the grounded profile. "none" creates nothing.')
@allowed([
  'none'
  'free'
  'basic'
])
param searchServiceSku string = 'none'

@description('Resource group for the optional resources (search, document extraction and Cosmos DB). Empty reuses resourceGroupName; any other name is created by this template so an experiment can be removed by deleting that group.')
param searchResourceGroupName string = ''

@description('Semantic ranker plan for the search service.')
@allowed([
  'disabled'
  'free'
])
param searchSemanticSearch string = 'disabled'

@description('Opt-in Azure AI Document Intelligence resource that extracts PDF and Word files for ingestion. "none" creates nothing; F0 is free for development, S0 is billed per page.')
@allowed([
  'none'
  'F0'
  'S0'
])
param documentIntelligenceSku string = 'none'

@description('Opt-in Azure Cosmos DB account for durable run state and audit events. "none" creates nothing; "free" uses the one free-tier account per subscription (development only); "serverless" bills per request.')
@allowed([
  'none'
  'free'
  'serverless'
])
param cosmosSku string = 'none'

@description('Days before audit events expire in the optional Cosmos DB account.')
@minValue(1)
param cosmosAuditRetentionDays int = 90

var useOwnGroup = (searchServiceSku != 'none' || documentIntelligenceSku != 'none' || cosmosSku != 'none') && !empty(searchResourceGroupName) && searchResourceGroupName != resourceGroupName
var searchTags = {
  'azd-env-name': environmentName
  'azd-service-name': 'harness'
}

resource existingGroup 'Microsoft.Resources/resourceGroups@2025-04-01' existing = {
  name: resourceGroupName
}

resource searchGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = if (useOwnGroup) {
  name: searchResourceGroupName
  location: location
  tags: searchTags
}

module search './search.bicep' = if (searchServiceSku != 'none' && !useOwnGroup) {
  scope: existingGroup
  params: {
    name: 'srch-harness-${uniqueString(subscription().id, resourceGroupName)}'
    location: location
    tags: searchTags
    sku: searchServiceSku == 'basic' ? 'basic' : 'free'
    semanticSearch: searchSemanticSearch
  }
}

module searchInOwnGroup './search.bicep' = if (searchServiceSku != 'none' && useOwnGroup) {
  scope: searchGroup
  params: {
    name: 'srch-harness-${uniqueString(subscription().id, searchResourceGroupName)}'
    location: location
    tags: searchTags
    sku: searchServiceSku == 'basic' ? 'basic' : 'free'
    semanticSearch: searchSemanticSearch
  }
}

module documentIntelligence './document-intelligence.bicep' = if (documentIntelligenceSku != 'none' && !useOwnGroup) {
  scope: existingGroup
  params: {
    name: 'di-harness-${uniqueString(subscription().id, resourceGroupName)}'
    location: location
    tags: searchTags
    sku: documentIntelligenceSku == 'S0' ? 'S0' : 'F0'
  }
}

module documentIntelligenceInOwnGroup './document-intelligence.bicep' = if (documentIntelligenceSku != 'none' && useOwnGroup) {
  scope: searchGroup
  params: {
    name: 'di-harness-${uniqueString(subscription().id, searchResourceGroupName)}'
    location: location
    tags: searchTags
    sku: documentIntelligenceSku == 'S0' ? 'S0' : 'F0'
  }
}

module cosmos './cosmos.bicep' = if (cosmosSku != 'none') {
  scope: resourceGroup(useOwnGroup ? searchResourceGroupName : resourceGroupName)
  dependsOn: [
    searchGroup
  ]
  params: {
    name: 'cosmos-harness-${uniqueString(subscription().id, useOwnGroup ? searchResourceGroupName : resourceGroupName)}'
    location: location
    tags: searchTags
    sku: cosmosSku == 'serverless' ? 'serverless' : 'free'
    auditRetentionDays: cosmosAuditRetentionDays
  }
}

module registry './registry.bicep' = {
  scope: existingGroup
  params: {
    name: 'crharness${uniqueString(subscription().id, resourceGroupName, environmentName)}'
    location: location
    tags: {
      'azd-env-name': environmentName
      'azd-service-name': 'harness'
    }

    foundryAccountName: foundryAccountName
    foundryProjectName: foundryProjectName
  }
}

module registration './registration.bicep' = if (registrationConfig != null) {
  scope: existingGroup
  params: {
    name: 'harness-registration-${uniqueString(subscription().id, resourceGroupName, environmentName)}'
    location: location
    tags: {
      'azd-env-name': environmentName
      'azd-service-name': 'harness'
    }
    identityName: registrationConfig!.identityName
    image: registrationConfig!.image
    exclusiveDeploymentOwner: registrationConfig!.exclusiveDeploymentOwner
    allowInitialExposure: registrationConfig!.allowInitialExposure
    projectResourceId: registry.outputs.projectId
    projectEndpoint: registry.outputs.projectEndpoint
    modelDeploymentName: modelDeploymentName
  }
}

output AZURE_RESOURCE_GROUP string = existingGroup.name
output AZURE_CONTAINER_REGISTRY_NAME string = registry.outputs.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer
output AZURE_CONTAINER_REGISTRY_RESOURCE_ID string = registry.outputs.resourceId
output AZURE_AI_PROJECT_ID string = registry.outputs.projectId
output AZURE_AI_PROJECT_ENDPOINT string = registry.outputs.projectEndpoint
output FOUNDRY_PROJECT_ENDPOINT string = registry.outputs.projectEndpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = modelDeploymentName
output HARNESS_REGISTRATION_RECEIPT object = registrationConfig != null ? registration!.outputs.receipt : {}
output AZURE_SEARCH_ENDPOINT string = (searchServiceSku != 'none' && useOwnGroup) ? searchInOwnGroup!.outputs.endpoint : (searchServiceSku != 'none' ? search!.outputs.endpoint : '')
output AZURE_SEARCH_SERVICE_NAME string = (searchServiceSku != 'none' && useOwnGroup) ? searchInOwnGroup!.outputs.name : (searchServiceSku != 'none' ? search!.outputs.name : '')
output AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT string = (documentIntelligenceSku != 'none' && useOwnGroup) ? documentIntelligenceInOwnGroup!.outputs.endpoint : (documentIntelligenceSku != 'none' ? documentIntelligence!.outputs.endpoint : '')
output AZURE_DOCUMENT_INTELLIGENCE_NAME string = (documentIntelligenceSku != 'none' && useOwnGroup) ? documentIntelligenceInOwnGroup!.outputs.name : (documentIntelligenceSku != 'none' ? documentIntelligence!.outputs.name : '')
output AZURE_COSMOS_ENDPOINT string = cosmosSku != 'none' ? cosmos!.outputs.endpoint : ''
output AZURE_COSMOS_NAME string = cosmosSku != 'none' ? cosmos!.outputs.name : ''
