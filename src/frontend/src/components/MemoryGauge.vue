<script setup lang="ts">
    import { computed, watch } from 'vue'
    import { useMemoryReading } from '@/composables/useDashboardWS'

    const { data, status } = useMemoryReading()

    const GIB = 1024 ** 3
    const MIB = 1024 ** 2

    const memory = computed(() => data.value)
    const percent = computed(() => memory.value?.percent ?? null)
    const hasSwap = computed(() => (memory.value?.swap_total ?? 0) > 0)
    const swapPercent = computed(() => (hasSwap.value ? memory.value!.swap_percent : null))

    const clampPercent = (value: number) => Math.min(100, Math.max(0, value))

    const memoryFill = computed(() => clampPercent(percent.value ?? 0))
    const swapFill = computed(() => clampPercent(swapPercent.value ?? 0))

    const memoryLevel = computed(() => {
        const value = percent.value ?? 0
        if (value >= 90) return 'danger'
        if (value >= 75) return 'warn'
        return 'normal'
    })

    // Heavy swapping is the real sign of memory pressure, so swap has its own thresholds
    const swapLevel = computed(() => {
        const value = swapPercent.value ?? 0
        if (value >= 80) return 'danger'
        if (value >= 50) return 'warn'
        return 'normal'
    })

    const formatBytes = (bytes: number | undefined) => {
        if (bytes === undefined) return '--'
        if (bytes < GIB) return `${Math.round(bytes / MIB)} MiB`
        return `${(bytes / GIB).toFixed(1)} GiB`
    }

    // The surrounding card (ResourceGauge.vue) owns the header and its status pill
    const emit = defineEmits<{ status: [status: string] }>()
    watch(status, (value) => emit('status', value), { immediate: true })

    // Same geometry as CpuGauge.vue: 180° arcs with pathLength=100, so the dash is the percentage
    const arcPath = (radius: number) => `M ${100 - radius} 100 A ${radius} ${radius} 0 0 1 ${100 + radius} 100`
    const OUTER_RADIUS = 84
    const INNER_RADIUS = 64
</script>

<template>
    <div class="gauge-body">
        <div class="gauge">
            <svg viewBox="0 0 200 110" role="img"
                 :aria-label="`Memory ${percent ?? 'unknown'} percent used, swap ${swapPercent ?? 'not available'}${swapPercent !== null ? ' percent used' : ''}`">
                <path class="track" :d="arcPath(OUTER_RADIUS)" stroke-width="14" />
                <path class="arc outer" :class="[memoryLevel, { empty: memoryFill < 0.5 }]" :d="arcPath(OUTER_RADIUS)" stroke-width="14"
                      pathLength="100" :stroke-dasharray="`${memoryFill} 100`" />

                <path class="track" :d="arcPath(INNER_RADIUS)" stroke-width="8" />
                <path class="arc inner" :class="[swapLevel, { empty: swapFill < 0.5 }]" :d="arcPath(INNER_RADIUS)" stroke-width="8"
                      pathLength="100" :stroke-dasharray="`${swapFill} 100`" />
            </svg>

            <div class="readout">
                <div class="readout-value">
                    {{ percent !== null ? Math.round(percent) : '--' }}<span class="unit">%</span>
                </div>
                <div class="readout-sub" :class="swapLevel">
                    <template v-if="!memory">swap --%</template>
                    <template v-else-if="hasSwap">swap {{ Math.round(swapPercent!) }}%</template>
                    <template v-else>no swap</template>
                </div>
            </div>
        </div>

        <dl class="legend">
            <div>
                <dt><span class="swatch outer"></span>Used</dt>
                <dd>{{ formatBytes(memory?.used) }}</dd>
            </div>
            <div>
                <dt><span class="swatch inner"></span>Swap</dt>
                <dd>{{ memory && !hasSwap ? 'None' : formatBytes(memory?.swap_used) }}</dd>
            </div>
            <div>
                <dt>Total</dt>
                <dd>{{ formatBytes(memory?.total) }}</dd>
            </div>
        </dl>
    </div>
</template>

<style scoped src="../assets/gauge.css"></style>

<style scoped>
    .gauge-body {
        --outer-color: var(--accent);
        --inner-color: var(--swap);
    }
</style>
