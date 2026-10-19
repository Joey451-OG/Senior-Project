<script setup lang="ts">
    import { computed, ref, watch } from 'vue'
    import { useStorage } from '@vueuse/core'
    import { useUserProcs } from '@/composables/useDashboardWS'
    import { killProcess } from '@/composables/usePrivilegedAPI'
    import { type UserProc } from '@/globals/projectTypes'
    import StatusPill from './StatusPill.vue'

    const { data, status } = useUserProcs();

    type MetricKey = 'cpu_percent' | 'memory_percent' | 'disk_percent' | 'io_percent'
    type SortKey = 'pid' | 'name' | MetricKey

    const metrics: { key: MetricKey, label: string }[] = [
        { key: 'cpu_percent', label: 'CPU' },
        { key: 'memory_percent', label: 'Memory' },
        { key: 'disk_percent', label: 'Disk' },
        { key: 'io_percent', label: 'I/O' },
    ]

    const columns: { key: SortKey, label: string, numeric: boolean }[] = [
        { key: 'pid', label: 'PID', numeric: true },
        { key: 'name', label: 'Name', numeric: false },
        ...metrics.map(metric => ({ ...metric, numeric: true })),
    ]

    const sortKey = ref<SortKey>('cpu_percent')
    const sortDescending = ref(true)
    const filter = ref('')
    // Only processes using more than this % of any metric are listed; remembered per browser
    const cutoff = useStorage('waru.processCutoff', 1)
    const collapsedUsers = ref(new Set<string>())

    function setSort(key: SortKey) {
        if (sortKey.value === key) {
            sortDescending.value = !sortDescending.value
        } else {
            sortKey.value = key
            // Numbers are most useful biggest-first, text alphabetical
            sortDescending.value = columns.find(column => column.key === key)?.numeric ?? false
        }
    }

    function toggleUser(username: string) {
        if (collapsedUsers.value.has(username)) collapsedUsers.value.delete(username)
        else collapsedUsers.value.add(username)
    }

    const isAboveCutoff = (proc: UserProc) =>
        metrics.some(metric => (proc[metric.key] ?? 0) > (cutoff.value || 0))

    function matchesFilter(proc: UserProc, needle: string) {
        return !needle
            || proc.name.toLowerCase().includes(needle)
            || proc.username.toLowerCase().includes(needle)
            || String(proc.pid).includes(needle)
    }

    function compareProcs(a: UserProc, b: UserProc) {
        const direction = sortDescending.value ? -1 : 1
        const left = a[sortKey.value] ?? -1
        const right = b[sortKey.value] ?? -1
        if (typeof left === 'number' && typeof right === 'number') return (left - right) * direction
        return String(left).localeCompare(String(right)) * direction
    }

    // Disk/I/O are null for processes the backend can't read, so a total is only shown if any were readable
    function sumMetric(procs: UserProc[], key: MetricKey): number | null {
        const readable = procs.filter(proc => proc[key] !== null)
        return readable.length ? readable.reduce((sum, proc) => sum + (proc[key] ?? 0), 0) : null
    }

    interface UserGroup {
        username: string,
        visible: UserProc[],
        total: number,
        totals: Record<MetricKey, number | null>
    }

    const groups = computed<UserGroup[]>(() => {
        if (!data.value) return []
        const needle = filter.value.trim().toLowerCase()

        const byUser = new Map<string, UserProc[]>()
        for (const proc of data.value) {
            const list = byUser.get(proc.username) ?? []
            list.push(proc)
            byUser.set(proc.username, list)
        }

        const result: UserGroup[] = []
        for (const [username, procs] of byUser) {
            const visible = procs.filter(proc => isAboveCutoff(proc) && matchesFilter(proc, needle))
            if (visible.length === 0) continue

            const totals = Object.fromEntries(
                metrics.map(metric => [metric.key, sumMetric(procs, metric.key)])
            ) as Record<MetricKey, number | null>

            result.push({ username, visible: visible.sort(compareProcs), total: procs.length, totals })
        }

        // Busiest users first by the active metric; alphabetical when sorting by a text/id column
        const key = sortKey.value
        if (key === 'pid' || key === 'name') {
            return result.sort((a, b) => a.username.localeCompare(b.username))
        }
        return result.sort((a, b) => (b.totals[key] ?? -1) - (a.totals[key] ?? -1))
    })

    const shownCount = computed(() => groups.value.reduce((sum, group) => sum + group.visible.length, 0))

    const formatPercent = (value: number | null) => value === null ? '—' : `${value.toFixed(1)}%`

    // Kill requests in flight, and pids already signalled (dimmed until the next packet drops them)
    const pendingKills = ref(new Set<number>())
    const killedPids = ref(new Set<number>())
    const killError = ref<string | null>(null)

    watch(data, (procs) => {
        if (!procs) return
        const livePids = new Set(procs.map(proc => proc.pid))
        for (const pid of killedPids.value) {
            if (!livePids.has(pid)) killedPids.value.delete(pid)
        }
    })

    // The helper refuses root processes anyway; this just avoids offering a button that can't work
    const canKill = (proc: UserProc) => proc.username !== 'root'

    async function requestKill(proc: UserProc) {
        if (!window.confirm(`Kill ${proc.name} (PID ${proc.pid}) owned by ${proc.username}?`)) return
        pendingKills.value.add(proc.pid)
        try {
            await killProcess(proc.pid)
            killedPids.value.add(proc.pid)
            killError.value = null
        } catch (err) {
            killError.value = `Could not kill ${proc.name} (PID ${proc.pid}): ${err instanceof Error ? err.message : err}`
        } finally {
            pendingKills.value.delete(proc.pid)
        }
    }
