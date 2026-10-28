// Every time on the dashboard is shown in 24-hour format, whatever the browser's locale prefers
const clock: Intl.DateTimeFormatOptions = { hourCycle: 'h23' }

// e.g. "Oct 27, 2026, 20:01:22"
export function formatDateTime(epochSeconds: number): string {
    return new Date(epochSeconds * 1000).toLocaleString([], { ...clock, dateStyle: 'medium', timeStyle: 'medium' })
}

// "20:01" for today, otherwise "Oct 22, 17:57"
export function formatShortDateTime(epochSeconds: number): string {
    const date = new Date(epochSeconds * 1000)
    const isToday = date.toDateString() === new Date().toDateString()
    return isToday
        ? date.toLocaleTimeString([], { ...clock, hour: '2-digit', minute: '2-digit' })
        : date.toLocaleString([], { ...clock, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
