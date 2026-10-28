<script setup lang="ts">
    import { computed, ref } from 'vue'
    import { useLoggedInUsers } from '@/composables/useDashboardWS'
    import { logoutUser } from '@/composables/usePrivilegedAPI'
    import { type LoggedOffUser, type LogoutNotice } from '@/globals/projectTypes'
    import { formatDateTime, formatShortDateTime } from '@/globals/timeFormat'
    import StatusPill from './StatusPill.vue'
    import UserSessionDialog from './UserSessionDialog.vue'

    const { data, status } = useLoggedInUsers();

    // Longest-logged-in first; the backend already sorts this way, but keep it stable here too
    const users = computed(() => [...(data.value ?? [])].sort((a, b) => a.login_time - b.login_time))

    const plural = (count: number, singular: string, pluralForm = `${singular}s`) =>
        `${count} ${count === 1 ? singular : pluralForm}`

    // The pop-up follows a username, so it keeps working after that user logs out
    const selectedUsername = ref<string | null>(null)
    const selectedUser = computed(() => users.value.find(user => user.username === selectedUsername.value) ?? null)

    // Log-out state is shared by the card's buttons and the pop-up's
    const pendingLogouts = ref(new Set<string>())
    const notice = ref<LogoutNotice | null>(null)

    // The helpers refuse root anyway; this just avoids offering a button that can't work
    const canLogout = (username: string) => username !== 'root'

    function describeLogout(result: LoggedOffUser, force: boolean): LogoutNotice {
        let text = `Logged out ${result.username}: sent SIGTERM to ${plural(result.killed_pids.length, 'process', 'processes')}`
        if (force) text += `, SIGKILL to ${result.force_killed_pids.length} more`

        const stillRunning = force ? result.force_refused.length : result.remaining_pids.length
        if (stillRunning === 0) return { username: result.username, text, kind: 'ok' }

        text += `; ${plural(stillRunning, 'process', 'processes')} still running`
        text += force
            ? ' (root-owned processes are never killed)'
            : '. Force Logout in the user\'s pop-up sends them SIGKILL.'
        return { username: result.username, text, kind: 'warn' }
    }

    async function requestLogout(username: string, force: boolean) {
        const question = force
            ? `Force log out ${username}? Every process of theirs gets SIGTERM, and anything still running after that gets SIGKILL. Unsaved work will be lost.`
            : `Log out ${username}? Every process of theirs will be sent SIGTERM.`
        if (!window.confirm(question)) return

        pendingLogouts.value.add(username)
        try {
            notice.value = describeLogout(await logoutUser(username, force), force)
        } catch (err) {
            notice.value = {
                username,
                text: `Could not log out ${username}: ${err instanceof Error ? err.message : err}`,
                kind: 'error'
            }
        } finally {
            pendingLogouts.value.delete(username)
        }
    }
</script>

