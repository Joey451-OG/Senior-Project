import { ref } from 'vue'
import { useWebSocket } from '@vueuse/core'
import { 
    type LoadReading, 
    type TemperatureReading,
    type UserProc,
    type UserProcsPacket,
    type LoggedInUser,
    type LoggedInUsersPacket
} from '@/globals/projectTypes'
import { domain, port } from "@/globals/projectVars";

export function useTemperatureReading() {
    const data = ref<TemperatureReading | null>(null)

    const ws_url: string = `ws://${domain}:${[port]}/ws/cpu-temp`

    const { status, close, open } = useWebSocket(ws_url, {
        autoReconnect: {
            retries: 5,
            delay: 1000,
            onFailed() {
                console.error('temperature-reading socket: failed to reconnect after 5 attempts')
            },
        },

        onMessage(ws, event) {
            try {
                if (JSON.parse(event.data).type == 'ping') {
                    // found a ping packet, respond
                    console.log("SENDING PING")
                    ws.send(JSON.stringify('pong'))
                    return
                }

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

    const ws_url: string = `ws://${domain}:${[port]}/ws/cpu-load`

    const { status, close, open } = useWebSocket(ws_url, {
        autoReconnect: {
            retries: 5,
            delay: 1000,
            onFailed() {
                console.error('load-reading socket: failed to reconnect after 5 attempts')
            },
        },

        onMessage(ws, event) {
            try {
                if (JSON.parse(event.data).type == 'ping') {
                    // found a ping packet, respond
                    console.log("SENDING PING")
                    ws.send(JSON.stringify('pong'))
                    return
                }

                data.value = JSON.parse(event.data) as LoadReading
            } catch (err) {
                console.error('Failed to parse load-reading message', err)
            }
        },
    })

    return { data, status, close, open }    
}

export function useUserProcs() {
    const data = ref<UserProc[] | null>(null)
    const ws_url: string = `ws://${domain}:${port}/ws/user-procs`

    const { status, close, open } = useWebSocket(ws_url, {
        autoReconnect: {
            retries: 5,
            delay: 1000,
            onFailed() {
                console.error('user-procs socket: failed to reconnect after 5 attempts')
            },
        },

        onMessage(ws, event) {
            try {
                if (JSON.parse(event.data).type == 'ping') {
                    // found a ping packet, respond
                    console.log("SENDING PING")
                    ws.send(JSON.stringify('pong'))
                    return
                }
                
                const packet = JSON.parse(event.data) as UserProcsPacket
                data.value = packet.data.map(([pid, name, username, exe, cpu_percent, memory_percent, disk_percent, io_percent]) => ({
                    pid,
                    name: name ?? '',
                    username: username ?? '',
                    exe: exe ?? '',
                    cpu_percent: cpu_percent ?? 0,
                    memory_percent: memory_percent ?? 0,
                    disk_percent,
                    io_percent
                }))
            } catch (err) {
                console.error('Failed to parse user-procs message', err)
            }
        },
    })

    return { data, status, close, open }    
}

export function useLoggedInUsers() {
    const data = ref<LoggedInUser[] | null>(null)
    const ws_url: string = `ws://${domain}:${port}/ws/logged-in-users`

    const { status, close, open } = useWebSocket(ws_url, {
        autoReconnect: {
            retries: 5,
            delay: 1000,
            onFailed() {
                console.error('logged-in-users socket: failed to reconnect after 5 attempts')
            },
        },

        onMessage(ws, event) {
            try {
                if (JSON.parse(event.data).type == 'ping') {
                    // found a ping packet, respond
                    console.log("SENDING PING")
                    ws.send(JSON.stringify('pong'))
                    return
                }

                const packet = JSON.parse(event.data) as LoggedInUsersPacket
                data.value = packet.data.map(([username, login_time, logged_in_for, logged_in_seconds, session_count, shell, process_count]) => ({
                    username,
                    login_time,
                    logged_in_for,
                    logged_in_seconds,
                    session_count,
                    shell,
                    process_count
                }))
            } catch (err) {
                console.error('Failed to parse logged-in-users message', err)
            }
        },
    })

    return { data, status, close, open }
}
