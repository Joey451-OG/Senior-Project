<script setup lang="ts">
    import { useIntervalFn } from '@vueuse/core'
    import { useAuth } from '@/composables/useAuth'
    import WaruLogo from '@/components/WaruLogo.vue'

    const { email, recheckAdministrator, signOut } = useAuth()

    // Picks up the account being added to /etc/waru/auth-accounts without a reload
    const recheck_interval_ms = 15000
    useIntervalFn(recheckAdministrator, recheck_interval_ms)
</script>

<template>
    <main class="not-administrator">
        <div class="center">
            <WaruLogo :size="128" />
        </div>

        <footer class="message">
            <p>You're signed in, but you are not yet an administrator. Please contact your system administrator.</p>
            <p class="account muted">
                Signed in as <span class="mono">{{ email }}</span> ·
                <button type="button" class="link" @click="signOut()">Sign out</button>
            </p>
        </footer>
    </main>
</template>

<style scoped>
    .not-administrator {
        display: flex;
        flex-direction: column;
        min-height: 100vh;
        padding: 24px 16px;
    }

    .center {
        flex: 1;
        display: grid;
        place-items: center;
    }

    .message {
        text-align: center;
    }

    .message p {
        margin: 0 0 8px;
    }

    .account {
        font-size: 0.85rem;
    }

    .link {
        padding: 0;
        border: none;
        background: none;
        color: var(--accent);
        font: inherit;
        cursor: pointer;
    }

    .link:hover {
        text-decoration: underline;
    }
</style>
