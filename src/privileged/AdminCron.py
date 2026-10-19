"""
Privileged cron helper (stateless request/response).

This process runs with the privileges needed to read and edit other users'
crontabs and does nothing else. It never edits root's crontab (or that of any
other uid-0 account). It keeps no state, runs no background loop, and never talks
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
    snapshot       hash and enabled job count of one user's crontab
                   args: {"username": str}
    list_crontabs  every user that has a crontab, with its hash, enabled job count and jobs
                   args: none
                   result: {"crontabs": [{"username", "content_hash", "enabled_job_count",
                                          "jobs": [{"schedule", "command", "comment", "isEnabled"}]}],
                            "failed_usernames": [str]}
    read_crontab   full text of one user's crontab, for editing
                   args: {"username": str}
                   result: {"username", "content", "content_hash"}
    write_crontab  replace one non-root user's crontab, but only if it still matches
                   expected_hash (the content_hash the editor started from); otherwise
                   fails with "Crontab changed since it was read" and writes nothing.
                   Every line must parse; invalid lines are rejected, not commented out.
                   args: {"username": str, "content": str, "expected_hash": str}
                   result: same as read_crontab, for the newly written crontab

Dependencies:
    pip install python-crontab

Environment variables (all optional):
    CRON_HELPER_SOCKET           Unix socket path (default: /run/waru-dashboard/cron_helper.sock)
    CRON_HELPER_USER_GROUP       group that owns the socket (default: waru)

Access: only processes in the user group (primary or supplementary) may connect.
This is enforced twice: the socket file is 0660 root:<group>, and every connection's
groups are checked again from the kernel's socket credentials (SO_PEERCRED +
SO_PEERGROUPS) before any request is read.

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
import threading
from contextlib import suppress
from typing import Callable, Optional

from crontab import CronTab

logger = logging.getLogger("cron_privileged_helper")

unix_socket_path = os.environ.get("CRON_HELPER_SOCKET", "/run/waru-dashboard/cron_helper.sock")
user_group = os.environ.get("CRON_HELPER_USER_GROUP", "waru")

maximum_concurrent_connections = 8
# write_crontab carries a whole crontab on one line, so leave room beyond it for JSON escaping.
maximum_crontab_bytes = 64 * 1024
maximum_request_line_bytes = 4 * maximum_crontab_bytes
idle_connection_timeout_seconds = 300.0
shutdown_grace_period_seconds = 3.0

# Linux's SO_PEERGROUPS (asm-generic/socket.h); Python's socket module doesn't export it.
so_peergroups_option = getattr(socket, "SO_PEERGROUPS", 59)
# CPython caps getsockopt buffers at 1024 bytes, i.e. up to 256 supplementary groups.
maximum_peer_groups_bytes = 1024

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

    def __init__(self, allowed_group_id: int) -> None:
        self.allowed_group_id = allowed_group_id
        self.active_connection_count = 0

    async def handleClientConnection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            peer_group_ids = getPeerGroupIds(writer)
            isPeerInUserGroup = peer_group_ids is not None and self.allowed_group_id in peer_group_ids
            if not isPeerInUserGroup:
                logger.warning("Rejected connection from peer uid %s: not in group %r", getPeerUserId(writer), user_group)
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


def resolveEditableUsername(requested_username: str) -> str:
    """Like resolveLocalUsername, but refuses root and any other uid-0 account."""
    username = resolveLocalUsername(requested_username)
    if pwd.getpwnam(username).pw_uid == 0:
        raise RequestError("Refusing to edit root's crontab")
    return username


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
    username = resolveLocalUsername(request_payload.get("username"))
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


def getPeerGroupIds(writer: asyncio.StreamWriter) -> Optional[set[int]]:
    """
    Return every gid (primary + supplementary) the peer process held when it connected,
    straight from the kernel (SO_PEERCRED + SO_PEERGROUPS). Reading the socket's
    credentials instead of /proc/<pid> means a recycled pid can't stand in for the peer.
    Returns None when they can't be read, which callers treat as "not allowed".
    """
    client_socket = writer.get_extra_info("socket")
    if client_socket is None:
        return None
    try:
        credentials_bytes = client_socket.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        supplementary_groups_bytes = client_socket.getsockopt(
            socket.SOL_SOCKET, so_peergroups_option, maximum_peer_groups_bytes
        )
    except OSError:
        # Includes ERANGE when the peer has more groups than fit in the buffer.
        return None
    _process_id, _user_id, primary_group_id = struct.unpack("3i", credentials_bytes)
    supplementary_group_count = len(supplementary_groups_bytes) // struct.calcsize("I")
    supplementary_group_ids = struct.unpack(
        f"{supplementary_group_count}I", supplementary_groups_bytes[: supplementary_group_count * struct.calcsize("I")]
    )
    return {primary_group_id, *supplementary_group_ids}


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
    # Look the group up before creating anything, so a missing group fails at startup.
    try:
        gid_user_group = grp.getgrnam(user_group).gr_gid
    except KeyError:
        raise RuntimeError(f"Group {user_group!r} does not exist; create it and add the main API's user") from None

    helper_server = PrivilegedHelperServer(gid_user_group)
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

    # Hand the socket to the group, then lock it to owner (root) + group only.
    # Connecting needs write permission on the socket file, so the kernel itself
    # rejects anyone outside the group. Leaving the uid as -1 keeps the owner unchanged.
    os.chown(unix_socket_path, uid=-1, gid=gid_user_group)
    os.chmod(unix_socket_path, 0o660)
    
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

# Handlers run in worker threads; this keeps two writes from interleaving their
# hash check and write. (An edit made outside the dashboard can still race us.)
crontab_write_lock = threading.Lock()


def readCrontabContent(username: str) -> dict:
    """Blocking read of a user's crontab text. Hashed the same way as snapshot/list_crontabs."""
    rendered_crontab_text = CronTab(user=username).render()
    return {
        "username": username,
        "content": rendered_crontab_text,
        "content_hash": hashlib.sha256(rendered_crontab_text.encode("utf-8")).hexdigest(),
    }


