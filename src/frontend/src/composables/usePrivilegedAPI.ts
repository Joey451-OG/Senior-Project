import { ref, watch } from 'vue'
import { useEventSource } from '@vueuse/core'
import {
    type AdminCommandListing,
    type AdminCommandRunEvent,
    type CronChangeEvent,
    type CrontabContent,
    type CrontabListing,
    type CrontabSnapshot,
    type KilledProcess,
    type LoggedOffUser
} from '@/globals/projectTypes'
import { domain, port } from "@/globals/projectVars";

const papi_url: string = `http://${domain}:${port}/papi`
// Every request carries the session cookie (credentials: 'include'); the API refuses any without it.

// FastAPI errors carry a readable {"detail": "..."} body
async function readErrorDetail(response: Response, fallback: string): Promise<string> {
    const body = await response.json().catch(() => null)
    return typeof body?.detail === 'string' ? body.detail : fallback
}

export function useCrontabs() {
    const listing = ref<CrontabListing | null>(null)
    const lastChange = ref<CronChangeEvent | null>(null)
    const error = ref<string | null>(null)

    async function refresh() {
        try {
            const response = await fetch(`${papi_url}/cron`, { credentials: 'include' })
            if (!response.ok) {
                error.value = `GET /papi/cron failed: ${response.status} ${await response.text()}`
                return
            }
            listing.value = await response.json() as CrontabListing
            error.value = null
        } catch (err) {
            error.value = `GET /papi/cron failed: ${err}`
        }
    }

    // Changes are infrequent, so the backend pushes a "cron_changed" SSE event
    // and the full listing is re-fetched only when one arrives.
    const { data: eventData, status, close, open } = useEventSource(`${papi_url}/cron/events`, ['cron_changed'] as const, {
        // Sends the session cookie, which the API requires
        withCredentials: true,
        autoReconnect: {
            retries: 5,
            delay: 1000,
            onFailed() {
                console.error('cron-events stream: failed to reconnect after 5 attempts')
            },
        },
    })

    watch(eventData, (rawEvent) => {
        if (!rawEvent) return
        try {
            lastChange.value = JSON.parse(rawEvent) as CronChangeEvent
        } catch (err) {
            console.error('Failed to parse cron_changed event', err)
        }
        refresh()
    })

    refresh()

    return { listing, lastChange, error, status, refresh, close, open }
}

export async function fetchCrontabSnapshot(username: string): Promise<CrontabSnapshot> {
    const response = await fetch(`${papi_url}/cron/${encodeURIComponent(username)}`, { credentials: 'include' })
    if (!response.ok) {
        throw new Error(`GET /papi/cron/${username} failed: ${response.status} ${await response.text()}`)
    }
    return await response.json() as CrontabSnapshot
}

// Sends SIGTERM via the privileged process helper, which refuses root-owned processes.
export async function killProcess(pid: number): Promise<KilledProcess> {
    const response = await fetch(`${papi_url}/processes/${pid}`, { credentials: 'include', method: 'DELETE' })
    if (!response.ok) {
        throw new Error(await readErrorDetail(response, `DELETE /papi/processes/${pid} failed: ${response.status}`))
    }
    return await response.json() as KilledProcess
}

// force: also SIGKILL whatever is still running after the SIGTERM pass
export async function logoutUser(username: string, force = false): Promise<LoggedOffUser> {
    const url = `${papi_url}/users/${encodeURIComponent(username)}/logout${force ? '?force=true' : ''}`
    const response = await fetch(url, { credentials: 'include', method: 'POST' })
    if (!response.ok) {
        throw new Error(await readErrorDetail(response, `POST /papi/users/${username}/logout failed: ${response.status}`))
    }
    return await response.json() as LoggedOffUser
}

export async function fetchCrontabContent(username: string): Promise<CrontabContent> {
    const response = await fetch(`${papi_url}/cron/${encodeURIComponent(username)}/content`, { credentials: 'include' })
    if (!response.ok) {
        throw new Error(await readErrorDetail(response, `GET /papi/cron/${username}/content failed: ${response.status}`))
    }
    return await response.json() as CrontabContent
}

// Thrown when the crontab no longer matches expectedHash, i.e. it changed since it was read.
export class CrontabConflictError extends Error {}

export async function saveCrontab(username: string, content: string, expectedHash: string): Promise<CrontabContent> {
    const response = await fetch(`${papi_url}/cron/${encodeURIComponent(username)}`, {
        credentials: 'include',
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, expected_hash: expectedHash }),
    })
    if (response.status === 409) {
        throw new CrontabConflictError(await readErrorDetail(response, 'Crontab changed since it was read'))
    }
    if (!response.ok) {
        throw new Error(await readErrorDetail(response, `PUT /papi/cron/${username} failed: ${response.status}`))
    }
    return await response.json() as CrontabContent
}

export function useAdminCommands() {
    const listing = ref<AdminCommandListing | null>(null)
    const error = ref<string | null>(null)

    async function refresh() {
        try {
            const response = await fetch(`${papi_url}/commands`, { credentials: 'include' })
            if (!response.ok) {
                error.value = await readErrorDetail(response, `GET /papi/commands failed: ${response.status}`)
                return
            }
            listing.value = await response.json() as AdminCommandListing
            error.value = null
        } catch (err) {
            error.value = `GET /papi/commands failed: ${err}`
        }
    }

    // The backend polls /etc/waru/sudo_commands and pushes "commands_changed" when its
    // commands change, so lines added to or removed from the file show up on their own.
    const { data: eventData, status } = useEventSource(`${papi_url}/commands/events`, ['commands_changed'] as const, {
        // Sends the session cookie, which the API requires
        withCredentials: true,
        autoReconnect: {
            retries: 5,
            delay: 1000,
            onFailed() {
                console.error('commands-events stream: failed to reconnect after 5 attempts')
            },
        },
    })

    // The event only says "something changed"; the listing itself is always re-fetched
    watch(eventData, (rawEvent) => {
        if (rawEvent) refresh()
    })

    refresh()

    return { listing, error, status, refresh }
}

/**
 * Runs one command from the commands file and calls onEvent for every event the
 * helper streams back, resolving once the stream ends. Aborting `signal` only stops
 * watching: the command keeps running on the server until it finishes.
 */
export async function runAdminCommand(
    commandId: string,
    onEvent: (event: AdminCommandRunEvent) => void,
    signal?: AbortSignal,
): Promise<void> {
    // POST because it has side effects, which rules out EventSource; the body is read as a stream instead
    const response = await fetch(`${papi_url}/commands/${encodeURIComponent(commandId)}/run`, { credentials: 'include', method: 'POST', signal })
    if (!response.ok || !response.body) {
        throw new Error(await readErrorDetail(response, `POST /papi/commands/${commandId}/run failed: ${response.status}`))
    }

    // Newline-delimited JSON; a network chunk can end mid-line, so keep the tail for the next one
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
    let buffered = ''
    while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffered += value
        const lines = buffered.split('\n')
        buffered = lines.pop() ?? ''
        for (const line of lines) {
            if (line.trim()) onEvent(JSON.parse(line) as AdminCommandRunEvent)
        }
    }
    if (buffered.trim()) onEvent(JSON.parse(buffered) as AdminCommandRunEvent)
}
