"""
Privileged cron helper (stateless request/response).

This process runs with the privileges needed to read other users' crontabs and
does nothing else. It keeps no state, runs no background loop, and never talks
to browsers. Only the unprivileged main API connects to it, over a Unix domain
socket. Polling, version counters, and Server-Sent Events all live in the main
API.

Standard library plus python-crontab only. No web framework.

Protocol: one JSON object per line (newline-terminated), in both directions.
A connection may carry any number of requests; the helper answers each one in
order. The helper closes connections that stay idle too long, so the client
should reconnect when it finds its connection closed.

    request:   {"command": "snapshot", "username": "alice"}
    success:   {"ok": true, "result": {"username": "alice",
                                       "content_hash": "<sha256 hex>",
                                       "enabled_job_count": 3}}
    failure:   {"ok": false, "error": "Unknown user"}

Commands:
    snapshot   hash and enabled job count of one user's crontab

Dependencies:
    pip install python-crontab

Environment variables (all optional):
    CRON_HELPER_SOCKET           Unix socket path (default: /run/dashboard/cron_helper.sock)
    CRON_HELPER_ALLOWED_UID      if set, only this peer uid may connect (checked via SO_PEERCRED,
                                 on top of the socket's 0660 file permissions). Strongly
                                 recommended: set it to the uid of the main API's service user.

Quick test once running:
    echo '{"command": "snapshot", "username": "'$USER'"}' | socat - UNIX-CONNECT:/run/waru-dashboard/cron_helper.sock
"""

import asyncio
import grp
import hashlib
import json
import logging
import os
import pwd
import signal
import socket
import stat
import struct
from contextlib import suppress
from typing import Callable, Optional

from crontab import CronTab

logger = logging.getLogger("cron_privileged_helper")

unix_socket_path = os.environ.get("CRON_HELPER_SOCKET", "/run/waru-dashboard/cron_helper.sock")
allowed_peer_uid_text = os.environ.get("CRON_HELPER_ALLOWED_UID")
user_group = os.environ.get("CRON_HELPER_USER_GROUP", "waru")
allowed_peer_uid = int(allowed_peer_uid_text) if allowed_peer_uid_text is not None else None

maximum_concurrent_connections = 8
maximum_request_line_bytes = 4096
idle_connection_timeout_seconds = 300.0
shutdown_grace_period_seconds = 3.0

crontab_spool_directory_paths = (
    "/var/spool/cron",           # cronie (Fedora/RHEL/Arch): one file per user
    "/var/spool/cron/crontabs",  # Debian/Ubuntu
    "/var/spool/cron/tabs",      # SUSE and BSD-style layouts
)

command_handlers : dict[str, Callable[[dict], dict]] # Instantiated above if name-main

class RequestError(Exception):
    """An error whose message is safe to send back to the client."""

