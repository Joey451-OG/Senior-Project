<script setup lang="ts">
    import { useTemperatureReading } from '@/composables/useDashboardWS'

    const { data, status } = useTemperatureReading()
</script>

<template>
    <div v-if="status === 'OPEN' && data">
        <p>CPU: {{ data }}&deg;C</p>
    </div>
    <div v-else-if="status === 'CONNECTING'">Connecting...</div>
    <div v-else>Disconnected - retrying...</div>
</template>