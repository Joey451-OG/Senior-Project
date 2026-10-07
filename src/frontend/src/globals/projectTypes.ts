export interface TemperatureReading { 
    [index: number]: { current: number; high: number; critical: number; }
}

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
    cpu_percent: number,
    memory_percent: number
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
