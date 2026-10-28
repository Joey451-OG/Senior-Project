"""
Privileged log-off helper (stateless request/response).

This process runs with the privileges needed to signal other users' processes
and does nothing else: its only job is logging a user off by ending every one
of their processes. Each process is ended through AdminProcesses.killProcess,
so it gets the same pidfd-based kill and the same root protection as the
process helper. It will never log off root. It keeps no state, runs no
background loop, and never talks to browsers. Only the unprivileged main API
connects to it, over a Unix domain socket.

Standard library only. No web framework.

Protocol: one JSON object per line (newline-terminated), in both directions.
A connection may carry any number of requests; the helper answers each one in
order. The helper closes connections that stay idle too long, so the client
should reconnect when it finds its connection closed.

    request:   {"command": "logoff_user", "username": "alice"}
    success:   {"ok": true, "result": {"username": "alice", "uid": 1001, "signal": "SIGTERM",
                                       "killed_pids": [2210, 2215], "refused": [{"pid": 2301, "error": "..."}],
                                       "remaining_pids": []}}
    failure:   {"ok": false, "error": "Refusing to log off root"}

Commands:
    logoff_user    send SIGTERM to every process whose real uid is the user's
                   args: {"username": str}
                   killed_pids     processes that were signalled
                   refused         processes AdminProcesses refused (e.g. a setuid-root
                                   program the user started, such as sudo)
                   remaining_pids  the user's processes still alive shortly after the sweep
                                   (e.g. an interactive shell that ignores SIGTERM)

Root protection:
    Any account with uid 0 is refused outright. On top of that, every process
    goes through AdminProcesses.killProcess, which refuses a process if ANY of
    its real, effective, saved or filesystem uids is 0.

Environment variables (all optional):
    LOGOFF_HELPER_SOCKET        Unix socket path (default: /run/waru-dashboard/logoff_helper.sock)
    LOGOFF_HELPER_USER_GROUP    group that owns the socket (default: waru)

Access: only processes in the user group (primary or supplementary) may connect.
This is enforced twice: the socket file is 0660 root:<group>, and every connection's
groups are checked again from the kernel's socket credentials (SO_PEERCRED +
SO_PEERGROUPS) before any request is read.

Quick test once running:
    echo '{"command": "logoff_user", "username": "alice"}' | socat - UNIX-CONNECT:/run/waru-dashboard/logoff_helper.sock
"""

import asyncio
import grp
import json
import logging
import os
import pwd
import signal
import socket
import stat
import struct
import time
from contextlib import suppress
from typing import Callable, Optional

# AdminProcesses sits next to this file, which is on sys.path when this helper is run as a script.
# Its RequestError is reused so errors raised inside killProcess are reported to the client as-is.
from AdminProcesses import RequestError, killProcess, kill_signal, validateRequestedPid

logger = logging.getLogger("logoff_privileged_helper")

unix_socket_path = os.environ.get("LOGOFF_HELPER_SOCKET", "/run/waru-dashboard/logoff_helper.sock")
user_group = os.environ.get("LOGOFF_HELPER_USER_GROUP", "waru")

maximum_concurrent_connections = 8
maximum_request_line_bytes = 4096
idle_connection_timeout_seconds = 300.0
shutdown_grace_period_seconds = 3.0
# How long to give processes to exit after SIGTERM before reporting who is still alive.
logoff_settle_seconds = 1.0

# Linux's SO_PEERGROUPS (asm-generic/socket.h); Python's socket module doesn't export it.
so_peergroups_option = getattr(socket, "SO_PEERGROUPS", 59)
# CPython caps getsockopt buffers at 1024 bytes, i.e. up to 256 supplementary groups.
maximum_peer_groups_bytes = 1024

root_uid = 0

command_handlers : dict[str, Callable[[dict], dict]] # Instantiated above if name-main

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

def resolveLogoffUser(requested_username) -> pwd.struct_passwd:
    """
    Never trust the client-supplied string: it must be a real account in the
    system user database, and never root or any other uid-0 account.
    """
    isUsernameNonEmptyString = type(requested_username) == str and requested_username != ""
    if not isUsernameNonEmptyString:
        raise RequestError("username must be a non-empty string")
    try:
        user_entry = pwd.getpwnam(requested_username)
    except (KeyError, ValueError):
        raise RequestError("Unknown user") from None
    if user_entry.pw_uid == root_uid:
        logger.warning("Refused to log off %s: uid 0", user_entry.pw_name)
        raise RequestError("Refusing to log off root")
    return user_entry


def readProcessRealUserId(pid: int) -> Optional[int]:
    """Return the real uid from /proc/<pid>/status, or None if the process is gone."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as status_file:
            for status_line in status_file:
                if status_line.startswith("Uid:"):
                    return int(status_line.split()[1])
    except (FileNotFoundError, ProcessLookupError):
        return None
    return None


def listUserProcessIds(uid: int) -> list[int]:
    """Every pid whose real uid is uid. This is only a candidate list; killProcess re-checks each one."""
    user_process_ids = []
    for proc_entry_name in os.listdir("/proc"):
        if not proc_entry_name.isdigit():
            continue
        pid = int(proc_entry_name)
        if readProcessRealUserId(pid) == uid:
            user_process_ids.append(pid)
    return sorted(user_process_ids)


def logoffUser(user_entry: pwd.struct_passwd) -> dict:
    """
    Blocking log-off: SIGTERM every process the user owns through AdminProcesses,
    then wait briefly and report which of their processes are still alive.
    """
    killed_process_ids = []
    refused_processes = []
    for pid in listUserProcessIds(user_entry.pw_uid):
        try:
            killProcess(validateRequestedPid(pid))
        except RequestError as request_error:
            # The process exiting on its own between listing and killing is not a refusal.
            if str(request_error) != "No such process":
                refused_processes.append({"pid": pid, "error": str(request_error)})
            continue
        except Exception:
            logger.exception("Failed to kill pid %d while logging off %s", pid, user_entry.pw_name)
            refused_processes.append({"pid": pid, "error": "Failed to kill process"})
            continue
        killed_process_ids.append(pid)

    time.sleep(logoff_settle_seconds)
    remaining_process_ids = listUserProcessIds(user_entry.pw_uid)

    logger.info(
        "Logged off %s: signalled %d processes, %d refused, %d still running",
        user_entry.pw_name, len(killed_process_ids), len(refused_processes), len(remaining_process_ids),
    )
    return {
        "username": user_entry.pw_name,
        "uid": user_entry.pw_uid,
        "signal": kill_signal.name,
        "killed_pids": killed_process_ids,
        "refused": refused_processes,
        "remaining_pids": remaining_process_ids,
    }


def handleLogoffUserCommand(request_payload: dict) -> dict:
    user_entry = resolveLogoffUser(request_payload.get("username"))
    try:
        return logoffUser(user_entry)
    except RequestError:
        raise
    except Exception:
        logger.exception("Failed to log off user %s", user_entry.pw_name)
        raise RequestError("Failed to log off user") from None


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

# Each handler is a blocking function that takes the request dictionary and returns a result dictionary.
command_handlers: dict[str, Callable[[dict], dict]] = {
    "logoff_user": handleLogoffUserCommand,
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(runHelper())
