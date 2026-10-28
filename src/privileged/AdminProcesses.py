"""
Privileged process helper (stateless request/response).

This process runs with the privileges needed to signal other users' processes
and does nothing else: its only job is killing processes by pid. It will never
kill a process owned by root. It keeps no state, runs no background loop, and
never talks to browsers. Only the unprivileged main API connects to it, over a
Unix domain socket.

Standard library only. No web framework.

Protocol: one JSON object per line (newline-terminated), in both directions.
A connection may carry any number of requests; the helper answers each one in
order. The helper closes connections that stay idle too long, so the client
should reconnect when it finds its connection closed.

    request:   {"command": "kill_process", "pid": 12345}
    success:   {"ok": true, "result": {"pid": 12345, "username": "alice", "signal": "SIGTERM"}}
    failure:   {"ok": false, "error": "Refusing to kill a process owned by root"}

Commands:
    kill_process           send SIGTERM to one process
                           args: {"pid": int}
    force_kill_processes   send SIGKILL to each of several processes
                           args: {"pids": [int, ...]}
                           result: {"signal": "SIGKILL", "killed_pids": [...],
                                    "refused": [{"pid": int, "error": str}, ...]}
                           Every pid is validated before any is signalled; a malformed or
                           protected pid fails the whole request. Root-owned or vanished
                           processes are reported per pid in "refused" instead.

Root protection:
    A process is refused if ANY of its real, effective, saved or filesystem uids
    is 0, so setuid-root programs (sudo, passwd, ...) started by normal users are
    protected too. The check and the signal both go through a pidfd, so a pid
    that gets recycled between the two can never redirect the signal onto a
    different (possibly root-owned) process.

Environment variables (all optional):
    PROCESS_HELPER_SOCKET        Unix socket path (default: /run/waru-dashboard/process_helper.sock)
    PROCESS_HELPER_USER_GROUP    group that owns the socket (default: waru)

Access: only processes in the user group (primary or supplementary) may connect.
This is enforced twice: the socket file is 0660 root:<group>, and every connection's
groups are checked again from the kernel's socket credentials (SO_PEERCRED +
SO_PEERGROUPS) before any request is read.

Quick test once running:
    echo '{"command": "kill_process", "pid": 12345}' | socat - UNIX-CONNECT:/run/waru-dashboard/process_helper.sock
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
from contextlib import suppress
from typing import Callable, Optional

logger = logging.getLogger("process_privileged_helper")

unix_socket_path = os.environ.get("PROCESS_HELPER_SOCKET", "/run/waru-dashboard/process_helper.sock")
user_group = os.environ.get("PROCESS_HELPER_USER_GROUP", "waru")

maximum_concurrent_connections = 8
maximum_request_line_bytes = 4096
idle_connection_timeout_seconds = 300.0
shutdown_grace_period_seconds = 3.0

# Linux's SO_PEERGROUPS (asm-generic/socket.h); Python's socket module doesn't export it.
so_peergroups_option = getattr(socket, "SO_PEERGROUPS", 59)
# CPython caps getsockopt buffers at 1024 bytes, i.e. up to 256 supplementary groups.
maximum_peer_groups_bytes = 1024

root_uid = 0
# pid 1 is init and pid 2 is kthreadd; both are root-owned anyway, this is just belt and braces.
protected_pids = {1, 2}
kill_signal = signal.SIGTERM
force_kill_signal = signal.SIGKILL
# A request line is capped at maximum_request_line_bytes anyway; this just keeps the batch bounded.
maximum_force_kill_pids = 512

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

def validateRequestedPid(requested_pid) -> int:
    """
    Never trust the client-supplied value: it must be a plain positive integer
    (bool is a subclass of int in Python, so it is rejected explicitly).
    """
    isPidPositiveInteger = type(requested_pid) == int and requested_pid > 0
    if not isPidPositiveInteger:
        raise RequestError("pid must be a positive integer")
    if requested_pid in protected_pids or requested_pid == os.getpid():
        raise RequestError("Refusing to kill a protected process")
    return requested_pid


def readProcessUserIds(pid: int) -> tuple[int, int, int, int]:
    """Return the (real, effective, saved, filesystem) uids from /proc/<pid>/status."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as status_file:
            for status_line in status_file:
                if status_line.startswith("Uid:"):
                    real_uid, effective_uid, saved_uid, filesystem_uid = (
                        int(uid_text) for uid_text in status_line.split()[1:5]
                    )
                    return real_uid, effective_uid, saved_uid, filesystem_uid
    except (FileNotFoundError, ProcessLookupError):
        raise RequestError("No such process") from None
    raise RequestError("Could not read process owner")


