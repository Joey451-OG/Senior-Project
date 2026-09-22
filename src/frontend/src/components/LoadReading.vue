<script setup lang="ts">
    import { useLoadReading } from '@/composables/useDashboardWS'

    const { data, status } = useLoadReading();
</script>

<template>
    <div v-if="status === 'OPEN' && data">
        <p>CPU: {{ data["value"] }}%</p>
    </div>
    <div v-else-if="status === 'CONNECTING'">Connecting...</div>
    <div v-else>Disconnected - retrying...</div>
</template>