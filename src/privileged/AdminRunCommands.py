"""
Privileged command runner helper (stateless request/response).

This process runs as root and does nothing but run the commands an administrator
has listed in /etc/waru/sudo_commands, one command per line. Clients can only pick
a command from that file by its id; they can never supply command text or arguments
of their own. It keeps no state, runs no background loop, and never talks to
browsers. Only the unprivileged main API connects to it, over a Unix domain socket.

Standard library only. No web framework.

Protocol: one JSON object per line (newline-terminated), in both directions.
A connection may carry any number of requests; the helper answers each one in
order. The helper closes connections that stay idle too long, so the client
should reconnect when it finds its connection closed.

    request:   {"command": "list_commands"}
    success:   {"ok": true, "result": {"commands": [{"command_id": "<sha256 hex>", "command": "apt update"}]}}
    failure:   {"ok": false, "error": "Commands file not found"}

Commands:
    list_commands  every command in the commands file
                   args: none
                   result: {"commands": [{"command_id": str, "command": str}]}
    run_command    run one command from the file through /bin/sh, streaming its output
                   args: {"command_id": str}
                   If the command can't be started (unknown id, unsafe file, ...) the
                   answer is a single ordinary failure line and the connection stays
                   usable. Otherwise the connection turns into a one-way output stream
                   for that command and is closed once it ends:

        {"ok": true, "event": "started", "command_id": "...", "command": "apt update"}
        {"ok": true, "event": "output", "stream": "stdout", "data": "Hit:1 http://..."}
        {"ok": true, "event": "output", "stream": "stderr", "data": "W: ..."}
        ...
        {"ok": true, "event": "finished", "result": {"command_id", "command", "exit_code",
                                                     "timed_out", "duration_seconds"}}

                   "data" is raw output as it arrives (chunks, not lines; it may contain
                   \r progress updates). exit_code is negative when the command was killed
                   by a signal (-9 after a timeout), following Python's subprocess convention.
                   If something goes wrong mid-run the stream ends with
                   {"ok": false, "error": ...} instead of "finished".

Streaming rules:
    - The command never gets input: its stdin is /dev/null, and the helper reads
      nothing more from the connection once the command has started. Anything the
      client sends after run_command is ignored, and the connection is closed when
      the command ends, so one connection runs at most one command.
    - If the client disconnects (or stops reading for too long), the command is NOT
      killed; it keeps running to completion with its output discarded, since
      interrupting something like apt halfway is worse than letting it finish.
    - The command is "finished" when /bin/sh exits. Output from anything it left
      running in the background is relayed for a short grace period, then dropped.

Commands file (/etc/waru/sudo_commands):
    - Each non-blank line is one command, run as root with `/bin/sh -c <line>`.
      Lines starting with '#' are comments. Duplicate lines are listed once.
    - A command's id is the sha256 of its line, so ids stay valid when lines are
      added or reordered, and an id stops working as soon as its line is edited
      or removed (the dashboard can never run a command that is no longer listed).
    - The file is re-read on every request, so edits take effect immediately.
    - It must be a regular file (not a symlink) owned by root:root with mode 0644
      (rw-r--r--), inside a root-owned /etc/waru that only root can write to.
      Otherwise the helper refuses to list or run anything.

    sudo install -d -o root -g root -m 0755 /etc/waru
    sudo install -o root -g root -m 0644 /dev/null /etc/waru/sudo_commands

Environment variables (all optional):
    RUN_COMMANDS_HELPER_SOCKET           Unix socket path (default: /run/waru-dashboard/run_commands_helper.sock)
    RUN_COMMANDS_HELPER_USER_GROUP       group that owns the socket (default: waru)
    RUN_COMMANDS_HELPER_TIMEOUT_SECONDS  how long a command may run before it is killed (default: 1800)

Access: only processes in the user group (primary or supplementary) may connect.
This is enforced twice: the socket file is 0660 root:<group>, and every connection's
groups are checked again from the kernel's socket credentials (SO_PEERCRED +
SO_PEERGROUPS) before any request is read.

Quick test once running (-t keeps socat reading until the helper closes the stream):
    echo '{"command": "list_commands"}' | socat - UNIX-CONNECT:/run/waru-dashboard/run_commands_helper.sock
    echo '{"command": "run_command", "command_id": "<id from list_commands>"}' \\
        | socat -t 3600 - UNIX-CONNECT:/run/waru-dashboard/run_commands_helper.sock
"""

import asyncio
import codecs
import grp
import hashlib
import json
import logging
import os
import signal
import socket
import stat
import struct
import time
from contextlib import suppress
from typing import Callable, Optional

logger = logging.getLogger("run_commands_privileged_helper")

