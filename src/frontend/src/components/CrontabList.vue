<script setup lang="ts">
    import { computed, ref } from 'vue'
    import {
        useCrontabs,
        fetchCrontabContent,
        fetchCrontabSnapshot,
        saveCrontab,
        CrontabConflictError,
    } from '@/composables/usePrivilegedAPI'
    import StatusPill from './StatusPill.vue'

    const { listing, lastChange, error, status, refresh } = useCrontabs();

    const totalJobs = computed(() =>
        listing.value?.crontabs.reduce((sum, crontab) => sum + crontab.jobs.length, 0) ?? 0
    )

    // --- Editor ---

    interface EditSession {
        username: string,
        // Hash and text of the version the draft started from
        baseHash: string,
        originalContent: string
    }

    const editing = ref<EditSession | null>(null)
    const draft = ref('')
    const loadingUsername = ref<string | null>(null)
    const isSaving = ref(false)
    const editError = ref<string | null>(null)

    // The helper refuses root anyway; this just avoids offering an editor that can't save
    const canEdit = (username: string) => username !== 'root'

    const isDirty = computed(() => editing.value !== null && draft.value !== editing.value.originalContent)

    // The listing is refreshed on every cron_changed event, so it holds the latest hash.
    // A user missing from the listing has had their crontab emptied or removed.
    const hasChangedElsewhere = computed(() => {
        if (!editing.value || !listing.value) return false
        const latest = listing.value.crontabs.find(crontab => crontab.username === editing.value?.username)
        return latest?.content_hash !== editing.value.baseHash
    })

    const errorMessage = (err: unknown) => err instanceof Error ? err.message : String(err)

    async function startEditing(username: string) {
        if (isDirty.value && !window.confirm(`Discard your unsaved changes to ${editing.value?.username}'s crontab?`)) return
        loadingUsername.value = username
        try {
            const current = await fetchCrontabContent(username)
            editing.value = { username, baseHash: current.content_hash, originalContent: current.content }
            draft.value = current.content
            editError.value = null
            // Make sure the listing isn't older than what was just loaded, so it isn't flagged as changed
            refresh()
        } catch (err) {
            editError.value = `Could not load ${username}'s crontab: ${errorMessage(err)}`
        } finally {
            loadingUsername.value = null
        }
    }

    function stopEditing() {
        if (isDirty.value && !window.confirm('Discard your unsaved changes?')) return
        editing.value = null
        editError.value = null
    }

    async function loadLatest() {
        if (!editing.value) return
        if (isDirty.value && !window.confirm('Replace your edits with the latest version of this crontab?')) return
        // Clear first so startEditing doesn't ask about discarding a second time
        const username = editing.value.username
        editing.value = null
        await startEditing(username)
    }

    async function save() {
        if (!editing.value) return
        const { username } = editing.value
        let expectedHash = editing.value.baseHash
        isSaving.value = true
        editError.value = null
        try {
            // Loops only when the crontab keeps changing and the user keeps choosing to overwrite
            while (true) {
                try {
                    await saveCrontab(username, draft.value, expectedHash)
                    editing.value = null
                    return
                } catch (err) {
                    if (!(err instanceof CrontabConflictError)) throw err
                    const shouldOverwrite = window.confirm(
                        `${username}'s crontab was changed somewhere else while you were editing it.\n\n` +
                        'Overwrite those changes with your version?'
                    )
                    if (!shouldOverwrite) {
                        editError.value = 'Not saved. Use "Load latest" to see the other changes, or save again to overwrite them.'
                        return
                    }
                    // Overwrite exactly the version that is there now; if it changes again, ask again
                    expectedHash = (await fetchCrontabSnapshot(username)).content_hash
                }
            }
        } catch (err) {
            editError.value = `Could not save ${username}'s crontab: ${errorMessage(err)}`
        } finally {
            isSaving.value = false
        }
    }
</script>

