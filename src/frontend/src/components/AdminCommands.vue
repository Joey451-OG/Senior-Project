<script setup lang="ts">
    import { computed, nextTick, ref, shallowRef } from 'vue'
    import { useAdminCommands, runAdminCommand } from '@/composables/usePrivilegedAPI'
    import { type AdminCommand, type AdminCommandResult, type AdminCommandRunEvent } from '@/globals/projectTypes'
    import StatusPill from './StatusPill.vue'

    // The list re-fetches itself whenever /etc/waru/sudo_commands changes
    const { listing, error, status } = useAdminCommands()

    // --- Terminal ---

    // Only the newest lines are kept, so a very chatty command can't bog down the page
    const maximumTerminalLines = 5000
    // Colors, cursor movement, etc.; the window only shows plain text
    const ansiEscapePattern = /\x1b\[[0-9;?]*[ -\/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)|\x1b[@-_]/g

    // Plain array as the working buffer; terminalText is what the template renders
    let terminalLines: string[] = ['']
    // A \r at the very end of a chunk could be the start of \r\n, so it's decided on the next character
    let hasPendingCarriageReturn = false
    const terminalText = shallowRef('')
    const terminalElement = ref<HTMLPreElement | null>(null)
    // Auto-scroll only while the user is looking at the bottom, so scrolling up to read isn't interrupted
    const isPinnedToBottom = ref(true)

    function resetTerminal() {
        terminalLines = ['']
        hasPendingCarriageReturn = false
        terminalText.value = ''
        isPinnedToBottom.value = true
    }

    // Handles \r the way a terminal does, so progress lines (e.g. apt's) update in place
    function writeToTerminal(text: string) {
        for (const character of text.replace(ansiEscapePattern, '')) {
            if (hasPendingCarriageReturn) {
                hasPendingCarriageReturn = false
                if (character === '\n') {
                    terminalLines.push('')
                    continue
                }
                terminalLines[terminalLines.length - 1] = ''
            }
            if (character === '\r') {
                hasPendingCarriageReturn = true
            } else if (character === '\n') {
                terminalLines.push('')
            } else {
                terminalLines[terminalLines.length - 1] += character
            }
        }
        if (terminalLines.length > maximumTerminalLines) {
            terminalLines = terminalLines.slice(-maximumTerminalLines)
        }
        terminalText.value = terminalLines.join('\n')
        scrollToBottomIfPinned()
    }

    async function scrollToBottomIfPinned() {
        if (!isPinnedToBottom.value) return
        await nextTick()
        if (terminalElement.value) terminalElement.value.scrollTop = terminalElement.value.scrollHeight
    }

    function onTerminalScroll() {
        const element = terminalElement.value
        if (!element) return
        isPinnedToBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 24
    }

    // --- Running a command ---

    interface CommandRun {
        command: AdminCommand,
        state: 'starting' | 'running' | 'finished' | 'failed',
        result: AdminCommandResult | null,
        error: string | null
    }

    const run = ref<CommandRun | null>(null)
    const dialogElement = ref<HTMLDialogElement | null>(null)
    let abortController: AbortController | null = null

    const isRunActive = computed(() => run.value?.state === 'starting' || run.value?.state === 'running')

    const runStatusText = computed(() => {
        const currentRun = run.value
        if (!currentRun) return ''
        switch (currentRun.state) {
            case 'starting': return 'Starting…'
            case 'running': return 'Running…'
            case 'failed': return currentRun.error ?? 'Failed'
            case 'finished': {
                const result = currentRun.result!
                const duration = `${result.duration_seconds.toFixed(1)}s`
                if (result.timed_out) return `Timed out and killed after ${duration}`
                if (result.exit_code < 0) return `Killed by signal ${-result.exit_code} after ${duration}`
                return `Exited with code ${result.exit_code} after ${duration}`
            }
        }
    })

    const runStatusClass = computed(() => {
        const currentRun = run.value
        if (!currentRun || isRunActive.value) return 'active'
        const isSuccess = currentRun.state === 'finished' && currentRun.result?.exit_code === 0
        return isSuccess ? 'success' : 'failure'
    })

    function handleRunEvent(currentRun: CommandRun, runEvent: AdminCommandRunEvent) {
        if (!runEvent.ok) {
            currentRun.state = 'failed'
            currentRun.error = runEvent.error
            return
        }
        switch (runEvent.event) {
            case 'started':
                currentRun.state = 'running'
                break
            case 'output':
                writeToTerminal(runEvent.data)
                break
            case 'finished':
                currentRun.state = 'finished'
                currentRun.result = runEvent.result
                break
        }
    }

    const errorMessage = (err: unknown) => err instanceof Error ? err.message : String(err)

    async function startRun(command: AdminCommand) {
        if (!window.confirm(`Run this command as root?\n\n${command.command}`)) return

        resetTerminal()
        run.value = { command, state: 'starting', result: null, error: null }
        // Work on the reactive proxy so state changes re-render
        const currentRun = run.value
        dialogElement.value?.showModal()

        const thisRunAbortController = new AbortController()
        abortController = thisRunAbortController
        try {
            await runAdminCommand(command.command_id, runEvent => handleRunEvent(currentRun, runEvent), thisRunAbortController.signal)
            if (currentRun.state === 'starting' || currentRun.state === 'running') {
                currentRun.state = 'failed'
                currentRun.error = 'Lost the connection before the command finished; it may still be running'
            }
        } catch (err) {
            if (thisRunAbortController.signal.aborted) return
            currentRun.state = 'failed'
            currentRun.error = errorMessage(err)
        }
    }

    function closeWindow() {
        if (isRunActive.value && !window.confirm(
            'The command will keep running on the server, but you won\'t see the rest of its output.\n\nClose anyway?'
        )) return
        abortController?.abort()
        abortController = null
        dialogElement.value?.close()
        run.value = null
    }
</script>

<template>
    <section class="card commands-card">
        <header class="card-header">
            <h2 class="card-title">
                Admin Commands <span v-if="listing" class="count">{{ listing.commands.length }}</span>
            </h2>
            <StatusPill :status="status" />
        </header>

        <p v-if="error" class="error">{{ error }}</p>
        <p v-if="listing?.error" class="error">
            {{ listing.error }} <span class="mono">(/etc/waru/sudo_commands)</span>
        </p>

        <div v-if="!listing && !error" class="placeholder">Loading commands…</div>
        <div v-else-if="listing && !listing.error && listing.commands.length === 0" class="placeholder">
            No commands in <span class="mono">/etc/waru/sudo_commands</span>
        </div>

        <TransitionGroup v-else-if="listing" tag="ul" name="command" class="commands">
            <li v-for="command in listing.commands" :key="command.command_id">
                <code class="command">{{ command.command }}</code>
                <button type="button" class="primary run" :disabled="run !== null" @click="startRun(command)">Run</button>
            </li>
        </TransitionGroup>

        <!-- showModal() lifts this into the top layer, so living inside the card doesn't confine it.
             Esc fires "cancel"; route it through closeWindow so a running command asks first. -->
        <dialog ref="dialogElement" class="run-window" aria-label="Command output" @cancel.prevent="closeWindow">
            <template v-if="run">
                <header class="run-header">
                    <code class="run-command" :title="run.command.command">{{ run.command.command }}</code>
                    <span class="run-status" :class="runStatusClass">
                        <span v-if="isRunActive" class="spinner" aria-hidden="true"></span>{{ runStatusText }}
                    </span>
                    <button type="button" class="secondary" @click="closeWindow">Close</button>
                </header>
                <pre ref="terminalElement" class="terminal" @scroll="onTerminalScroll">{{ terminalText }}</pre>
            </template>
        </dialog>
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

    .error {
        margin: 0 0 12px;
        padding: 8px 12px;
        border-radius: 8px;
        background: color-mix(in srgb, var(--danger) 12%, transparent);
        color: var(--danger);
        font-size: 0.85rem;
    }

    .commands {
        list-style: none;
        margin: 0;
        padding: 0;
        border: 1px solid var(--border);
        border-radius: 10px;
        overflow: hidden;
    }

    .commands li {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 10px 14px;
        border-bottom: 1px solid var(--border);
    }

    .commands li:last-child {
        border-bottom: none;
    }

    /* Commands added to or removed from the file fade in and out */
    .command-enter-active,
    .command-leave-active {
        transition: opacity 0.3s ease, background-color 1.5s ease;
    }

    .command-enter-from {
        opacity: 0;
        background: color-mix(in srgb, var(--accent) 18%, transparent);
    }

    .command-leave-to {
        opacity: 0;
    }

    code {
        font-family: var(--mono);
        font-size: 0.8rem;
    }

    .command {
        flex: 1;
        min-width: 0;
        overflow-wrap: anywhere;
    }

    button.primary,
    button.secondary {
        flex-shrink: 0;
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

    button.primary:disabled {
        cursor: default;
        opacity: 0.5;
    }

    /* --- Output window --- */

    .run-window {
        width: min(960px, calc(100vw - 32px));
        height: min(640px, calc(100vh - 32px));
        padding: 0;
        border: 1px solid var(--border);
        border-radius: var(--radius);
        background: var(--surface);
        color: var(--text);
        overflow: hidden;
    }

    .run-window[open] {
        display: flex;
        flex-direction: column;
    }

    .run-window::backdrop {
        background: rgba(0, 0, 0, 0.55);
    }

    .run-header {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 10px 14px;
        border-bottom: 1px solid var(--border);
    }

    .run-command {
        flex: 1;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-weight: 600;
    }

    .run-status {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        flex-shrink: 1;
        min-width: 0;
        font-size: 0.8rem;
        overflow-wrap: anywhere;
    }

    .run-status.active { color: var(--text-muted); }
    .run-status.success { color: var(--ok); }
    .run-status.failure { color: var(--danger); }

    .spinner {
        flex-shrink: 0;
        width: 10px;
        height: 10px;
        border: 2px solid currentColor;
        border-right-color: transparent;
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
    }

    @keyframes spin {
        to { transform: rotate(360deg); }
    }

    .terminal {
        flex: 1;
        margin: 0;
        padding: 12px 14px;
        overflow: auto;
        background: #000;
        color: #fff;
        font-family: var(--mono);
        font-size: 0.8rem;
        line-height: 1.4;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
    }
</style>