<template>
    <section class="card users-card">
        <header class="card-header">
            <h2 class="card-title">
                Logged in
                <span v-if="data" class="count">{{ data.length }}</span>
            </h2>
            <StatusPill :status="status" />
        </header>

        <p v-if="notice" class="notice" :class="notice.kind">
            {{ notice.text }}
            <button type="button" class="dismiss" aria-label="Dismiss" @click="notice = null">×</button>
        </p>

        <div v-if="!data" class="placeholder">Waiting for session data…</div>
        <div v-else-if="users.length === 0" class="placeholder">Nobody is logged in</div>
        <ul v-else class="user-list">
            <li v-for="user in users" :key="user.username" class="user">
                <button type="button" class="user-open" :title="`Show ${user.username}'s processes`"
                        @click="selectedUsername = user.username">
                    <span class="user-line">
                        <span class="avatar" aria-hidden="true">{{ user.username.charAt(0).toUpperCase() }}</span>
                        <span class="username">{{ user.username }}</span>
                        <span v-if="user.shell" class="shell mono" title="Current shell">{{ user.shell }}</span>
                        <span class="duration" :title="`Logged in for ${user.logged_in_for}`">{{ user.logged_in_for }}</span>
                    </span>
                    <span class="details muted">
                        <span :title="formatDateTime(user.login_time)">since {{ formatShortDateTime(user.login_time) }}</span>
                        <span class="separator" aria-hidden="true">·</span>
                        <span>{{ plural(user.session_count, 'session') }}</span>
                        <span class="separator" aria-hidden="true">·</span>
                        <span>{{ plural(user.process_count, 'process', 'processes') }}</span>
                    </span>
                </button>
                <button v-if="canLogout(user.username)" type="button" class="logout"
                        :disabled="pendingLogouts.has(user.username)"
                        :title="`Log out ${user.username}`" :aria-label="`Log out ${user.username}`"
                        @click="requestLogout(user.username, false)">
                    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
                        <path d="M8 1.5v6M4.4 3.8a5.5 5.5 0 1 0 7.2 0" fill="none" stroke="currentColor"
                              stroke-width="1.8" stroke-linecap="round" />
                    </svg>
                </button>
            </li>
        </ul>

        <UserSessionDialog v-if="selectedUsername"
                           :username="selectedUsername"
                           :user="selectedUser"
                           :is-logging-out="pendingLogouts.has(selectedUsername)"
                           :notice="notice?.username === selectedUsername ? notice : null"
                           @close="selectedUsername = null"
                           @logout="force => requestLogout(selectedUsername!, force)"
                           @dismiss-notice="notice = null" />
    </section>
</template>

<style scoped>
    .count {
        margin-left: 6px;
        padding: 1px 7px;
        border-radius: 999px;
        background: var(--surface-2);
        font-variant-numeric: tabular-nums;
    }

    .user-list {
        margin: 0 -20px -18px;
        padding: 0;
        list-style: none;
        max-height: 360px;
        overflow: auto;
        scrollbar-color: var(--border) transparent;
    }

    .user {
        display: flex;
        align-items: center;
        gap: 4px;
        padding-right: 12px;
        border-top: 1px solid var(--border);
    }

    .user:hover {
        background: var(--surface-2);
    }

    .user-open {
        display: block;
        flex: 1;
        min-width: 0;
        padding: 10px 8px 10px 20px;
        border: none;
        background: none;
        color: inherit;
        font: inherit;
        text-align: left;
        cursor: pointer;
    }

    .user-open:focus-visible {
        outline: 2px solid color-mix(in srgb, var(--accent) 60%, transparent);
        outline-offset: -2px;
    }

    .user-line {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
    }

    .avatar {
        display: grid;
        flex: none;
        place-items: center;
        width: 24px;
        height: 24px;
        border-radius: 50%;
        background: color-mix(in srgb, var(--accent) 16%, transparent);
        color: var(--accent);
        font-size: 0.75rem;
        font-weight: 700;
    }

    .username {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-weight: 600;
    }

    .shell {
        flex: none;
        padding: 1px 6px;
        border: 1px solid var(--border);
        border-radius: 6px;
        background: var(--surface-2);
        color: var(--text-muted);
        font-size: 0.72rem;
    }

    .duration {
        flex: none;
        margin-left: auto;
        font-size: 0.85rem;
        font-variant-numeric: tabular-nums;
    }

    .details {
        display: flex;
        flex-wrap: wrap;
        gap: 2px 6px;
        margin: 2px 0 0 32px;
        font-size: 0.8rem;
        font-variant-numeric: tabular-nums;
    }

    .logout {
        display: grid;
        flex: none;
        place-items: center;
        width: 28px;
        height: 28px;
        padding: 0;
        border: none;
        border-radius: 6px;
        background: none;
        color: var(--danger);
        cursor: pointer;
        opacity: 0.7;
    }

    .logout:hover:not(:disabled),
    .logout:focus-visible {
        opacity: 1;
        background: color-mix(in srgb, var(--danger) 14%, transparent);
    }

    .logout:disabled {
        cursor: default;
        opacity: 0.3;
    }
</style>
