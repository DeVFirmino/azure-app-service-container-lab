# Azure walkthrough

Everything runs on your machine against the Azure control plane, except `az acr build`, which uploads your source and builds it in Azure. One block at a time; read the output before moving on.

This creates billable resources. [Cleanup](#cleanup) is the last step and should happen in the same session.

## 1. Variables

```zsh
export LAB_LOCATION="northeurope"
export LAB_RG="ai200-appservice-lab-rg"
export LAB_SUFFIX="$(openssl rand -hex 3)"
export LAB_ACR="ai200acr${LAB_SUFFIX}"
export LAB_PLAN="ai200-appservice-plan"
export LAB_WEBAPP="ai200-appservice-${LAB_SUFFIX}"
export LAB_IMAGE="appservice-config-api"
export LAB_TAG="v1"
echo "suffix: $LAB_SUFFIX"
```

Registry and Web App names are globally unique, hence the suffix. Write it down: a new terminal needs this re-run with the same value.

## 2. Resource group

```zsh
az group create --name "$LAB_RG" --location "$LAB_LOCATION" --output table
```

One dedicated group makes cleanup a single decision instead of a hunt through the portal.

## 3. Registry, admin off

```zsh
az acr create --resource-group "$LAB_RG" --name "$LAB_ACR" \
  --sku Basic --admin-enabled false --role-assignment-mode rbac --output table
```

The admin user is one shared password for the whole registry and does not say who pulled what. Disabling it forces the identity path.

`--role-assignment-mode rbac` is the current default, pinned here on purpose. Microsoft has said `rbac-abac` will become the default, and legacy roles including `AcrPull` are not honoured in an ABAC-enabled registry, where the equivalent is `Container Registry Repository Reader` plus `Container Registry Repository Catalog Lister`. Pinning keeps this lab working when the default moves.

```zsh
az acr show --resource-group "$LAB_RG" --name "$LAB_ACR" \
  --query "{sku:sku.name, admin:adminUserEnabled, mode:roleAssignmentMode}" --output table
```

**Success proves:** the registry exists with `admin: False` and `mode: RBAC`. There is no password to leak, and `AcrPull` will be honoured.

## 4. Build with ACR Tasks

Run from the repository root.

```zsh
az acr build --registry "$LAB_ACR" --resource-group "$LAB_RG" \
  --image "${LAB_IMAGE}:${LAB_TAG}" --file Dockerfile .

az acr repository show-tags --name "$LAB_ACR" --repository "$LAB_IMAGE" --output table
```

**Success proves:** the named tag exists in the private registry. No local Docker daemon, no `docker push`.

## 5. Linux plan

```zsh
az appservice plan create --resource-group "$LAB_RG" --name "$LAB_PLAN" \
  --is-linux --sku B1 --output table
```

B1 is the cheapest dedicated compute. Free and Shared run on shared instances with CPU quotas that make container symptoms hard to trust.

**Billing starts here**, not at the first request.

## 6. Web App

```zsh
az webapp create --resource-group "$LAB_RG" --plan "$LAB_PLAN" --name "$LAB_WEBAPP" \
  --container-image-name "${LAB_ACR}.azurecr.io/${LAB_IMAGE}:${LAB_TAG}" --output table
```

`--container-image-name` is current; `--deployment-container-image-name` still works but warns.

**Expect the site to be broken here** and do not test the URL. You pointed it at a private registry with no way to authenticate. Steps 7 to 10 are the fix.

## 7. Give it an identity

```zsh
WEBAPP_PRINCIPAL_ID="$(az webapp identity assign --resource-group "$LAB_RG" \
  --name "$LAB_WEBAPP" --query principalId --output tsv)"
```

**Success proves:** the app has an identity. It has been granted nothing.

## 8. AcrPull on the registry

```zsh
ACR_RESOURCE_ID="$(az acr show --resource-group "$LAB_RG" --name "$LAB_ACR" \
  --query id --output tsv)"

az role assignment create --assignee "$WEBAPP_PRINCIPAL_ID" \
  --scope "$ACR_RESOURCE_ID" --role AcrPull --output table
```

The scope is the registry, not the group. `AcrPull` reads images and nothing else.

If the command cannot find the principal, directory replication has not caught up. Wait a minute, or use `--assignee-object-id "$WEBAPP_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal`.

**Success proves:** the registry-scoped pull assignment now exists. It does not mean a pull will work yet. RBAC propagation takes time, App Service has not been told to use the identity, and the registry still has to accept the token audience.

## 9. Tell App Service to use it

```zsh
az webapp config set --resource-group "$LAB_RG" --name "$LAB_WEBAPP" \
  --generic-configurations '{"acrUseManagedIdentityCreds": true}' --output table
```

The step people skip, and why "I granted AcrPull and it still fails" is so common. Without it the platform keeps looking for `DOCKER_REGISTRY_SERVER_USERNAME` and `DOCKER_REGISTRY_SERVER_PASSWORD`.

App Service pulls also need ARM-scoped Entra tokens, which a registry can be configured to reject. New registries accept them, so yours is fine, but the check exists:

```zsh
az acr config authentication-as-arm show --registry "$LAB_ACR"
```

`enabled` is what the pull needs. Disabled gives an `UNAUTHORIZED` error that looks exactly like a missing role.

## 10. Port and app settings

```zsh
az webapp config appsettings set --resource-group "$LAB_RG" --name "$LAB_WEBAPP" \
  --settings WEBSITES_PORT=8080 APP_VERSION="$LAB_TAG" \
             EXTERNAL_API_KEY="placeholder-not-a-real-key" --output table
```

`WEBSITES_PORT` is not read by your app. It tells App Service which container port receives HTTP traffic, including the startup availability ping. The default is 80 and only one port is allowed.

`APP_VERSION` is read by the app and echoed at `/`. It is an app setting, not a property of the image, so it confirms the setting arrived rather than proving which image is running.

`EXTERNAL_API_KEY` practises the mechanism and protects nothing, so **use the fake value shown**: the command records it in your shell history. App settings are encrypted at rest and hidden in the portal until revealed, but shell history is not. Real secrets belong in Key Vault references, which is a different lab.

App settings become environment variables at container start, which is what lets one image run everywhere. Bake the key into the Dockerfile and you have shipped a secret to anyone who can pull.

Changing a setting restarts the app.

## 11. Logs, then verify

```zsh
az webapp log config --resource-group "$LAB_RG" --name "$LAB_WEBAPP" \
  --docker-container-logging filesystem --output table
az webapp restart --resource-group "$LAB_RG" --name "$LAB_WEBAPP"

for p in / /health/live /health/ready /config; do
  curl -s "https://${LAB_WEBAPP}.azurewebsites.net$p"; echo
done
```

Give it a minute; the first pull fetches every layer.

| Response | Proves | Does not prove |
|---|---|---|
| `/` returns `"version":"v1"` | A container started, traffic reached the configured port, and `APP_VERSION` arrived | Which image is running. `APP_VERSION` is set independently of the image |
| `/health/live` 200 | The process answers HTTP | Anything about dependencies |
| `/health/ready` 200 | The same, in this lab | That it would refuse traffic when unhealthy |
| `/config` `true` | The setting arrived as an env var | That the value is correct, only that it is non-empty |

To check which image the app is actually configured to run:

```zsh
az webapp config container show --resource-group "$LAB_RG" --name "$LAB_WEBAPP" --output table
```

`"version":"unknown"` at `/` means the container predates step 10. Restart and recheck. Get all four responses before moving on.

## Break the port on purpose

Port mismatch is the most common custom container failure and it produces a page that says nothing. Cause it once while you already know the answer.

```zsh
az webapp config appsettings set --resource-group "$LAB_RG" --name "$LAB_WEBAPP" \
  --settings WEBSITES_PORT=8000 --output table

az webapp log tail --resource-group "$LAB_RG" --name "$LAB_WEBAPP"
```

The container still listens on 8080, compiled into `CMD`. App Service now sends traffic and its availability ping to 8000. From another terminal you get a 503 with no detail, but the log shows uvicorn starting normally on `0.0.0.0:8080`, followed by a line like:

```
Container appservice-config-api_0_xxxxxxxx didn't respond to HTTP pings on port: 8000,
failing site start. See container logs for debugging.
```

Uvicorn bound 8080 and App Service probed 8000. That mismatch explains this failure.

A pull failure looks different: there is no container, so there is no application stdout at all, and the pull error appears in the platform startup log instead. Whether you have application output is the first fork in the diagnosis.

```zsh
az webapp config appsettings set --resource-group "$LAB_RG" --name "$LAB_WEBAPP" \
  --settings WEBSITES_PORT=8080 --output table
az webapp restart --resource-group "$LAB_RG" --name "$LAB_WEBAPP"
```

Because you injected exactly one change and reversed exactly that change, returning to 200 is strong causal evidence that the port mismatch caused the failure you observed.

## What it costs

[Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices), checked 17 September 2026, same in North Europe, West Europe and France Central. Order of magnitude, not a quote.

| What | Rate | Billed |
|---|---|---|
| App Service plan B1 Linux | 0.018 USD/hour | Per hour the plan exists, running or not |
| Container Registry Basic | 0.1666 USD/day | Per day, 10 GB included |
| ACR Tasks | 6,000 vCPU-seconds/month free, then 0.0001 USD/second | Per second of build |

The App Service plan keeps billing until cleanup, and cost reporting lags 8 to 24 hours.

## Cleanup

```zsh
az group delete --name "$LAB_RG" --yes
az group exists --name "$LAB_RG"
```

The delete removes the Web App, plan, registry, image, identity and the registry-scoped role assignment, and is not reversible. `az group exists` prints `false` once it is gone; `true` means it is still running.

## Evidence checklist

Nothing here has been run. This repository is the material; the record is yours.

- [ ] Local build ran, four endpoints answered, `whoami` printed `appuser`
- [ ] Registry shows `admin: False` and `mode: RBAC`
- [ ] `az acr build` succeeded and the tag is listed
- [ ] Role assignment created with the registry resource ID as scope
- [ ] `acrUseManagedIdentityCreds` applied
- [ ] All four endpoints answered, and `az webapp config container show` names the expected image
- [ ] Broken-port log showed a clean start on 8080 and a ping failure on 8000
- [ ] Restoring 8080 returned the site to 200
- [ ] `az group exists` returned `false`

Then answer one question in writing: which identity pulled the image, and how do you know.

## References

- [Configure a custom container for App Service](https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container?pivots=container-linux)
- [Managed identities in App Service](https://learn.microsoft.com/en-us/azure/app-service/overview-managed-identity)
- [Registry acceptance of Entra authentication scopes](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-disable-authentication-as-arm)
- [ACR ABAC repository permissions](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-rbac-abac-repository-permissions)
- [Key Vault references in App Service](https://learn.microsoft.com/en-us/azure/app-service/app-service-key-vault-references)