class PrivilegedHelperServer:
    """Accepts connections on the Unix socket and answers one JSON request per line."""

    def __init__(self) -> None:
        self.active_connection_count = 0

    async def handleClientConnection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            if allowed_peer_uid is not None:
                isPeerAllowed = getPeerUserId(writer) == allowed_peer_uid
                if not isPeerAllowed:
                    logger.warning(f"Rejected connection from unexpected peer uid ({getPeerUserId(writer)})")
                    return

            isOverCapacity = self.active_connection_count >= maximum_concurrent_connections
            if isOverCapacity:
                await sendJsonLine(writer, {"ok": False, "error": "Too many connections"})
                return

            self.active_connection_count += 1
            try:
                await self.serveRequests(reader, writer)
            finally:
                self.active_connection_count -= 1
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception:
            logger.exception("Unexpected error while handling a client")
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def serveRequests(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            try:
                request_line_bytes = await asyncio.wait_for(
                    reader.readline(), timeout=idle_connection_timeout_seconds
                )
            except asyncio.TimeoutError:
                return
            except ValueError:
                # Line exceeded the reader's size limit.
                await sendJsonLine(writer, {"ok": False, "error": "Request too large"})
                return

            hasClientDisconnected = request_line_bytes == b""
            if hasClientDisconnected:
                return

            response_dictionary = await buildResponse(request_line_bytes)
            await sendJsonLine(writer, response_dictionary)

def resolveLocalUsername(requested_username: str) -> str:
    """
    Never trust the client-supplied string: it must be a real account in the
    system user database. Returns the canonical account name.
    """
    isUsernameNonEmptyString = type(requested_username) == str and requested_username != ""
    if not isUsernameNonEmptyString:
        raise RequestError("username must be a non-empty string")
    try:
        user_entry = pwd.getpwnam(requested_username)
    except (KeyError, ValueError):
        raise RequestError("Unknown user") from None
    return user_entry.pw_name


def readCrontabSnapshot(username: str) -> dict:
    """
    Blocking read of a user's crontab. Reading another user's crontab requires
    privileges, which is why this lives in the privileged helper.

    Note: the hash is taken over python-crontab's rendered output, so edits
    that only change insignificant whitespace inside a job line may not be
    detected, because python-crontab re-renders parsed jobs.
    """
    crontab_for_user = CronTab(user=username)
    rendered_crontab_text = crontab_for_user.render()
    content_hash = hashlib.sha256(rendered_crontab_text.encode("utf-8")).hexdigest()
    # python-crontab parses commented-out lines that look like schedules as DISABLED jobs,
    # and len() counts them, so count only enabled jobs.
    enabled_job_count = sum(1 for cron_job in crontab_for_user if cron_job.is_enabled())
    return {
        "username": username,
        "content_hash": content_hash,
        "enabled_job_count": enabled_job_count,
    }


def handleSnapshotCommand(request_payload: dict) -> dict:
    username = resolveLocalUsername(request_payload["username"])
    try:
        return readCrontabSnapshot(username)
    except Exception:
        logger.exception("Failed to read crontab for user %s", username)
        raise RequestError("Failed to read crontab") from None


async def buildResponse(request_line_bytes: bytes) -> dict:
    try:
        request_payload = json.loads(request_line_bytes)
    except ValueError:
        return {"ok": False, "error": "Invalid JSON"}
    if not isinstance(request_payload, dict):
        return {"ok": False, "error": "Request must be a JSON object"}

    command_name = request_payload.get("command")
    command_handler = command_handlers.get(command_name) if isinstance(command_name, str) else None
    if command_handler is None:
        return {"ok": False, "error": "Unknown command"}

    try:
        result_dictionary = await asyncio.to_thread(command_handler, request_payload)
    except RequestError as request_error:
        return {"ok": False, "error": str(request_error)}
    except Exception:
        logger.exception("Unexpected error while running command %s", command_name)
        return {"ok": False, "error": "Internal error"}
    return {"ok": True, "result": result_dictionary}


async def sendJsonLine(writer: asyncio.StreamWriter, response_dictionary: dict) -> None:
    writer.write((json.dumps(response_dictionary) + "\n").encode("utf-8"))
    await writer.drain()


def getPeerUserId(writer: asyncio.StreamWriter) -> Optional[int]:
    """Return the uid of the process on the other end of the Unix socket (Linux SO_PEERCRED)."""
    client_socket = writer.get_extra_info("socket")
    if client_socket is None:
        return None
    credentials_bytes = client_socket.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
    )
    _process_id, user_id, _group_id = struct.unpack("3i", credentials_bytes)
    return user_id


def removeStaleSocketFile(socket_path: str) -> None:
    """Remove a leftover socket from a previous run, but never delete anything that isn't a socket."""
    try:
        existing_file_mode = os.stat(socket_path).st_mode
    except FileNotFoundError:
        return
    isExistingFileSocket = stat.S_ISSOCK(existing_file_mode)
    if not isExistingFileSocket:
        raise RuntimeError(f"{socket_path} exists and is not a socket; refusing to remove it")
    os.unlink(socket_path)


