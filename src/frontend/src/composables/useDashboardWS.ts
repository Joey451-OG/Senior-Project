import { ref } from 'vue'
import { useWebSocket } from '@vueuse/core'
import { type LoadReading, type TemperatureReading } from '@/types/projectTypes'

export function useTemperatureReading() {
    const data = ref<TemperatureReading | null>(null)

    const { status, close, open } = useWebSocket('ws://localhost:8000/ws/cpu-temp', {
        autoReconnect: {
            retries: 5,
            delay: 1000,
            onFailed() {
                console.error('temperature-reading socket: failed to reconnect after 5 attempts')
            },
        },

        onMessage(ws, event) {
            try {
                data.value = JSON.parse(event.data) as TemperatureReading
            } catch (err) {
                console.error('Failed to parse temperature-reading message', err)
            }
        },
    })

    return { data, status, close, open }
}

export function useLoadReading() {
    const data = ref<LoadReading | null>(null)

    const { status, close, open } = useWebSocket('ws://localhost:8000/ws/cpu-load', {
        autoReconnect: {
            retries: 5,
            delay: 1000,
            onFailed() {
                console.error('load-reading socket: failed to reconnect after 5 attempts')
            },
        },

        onMessage(ws, event) {
            try {
                data.value = JSON.parse(event.data) as LoadReading
            } catch (err) {
                console.error('Failed to parse load-reading message', err)
            }
        },
    })

    return { data, status, close, open }    
}