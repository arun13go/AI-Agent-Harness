targetScope = 'resourceGroup'

@description('Name of the opt-in harness registration deployment script.')
param name string

@description('Deployment-script location; must support the selected script service.')
param location string

@description('Tags applied only to the registration deployment script.')
param tags object

@description('Existing dedicated user-assigned identity in this resource group. No roles are granted.')
@minLength(1)
param identityName string

@description('Existing Foundry project ARM ID from the canonical registry prerequisites.')
param projectResourceId string

@description('Existing Foundry project endpoint from the canonical registry prerequisites.')
param projectEndpoint string

@description('Already available immutable ACR image reference, including sha256 digest.')
@minLength(1)
param image string

@description('Existing model deployment; does not create or change a model.')
param modelDeploymentName string

@description('Acknowledge exclusive release ownership across ARM, azd, SDK and other writers.')
param exclusiveDeploymentOwner bool

@description('Explicitly allow initial endpoint exposure when no previous agent exists.')
param allowInitialExposure bool

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: identityName
}

var bundle = loadJsonContent('./registration-bundle.json')

module script 'br/public:avm/res/resources/deployment-script:0.5.2' = {
  params: {
    name: name
    location: location
    tags: tags
    kind: 'AzureCLI'
    azCliVersion: '2.89.1'
    managedIdentities: {
      userAssignedResourceIds: [identity.id]
    }
    scriptContent: loadTextContent('./scripts/register-agent.sh')
    environmentVariables: [
      {
        name: 'HARNESS_RUNNER_BUNDLE'
        value: string(bundle)
      }
      {
        name: 'HARNESS_REGISTRATION_TARGET'
        value: string({
          project_resource_id: projectResourceId
          project_endpoint: projectEndpoint
        })
      }
      {
        name: 'HARNESS_REGISTRATION_RELEASE'
        value: string({
          image: image
          model_deployment_name: modelDeploymentName
        })
      }
      {
        name: 'HARNESS_REGISTRATION_CLIENT_ID'
        value: identity.properties.clientId
      }
      {
        name: 'HARNESS_EXCLUSIVE_OWNER'
        value: toLower(string(exclusiveDeploymentOwner))
      }
      {
        name: 'HARNESS_ALLOW_INITIAL_EXPOSURE'
        value: toLower(string(allowInitialExposure))
      }
    ]
    timeout: 'PT20M'
    retentionInterval: 'PT24H'
    cleanupPreference: 'Always'
    // Override AVM's utcNow default so an unchanged release does not force a rerun.
    baseTime: uniqueString(bundle.sha256, loadTextContent('./scripts/register-agent.sh'), image, modelDeploymentName, projectResourceId, projectEndpoint, identity.id, string(exclusiveDeploymentOwner), string(allowInitialExposure))
    runOnce: false
    enableTelemetry: false
  }
}

output receipt object = script.outputs.outputs