</script>

<template>
    <section class="card procs-card">
        <header class="card-header">
            <h2 class="card-title">
                Processes
                <span v-if="data" class="count" :title="`${shownCount} of ${data.length} processes shown`">
                    {{ shownCount }} / {{ data.length }}
                </span>
            </h2>
            <div class="controls">
                <label class="cutoff">
                    Above
                    <input v-model.number="cutoff" type="number" min="0" max="100" step="0.1" aria-label="Usage cutoff percent" />
                    %
                </label>
                <input v-model="filter" type="search" placeholder="Filter by name, user or PID" aria-label="Filter processes" />
                <StatusPill :status="status" />
            </div>
        </header>

        <p v-if="killError" class="error">
            {{ killError }}
            <button type="button" class="dismiss" aria-label="Dismiss" @click="killError = null">×</button>
        </p>

        <div v-if="!data" class="placeholder">Waiting for process data…</div>
        <div v-else class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th v-for="column in columns" :key="column.key" :class="{ numeric: column.numeric }">
                            <button type="button" @click="setSort(column.key)" :class="{ active: sortKey === column.key }">
                                {{ column.label }}
                                <span class="arrow">{{ sortKey === column.key ? (sortDescending ? '▾' : '▴') : '' }}</span>
                            </button>
                        </th>
                        <th class="action"><span class="visually-hidden">Kill</span></th>
                    </tr>
                </thead>
                <tbody v-for="group in groups" :key="group.username">
                    <tr class="group-row" @click="toggleUser(group.username)">
                        <td colspan="2">
                            <span class="chevron" :class="{ collapsed: collapsedUsers.has(group.username) }">▾</span>
                            <span class="group-name">{{ group.username }}</span>
                            <span class="muted group-count">{{ group.visible.length }} of {{ group.total }}</span>
                        </td>
                        <td v-for="metric in metrics" :key="metric.key" class="numeric group-total">
                            {{ formatPercent(group.totals[metric.key]) }}
                        </td>
                        <td class="action"></td>
                    </tr>
                    <template v-if="!collapsedUsers.has(group.username)">
                        <tr v-for="proc in group.visible" :key="proc.pid" :class="{ killed: killedPids.has(proc.pid) }">
                            <td class="numeric mono muted">{{ proc.pid }}</td>
                            <td class="name" :title="proc.exe">{{ proc.name }}</td>
                            <td v-for="metric in metrics" :key="metric.key" class="numeric">
                                <div class="bar-cell" :class="{ unreadable: proc[metric.key] === null }"
                                     :title="proc[metric.key] === null ? 'Not readable without elevated privileges' : undefined">
                                    <span class="bar"><span class="fill" :style="{ width: `${Math.min(proc[metric.key] ?? 0, 100)}%` }"></span></span>
                                    {{ formatPercent(proc[metric.key]) }}
                                </div>
                            </td>
                            <td class="action">
                                <button v-if="canKill(proc)" type="button" class="kill"
                                        :disabled="pendingKills.has(proc.pid) || killedPids.has(proc.pid)"
                                        :title="killedPids.has(proc.pid) ? 'SIGTERM sent' : `Kill ${proc.name} (PID ${proc.pid})`"
                                        :aria-label="`Kill ${proc.name} (PID ${proc.pid})`"
                                        @click="requestKill(proc)">
                                    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                                        <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                                    </svg>
                                </button>
                            </td>
                        </tr>
                    </template>
                </tbody>
                <tbody v-if="groups.length === 0">
                    <tr>
                        <td :colspan="columns.length + 1" class="placeholder">No processes above {{ cutoff }}%</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </section>