unix_socket_path = os.environ.get("RUN_COMMANDS_HELPER_SOCKET", "/run/waru-dashboard/run_commands_helper.sock")
user_group = os.environ.get("RUN_COMMANDS_HELPER_USER_GROUP", "waru")
# Long by default: commands like apt upgrade can legitimately take many minutes.
command_timeout_seconds = float(os.environ.get("RUN_COMMANDS_HELPER_TIMEOUT_SECONDS", "1800"))

maximum_concurrent_connections = 8
maximum_request_line_bytes = 4096
idle_connection_timeout_seconds = 300.0
shutdown_grace_period_seconds = 3.0

# Linux's SO_PEERGROUPS (asm-generic/socket.h); Python's socket module doesn't export it.
so_peergroups_option = getattr(socket, "SO_PEERGROUPS", 59)
# CPython caps getsockopt buffers at 1024 bytes, i.e. up to 256 supplementary groups.
maximum_peer_groups_bytes = 1024

# Deliberately not configurable: the location is part of the trust model.
commands_file_path = "/etc/waru/sudo_commands"
commands_directory_path = os.path.dirname(commands_file_path)
required_commands_file_mode = 0o644
maximum_commands_file_bytes = 64 * 1024

root_uid = 0
root_gid = 0

# Commands get a fixed, minimal environment instead of inheriting the helper's.
command_environment = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    # Commands can never be answered, so tell apt/dpkg not to ask questions.
    "DEBIAN_FRONTEND": "noninteractive",
}
# Each "output" event carries at most this many bytes of output.
output_chunk_bytes = 4096
# A client that hasn't accepted an output event for this long is treated as gone, so a
# stalled client can't back up the command's pipes and freeze it.
client_write_timeout_seconds = 10.0
# How long to keep relaying output after /bin/sh exits, for anything it left in the background.
output_drain_grace_seconds = 2.0
shell_exit_poll_seconds = 0.05

# Process group ids of commands still running, so shutdown can kill them.
# Only touched from the event loop thread, so no lock is needed.
running_process_group_ids: set[int] = set()
# Relays still draining (and discarding) output from background children after their
# command finished. Held here so the tasks aren't garbage collected mid-run.
lingering_relay_tasks: set[asyncio.Task] = set()

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

            request_payload, error_response = parseRequest(request_line_bytes)
            if request_payload is None:
                await sendJsonLine(writer, error_response)
                continue

            isRunCommandRequest = request_payload.get("command") == "run_command"
            if isRunCommandRequest:
                hasCommandStarted = await handleRunCommandRequest(request_payload, writer)
                if hasCommandStarted:
                    # The connection was a one-way output stream for that command; it takes no more requests.
                    return
                continue

            response_dictionary = await buildResponse(request_payload)
            await sendJsonLine(writer, response_dictionary)


# --- Commands file ---

def verifyCommandsDirectory() -> None:
    """/etc/waru must be a real root-owned directory that nobody but root can write to."""
    try:
        directory_status = os.lstat(commands_directory_path)
    except FileNotFoundError:
        raise RequestError("Commands file not found") from None

    isRootOnlyDirectory = (
        stat.S_ISDIR(directory_status.st_mode)
        and directory_status.st_uid == root_uid
        and not directory_status.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    )
    if not isRootOnlyDirectory:
        logger.error("Refusing to use %s: must be a root-owned directory writable only by root", commands_directory_path)
        raise RequestError("Commands file has unsafe ownership or permissions")


def readCommandsFileText() -> str:
    """
    Read the commands file, but only if it is a regular root:root 0644 file.
    The checks are made on the open file descriptor (fstat), so the file that was
    checked is exactly the file that gets read. O_NOFOLLOW refuses a symlink and
    O_NONBLOCK keeps a FIFO planted at the path from hanging the open.
    """
    verifyCommandsDirectory()
    try:
        commands_file_fd = os.open(
            commands_file_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        )
    except FileNotFoundError:
        raise RequestError("Commands file not found") from None
    except OSError as error:
        logger.error("Cannot open %s: %s", commands_file_path, error.strerror)
        raise RequestError("Commands file has unsafe ownership or permissions") from None

    with os.fdopen(commands_file_fd, "rb") as commands_file:
        file_status = os.fstat(commands_file.fileno())
        isSafeCommandsFile = (
            stat.S_ISREG(file_status.st_mode)
            and file_status.st_uid == root_uid
            and file_status.st_gid == root_gid
            and stat.S_IMODE(file_status.st_mode) == required_commands_file_mode
        )
        if not isSafeCommandsFile:
            logger.error(
                "Refusing to use %s: must be a regular file owned by root:root with mode %o "
                "(found uid %d, gid %d, mode %o)",
                commands_file_path, required_commands_file_mode,
                file_status.st_uid, file_status.st_gid, stat.S_IMODE(file_status.st_mode),
            )
            raise RequestError("Commands file has unsafe ownership or permissions")

        commands_file_bytes = commands_file.read(maximum_commands_file_bytes + 1)

    if len(commands_file_bytes) > maximum_commands_file_bytes:
        raise RequestError("Commands file too large")
    try:
        return commands_file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise RequestError("Commands file is not valid UTF-8") from None


