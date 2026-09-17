import os

from fastapi import FastAPI

APPLICATION_NAME = "appservice-config-api"

app = FastAPI(title=APPLICATION_NAME)


def application_version() -> str:
    return os.getenv("APP_VERSION", "unknown")


@app.get("/")
async def root() -> dict[str, str]:
    return {"application": APPLICATION_NAME, "version": application_version()}


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/config")
async def config() -> dict[str, bool]:
    """Report whether the external API key is configured, never its value."""
    return {"external_api_key_configured": bool(os.getenv("EXTERNAL_API_KEY"))}
