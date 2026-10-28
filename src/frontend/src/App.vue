<script setup lang="ts">
    import { useDark, useToggle } from '@vueuse/core'
    import { domain } from '@/globals/projectVars'

    // Follows the OS setting until the user picks a theme, then remembers it in localStorage
    const isDark = useDark({ attribute: 'data-theme', valueDark: 'dark', valueLight: 'light' })
    const toggleDark = useToggle(isDark)

    const name = "Joey Garneau"
    const year = 2026
</script>

<template>
    <header class="topbar">
        <div class="brand">
            <span class="logo">W</span>
            <h1>Waru</h1>
        </div>
        <div class="topbar-right">
            <span class="host mono">{{ domain }}</span>
            <button type="button" class="theme-toggle" @click="toggleDark()"
                    :aria-label="isDark ? 'Switch to light mode' : 'Switch to dark mode'">
                {{ isDark ? '☀' : '☾' }}
            </button>
        </div>
    </header>

    <main class="dashboard">
        <cpu-gauge class="cpu"/>
        <logged-in-users class="users"/>
        <user-procs class="procs"/>
        <crontab-list class="cron"/>
        <admin-commands class="cmds"/>
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

    .logo {
        display: grid;
        place-items: center;
        width: 30px;
        height: 30px;
        border-radius: 8px;
        background: var(--accent);
        color: #fff;
        font-weight: 700;
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

    .theme-toggle:hover {
        border-color: var(--accent);
        color: var(--accent);
    }

    .host {
        font-size: 0.8rem;
        color: var(--text-muted);
    }

    .dashboard {
        display: grid;
        grid-template-columns: minmax(280px, 340px) 1fr;
        grid-template-areas:
            "cpu   procs"
            "users procs"
            "cron  cron"
            "cmds  cmds";
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
