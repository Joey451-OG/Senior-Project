from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocketClose
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ServerSettings import frontend_directory
from routes.AuthRoute import auth_router
from routes.WebsocketRoute import websocket_router, registerBroadcastJobs
from routes.PrivilegedAPIEndpointRoute import (
    p_api_endpoint_router, registerCronPollJob, registerAdminCommandsPollJob,
    cron_helper_client, process_helper_client, run_commands_helper_client, logoff_helper_client,
)

# One scheduler for the whole app; each router registers its own jobs on it.
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(application: FastAPI):
    registerBroadcastJobs(scheduler)
    registerCronPollJob(scheduler)
    registerAdminCommandsPollJob(scheduler)
    scheduler.start()

    yield

    scheduler.shutdown()
    await cron_helper_client.close()
    await process_helper_client.close()
    await run_commands_helper_client.close()
    await logoff_helper_client.close()


class FrontendFiles(StaticFiles):
    """
    The built Vue app. Vite puts a content hash in the name of every file under assets/, so those can
    be cached for good; everything else (index.html above all) must be revalidated on each load, or a
    browser would keep an old index.html after an upgrade and ask for asset files that no longer exist.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # A WebSocket to a path no /ws route handles: refuse it the way the router refuses unknown
            # paths, instead of letting StaticFiles fail on it (it only understands plain HTTP).
            await WebSocketClose()(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        # 304 included: a revalidated asset must keep its long-lived caching, not be downgraded to no-cache
        isHashedAsset = path.startswith("assets/") and response.status_code in (200, 304)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if isHashedAsset else "no-cache"
        return response


def mountFrontend(application: FastAPI, directory: str) -> None:
    """Serve the built frontend at /. Call it after every router is included: routes are matched in
    order, so /auth, /ws and /papi keep working and only the remaining paths fall through to the files."""
    application.mount("/", FrontendFiles(directory=directory, html=True), name="frontend")


api = FastAPI(lifespan=lifespan)

# No CORS middleware: the browser only ever reaches this API through the page's own origin
# (the reverse proxy when deployed, Vite's dev-server proxy in development), so every
# request is same-origin. Origins are still checked, by the auth router (WARU_ALLOWED_ORIGINS).
api.include_router(auth_router)
api.include_router(websocket_router)
api.include_router(p_api_endpoint_router)

# Installed as a service, the API serves the page too (WARU_FRONTEND_DIR); in development Vite does.
if frontend_directory:
    mountFrontend(api, frontend_directory)
