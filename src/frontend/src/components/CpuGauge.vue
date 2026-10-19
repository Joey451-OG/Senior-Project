<script setup lang="ts">
    import { computed } from 'vue'
    import { useLoadReading, useTemperatureReading } from '@/composables/useDashboardWS'
    import { type TemperatureSensor } from '@/globals/projectTypes'
    import StatusPill from './StatusPill.vue'

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

    // Both arcs share the same center; each is a 180° path with pathLength=100
    // so stroke-dasharray maps directly onto a percentage.
    const arcPath = (radius: number) => `M ${100 - radius} 100 A ${radius} ${radius} 0 0 1 ${100 + radius} 100`
    const OUTER_RADIUS = 84
    const INNER_RADIUS = 64
</script>

<template>
    <section class="card gauge-card">
        <header class="card-header">
            <h2 class="card-title">CPU</h2>
            <StatusPill :status="status" />
        </header>

        <div class="gauge">
            <svg viewBox="0 0 200 110" role="img"
                 :aria-label="`CPU load ${load ?? 'unknown'} percent, temperature ${temp ?? 'unknown'} degrees Celsius`">
                <path class="track" :d="arcPath(OUTER_RADIUS)" stroke-width="14" />
                <path class="arc load" :class="[loadLevel, { empty: loadFill < 0.5 }]" :d="arcPath(OUTER_RADIUS)" stroke-width="14"
                      pathLength="100" :stroke-dasharray="`${loadFill} 100`" />

                <path class="track" :d="arcPath(INNER_RADIUS)" stroke-width="8" />
                <path class="arc temp" :class="[tempLevel, { empty: tempFill < 0.5 }]" :d="arcPath(INNER_RADIUS)" stroke-width="8"
                      pathLength="100" :stroke-dasharray="`${tempFill} 100`" />
            </svg>

            <div class="readout">
                <div class="load-value">
                    {{ load !== null ? Math.round(load) : '--' }}<span class="unit">%</span>
                </div>
                <div class="temp-value" :class="tempLevel">
                    {{ temp !== null ? Math.round(temp) : '--' }}&deg;C
                </div>
            </div>
        </div>

        <dl class="legend">
            <div>
                <dt><span class="swatch load"></span>Load</dt>
                <dd>{{ load !== null ? `${load.toFixed(1)}%` : '--' }}</dd>
            </div>
            <div>
                <dt><span class="swatch temp"></span>Temp</dt>
                <dd>{{ temp !== null ? `${temp.toFixed(1)}°C` : '--' }}</dd>
            </div>
            <div>
                <dt>High / Crit</dt>
                <dd>{{ tempHigh }}° / {{ tempCritical }}°</dd>
            </div>
        </dl>
    </section>
</template>

<style scoped>
    .gauge {
        position: relative;
        max-width: 300px;
        margin: 0 auto;
    }

    svg {
        display: block;
        width: 100%;
        overflow: visible;
    }

    path {
        fill: none;
        stroke-linecap: round;
    }

    .track {
        stroke: var(--track);
    }

    .arc {
        transition: stroke-dasharray 0.6s ease, stroke 0.3s ease;
    }

    /* A round linecap still draws a dot at 0%, so hide arcs with nothing to show */
    .arc.empty { opacity: 0; }

    .arc.load { stroke: var(--accent); }
    .arc.temp { stroke: var(--temp); }
    .arc.warn { stroke: var(--warn); }
    .arc.danger { stroke: var(--danger); }

    .readout {
        position: absolute;
        left: 0;
        right: 0;
        bottom: 0;
        text-align: center;
        line-height: 1.1;
    }

    .load-value {
        font-size: 2.6rem;
        font-weight: 650;
        font-variant-numeric: tabular-nums;
        letter-spacing: -0.02em;
    }

    .unit {
        font-size: 1.1rem;
        font-weight: 500;
        color: var(--text-muted);
        margin-left: 2px;
    }

    .temp-value {
        font-size: 0.95rem;
        font-weight: 500;
        color: var(--temp);
        font-variant-numeric: tabular-nums;
    }

    .temp-value.warn { color: var(--warn); }
    .temp-value.danger { color: var(--danger); }

    .legend {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 8px;
        margin: 20px 0 0;
        padding-top: 14px;
        border-top: 1px solid var(--border);
        text-align: center;
    }

    dt {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        font-size: 0.75rem;
        color: var(--text-muted);
    }

    dd {
        margin: 2px 0 0;
        font-weight: 600;
        font-variant-numeric: tabular-nums;
    }

    .swatch {
        width: 10px;
        height: 10px;
        border-radius: 3px;
    }

    .swatch.load { background: var(--accent); }
    .swatch.temp { background: var(--temp); }
</style>