def computeCommandId(command_line: str) -> str:
    return hashlib.sha256(command_line.encode("utf-8")).hexdigest()


def loadCommands() -> dict[str, str]:
    """Return {command_id: command_line} for every command in the file, in file order."""
    commands_by_id: dict[str, str] = {}
    for raw_line in readCommandsFileText().splitlines():
        command_line = raw_line.strip()
        isBlankOrComment = command_line == "" or command_line.startswith("#")
        if isBlankOrComment or "\0" in command_line:
            continue
        commands_by_id.setdefault(computeCommandId(command_line), command_line)
    return commands_by_id


# --- Running a command ---

def killProcessGroup(process_group_id: int) -> None:
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process_group_id, signal.SIGKILL)


class CommandOutputStream:
    """
    Sends a running command's events to the client. Once the client is gone (it
    disconnected, or stopped reading for client_write_timeout_seconds), every later
    send is silently dropped, so the command itself is never held up or interrupted.
    """

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.isClientConnected = True
        # Turned off once the command has finished; later output is read but discarded.
        self.isRelayingOutput = True
        # stdout and stderr are relayed concurrently; keep their lines and drains apart.
        self.send_lock = asyncio.Lock()

    async def sendOutput(self, stream_name: str, output_text: str) -> None:
        if self.isRelayingOutput:
            await self.send({"ok": True, "event": "output", "stream": stream_name, "data": output_text})

    async def send(self, event_dictionary: dict) -> None:
        async with self.send_lock:
            if not self.isClientConnected:
                return
            try:
                await asyncio.wait_for(sendJsonLine(self.writer, event_dictionary), timeout=client_write_timeout_seconds)
            except (ConnectionError, asyncio.TimeoutError):
                logger.warning("Client stopped receiving command output; the command keeps running")
                self.isClientConnected = False


async def relayCommandOutput(
    pipe_reader: asyncio.StreamReader, stream_name: str, output_stream: CommandOutputStream
) -> None:
    """Forward one pipe to the client in chunks as they arrive, until the pipe closes."""
    # Incremental, so a multi-byte character split across two reads still decodes cleanly.
    utf8_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        output_bytes = await pipe_reader.read(output_chunk_bytes)
        isPipeClosed = output_bytes == b""
        output_text = utf8_decoder.decode(output_bytes, final=isPipeClosed)
        if output_text:
            await output_stream.sendOutput(stream_name, output_text)
        if isPipeClosed:
            return


async def waitForShellExit(command_process: asyncio.subprocess.Process) -> int:
    """
    Wait for /bin/sh itself to exit. Process.wait() isn't used because it also waits
    for the output pipes to close, which a background child (`daemon &`) can hold open
    long after the shell is gone.
    """
    while command_process.returncode is None:
        await asyncio.sleep(shell_exit_poll_seconds)
    return command_process.returncode