async def runHelper() -> None:
    if allowed_peer_uid is None:
        logger.warning("CRON_HELPER_ALLOWED_UID is not set; relying on socket file permissions only")

    helper_server = PrivilegedHelperServer()
    os.makedirs(os.path.dirname(unix_socket_path), exist_ok=True)
    removeStaleSocketFile(unix_socket_path)

    # Create the socket with owner+group access only (0660), then set it explicitly as well.
    # The socket's group must contain the main API's service user and nobody else.
    previous_umask = os.umask(0o117)
    try:
        unix_server = await asyncio.start_unix_server(
            helper_server.handleClientConnection,
            path=unix_socket_path,
            limit=maximum_request_line_bytes,
        )
    finally:
        os.umask(previous_umask)

    gid_user_group = grp.getgrnam(user_group).gr_gid
    os.chmod(unix_socket_path, 0o666)
    
    # the bellow code may not be needed
    # leaving the uid as -1 leaves the permission unchanged
    os.chown(unix_socket_path, uid=-1, gid=gid_user_group)
    
    stop_event = asyncio.Event()
    running_loop = asyncio.get_running_loop()

    def handleStopSignal() -> None:
        hasShutdownAlreadyStarted = stop_event.is_set()
        if hasShutdownAlreadyStarted:
            # Second ^C: stop waiting for anything and leave immediately.
            logger.warning("Second stop signal received; forcing exit")
            logging.shutdown()
            os._exit(1)
        stop_event.set()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        running_loop.add_signal_handler(signal_number, handleStopSignal)

    logger.info("Listening on %s", unix_socket_path)
    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down")
        unix_server.close()
        with suppress(FileNotFoundError):
            os.unlink(unix_socket_path)

        # Cancel open client connections and give them a moment to wind down.
        current_task = asyncio.current_task()
        remaining_tasks = [task for task in asyncio.all_tasks() if task is not current_task]
        for remaining_task in remaining_tasks:
            remaining_task.cancel()
        if remaining_tasks:
            await asyncio.wait(remaining_tasks, timeout=shutdown_grace_period_seconds)

    # Exit directly instead of returning to asyncio.run(), which would wait on any
    # in-flight worker thread. This helper holds no state, and the socket is already removed.
    logger.info("Shutdown complete")
    logging.shutdown()
    os._exit(0) 

def findUsernamesFromCrontabSpool() -> Optional[set[str]]:
    """
    Cheap discovery: accounts that have a file in a known crontab spool directory.
    Returns None when no known spool directory could be read, meaning discovery is
    not possible on this system and the caller should fall back to trying every account.
    """
    spool_filenames: set[str] = set()
    hasReadableSpoolDirectory = False
    for spool_directory_path in crontab_spool_directory_paths:
        try:
            with os.scandir(spool_directory_path) as directory_entries:
                for directory_entry in directory_entries:
                    if directory_entry.is_file(follow_symlinks=False):
                        spool_filenames.add(directory_entry.name)
            hasReadableSpoolDirectory = True
        except OSError:
            continue

    if not hasReadableSpoolDirectory:
        return None

    # Only real accounts count; stray files (editor temp files, placeholders) are dropped.
    usernames_with_spool_files: set[str] = set()
    for spool_filename in spool_filenames:
        try:
            usernames_with_spool_files.add(pwd.getpwnam(spool_filename).pw_name)
        except (KeyError, ValueError):
            continue
    return usernames_with_spool_files


def listCandidateUsernames() -> list[str]:
    usernames_from_spool = findUsernamesFromCrontabSpool()
    if usernames_from_spool is None:
        # Unknown spool layout: fall back to trying every account (one `crontab -l` each).
        return sorted({user_entry.pw_name for user_entry in pwd.getpwall()})
    return sorted(usernames_from_spool)


def readCrontabDetails(username: str) -> Optional[dict]:
    """Blocking full read of one user's crontab, or None if the user has no crontab content."""
    crontab_for_user = CronTab(user=username)
    rendered_crontab_text = crontab_for_user.render()
    hasCrontabContent = rendered_crontab_text.strip() != ""
    if not hasCrontabContent:
        return None

    job_dictionaries = [
        {
            "schedule": str(cron_job.slices),
            "command": cron_job.command,
            "comment": cron_job.comment,
            "isEnabled": cron_job.is_enabled(),
        }
        for cron_job in crontab_for_user
    ]
    return {
        "username": username,
        "content_hash": hashlib.sha256(rendered_crontab_text.encode("utf-8")).hexdigest(),
        "enabled_job_count": sum(1 for job_dictionary in job_dictionaries if job_dictionary["isEnabled"]),
        "jobs": job_dictionaries,
    }


def handleListCrontabsCommand(request_payload: dict) -> dict:
    crontab_dictionaries: list[dict] = []
    failed_usernames: list[str] = []
    for username in listCandidateUsernames():
        try:
            crontab_details = readCrontabDetails(username)
        except Exception:
            logger.exception("Failed to read crontab for user %s", username)
            failed_usernames.append(username)
            continue
        if crontab_details is not None:
            crontab_dictionaries.append(crontab_details)
    return {"crontabs": crontab_dictionaries, "failed_usernames": failed_usernames}

# Each handler is a blocking function that takes the request dictionary and returns a result dictionary.
command_handlers: dict[str, Callable[[dict], dict]] = {
    "snapshot": handleSnapshotCommand,
    "list_crontabs": handleListCrontabsCommand,
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(runHelper())