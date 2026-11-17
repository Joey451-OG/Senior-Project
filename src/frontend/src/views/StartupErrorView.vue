<script setup lang="ts">
    import { useTheme } from '@/composables/useTheme'
    import WaruLogo from '@/components/WaruLogo.vue'

    // Shown instead of the app when the Firebase config couldn't be loaded (see main.ts)
    defineProps<{ message: string }>()

    useTheme()

    function retry() {
        window.location.reload()
    }
</script>

<template>
    <main class="startup-error">
        <WaruLogo :size="64" />
        <p>{{ message }}</p>
        <p class="muted hint">The dashboard can't start until the server answers. Check that the Waru service is running.</p>
        <button type="button" class="retry" @click="retry">Retry</button>
    </main>
</template>

<style scoped>
    .startup-error {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 12px;
        min-height: 100vh;
        padding: 24px 16px;
        text-align: center;
    }

    p {
        margin: 0;
        max-width: 40rem;
    }

    .hint {
        font-size: 0.85rem;
    }

    .retry {
        padding: 8px 20px;
        border: none;
        border-radius: 6px;
        background: var(--accent);
        color: var(--surface);
        font: inherit;
        cursor: pointer;
    }
</style>