<template>
    <section class="card cron-card">
        <header class="card-header">
            <h2 class="card-title">Cron Jobs <span v-if="listing" class="count">{{ totalJobs }}</span></h2>
            <div class="meta">
                <span v-if="lastChange" class="muted changed">
                    Changed: {{ lastChange.changed_usernames.join(', ') }}
                </span>
                <StatusPill :status="status" />
            </div>
        </header>

        <p v-if="error" class="error">{{ error }}</p>
        <p v-if="editError && !editing" class="error">{{ editError }}</p>

        <section v-if="editing" class="editor" aria-label="Crontab editor">
            <header class="editor-header">
                <span>Editing <strong>{{ editing.username }}</strong>'s crontab</span>
                <span class="mono muted hash" title="Hash of the version you started from">{{ editing.baseHash.slice(0, 8) }}</span>
            </header>

            <p v-if="hasChangedElsewhere" class="warning">
                <span>This crontab has changed since you started editing. Saving will ask before overwriting it.</span>
                <button type="button" class="link" @click="loadLatest">Load latest</button>
            </p>

            <textarea v-model="draft" class="editor-text" spellcheck="false" autocomplete="off"
                      :rows="Math.min(Math.max(draft.split('\n').length + 1, 6), 24)"
                      :disabled="isSaving" aria-label="Crontab contents"></textarea>

            <p v-if="editError" class="error">{{ editError }}</p>

            <div class="editor-actions">
                <span class="muted hint">One job per line: minute hour day month weekday command</span>
                <button type="button" class="secondary" :disabled="isSaving" @click="stopEditing">Cancel</button>
                <button type="button" class="primary" :disabled="isSaving || !isDirty" @click="save">
                    {{ isSaving ? 'Saving…' : 'Save' }}
                </button>
            </div>
        </section>

        <div v-if="!listing && !error" class="placeholder">Loading crontabs…</div>
        <div v-else-if="listing && listing.crontabs.length === 0" class="placeholder">No users have a crontab</div>

        <div v-else-if="listing" class="users">
            <article v-for="crontab in listing.crontabs" :key="crontab.username" class="user">
                <header class="user-header">
                    <span class="avatar">{{ crontab.username.charAt(0).toUpperCase() }}</span>
                    <span class="username">{{ crontab.username }}</span>
                    <span class="muted job-count">
                        {{ crontab.enabled_job_count }} / {{ crontab.jobs.length }} enabled
                    </span>
                    <span class="mono muted hash" title="Crontab content hash">{{ crontab.content_hash.slice(0, 8) }}</span>
                    <button v-if="canEdit(crontab.username)" type="button" class="secondary edit"
                            :disabled="loadingUsername !== null || editing?.username === crontab.username"
                            @click="startEditing(crontab.username)">
                        {{ loadingUsername === crontab.username ? 'Loading…' : 'Edit' }}
                    </button>
                </header>

                <ul v-if="crontab.jobs.length" class="jobs">
                    <li v-for="(job, index) in crontab.jobs" :key="index" :class="{ disabled: !job.is_enabled }">
                        <code class="schedule">{{ job.schedule }}</code>
                        <div class="job-body">
                            <code class="command">{{ job.command }}</code>
                            <span v-if="job.comment" class="comment">{{ job.comment }}</span>
                        </div>
                        <span v-if="!job.is_enabled" class="badge">Disabled</span>
                    </li>
                </ul>
                <p v-else class="muted empty">Crontab is empty</p>
            </article>
        </div>

        <p v-if="listing && listing.failed_usernames.length" class="error">
            Could not read crontabs for: {{ listing.failed_usernames.join(', ') }}
        </p>
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

    .meta {
        display: flex;
        align-items: center;
        gap: 10px;
        min-width: 0;
    }

    .changed {
        font-size: 0.8rem;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .error {
        margin: 0 0 12px;
        padding: 8px 12px;
        border-radius: 8px;
        background: color-mix(in srgb, var(--danger) 12%, transparent);
        color: var(--danger);
        font-size: 0.85rem;
    }

    .warning {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin: 0 0 12px;
        padding: 8px 12px;
        border-radius: 8px;
        background: color-mix(in srgb, var(--warn) 16%, transparent);
        color: var(--text);
        font-size: 0.85rem;
    }

    .editor {
        margin-bottom: 14px;
        padding: 12px 14px;
        border: 1px solid var(--accent);
        border-radius: 10px;
    }

    .editor-header {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 10px;
        font-size: 0.9rem;
    }

    .editor-text {
        box-sizing: border-box;
        width: 100%;
        padding: 10px 12px;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface-2);
        color: var(--text);
        font-family: var(--mono);
        font-size: 0.8rem;
        line-height: 1.5;
        resize: vertical;
        white-space: pre;
        overflow-wrap: normal;
        overflow-x: auto;
    }

    .editor-text:focus {
        outline: 2px solid color-mix(in srgb, var(--accent) 40%, transparent);
        border-color: var(--accent);
    }

    .editor .error {
        margin: 10px 0 0;
    }

    .editor-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 10px;
    }

    .hint {
        margin-right: auto;
        font-size: 0.75rem;
    }

    button.primary,
    button.secondary {
        padding: 5px 14px;
        border-radius: 8px;
        font: inherit;
        font-size: 0.8rem;
        font-weight: 600;
        cursor: pointer;
    }

    button.primary {
        border: 1px solid var(--accent);
        background: var(--accent);
        color: #fff;
    }

    button.secondary {
        border: 1px solid var(--border);
        background: var(--surface);
        color: var(--text);
    }

    button.primary:disabled,
    button.secondary:disabled {
        cursor: default;
        opacity: 0.5;
    }

    button.link {
        flex-shrink: 0;
        padding: 0;
        border: none;
        background: none;
        color: var(--accent);
        font: inherit;
        font-weight: 600;
        cursor: pointer;
    }

    .edit {
        padding: 2px 10px;
    }

    .users {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
        gap: 14px;
    }

    .user {
        border: 1px solid var(--border);
        border-radius: 10px;
        overflow: hidden;
        min-width: 0;
    }

    .user-header {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 14px;
        background: var(--surface-2);
        border-bottom: 1px solid var(--border);
    }

    .avatar {
        display: grid;
        place-items: center;
        width: 26px;
        height: 26px;
        border-radius: 50%;
        background: var(--accent);
        color: #fff;
        font-size: 0.8rem;
        font-weight: 600;
    }

    .username {
        font-weight: 600;
    }

    .job-count {
        font-size: 0.8rem;
    }

    .hash {
        margin-left: auto;
        font-size: 0.75rem;
    }

    .jobs {
        list-style: none;
        margin: 0;
        padding: 0;
    }

    .jobs li {
        display: flex;
        align-items: flex-start;
        gap: 12px;
        padding: 10px 14px;
        border-bottom: 1px solid var(--border);
    }

    .jobs li:last-child {
        border-bottom: none;
    }

    .jobs li.disabled {
        opacity: 0.55;
    }

    code {
        font-family: var(--mono);
        font-size: 0.8rem;
    }

    .schedule {
        flex-shrink: 0;
        padding: 2px 8px;
        border-radius: 6px;
        background: color-mix(in srgb, var(--accent) 14%, transparent);
        color: var(--accent);
        white-space: nowrap;
    }

    .job-body {
        display: flex;
        flex-direction: column;
        gap: 2px;
        min-width: 0;
        flex: 1;
    }

    .command {
        overflow-wrap: anywhere;
    }

    .comment {
        font-size: 0.8rem;
        color: var(--text-muted);
    }

    .badge {
        flex-shrink: 0;
        padding: 1px 8px;
        border-radius: 999px;
        border: 1px solid var(--border);
        font-size: 0.7rem;
        color: var(--text-muted);
    }

    .empty {
        margin: 0;
        padding: 12px 14px;
        font-size: 0.85rem;
    }
</style>
