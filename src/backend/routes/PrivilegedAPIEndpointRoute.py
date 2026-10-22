"""
Unprivileged front for the privileged helpers:
    - cron helper          (src/privileged/AdminCron.py)
    - process helper       (src/privileged/AdminProcesses.py), which only kills non-root processes
    - run-commands helper  (src/privileged/AdminRunCommands.py), which only runs the commands
                           listed in /etc/waru/sudo_commands

Both helpers are stateless: they only answer commands over their Unix sockets,
using the same one-JSON-line-per-request protocol. For cron, this router owns
everything stateful on the main-API side:
    - the socket client that talks to the helper
    - polling the helper for crontab changes (scheduled by main.py's lifespan)
    - the version counter that bumps whenever any user's crontab changes
    - Server-Sent Events that tell the frontend a change happened

Changes are infrequent, so the frontend listens on /papi/cron/events and
re-fetches /papi/cron when an event arrives, instead of holding a WebSocket.

Admin commands work the same way: the commands file is polled through the helper,
and /papi/commands/events tells the frontend to re-fetch /papi/commands whenever a
command is added, removed or edited (or the file becomes unreadable). Running one
streams its output back as newline-delimited JSON, relayed line for line from the helper.
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
from fastapi.responses import StreamingResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from PROJECTTypes import (
    AdminCommand, AdminCommandListing, AdminCommandsChangeEvent,
    CronChangeEvent, CrontabContent, CrontabListing, CrontabSnapshot, CrontabWriteRequest,
    KilledProcess, UserCrontab,
)

logger = logging.getLogger("privileged_api")

# Must match the helper's CRON_HELPER_SOCKET.
cron_helper_socket_path = os.environ.get("CRON_HELPER_SOCKET", "/run/waru-dashboard/cron_helper.sock")
# Must match the helper's PROCESS_HELPER_SOCKET.
process_helper_socket_path = os.environ.get("PROCESS_HELPER_SOCKET", "/run/waru-dashboard/process_helper.sock")
# Must match the helper's RUN_COMMANDS_HELPER_SOCKET.
run_commands_helper_socket_path = os.environ.get("RUN_COMMANDS_HELPER_SOCKET", "/run/waru-dashboard/run_commands_helper.sock")

# list_crontabs can fall back to `crontab -l` for every account, so give it room.
helper_request_timeout_seconds = 30.0
# The helper drops connections idle for 300s; reconnect before that instead of hitting a dead socket.
helper_reconnect_after_idle_seconds = 240.0
# Responses carry every user's jobs on one line, so allow far more than asyncio's 64 KiB default.
helper_response_line_limit_bytes = 4 * 1024 * 1024

cron_poll_interval_seconds = 5
commands_poll_interval_seconds = 5


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
run_commands_helper_client = PrivilegedHelperClient(run_commands_helper_socket_path)

# Helper error messages that map to something more specific than 400.
helper_error_status_codes = {
    "Unknown user": 404,
    "Refusing to edit root's crontab": 403,
    "Crontab changed since it was read": 409,
    "No such process": 404,
    "Refusing to kill a process owned by root": 403,
    "Refusing to kill a protected process": 403,
    "Unknown command id": 404,
    "Commands file not found": 404,
    "Commands file has unsafe ownership or permissions": 403,
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


# --- Admin commands: change tracking, polling and output streaming ---

class AdminCommandsTracker:
    """
    Bumps a version counter whenever the list of commands in /etc/waru/sudo_commands
    changes (a line added, removed, edited or reordered) or the helper starts or stops
    refusing the file.
    """

    def __init__(self) -> None:
        self.version = 0
        self.last_seen_state: Optional[tuple[tuple[str, ...], Optional[str]]] = None
        # Replaced on every change; SSE streams wait on whichever one is current.
        self.change_signal = asyncio.Event()

    def applyListing(self, commands: list[AdminCommand], error: Optional[str]) -> None:
        new_state = (tuple(command.command_id for command in commands), error)
        hasBaseline = self.last_seen_state is not None
        hasChanged = hasBaseline and new_state != self.last_seen_state
        self.last_seen_state = new_state
        if not hasChanged:
            return

        self.version += 1
        logger.info("Admin commands changed (version %d): %d command(s)%s",
                    self.version, len(commands), f", error: {error}" if error else "")
        previous_change_signal = self.change_signal
        self.change_signal = asyncio.Event()
        previous_change_signal.set()


admin_commands_tracker = AdminCommandsTracker()


async def fetchAdminCommandListing() -> AdminCommandListing:
    """
    A refused commands file (missing, unsafe permissions, ...) is reported inside the
    listing rather than as an HTTP error, so the frontend shows an empty list with the
    reason, and fixing the file is picked up like any other change.
    """
    try:
        result_dictionary = await run_commands_helper_client.sendCommand("list_commands")
        commands = [AdminCommand.model_validate(command) for command in result_dictionary["commands"]]
        error = None
    except HelperCommandError as helper_error:
        commands, error = [], str(helper_error)
    admin_commands_tracker.applyListing(commands, error)
    return AdminCommandListing(version=admin_commands_tracker.version, commands=commands, error=error)


async def pollAdminCommands() -> None:
    try:
        await fetchAdminCommandListing()
    except HelperUnavailableError as error:
        logger.warning("Admin commands poll failed: %s", error)


def registerAdminCommandsPollJob(scheduler: AsyncIOScheduler, poll_interval_seconds: int = commands_poll_interval_seconds):
    scheduler.add_job(pollAdminCommands, "interval", seconds=poll_interval_seconds,
                      id="admin_commands_poll", replace_existing=True)


async def startAdminCommandRun(command_id: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
    """
    Opens a dedicated helper connection (the helper closes it once the command ends,
    so the shared client can't be used) and sends run_command. Returns the connection
    and the helper's "started" line, or raises if the command didn't start.
    """
    try:
        reader, writer = await asyncio.open_unix_connection(
            run_commands_helper_socket_path, limit=helper_response_line_limit_bytes
        )
    except OSError as error:
        raise HelperUnavailableError(f"Cannot connect to {run_commands_helper_socket_path}: {error.strerror}") from None

    try:
        writer.write((json.dumps({"command": "run_command", "command_id": command_id}) + "\n").encode("utf-8"))
        await writer.drain()
        started_line_bytes = await asyncio.wait_for(reader.readline(), timeout=helper_request_timeout_seconds)
        if started_line_bytes == b"":
            raise HelperUnavailableError("Helper closed the connection")
        started_dictionary = json.loads(started_line_bytes)
        if not started_dictionary.get("ok"):
            raise HelperCommandError(started_dictionary.get("error", "Unknown helper error"))
    except BaseException as error:
        writer.close()
        if isinstance(error, (ConnectionError, asyncio.TimeoutError, ValueError)):
            raise HelperUnavailableError(f"Helper connection failed: {error!r}") from None
        raise
    return reader, writer, started_line_bytes


async def relayAdminCommandRun(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, started_line_bytes: bytes
) -> AsyncIterable[bytes]:
    """
    Passes the helper's event lines through unchanged until the helper closes the
    connection. No read timeout: a command may legitimately be silent for minutes,
    and the helper enforces its own time limit. If the browser goes away, the helper
    connection is closed, and the helper lets the command finish on its own.
    """
    try:
        yield started_line_bytes
        while True:
            try:
                event_line_bytes = await reader.readline()
            except (ConnectionError, ValueError) as error:
                logger.warning("Lost the run-commands helper mid-run: %r", error)
                return
            if event_line_bytes == b"":
                return
            yield event_line_bytes
    finally:
        writer.close()


# --- Routes (final paths: /papi/cron, /papi/cron/events, /papi/cron/{username}[/content], /papi/processes/{pid},
#                         /papi/commands, /papi/commands/events, /papi/commands/{command_id}/run) ---

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


@p_api_endpoint_router.get("/commands", response_model=AdminCommandListing)
async def listAdminCommands() -> AdminCommandListing:
    try:
        return await fetchAdminCommandListing()
    except HelperUnavailableError as error:
        raiseHttpErrorForHelperFailure(error)


@p_api_endpoint_router.get("/commands/events", response_class=EventSourceResponse)
async def streamAdminCommandChanges(last_event_id: Optional[str] = Header(default=None)) -> AsyncIterable[ServerSentEvent]:
    """
    Emits a "commands_changed" event (id = version) each time the commands file's
    list of commands changes. Same reconnect behavior as /cron/events.
    """
    try:
        last_seen_version = int(last_event_id) if last_event_id is not None else None
    except ValueError:
        last_seen_version = None

    hasMissedChange = last_seen_version is not None and admin_commands_tracker.version > last_seen_version
    if hasMissedChange:
        version = admin_commands_tracker.version
        yield ServerSentEvent(event="commands_changed", id=str(version), data=AdminCommandsChangeEvent(version=version))

    while True:
        await admin_commands_tracker.change_signal.wait()
        version = admin_commands_tracker.version
        yield ServerSentEvent(event="commands_changed", id=str(version), data=AdminCommandsChangeEvent(version=version))


@p_api_endpoint_router.post("/commands/{command_id}/run")
async def runAdminCommand(command_id: str = Path(pattern="^[0-9a-f]{64}$")) -> StreamingResponse:
    """
    Runs one command from /etc/waru/sudo_commands as root and streams its output as
    newline-delimited JSON, one helper event per line ("started", "output"..., then
    "finished", or {"ok": false, "error"} if it failed mid-run). Errors before the
    command starts are ordinary HTTP errors (404 unknown id, 403 unsafe file, 503
    helper down). POST, not GET, because it has side effects, so EventSource can't
    be used; the frontend reads the response body as a stream instead.
    """
    try:
        reader, writer, started_line_bytes = await startAdminCommandRun(command_id)
    except (HelperUnavailableError, HelperCommandError) as error:
        raiseHttpErrorForHelperFailure(error)
    return StreamingResponse(
        relayAdminCommandRun(reader, writer, started_line_bytes),
        media_type="application/x-ndjson",
        # Stops proxies (e.g. nginx) from buffering the output until the command ends.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
