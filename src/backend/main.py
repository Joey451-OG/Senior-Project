from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
 
from ServerSettings import frontend_origins
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

# frontend_origins (ServerSettings.py) are the Vite dev server's origins. Credentials are
# allowed so the browser sends the session cookie with fetch/EventSource requests.
api.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
api.include_router(auth_router)
api.include_router(websocket_router)
api.include_router(p_api_endpoint_router)  