def isProcessAlive(process_fd: int) -> bool:
    try:
        signal.pidfd_send_signal(process_fd, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM still means the process exists (only happens when the helper isn't root).
        return True
    return True


def lookupUsername(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def killProcess(pid: int, signal_to_send: signal.Signals = kill_signal) -> dict:
    """
    Blocking kill of one non-root process (SIGTERM unless signal_to_send says otherwise).

    Order matters for safety against pid reuse:
      1. pin the process with a pidfd,
      2. read its uids from /proc/<pid>,
      3. confirm the pinned process is still alive, which proves the /proc read
         described that same process (a pid can't be reused while its process exists),
      4. signal through the pidfd, never through the raw pid.
    """
    try:
        process_fd = os.pidfd_open(pid)
    except ProcessLookupError:
        raise RequestError("No such process") from None

    try:
        process_user_ids = readProcessUserIds(pid)
        if not isProcessAlive(process_fd):
            raise RequestError("No such process")

        isOwnedByRoot = root_uid in process_user_ids
        if isOwnedByRoot:
            logger.warning("Refused to kill pid %d: owned by root (uids %s)", pid, process_user_ids)
            raise RequestError("Refusing to kill a process owned by root")

        try:
            signal.pidfd_send_signal(process_fd, signal_to_send)
        except ProcessLookupError:
            raise RequestError("No such process") from None
    finally:
        os.close(process_fd)

    real_uid = process_user_ids[0]
    username = lookupUsername(real_uid)
    logger.info("Sent %s to pid %d (owner %s)", signal_to_send.name, pid, username)
    return {"pid": pid, "username": username, "signal": signal_to_send.name}


def handleKillProcessCommand(request_payload: dict) -> dict:
    pid = validateRequestedPid(request_payload.get("pid"))
    try:
        return killProcess(pid)
    except RequestError:
        raise
    except Exception:
        logger.exception("Failed to kill pid %d", pid)
        raise RequestError("Failed to kill process") from None


def validateRequestedPids(requested_pids) -> list[int]:
    """The pid list must be a non-empty JSON array of valid pids; duplicates are dropped, order kept."""
    isPidsNonEmptyList = type(requested_pids) == list and len(requested_pids) > 0
    if not isPidsNonEmptyList:
        raise RequestError("pids must be a non-empty list")
    if len(requested_pids) > maximum_force_kill_pids:
        raise RequestError(f"pids may contain at most {maximum_force_kill_pids} entries")
    return list(dict.fromkeys(validateRequestedPid(requested_pid) for requested_pid in requested_pids))


def handleForceKillProcessesCommand(request_payload: dict) -> dict:
    pids = validateRequestedPids(request_payload.get("pids"))
    killed_process_ids = []
    refused_processes = []
    for pid in pids:
        try:
            killProcess(pid, force_kill_signal)
        except RequestError as request_error:
            refused_processes.append({"pid": pid, "error": str(request_error)})
            continue
        except Exception:
            logger.exception("Failed to force kill pid %d", pid)
            refused_processes.append({"pid": pid, "error": "Failed to kill process"})
            continue
        killed_process_ids.append(pid)
    return {"signal": force_kill_signal.name, "killed_pids": killed_process_ids, "refused": refused_processes}


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
    "kill_process": handleKillProcessCommand,
    "force_kill_processes": handleForceKillProcessesCommand,
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(runHelper())
