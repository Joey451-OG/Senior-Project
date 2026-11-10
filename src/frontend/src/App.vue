<script setup lang="ts">
    import { useAuth } from '@/composables/useAuth'
    import { useTheme } from '@/composables/useTheme'
    import WaruLogo from '@/components/WaruLogo.vue'
    import DashboardView from '@/views/DashboardView.vue'
    import NotAdministratorView from '@/views/NotAdministratorView.vue'
    import SignInView from '@/views/SignInView.vue'

    // Applies the saved theme on every page, not only the dashboard
    useTheme()

    const { phase } = useAuth()
</script>

<template>
    <!-- The dashboard only mounts (and opens its sockets) once the account is authorized -->
    <DashboardView v-if="phase === 'authorized'" />
    <NotAdministratorView v-else-if="phase === 'pending'" />
    <div v-else-if="phase === 'loading'" class="splash">
        <WaruLogo :size="64" />
    </div>
    <SignInView v-else />
</template>

<style scoped>
    .splash {
        display: grid;
        place-items: center;
        min-height: 100vh;
    }
</style>
