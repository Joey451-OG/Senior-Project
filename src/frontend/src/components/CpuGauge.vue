<script setup lang="ts">
    import { computed, watch } from 'vue'
    import { useLoadReading, useTemperatureReading } from '@/composables/useDashboardWS'
    import { type TemperatureSensor } from '@/globals/projectTypes'

    const { data: loadData, status: loadStatus } = useLoadReading()
    const { data: tempData, status: tempStatus } = useTemperatureReading()

    // Labels that already represent the whole CPU package (Intel coretemp, AMD k10temp)
    const PACKAGE_LABELS = ['Package id 0', 'Tctl', 'Tdie']

    // Fallbacks used when a sensor doesn't report its own thresholds
    const DEFAULT_HIGH = 80
    const DEFAULT_CRITICAL = 100

    const load = computed(() => loadData.value?.value ?? null)

    // Collapse every sensor into one "overall" CPU temperature: the package sensor
    // when there is one, otherwise the hottest individual reading.
    const overallTemp = computed<TemperatureSensor | null>(() => {
        const reading = tempData.value
        if (!reading) return null

        for (const label of PACKAGE_LABELS) {
            if (reading[label]) return reading[label]
        }

        let hottest: TemperatureSensor | null = null
        for (const sensor of Object.values(reading)) {
            if (sensor[0] === null) continue
            if (!hottest || sensor[0] > (hottest[0] ?? -Infinity)) hottest = sensor
        }
        return hottest
    })

    const temp = computed(() => overallTemp.value?.[0] ?? null)
    const tempHigh = computed(() => overallTemp.value?.[1] ?? DEFAULT_HIGH)
    const tempCritical = computed(() => overallTemp.value?.[2] ?? DEFAULT_CRITICAL)

    const clampPercent = (value: number) => Math.min(100, Math.max(0, value))

    const loadFill = computed(() => clampPercent(load.value ?? 0))
    const tempFill = computed(() => clampPercent(((temp.value ?? 0) / tempCritical.value) * 100))

    const loadLevel = computed(() => {
        const value = load.value ?? 0
        if (value >= 90) return 'danger'
        if (value >= 70) return 'warn'
        return 'normal'
    })

    const tempLevel = computed(() => {
        const value = temp.value ?? 0
        if (value >= tempCritical.value) return 'danger'
        if (value >= tempHigh.value) return 'warn'
        return 'normal'
    })

    const status = computed(() => {
        if (loadStatus.value === 'OPEN' && tempStatus.value === 'OPEN') return 'OPEN'
        if (loadStatus.value === 'CLOSED' && tempStatus.value === 'CLOSED') return 'CLOSED'
        return 'CONNECTING'
    })

    // The surrounding card (ResourceGauge.vue) owns the header and its status pill
    const emit = defineEmits<{ status: [status: string] }>()
    watch(status, (value) => emit('status', value), { immediate: true })

    // Both arcs share the same center; each is a 180° path with pathLength=100
    // so stroke-dasharray maps directly onto a percentage.
    const arcPath = (radius: number) => `M ${100 - radius} 100 A ${radius} ${radius} 0 0 1 ${100 + radius} 100`
    const OUTER_RADIUS = 84
    const INNER_RADIUS = 64
</script>

<template>
    <div class="gauge-body">
        <div class="gauge">
            <svg viewBox="0 0 200 110" role="img"
                 :aria-label="`CPU load ${load ?? 'unknown'} percent, temperature ${temp ?? 'unknown'} degrees Celsius`">
                <path class="track" :d="arcPath(OUTER_RADIUS)" stroke-width="14" />
                <path class="arc outer" :class="[loadLevel, { empty: loadFill < 0.5 }]" :d="arcPath(OUTER_RADIUS)" stroke-width="14"
                      pathLength="100" :stroke-dasharray="`${loadFill} 100`" />

                <path class="track" :d="arcPath(INNER_RADIUS)" stroke-width="8" />
                <path class="arc inner" :class="[tempLevel, { empty: tempFill < 0.5 }]" :d="arcPath(INNER_RADIUS)" stroke-width="8"
                      pathLength="100" :stroke-dasharray="`${tempFill} 100`" />
            </svg>

            <div class="readout">
                <div class="readout-value">
                    {{ load !== null ? Math.round(load) : '--' }}<span class="unit">%</span>
                </div>
                <div class="readout-sub" :class="tempLevel">
                    {{ temp !== null ? Math.round(temp) : '--' }}&deg;C
                </div>
            </div>
        </div>

        <dl class="legend">
            <div>
                <dt><span class="swatch outer"></span>Load</dt>
                <dd>{{ load !== null ? `${load.toFixed(1)}%` : '--' }}</dd>
            </div>
            <div>
                <dt><span class="swatch inner"></span>Temp</dt>
                <dd>{{ temp !== null ? `${temp.toFixed(1)}°C` : '--' }}</dd>
            </div>
            <div>
                <dt>High / Crit</dt>
                <dd>{{ tempHigh }}° / {{ tempCritical }}°</dd>
            </div>
        </dl>
    </div>
</template>

<style scoped src="../assets/gauge.css"></style>

<style scoped>
    .gauge-body {
        --outer-color: var(--accent);
        --inner-color: var(--temp);
    }
</style>
