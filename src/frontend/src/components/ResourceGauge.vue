<script setup lang="ts">
    import { computed, reactive } from 'vue'
    import { useStorage } from '@vueuse/core'
    import CpuGauge from './CpuGauge.vue'
    import MemoryGauge from './MemoryGauge.vue'
    import StatusPill from './StatusPill.vue'

    type Resource = 'cpu' | 'memory'

    const VIEWS: { id: Resource, label: string }[] = [
        { id: 'cpu', label: 'CPU' },
        { id: 'memory', label: 'Memory' },
    ]

    // Remembered per browser so the card comes back on the same view after a reload
    const selected = useStorage<string>('waru.resourceGauge', 'cpu')
    const active = computed<Resource>(() => (selected.value === 'memory' ? 'memory' : 'cpu'))

    const statuses = reactive<Record<Resource, string>>({ cpu: 'CONNECTING', memory: 'CONNECTING' })
</script>

<template>
    <section class="card">
        <header class="card-header">
            <div class="view-toggle" role="group" aria-label="Show CPU or memory usage">
                <button v-for="view in VIEWS" :key="view.id" type="button"
                        :class="{ active: active === view.id }" :aria-pressed="active === view.id"
                        @click="selected = view.id">
                    {{ view.label }}
                </button>
            </div>
            <StatusPill :status="statuses[active]" />
        </header>

        <!-- v-show, not v-if: both sockets stay open so switching views is instant -->
        <CpuGauge v-show="active === 'cpu'" @status="statuses.cpu = $event" />
        <MemoryGauge v-show="active === 'memory'" @status="statuses.memory = $event" />
    </section>
</template>

<style scoped>
    .view-toggle {
        display: inline-flex;
        padding: 2px;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface-2);
    }

    .view-toggle button {
        padding: 3px 10px;
        border: none;
        border-radius: 6px;
        background: none;
        color: var(--text-muted);
        font: inherit;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        cursor: pointer;
    }

    .view-toggle button:hover {
        color: var(--accent);
    }

    .view-toggle button.active {
        background: var(--surface);
        color: var(--accent);
        box-shadow: var(--shadow);
    }

    .view-toggle button:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 1px;
    }
</style>
