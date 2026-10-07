import { ref, watch } from 'vue'
import { useEventSource } from '@vueuse/core'
import {
    type CronChangeEvent,
    type CrontabListing,
    type CrontabSnapshot
} from '@/globals/projectTypes'
import { domain, port } from "@/globals/projectVars";

const papi_url: string = `http://${domain}:${port}/papi`

export function useCrontabs() {
    const listing = ref<CrontabListing | null>(null)
    const lastChange = ref<CronChangeEvent | null>(null)
    const error = ref<string | null>(null)

    async function refresh() {
        try {
            const response = await fetch(`${papi_url}/cron`)
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
    const response = await fetch(`${papi_url}/cron/${encodeURIComponent(username)}`)
    if (!response.ok) {
        throw new Error(`GET /papi/cron/${username} failed: ${response.status} ${await response.text()}`)
    }
    return await response.json() as CrontabSnapshot
}