async def streamCommandLine(command_id: str, command_line: str, writer: asyncio.StreamWriter) -> None:
    """
    Run one command as root and stream its output to the client while it runs.
    The command gets its own session (and so its own process group), so a timeout
    kills everything it started, not just the shell.
    """
    output_stream = CommandOutputStream(writer)
    logger.info("Running command %s: %s", command_id[:12], command_line)
    started_monotonic = time.monotonic()
    command_process = await asyncio.create_subprocess_exec(
        "/bin/sh", "-c", command_line,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd="/",
        env=command_environment,
        start_new_session=True,
    )
    # With start_new_session the child's process group id equals its pid.
    process_group_id = command_process.pid
    running_process_group_ids.add(process_group_id)
    # Both are set because stdout and stderr were requested as PIPE.
    assert command_process.stdout is not None and command_process.stderr is not None

    relay_tasks: list[asyncio.Task] = []
    hasCommandEndedNormally = False
    try:
        await output_stream.send({"ok": True, "event": "started", "command_id": command_id, "command": command_line})
        relay_tasks = [
            asyncio.create_task(relayCommandOutput(command_process.stdout, "stdout", output_stream)),
            asyncio.create_task(relayCommandOutput(command_process.stderr, "stderr", output_stream)),
        ]

        hasTimedOut = False
        try:
            exit_code = await asyncio.wait_for(waitForShellExit(command_process), timeout=command_timeout_seconds)
        except asyncio.TimeoutError:
            hasTimedOut = True
            logger.warning("Command %s timed out after %ss; killing it", command_id[:12], command_timeout_seconds)
            killProcessGroup(process_group_id)
            exit_code = await waitForShellExit(command_process)

        # Background children can hold the pipes open after the shell exits; don't wait on them forever.
        await asyncio.wait(relay_tasks, timeout=output_drain_grace_seconds)
        hasCommandEndedNormally = True
    finally:
        if hasCommandEndedNormally:
            # Keep draining whatever background children still write, so they never block on a
            # full pipe, but stop sending it; the relays end when the last writer closes the pipe.
            output_stream.isRelayingOutput = False
            for relay_task in relay_tasks:
                if not relay_task.done():
                    lingering_relay_tasks.add(relay_task)
                    relay_task.add_done_callback(lingering_relay_tasks.discard)
        else:
            for relay_task in relay_tasks:
                relay_task.cancel()
        hasShellStillRunning = command_process.returncode is None
        if hasShellStillRunning:
            # Only reached if this task was cancelled (helper shutdown) or something above failed.
            killProcessGroup(process_group_id)
        running_process_group_ids.discard(process_group_id)

    duration_seconds = round(time.monotonic() - started_monotonic, 3)
    logger.info("Command %s finished with exit code %d in %.3fs", command_id[:12], exit_code, duration_seconds)
    await output_stream.send({
        "ok": True,
        "event": "finished",
        "result": {
            "command_id": command_id,
            "command": command_line,
            "exit_code": exit_code,
            "timed_out": hasTimedOut,
            "duration_seconds": duration_seconds,
        },
    })


# --- Command handlers ---

def handleListCommandsCommand(request_payload: dict) -> dict:
    commands_by_id = loadCommands()
    return {
        "commands": [
            {"command_id": command_id, "command": command_line}
            for command_id, command_line in commands_by_id.items()
        ]
    }


def lookUpRequestedCommand(request_payload: dict) -> tuple[str, str]:
    """Return (command_id, command_line) for a run_command request."""
    command_id = request_payload.get("command_id")
    if not isinstance(command_id, str):
        raise RequestError("command_id must be a string")

    # Re-read the file on every run, so a line removed or edited since the
    # dashboard listed it can no longer be run under its old id.
    command_line = loadCommands().get(command_id)
    if command_line is None:
        raise RequestError("Unknown command id")
    return command_id, command_line


async def handleRunCommandRequest(request_payload: dict, writer: asyncio.StreamWriter) -> bool:
    """
    Answer a run_command request. Returns True once the command has started, after
    which the connection has been used as that command's output stream and must be closed.
    Returns False when it never started; the single error line sent leaves the connection usable.
    """
    try:
        command_id, command_line = await asyncio.to_thread(lookUpRequestedCommand, request_payload)
    except RequestError as request_error:
        await sendJsonLine(writer, {"ok": False, "error": str(request_error)})
        return False

    try:
        await streamCommandLine(command_id, command_line, writer)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to run command %s", command_id[:12])
        with suppress(ConnectionError):
            await sendJsonLine(writer, {"ok": False, "error": "Failed to run command"})
    return True


def parseRequest(request_line_bytes: bytes) -> tuple[Optional[dict], dict]:
    """Return (request_payload, {}), or (None, error_response) when the line isn't a JSON object."""
    try:
        request_payload = json.loads(request_line_bytes)
    except ValueError:
        return None, {"ok": False, "error": "Invalid JSON"}
    if not isinstance(request_payload, dict):
        return None, {"ok": False, "error": "Request must be a JSON object"}
    return request_payload, {}


async def buildResponse(request_payload: dict) -> dict:
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


def killRunningCommands() -> None:
    for process_group_id in list(running_process_group_ids):
        logger.warning("Killing still-running command (process group %d)", process_group_id)
        killProcessGroup(process_group_id)


async def runHelper() -> None:
    # Look the group up before creating anything, so a missing group fails at startup.
    try:
        gid_user_group = grp.getgrnam(user_group).gr_gid
    except KeyError:
        raise RuntimeError(f"Group {user_group!r} does not exist; create it and add the main API's user") from None

    # Not fatal: the file is checked again on every request, so it can be fixed without a restart.
    try:
        logger.info("Loaded %d command(s) from %s", len(loadCommands()), commands_file_path)
    except RequestError as request_error:
        logger.warning("%s: %s", commands_file_path, request_error)

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
            killRunningCommands()
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

        # Commands run in their own sessions, so they would outlive the helper unless killed here.
        killRunningCommands()

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
# run_command isn't here: it streams, so serveRequests sends it to handleRunCommandRequest instead.
command_handlers: dict[str, Callable[[dict], dict]] = {
    "list_commands": handleListCommandsCommand,
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(runHelper())
