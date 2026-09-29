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