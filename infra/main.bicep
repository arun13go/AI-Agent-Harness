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

resource existingGroup 'Microsoft.Resources/resourceGroups@2025-04-01' existing = {
  name: resourceGroupName
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
