"""
Unprivileged front for the privileged helpers:
    - cron helper     (src/privileged/AdminCron.py)
    - process helper  (src/privileged/AdminProcesses.py), which only kills non-root processes

Both helpers are stateless: they only answer commands over their Unix sockets,
using the same one-JSON-line-per-request protocol. For cron, this router owns
everything stateful on the main-API side:
    - the socket client that talks to the helper
    - polling the helper for crontab changes (scheduled by main.py's lifespan)
    - the version counter that bumps whenever any user's crontab changes
    - Server-Sent Events that tell the frontend a change happened

Changes are infrequent, so the frontend listens on /papi/cron/events and
re-fetches /papi/cron when an event arrives, instead of holding a WebSocket.
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterable
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import APIRouter, Header, HTTPException, Path
from fastapi.sse import EventSourceResponse, ServerSentEvent

from PROJECTTypes import (
    CronChangeEvent, CrontabContent, CrontabListing, CrontabSnapshot, CrontabWriteRequest,
    KilledProcess, UserCrontab,
)

logger = logging.getLogger("privileged_api")

# Must match the helper's CRON_HELPER_SOCKET.
cron_helper_socket_path = os.environ.get("CRON_HELPER_SOCKET", "/run/waru-dashboard/cron_helper.sock")
# Must match the helper's PROCESS_HELPER_SOCKET.
process_helper_socket_path = os.environ.get("PROCESS_HELPER_SOCKET", "/run/waru-dashboard/process_helper.sock")

# list_crontabs can fall back to `crontab -l` for every account, so give it room.
helper_request_timeout_seconds = 30.0
# The helper drops connections idle for 300s; reconnect before that instead of hitting a dead socket.
helper_reconnect_after_idle_seconds = 240.0
# Responses carry every user's jobs on one line, so allow far more than asyncio's 64 KiB default.
helper_response_line_limit_bytes = 4 * 1024 * 1024

cron_poll_interval_seconds = 5


# --- Helper client ---

class HelperUnavailableError(Exception):
    """The helper socket could not be reached or the connection broke mid-request."""

class HelperCommandError(Exception):
    """The helper answered {"ok": false, ...}. The message is safe to show clients."""


class PrivilegedHelperClient:
    """
    One persistent connection to the helper. Requests are serialized with a lock
    because the protocol answers one line per request, in order.
    """

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.last_used_monotonic = 0.0
        self.request_lock = asyncio.Lock()

    async def sendCommand(self, command_name: str, is_idempotent: bool = True, **arguments) -> dict:
        """
        Sends one command and returns its "result" dictionary.
        Idempotent commands (reads) are retried once on a fresh connection if the
        old one turned out to be dead. Non-idempotent commands (kills, future edits)
        are not, since the helper may already have applied them.
        """
        request_dictionary = {"command": command_name, **arguments}
        async with self.request_lock:
            try:
                response_dictionary = await self.exchangeRequest(request_dictionary)
            except HelperUnavailableError:
                if not is_idempotent:
                    raise
                response_dictionary = await self.exchangeRequest(request_dictionary)

        if not response_dictionary.get("ok"):
            raise HelperCommandError(response_dictionary.get("error", "Unknown helper error"))
        return response_dictionary["result"]

    async def exchangeRequest(self, request_dictionary: dict) -> dict:
        isConnectionStale = time.monotonic() - self.last_used_monotonic > helper_reconnect_after_idle_seconds
        if self.writer is None or self.writer.is_closing() or isConnectionStale:
            await self.reconnect()

        try:
            self.writer.write((json.dumps(request_dictionary) + "\n").encode("utf-8"))
            await self.writer.drain()
            response_line_bytes = await asyncio.wait_for(
                self.reader.readline(), timeout=helper_request_timeout_seconds
            )
        except (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError) as error:
            await self.close()
            raise HelperUnavailableError(f"Helper connection failed: {error!r}") from None

        hasHelperClosedConnection = response_line_bytes == b""
        if hasHelperClosedConnection:
            await self.close()
            raise HelperUnavailableError("Helper closed the connection")

        self.last_used_monotonic = time.monotonic()
        try:
            return json.loads(response_line_bytes)
        except ValueError:
            await self.close()
            raise HelperUnavailableError("Helper sent invalid JSON") from None

    async def reconnect(self) -> None:
        await self.close()
        try:
            self.reader, self.writer = await asyncio.open_unix_connection(
                self.socket_path, limit=helper_response_line_limit_bytes
            )
        except OSError as error:
            raise HelperUnavailableError(f"Cannot connect to {self.socket_path}: {error.strerror}") from None
        self.last_used_monotonic = time.monotonic()

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        self.reader = None
        self.writer = None


cron_helper_client = PrivilegedHelperClient(cron_helper_socket_path)
process_helper_client = PrivilegedHelperClient(process_helper_socket_path)

# Helper error messages that map to something more specific than 400.
helper_error_status_codes = {
    "Unknown user": 404,
    "Refusing to edit root's crontab": 403,
    "Crontab changed since it was read": 409,
    "No such process": 404,
    "Refusing to kill a process owned by root": 403,
    "Refusing to kill a protected process": 403,
}


def raiseHttpErrorForHelperFailure(error: Exception) -> None:
    if isinstance(error, HelperUnavailableError):
        logger.warning("%s", error)
        raise HTTPException(status_code=503, detail="Privileged helper unavailable") from None
    status_code = helper_error_status_codes.get(str(error), 400)
    raise HTTPException(status_code=status_code, detail=str(error)) from None


# --- Change tracking (version counter + SSE fan-out) ---

class CronChangeTracker:
    """
    Remembers the last seen content hash per user and bumps a version counter
    whenever the set of crontabs or any of their contents changes.
    """

    def __init__(self) -> None:
        self.version = 0
        self.content_hashes_by_username: dict[str, str] = {}
        self.hasBaseline = False
        self.latest_change_event: Optional[CronChangeEvent] = None
        # Replaced on every change; SSE streams wait on whichever one is current.
        self.change_signal = asyncio.Event()

    def applyListing(self, crontabs: list[UserCrontab]) -> None:
        new_content_hashes = {crontab.username: crontab.content_hash for crontab in crontabs}

        if not self.hasBaseline:
            # First successful read just establishes what "unchanged" looks like.
            self.content_hashes_by_username = new_content_hashes
            self.hasBaseline = True
            return

        all_usernames = new_content_hashes.keys() | self.content_hashes_by_username.keys()
        changed_usernames = sorted(
            username for username in all_usernames
            if new_content_hashes.get(username) != self.content_hashes_by_username.get(username)
        )
        if not changed_usernames:
            return

        self.content_hashes_by_username = new_content_hashes
        self.version += 1
        self.latest_change_event = CronChangeEvent(version=self.version, changed_usernames=changed_usernames)
        logger.info("Crontabs changed (version %d): %s", self.version, ", ".join(changed_usernames))

        previous_change_signal = self.change_signal
        self.change_signal = asyncio.Event()
        previous_change_signal.set()


cron_change_tracker = CronChangeTracker()


async def fetchCrontabListing() -> CrontabListing:
    result_dictionary = await cron_helper_client.sendCommand("list_crontabs")
    crontabs = [UserCrontab.model_validate(crontab) for crontab in result_dictionary["crontabs"]]
    # Any fresh read doubles as a poll, so a change seen here is broadcast too.
    cron_change_tracker.applyListing(crontabs)
    return CrontabListing(
        version=cron_change_tracker.version,
        crontabs=crontabs,
        failed_usernames=result_dictionary["failed_usernames"],
    )


# --- Poll job (scheduled by main.py's lifespan) ---

async def pollCrontabs() -> None:
    try:
        await fetchCrontabListing()
    except (HelperUnavailableError, HelperCommandError) as error:
        logger.warning("Cron poll failed: %s", error)


def registerCronPollJob(scheduler: AsyncIOScheduler, poll_interval_seconds: int = cron_poll_interval_seconds):
    scheduler.add_job(pollCrontabs, "interval", seconds=poll_interval_seconds,
                      id="cron_poll", replace_existing=True)


# --- Routes (final paths: /papi/cron, /papi/cron/events, /papi/cron/{username}[/content], /papi/processes/{pid}) ---

p_api_endpoint_router = APIRouter(prefix="/papi", tags=["Privileged Endpoints"])


@p_api_endpoint_router.get("/cron", response_model=CrontabListing)
async def listCrontabs() -> CrontabListing:
    try:
        return await fetchCrontabListing()
    except (HelperUnavailableError, HelperCommandError) as error:
        raiseHttpErrorForHelperFailure(error)


# Declared before /cron/{username} so "events" isn't captured as a username.
@p_api_endpoint_router.get("/cron/events", response_class=EventSourceResponse)
async def streamCronChanges(last_event_id: Optional[str] = Header(default=None)) -> AsyncIterable[ServerSentEvent]:
    """
    Emits a "cron_changed" event (id = version) each time any crontab changes.
    The browser's EventSource resends the last id on reconnect, so a client that
    missed a change while disconnected gets the latest event immediately.
    FastAPI inserts keepalive comments on its own while the stream is idle.
    """
    try:
        last_seen_version = int(last_event_id) if last_event_id is not None else None
    except ValueError:
        last_seen_version = None

    latest_change_event = cron_change_tracker.latest_change_event
    hasMissedChange = (
        last_seen_version is not None
        and latest_change_event is not None
        and latest_change_event.version > last_seen_version
    )
    if hasMissedChange:
        yield ServerSentEvent(event="cron_changed", id=str(latest_change_event.version), data=latest_change_event)

    while True:
        await cron_change_tracker.change_signal.wait()
        latest_change_event = cron_change_tracker.latest_change_event
        yield ServerSentEvent(event="cron_changed", id=str(latest_change_event.version), data=latest_change_event)


@p_api_endpoint_router.get("/cron/{username}", response_model=CrontabSnapshot)
async def getCrontabSnapshot(username: str) -> CrontabSnapshot:
    try:
        result_dictionary = await cron_helper_client.sendCommand("snapshot", username=username)
    except (HelperUnavailableError, HelperCommandError) as error:
        raiseHttpErrorForHelperFailure(error)
    return CrontabSnapshot.model_validate(result_dictionary)


@p_api_endpoint_router.get("/cron/{username}/content", response_model=CrontabContent)
async def getCrontabContent(username: str) -> CrontabContent:
    try:
        result_dictionary = await cron_helper_client.sendCommand("read_crontab", username=username)
    except (HelperUnavailableError, HelperCommandError) as error:
        raiseHttpErrorForHelperFailure(error)
    return CrontabContent.model_validate(result_dictionary)


@p_api_endpoint_router.put("/cron/{username}", response_model=CrontabContent)
async def writeCrontab(username: str, write_request: CrontabWriteRequest) -> CrontabContent:
    """
    Replaces a non-root user's crontab. Returns 409 if it changed since the editor
    read it (expected_hash no longer matches), so the client can ask before overwriting.
    Never retried, since the first attempt may have been written.
    """
    try:
        result_dictionary = await cron_helper_client.sendCommand(
            "write_crontab", is_idempotent=False, username=username,
            content=write_request.content, expected_hash=write_request.expected_hash,
        )
    except (HelperUnavailableError, HelperCommandError) as error:
        raiseHttpErrorForHelperFailure(error)
    # Poll right away so other dashboards get the cron_changed event now, not on the next tick.
    await pollCrontabs()
    return CrontabContent.model_validate(result_dictionary)


@p_api_endpoint_router.delete("/processes/{pid}", response_model=KilledProcess)
async def killProcess(pid: int = Path(gt=0)) -> KilledProcess:
    """
    Sends SIGTERM to one process. The helper refuses root-owned processes (403)
    and unknown pids (404). Never retried, since the first attempt may have landed.
    """
    try:
        result_dictionary = await process_helper_client.sendCommand("kill_process", is_idempotent=False, pid=pid)
    except (HelperUnavailableError, HelperCommandError) as error:
        raiseHttpErrorForHelperFailure(error)
    return KilledProcess.model_validate(result_dictionary)
