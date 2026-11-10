import { createGlobalState, useDark } from '@vueuse/core'

// Follows the OS setting until the user picks a theme, then remembers it in localStorage.
// Global so App.vue (which applies it on every page) and the dashboard's toggle share one state.
export const useTheme = createGlobalState(() =>
    useDark({ attribute: 'data-theme', valueDark: 'dark', valueLight: 'light' })
)