def handleReadCrontabCommand(request_payload: dict) -> dict:
    username = resolveLocalUsername(request_payload.get("username"))
    try:
        return readCrontabContent(username)
    except Exception:
        logger.exception("Failed to read crontab for user %s", username)
        raise RequestError("Failed to read crontab") from None


def parseNewCrontab(new_content) -> CronTab:
    """Validate client-supplied crontab text and parse it, rejecting anything cron wouldn't run."""
    isContentString = type(new_content) == str
    if not isContentString:
        raise RequestError("content must be a string")
    if len(new_content.encode("utf-8")) > maximum_crontab_bytes:
        raise RequestError(f"Crontab is larger than {maximum_crontab_bytes // 1024} KiB")
    if "\0" in new_content:
        raise RequestError("Crontab must not contain NUL characters")

    new_crontab = CronTab(tab=new_content)
    try:
        # errors=True raises on invalid lines instead of quietly commenting them out.
        new_crontab.render(errors=True)
    except ValueError as validation_error:
        raise RequestError(str(validation_error)) from None
    return new_crontab


def handleWriteCrontabCommand(request_payload: dict) -> dict:
    username = resolveEditableUsername(request_payload.get("username"))
    expected_hash = request_payload.get("expected_hash")
    isExpectedHashString = type(expected_hash) == str and expected_hash != ""
    if not isExpectedHashString:
        raise RequestError("expected_hash must be a non-empty string")
    new_crontab = parseNewCrontab(request_payload.get("content"))

    with crontab_write_lock:
        try:
            current_content_hash = readCrontabContent(username)["content_hash"]
        except Exception:
            logger.exception("Failed to read crontab for user %s", username)
            raise RequestError("Failed to read crontab") from None
        hasCrontabChanged = current_content_hash != expected_hash
        if hasCrontabChanged:
            raise RequestError("Crontab changed since it was read")

        try:
            # Installs through the system crontab binary (crontab -u <user> <file>).
            new_crontab.write(user=username, errors=True)
        except Exception:
            logger.exception("Failed to write crontab for user %s", username)
            raise RequestError("Failed to write crontab") from None
        logger.info("Wrote crontab for user %s", username)

        try:
            return readCrontabContent(username)
        except Exception:
            logger.exception("Failed to re-read crontab for user %s", username)
            raise RequestError("Crontab written, but re-reading it failed") from None

# Each handler is a blocking function that takes the request dictionary and returns a result dictionary.
command_handlers: dict[str, Callable[[dict], dict]] = {
    "snapshot": handleSnapshotCommand,
    "list_crontabs": handleListCrontabsCommand,
    "read_crontab": handleReadCrontabCommand,
    "write_crontab": handleWriteCrontabCommand,
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(runHelper())