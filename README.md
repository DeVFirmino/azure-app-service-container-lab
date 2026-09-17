# Pull a private image into App Service with a managed identity

Deploying a container to App Service looks like one step until the first pull fails. Three things have to line up: the registry trusts an identity, the Web App owns that identity, and App Service is told to use it instead of a username and password. Miss one and you get the same blank "Application Error".

This lab builds that path from an empty resource group, then breaks the port on purpose so you can read a real failure in the container log. Run every command yourself; there is no script.

Covers the AI-200 bullet "deploy containers to App Service, including configuring App Service to supply environment variables and secrets". No sidecars: that is a second model, and mixing them is how people set `WEBSITES_PORT` on a sidecar app and wonder why nothing happens.

![The Web App holds a managed identity, that identity has AcrPull on a private container registry, the image is pulled from there into a container listening on 8080, and app settings arrive as environment variables](img/managed-identity-pull.png)

The two arrows on the right do different jobs. `AcrPull` is a grant you make once, scoped to the registry. The pull is the runtime path that uses it, and it only happens because `acrUseManagedIdentityCreds` is set on the Web App, which the drawing cannot show.

## The app

| Endpoint | Returns |
|---|---|
| `GET /` | Name and configured version |
| `GET /health/live` | `{"status": "live"}` |
| `GET /health/ready` | `{"status": "ready"}` |
| `GET /config` | Whether `EXTERNAL_API_KEY` is set, as a boolean |

`/config` never returns the value. Both health endpoints return 200 unconditionally here, so they prove the process answers HTTP and nothing more. Set `APP_VERSION` to the image tag and `/` tells you which image is actually live.

## Before you start

- **A paid subscription.** ACR Tasks is paused for Azure free credits, so `az acr build` will fail there.
- Azure CLI signed in. Checked against 2.90.0.
- Rights to create role assignments. Contributor is not enough; Owner or User Access Administrator is.
- Docker, for the local run only.

## Run it locally

```zsh
docker build -t appservice-config-api:local .
cp .env.example .env
docker run --rm -p 8080:8080 --env-file .env appservice-config-api:local
curl localhost:8080/ && curl localhost:8080/config
```

Expect `"version":"local"` and `true`. Run it again without `--env-file` and both flip to `"unknown"` and `false`. Same image, configuration from outside. That is what the Azure half depends on. `docker run --rm appservice-config-api:local whoami` prints `appuser`.

## The Azure walkthrough

Everything runs on your machine against the Azure control plane, except `az acr build`, which uploads your source and builds it in Azure. One block at a time.

**1. Variables.** Registry and Web App names are globally unique, hence the suffix. Write it down: a new terminal needs this re-run with the same value.

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

**2. Resource group.** One dedicated group makes cleanup a single decision.

```zsh
az group create --name "$LAB_RG" --location "$LAB_LOCATION" --output table
```

**3. Registry, admin off.** The admin user is one shared password for the whole registry and does not say who pulled what. Disabling it forces the identity path.

```zsh
az acr create --resource-group "$LAB_RG" --name "$LAB_ACR" \
  --sku Basic --admin-enabled false --output table

az acr show --resource-group "$LAB_RG" --name "$LAB_ACR" \
  --query "{sku:sku.name, admin:adminUserEnabled}" --output table
```

→ `admin: False`. No password to leak.

**4. Build with ACR Tasks.** From the repo root. No local daemon, no `docker push`.

```zsh
az acr build --registry "$LAB_ACR" --resource-group "$LAB_RG" \
  --image "${LAB_IMAGE}:${LAB_TAG}" --file Dockerfile .

az acr repository show-tags --name "$LAB_ACR" --repository "$LAB_IMAGE" --output table
```

→ the image is in the private registry under a tag you can name.

**5. Linux plan.** B1 is the cheapest dedicated compute; Free and Shared have CPU quotas that make container symptoms hard to trust. **Billing starts here**, not at first request.

```zsh
az appservice plan create --resource-group "$LAB_RG" --name "$LAB_PLAN" \
  --is-linux --sku B1 --output table
```

**6. Web App.** `--container-image-name` is current; `--deployment-container-image-name` warns.

```zsh
az webapp create --resource-group "$LAB_RG" --plan "$LAB_PLAN" --name "$LAB_WEBAPP" \
  --container-image-name "${LAB_ACR}.azurecr.io/${LAB_IMAGE}:${LAB_TAG}" --output table
```

**Expect the site to be broken here** and do not test the URL. You pointed it at a private registry with no way to authenticate. Steps 7 to 10 are the fix.

**7. Identity.**

```zsh
WEBAPP_PRINCIPAL_ID="$(az webapp identity assign --resource-group "$LAB_RG" \
  --name "$LAB_WEBAPP" --query principalId --output tsv)"
```

→ the app has an identity. It has been granted nothing.

**8. AcrPull on the registry.** The scope is the registry, not the group. `AcrPull` reads images and nothing else.

```zsh
ACR_RESOURCE_ID="$(az acr show --resource-group "$LAB_RG" --name "$LAB_ACR" \
  --query id --output tsv)"

az role assignment create --assignee "$WEBAPP_PRINCIPAL_ID" \
  --scope "$ACR_RESOURCE_ID" --role AcrPull --output table
```

If it cannot find the principal, replication has not caught up: wait a minute, or use `--assignee-object-id "$WEBAPP_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal`.

→ the registry will answer a pull from this identity. It does not mean App Service will try.

**9. Tell App Service to use it.** The step people skip, and why "I granted AcrPull and it still fails" is so common. Without it the platform keeps looking for `DOCKER_REGISTRY_SERVER_USERNAME` and `DOCKER_REGISTRY_SERVER_PASSWORD`.

