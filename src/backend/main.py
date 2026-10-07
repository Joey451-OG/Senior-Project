from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
 
from routes.WebsocketRoute import websocket_router, registerBroadcastJobs
from routes.PrivilegedAPIEndpointRoute import p_api_endpoint_router, registerCronPollJob, cron_helper_client
 
# One scheduler for the whole app; each router registers its own jobs on it.
scheduler = AsyncIOScheduler()
 
 
@asynccontextmanager
async def lifespan(application: FastAPI):
    registerBroadcastJobs(scheduler)
    registerCronPollJob(scheduler)
    scheduler.start()
 
    yield
 
    scheduler.shutdown()
    await cron_helper_client.close()
 
 
api = FastAPI(lifespan=lifespan)

# The Vite dev server (port 3001) is a different origin from this API (port 3002),
# so the browser needs CORS headers before fetch/EventSource can read /papi responses.
frontend_origins = [
    "http://130.74.96.14:3001",
    "http://jgsps.cs.olemiss.edu:3001",
    "http://localhost:3001",
]
api.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
api.include_router(websocket_router)
api.include_router(p_api_endpoint_router)  