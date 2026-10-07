from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
 
from routes.WebsocketRoute import websocket_router, registerBroadcastJobs
 
# One scheduler for the whole app; each router registers its own jobs on it.
scheduler = AsyncIOScheduler()
 
 
@asynccontextmanager
async def lifespan(application: FastAPI):
    registerBroadcastJobs(scheduler)
    scheduler.start()
 
    yield
 
    scheduler.shutdown()
 
 
api = FastAPI(lifespan=lifespan)
api.include_router(websocket_router)  