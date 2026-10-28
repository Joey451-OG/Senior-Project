<script setup lang="ts">
    import { onMounted, ref } from 'vue'
    import { type LoggedInUser, type LogoutNotice } from '@/globals/projectTypes'
    import { formatDateTime } from '@/globals/timeFormat'
    import UserProcs from './UserProc.vue'

    // user is null once they're no longer logged in; the process list keeps following username
    const props = defineProps<{
        username: string,
        user: LoggedInUser | null,
        isLoggingOut: boolean,
        notice: LogoutNotice | null
    }>()

    const emit = defineEmits<{
        close: [],
        logout: [force: boolean],
        dismissNotice: []
    }>()

    const dialogElement = ref<HTMLDialogElement | null>(null)

    onMounted(() => dialogElement.value?.showModal())

    // Clicks on the dialog element itself (not its contents) land on the backdrop
    function closeOnBackdrop(event: MouseEvent) {
        if (event.target === dialogElement.value) emit('close')
    }

    // The helpers refuse root anyway; this just avoids offering buttons that can't work
    const canLogout = props.username !== 'root'
</script>

<template>
    <dialog ref="dialogElement" class="session-dialog" :aria-label="`Processes for ${username}`"
            @cancel.prevent="emit('close')" @click="closeOnBackdrop">
        <header class="dialog-header">
            <div class="identity">
                <span class="avatar" aria-hidden="true">{{ username.charAt(0).toUpperCase() }}</span>
                <div>
                    <h2>
                        {{ username }}
                        <span v-if="user?.shell" class="shell mono" title="Current shell">{{ user.shell }}</span>
                    </h2>
                    <p class="muted summary">
                        <template v-if="user">
                            Logged in for {{ user.logged_in_for }}
                            · since {{ formatDateTime(user.login_time) }}
                        </template>
                        <template v-else>No longer logged in</template>
                    </p>
                </div>
            </div>
            <div class="actions">
                <template v-if="canLogout">
                    <button type="button" class="action-button" :disabled="isLoggingOut"
                            title="Send SIGTERM to all of this user's processes"
                            @click="emit('logout', false)">
                        Log out
                    </button>
                    <button type="button" class="action-button force" :disabled="isLoggingOut"
                            title="Log out, then send SIGKILL to any process that survived SIGTERM"
                            @click="emit('logout', true)">
                        Force Logout
                    </button>
                </template>
                <button type="button" class="close" aria-label="Close" @click="emit('close')">×</button>
            </div>
        </header>

        <p v-if="notice" class="notice" :class="notice.kind">
            {{ notice.text }}
            <button type="button" class="dismiss" aria-label="Dismiss" @click="emit('dismissNotice')">×</button>
        </p>

        <UserProcs :username="username" />
    </dialog>
</template>

<style scoped>
    .session-dialog {
        width: min(1100px, calc(100vw - 32px));
        max-height: calc(100vh - 48px);
        padding: 20px;
        border: 1px solid var(--border);
        border-radius: var(--radius);
        background: var(--bg);
        color: var(--text);
        box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
    }

    .session-dialog::backdrop {
        background: rgba(10, 12, 16, 0.55);
        backdrop-filter: blur(2px);
    }

    .dialog-header {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 16px;
    }

    .identity {
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;
    }

    .avatar {
        display: grid;
        flex: none;
        place-items: center;
        width: 38px;
        height: 38px;
        border-radius: 50%;
        background: color-mix(in srgb, var(--accent) 16%, transparent);
        color: var(--accent);
        font-weight: 700;
    }

    h2 {
        display: flex;
        align-items: center;
        gap: 8px;
        margin: 0;
        font-size: 1.1rem;
    }

    .summary {
        margin: 2px 0 0;
        font-size: 0.85rem;
    }

    .shell {
        padding: 1px 6px;
        border: 1px solid var(--border);
        border-radius: 6px;
        background: var(--surface-2);
        color: var(--text-muted);
        font-size: 0.72rem;
        font-weight: 400;
    }

    .actions {
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .action-button {
        padding: 6px 12px;
        border: 1px solid var(--danger);
        border-radius: 8px;
        background: none;
        color: var(--danger);
        font: inherit;
        font-size: 0.85rem;
        font-weight: 600;
        cursor: pointer;
    }

    .action-button:hover:not(:disabled) {
        background: color-mix(in srgb, var(--danger) 12%, transparent);
    }

    .action-button.force {
        background: var(--danger);
        color: #fff;
    }

    .action-button.force:hover:not(:disabled) {
        background: color-mix(in srgb, var(--danger) 85%, #000);
    }

    .action-button:disabled {
        cursor: default;
        opacity: 0.5;
    }

    .close {
        width: 32px;
        height: 32px;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface-2);
        color: var(--text);
        font-size: 1.2rem;
        line-height: 1;
        cursor: pointer;
    }

    .close:hover {
        border-color: var(--accent);
        color: var(--accent);
    }
</style>
