targetScope = 'resourceGroup'

@description('Globally unique registry name.')
param name string

@description('Registry deployment region.')
param location string

@description('Tags applied only to the new registry.')
param tags object

@description('Existing Foundry account name.')
param foundryAccountName string

@description('Existing Foundry project name.')
param foundryProjectName string

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: foundryProjectName
}

module registry 'br/public:avm/res/container-registry/registry:0.13.1' = {
  params: {
    name: name
    location: location
    tags: tags
    acrSku: 'Basic'
    acrAdminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
    networkRuleSetDefaultAction: 'Allow'
    zoneRedundancy: 'Disabled'
    retentionPolicyStatus: 'disabled'
    roleAssignmentMode: 'LegacyRegistryPermissions'
    azureADAuthenticationAsArmPolicyStatus: 'enabled'
    enableTelemetry: false
    roleAssignments: [
      {
        principalId: project.identity.principalId!
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: 'AcrPull'
      }
    ]
  }
}

resource connection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'harness-${name}'
  properties: {
    category: 'ContainerRegistry'
    authType: 'AAD'
    target: 'https://${registry.outputs.loginServer}'
    metadata: {
      ResourceId: registry.outputs.resourceId
    }
  }
}

output name string = registry.outputs.name
output loginServer string = registry.outputs.loginServer
output resourceId string = registry.outputs.resourceId
output projectId string = project.id
output projectEndpoint string = project.properties.endpoints['AI Foundry API']
