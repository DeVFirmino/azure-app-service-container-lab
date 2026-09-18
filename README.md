# Pull a private image into App Service with a managed identity

Deploying a container to App Service looks like one step until the first pull fails. Three things have to line up: the registry trusts an identity, the Web App owns that identity, and App Service is told to use it instead of a username and password. Miss one and you get the same blank "Application Error".

This lab builds that path from an empty resource group, then breaks the port on purpose so you can read a real failure in the container log.

![The Web App's managed identity holds AcrPull on the private registry, so App Service can pull the image; WEBSITES_PORT tells the platform which container port to reach, while app settings arrive as environment variables](img/managed-identity-pull.png)

## The app

A four-endpoint FastAPI service, small on purpose, so that when something breaks you know it is the platform.

| Endpoint | Returns |
|---|---|
| `GET /` | Name and configured version |
| `GET /health/live` | `{"status": "live"}` |
| `GET /health/ready` | `{"status": "ready"}` |
| `GET /config` | Whether `EXTERNAL_API_KEY` is set, as a boolean |

`/config` never returns the value, so you can show a secret arrived without putting it in a screenshot. Both health endpoints return 200 unconditionally here, so they prove the process answers HTTP and nothing more.

## Run it locally

```zsh
docker build -t appservice-config-api:local .
cp .env.example .env
docker run --rm -p 8080:8080 --env-file .env appservice-config-api:local
curl localhost:8080/ && curl localhost:8080/config
```

Expect `"version":"local"` and `true`. Run it again without `--env-file` and both flip to `"unknown"` and `false`. Same image, configuration from outside. That is what the Azure half depends on.

`docker run --rm appservice-config-api:local whoami` prints `appuser`.

## Run it on Azure

**→ [Follow the Azure walkthrough](WALKTHROUGH.md)**

Eleven steps: registry, ACR Tasks build, Linux plan, Web App, managed identity, `AcrPull`, the site setting people forget, port and app settings, then a deliberate port failure to diagnose. It creates billable resources, so finish the cleanup step in the same session.

AI-200 practice for deploying a custom container to App Service and supplying configuration through app settings. Classic single-container model throughout, no sidecars.

## What you need

- **A paid subscription.** ACR Tasks is paused for Azure free credits, so `az acr build` fails there.
- Azure CLI signed in. Checked against 2.90.0 on 17 September 2026.
- Rights to create role assignments. Contributor is not enough; Owner or User Access Administrator is.
- Docker, for the local run only.

## References

- [AI-200 study guide](https://learn.microsoft.com/en-us/credentials/certifications/resources/study-guides/ai-200)
- [Configure a custom container for App Service](https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container?pivots=container-linux)
- [Managed identities in App Service](https://learn.microsoft.com/en-us/azure/app-service/overview-managed-identity)
