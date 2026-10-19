<script setup lang="ts">
    import { computed } from 'vue'

    // Accepts the status strings from both useWebSocket and useEventSource.
    const props = defineProps<{ status: string }>()

    const label = computed(() => {
        switch (props.status) {
            case 'OPEN': return 'Live'
            case 'CONNECTING': return 'Connecting'
            default: return 'Offline'
        }
    })
</script>

<template>
    <span class="pill" :class="status.toLowerCase()">
        <span class="dot"></span>{{ label }}
    </span>
</template>

<style scoped>
    .pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 500;
        background: var(--surface-2);
        color: var(--text-muted);
        white-space: nowrap;
    }

    .dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--danger);
    }

    .open .dot {
        background: var(--ok);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 25%, transparent);
    }

    .connecting .dot {
        background: var(--warn);
    }
</style>
