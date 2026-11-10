// Backend sends {sensor label: [current, high, critical]} (a NamedTuple serialized as an array)
export type TemperatureSensor = [current: number | null, high: number | null, critical: number | null]
export type TemperatureReading = Record<string, TemperatureSensor>

export interface UserSession {
    name: string | null,
    terminal: string | null,
    host: string | null,
    started: number | null,
    pid: number | null
}

export interface UserProc {
    pid: number,
    name: string,
    username: string,
    exe: string,
    // Shares of the whole machine; disk/io are null when the backend can't read the process's io counters
    cpu_percent: number,
    memory_percent: number,
    disk_percent: number | null,
    io_percent: number | null
}

// Wire format of /ws/user-procs: each process is a UserProcesses NamedTuple serialized as an array
export type UserProcTuple = [
    pid: number,
    name: string | null,
    username: string | null,
    exe: string | null,
    cpu_percent: number | null,
    memory_percent: number | null,
    disk_percent: number | null,
    io_percent: number | null
]

export interface UserProcsPacket {
    name: string,
    data: UserProcTuple[]
}

export interface LoggedInUser {
    username: string,
    login_time: number,         // epoch seconds of the user's earliest current session
    logged_in_for: string,      // formatted by the backend, e.g. "2d 3h 14m"
    logged_in_seconds: number,
    session_count: number,
    shell: string | null,
    process_count: number
}

// Wire format of /ws/logged-in-users: each user is a LoggedInUser NamedTuple serialized as an array
export type LoggedInUserTuple = [
    username: string,
    login_time: number,
    logged_in_for: string,
    logged_in_seconds: number,
    session_count: number,
    shell: string | null,
    process_count: number
]

export interface LoggedInUsersPacket {
    name: string,
    data: LoggedInUserTuple[]
}

export interface LoadReading {
    type: string | null,
    value: number | null
}

export interface CronJobEntry {
    schedule: string,
    command: string,
    comment: string,
    is_enabled: boolean
}

export interface CrontabSnapshot {
    username: string,
    content_hash: string,
    enabled_job_count: number
}

export interface UserCrontab extends CrontabSnapshot {
    jobs: CronJobEntry[]
}

export interface CrontabListing {
    version: number,
    crontabs: UserCrontab[],
    failed_usernames: string[]
}

export interface CronChangeEvent {
    version: number,
    changed_usernames: string[]
}

export interface CrontabContent {
    username: string,
    content: string,
    content_hash: string
}

export interface KilledProcess {
    pid: number,
    username: string,
    signal: string
}

export interface RefusedProcess {
    pid: number,
    error: string
}

export interface LoggedOffUser {
    // From the SIGTERM pass
    username: string,
    uid: number,
    killed_pids: number[],
    refused: RefusedProcess[],
    remaining_pids: number[],   // still alive shortly after the SIGTERM pass
    // Force logout only: SIGKILL sent to remaining_pids
    force_killed_pids: number[],
    force_refused: RefusedProcess[]
}

// Result banner shown after a log-out attempt, on the Logged in card and in that user's pop-up
export interface LogoutNotice {
    username: string,
    text: string,
    kind: 'ok' | 'warn' | 'error'
}


// --- Admin commands (lines of /etc/waru/sudo_commands, run as root by the privileged helper) ---

export interface AdminCommand {
    command_id: string,
    command: string
}

export interface AdminCommandListing {
    version: number,
    commands: AdminCommand[],
    // Why the commands file was refused (missing, unsafe permissions, ...); commands is then empty
    error: string | null
}

export interface AdminCommandsChangeEvent {
    version: number
}

export interface AdminCommandResult {
    command_id: string,
    command: string,
    // Negative when killed by a signal (-9 after a timeout)
    exit_code: number,
    timed_out: boolean,
    duration_seconds: number
}

// One line of the newline-delimited JSON streamed by POST /papi/commands/{id}/run
export type AdminCommandRunEvent =
    | { ok: true, event: 'started', command_id: string, command: string }
    | { ok: true, event: 'output', stream: 'stdout' | 'stderr', data: string }
    | { ok: true, event: 'finished', result: AdminCommandResult }
    | { ok: false, error: string }


// --- Authentication (Firebase sign-in, authorized by /etc/waru/auth-accounts) ---

export interface AuthStatus {
    email: string,
    uid: string,
    // False for signed-in accounts that aren't listed in /etc/waru/auth-accounts
    is_administrator: boolean
}
