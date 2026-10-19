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