```zsh
az webapp config set --resource-group "$LAB_RG" --name "$LAB_WEBAPP" \
  --generic-configurations '{"acrUseManagedIdentityCreds": true}' --output table
```

App Service pulls also need ARM-scoped Entra tokens, which a registry can reject. New registries accept them, so yours is fine, but `az acr config authentication-as-arm show --registry "$LAB_ACR"` should say `enabled`. Disabled gives an `UNAUTHORIZED` error that looks exactly like a missing role.

**10. Port and settings.**

```zsh
az webapp config appsettings set --resource-group "$LAB_RG" --name "$LAB_WEBAPP" \
  --settings WEBSITES_PORT=8080 APP_VERSION="$LAB_TAG" \
             EXTERNAL_API_KEY="placeholder-not-a-real-key" --output table
```

`WEBSITES_PORT` is not read by your app: it tells the platform where to send its health ping, defaulting to 80, and only one port is allowed. `APP_VERSION` is read by the app. `EXTERNAL_API_KEY` practises the mechanism and protects nothing, so **use a fake value** — it lands in your shell history and in the configuration blade in plain text. Real secrets go in Key Vault.

Settings are injected as environment variables at container start, which is what lets one image run everywhere. Bake the key into the Dockerfile and you have shipped a secret to anyone who can pull.

**11. Logs, then verify.** Changing a setting restarts the app; give it a minute for the first pull.

```zsh
az webapp log config --resource-group "$LAB_RG" --name "$LAB_WEBAPP" \
  --docker-container-logging filesystem --output table
az webapp restart --resource-group "$LAB_RG" --name "$LAB_WEBAPP"

for p in / /health/live /health/ready /config; do
  curl -s "https://${LAB_WEBAPP}.azurewebsites.net$p"; echo
done
```

| Response | Proves | Does not prove |
|---|---|---|
| `/` shows `"version":"v1"` | Pull worked, container started, traffic hit the right port, this is the `v1` image | Anything else about your config |
| `/health/live` 200 | The process answers HTTP | Anything about dependencies |
| `/health/ready` 200 | The same, in this lab | That it would refuse traffic when unhealthy |
| `/config` `true` | The setting arrived as an env var | That the value is correct |

`"version":"unknown"` means the container predates step 10. Restart and recheck. Get all four before moving on.

## Break the port on purpose

Port mismatch is the most common custom container failure and it produces a page that says nothing. Cause it once while you know the answer.

```zsh
az webapp config appsettings set --resource-group "$LAB_RG" --name "$LAB_WEBAPP" \
  --settings WEBSITES_PORT=8000 --output table

az webapp log tail --resource-group "$LAB_RG" --name "$LAB_WEBAPP"
```

The container still listens on 8080, compiled into `CMD`. The platform now probes 8000. From another terminal you get a 503 with no detail, but the log shows uvicorn starting fine on `0.0.0.0:8080` followed by:

```
Container appservice-config-api_0_xxxxxxxx didn't respond to HTTP pings on port: 8000,
failing site start. See container logs for debugging.
```

The app bound a port and the platform gave up waiting on a different one. Nothing crashed. **A clean container log plus a failed site means the problem is between them.** A pull failure looks nothing like this: it produces no application log at all, because there is no container. Whether you have app output is the first fork in the diagnosis.

```zsh
az webapp config appsettings set --resource-group "$LAB_RG" --name "$LAB_WEBAPP" \
  --settings WEBSITES_PORT=8080 --output table
az webapp restart --resource-group "$LAB_RG" --name "$LAB_WEBAPP"
```

Back to 200 proves the port was the only fault, and you can claim that because you changed one thing.

## What it costs

[Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices), checked 17 September 2026, same in North Europe, West Europe and France Central. Order of magnitude, not a quote.

| What | Rate | Billed |
|---|---|---|
| App Service plan B1 Linux | 0.018 USD/hour | Per hour the plan exists, running or not |
| Container Registry Basic | 0.1666 USD/day | Per day, 10 GB included |
| ACR Tasks | 6,000 vCPU-seconds/month free, then 0.0001 USD/second | Per second of build |

The build is effectively free, the registry is cents per day, and the plan is the meter that never stops. An afternoon costs a fraction of a dollar; a forgotten month costs money. Billing lags 8 to 24 hours.

## Cleanup

```zsh
az group delete --name "$LAB_RG" --yes
az group exists --name "$LAB_RG"
```

The delete removes the Web App, plan, registry, image, identity and the registry-scoped role assignment, and is not reversible. `az group exists` prints `false` once it is gone; `true` means it is still running.

## Evidence checklist

Nothing here has been run. This repository is the material; the record is yours.

- [ ] Local build ran, four endpoints answered, `whoami` printed `appuser`
- [ ] `adminUserEnabled: False` on the registry
- [ ] `az acr build` succeeded and the tag is listed
- [ ] Role assignment created with the registry resource ID as scope
- [ ] `acrUseManagedIdentityCreds` applied
- [ ] `/` returned the image tag; `/config` returned `true` with no value
- [ ] Broken-port log showed a clean start and a ping failure on 8000
- [ ] Restoring 8080 returned the site to 200
- [ ] `az group exists` returned `false`

Then answer one question in writing: which identity pulled the image, and how do you know.

## References

- [AI-200 study guide](https://learn.microsoft.com/en-us/credentials/certifications/resources/study-guides/ai-200)
- [Configure a custom container for App Service](https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container?pivots=container-linux)
- [Managed identities in App Service](https://learn.microsoft.com/en-us/azure/app-service/overview-managed-identity)
- [Registry acceptance of Entra authentication scopes](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-disable-authentication-as-arm)
