from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
 
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
 
 
api = FastAPI(lifespan=lifespan)

# No CORS middleware: the browser only ever reaches this API through the page's own origin
# (the reverse proxy when deployed, Vite's dev-server proxy in development), so every
# request is same-origin. Origins are still checked, by the auth router (WARU_ALLOWED_ORIGINS).
api.include_router(auth_router)
api.include_router(websocket_router)
api.include_router(p_api_endpoint_router)  