</template>

<style scoped>
    .procs-card {
        display: flex;
        flex-direction: column;
    }

    .count {
        margin-left: 6px;
        padding: 1px 7px;
        border-radius: 999px;
        background: var(--surface-2);
        font-variant-numeric: tabular-nums;
    }

    .controls {
        display: flex;
        align-items: center;
        gap: 10px;
    }

    .cutoff {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 0.8rem;
        color: var(--text-muted);
        white-space: nowrap;
    }

    input {
        padding: 6px 10px;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface-2);
        color: var(--text);
        font: inherit;
        font-size: 0.85rem;
    }

    input:focus {
        outline: 2px solid color-mix(in srgb, var(--accent) 40%, transparent);
        border-color: var(--accent);
    }

    input[type="search"] {
        width: 220px;
    }

    input[type="number"] {
        width: 64px;
        font-variant-numeric: tabular-nums;
    }

    .table-wrap {
        flex: 1;
        max-height: 560px;
        overflow: auto;
        margin: 0 -20px -18px;
        scrollbar-color: var(--border) transparent;
    }

    table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.875rem;
    }

    th {
        position: sticky;
        top: 0;
        z-index: 1;
        background: var(--surface);
        border-bottom: 1px solid var(--border);
        text-align: left;
        padding: 0;
    }

    th button {
        width: 100%;
        padding: 8px 12px;
        border: none;
        background: none;
        color: var(--text-muted);
        font: inherit;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        text-align: inherit;
        white-space: nowrap;
        cursor: pointer;
    }

    th button.active,
    th button:hover {
        color: var(--text);
    }

    th.numeric button {
        text-align: right;
    }

    .arrow {
        display: inline-block;
        width: 0.8em;
    }

    td {
        padding: 7px 12px;
        border-bottom: 1px solid var(--border);
        white-space: nowrap;
    }

    th:first-child button,
    td:first-child {
        padding-left: 20px;
    }

    th:last-child button,
    td:last-child {
        padding-right: 20px;
    }

    tbody tr:hover {
        background: var(--surface-2);
    }

    .group-row {
        background: var(--surface-2);
        cursor: pointer;
        user-select: none;
    }

    .group-row:hover {
        background: color-mix(in srgb, var(--accent) 8%, var(--surface-2));
    }

    .chevron {
        display: inline-block;
        width: 1em;
        color: var(--text-muted);
        transition: transform 0.15s ease;
    }

    .chevron.collapsed {
        transform: rotate(-90deg);
    }

    .group-name {
        font-weight: 600;
    }

    .group-count {
        margin-left: 8px;
        font-size: 0.8rem;
    }

    .group-total {
        font-weight: 600;
    }

    .numeric {
        text-align: right;
        font-variant-numeric: tabular-nums;
    }

    .name {
        max-width: 260px;
        overflow: hidden;
        text-overflow: ellipsis;
        font-weight: 500;
    }

    .bar-cell {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 8px;
    }

    .bar-cell.unreadable {
        color: var(--text-muted);
    }

    .bar {
        width: 40px;
        height: 4px;
        border-radius: 2px;
        background: var(--track);
        overflow: hidden;
    }

    .error {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin: 0 0 12px;
        padding: 8px 12px;
        border-radius: 8px;
        background: color-mix(in srgb, var(--danger) 12%, transparent);
        color: var(--danger);
        font-size: 0.85rem;
    }

    .dismiss {
        border: none;
        background: none;
        color: inherit;
        font-size: 1.1rem;
        line-height: 1;
        cursor: pointer;
    }

    .action {
        width: 1%;
        text-align: right;
    }

    .kill {
        display: inline-grid;
        place-items: center;
        width: 24px;
        height: 24px;
        padding: 0;
        border: none;
        border-radius: 6px;
        background: none;
        color: var(--danger);
        cursor: pointer;
        opacity: 0.7;
    }

    .kill:hover:not(:disabled),
    .kill:focus-visible {
        opacity: 1;
        background: color-mix(in srgb, var(--danger) 14%, transparent);
    }

    .kill:disabled {
        cursor: default;
        opacity: 0.3;
    }

    tr.killed td:not(.action) {
        opacity: 0.45;
        text-decoration: line-through;
    }

    .visually-hidden {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0 0 0 0);
        white-space: nowrap;
    }

    .fill {
        display: block;
        height: 100%;
        background: var(--accent);
        transition: width 0.4s ease;
    }
</style>
