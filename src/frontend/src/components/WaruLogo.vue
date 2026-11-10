<script setup lang="ts">
    const props = withDefaults(defineProps<{ size?: number }>(), { size: 30 })
</script>

<template>
    <span class="logo" aria-hidden="true" :style="{
        width: `${props.size}px`,
        height: `${props.size}px`,
        borderRadius: `${props.size * 0.27}px`,
        fontSize: `${props.size * 0.5}px`,
    }">W</span>
</template>

<style scoped>
    .logo {
        display: inline-grid;
        place-items: center;
        flex-shrink: 0;
        background: var(--accent);
        color: #fff;
        font-weight: 700;
        line-height: 1;
        user-select: none;
    }
</style>
