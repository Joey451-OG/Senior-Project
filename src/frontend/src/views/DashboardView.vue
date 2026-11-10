<script setup lang="ts">
    import { useToggle } from '@vueuse/core'
    import { domain } from '@/globals/projectVars'
    import { useAuth } from '@/composables/useAuth'
    import { useTheme } from '@/composables/useTheme'
    import WaruLogo from '@/components/WaruLogo.vue'

    const isDark = useTheme()
    const toggleDark = useToggle(isDark)
    const { email, signOut } = useAuth()

    const name = "Joey Garneau"
    const year = 2026
</script>

<template>
    <header class="topbar">
        <div class="brand">
            <WaruLogo />
            <h1>Waru</h1>
        </div>
        <div class="topbar-right">
            <span class="host mono">{{ domain }}</span>
            <span class="account" :title="email ?? ''">{{ email }}</span>
            <button type="button" class="theme-toggle" @click="toggleDark()"
                    :aria-label="isDark ? 'Switch to light mode' : 'Switch to dark mode'">
                {{ isDark ? '☀' : '☾' }}
            </button>
            <button type="button" class="sign-out" @click="signOut()">Sign out</button>
        </div>
    </header>

    <main class="dashboard">
        <cpu-gauge class="cpu"/>
        <logged-in-users class="users"/>
        <admin-commands class="cmds"/>
        <user-procs class="procs"/>
        <crontab-list class="cron"/>
    </main>

    <footer>
        <p>&copy; {{ name }} {{ year }}</p>
    </footer>
</template>

<!--The scoped keyword here means this style only applies to this file-->
<style scoped>
    .topbar {
        position: sticky;
        top: 0;
        z-index: 10;
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 24px;
        background: color-mix(in srgb, var(--surface) 85%, transparent);
        backdrop-filter: blur(8px);
        border-bottom: 1px solid var(--border);
    }

    .brand {
        display: flex;
        align-items: center;
        gap: 10px;
    }

    h1 {
        margin: 0;
        font-size: 1.2rem;
        font-weight: 650;
    }

    .topbar-right {
        display: flex;
        align-items: center;
        gap: 14px;
        min-width: 0;
    }

    .theme-toggle {
        display: grid;
        place-items: center;
        width: 32px;
        height: 32px;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface-2);
        color: var(--text);
        font-size: 1rem;
        cursor: pointer;
    }

    .theme-toggle:hover,
    .sign-out:hover {
        border-color: var(--accent);
        color: var(--accent);
    }

    .sign-out {
        height: 32px;
        padding: 0 12px;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface-2);
        color: var(--text);
        font: inherit;
        font-size: 0.8rem;
        font-weight: 600;
        cursor: pointer;
    }

    .host {
        font-size: 0.8rem;
        color: var(--text-muted);
    }

    .account {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 0.85rem;
    }

    .dashboard {
        display: grid;
        grid-template-columns: minmax(280px, 340px) 1fr;
        grid-template-areas:
            "cpu   procs"
            "users procs"
            "cmds  procs"
            "cron  cron";
        gap: 20px;
        max-width: 1280px;
        margin: 0 auto;
        padding: 24px;
        align-items: start;
    }

    .cpu { grid-area: cpu; }
    .users { grid-area: users; }
    .procs { grid-area: procs; }
    .cron { grid-area: cron; }
    .cmds { grid-area: cmds; }

    footer {
        padding: 8px 0 24px;
        text-align: center;
        font-family: var(--mono);
        font-size: 0.8rem;
        color: var(--text-muted);
    }
</style>
