<script setup lang="ts">
    import { useUserSession } from '@/composables/useDashboardWS'

    const { data, status } = useUserSession();
</script>

<template>
    <div v-if="status === 'OPEN' && data">
        <p>
            {{ data }}
        </p>
    </div>
    <div v-else-if="status === 'CONNECTING'">Connecting...</div>
    <div v-else>Disconnected - retrying...</div>
</template>