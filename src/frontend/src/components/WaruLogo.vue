<script setup lang="ts">
    const props = withDefaults(defineProps<{ size?: number }>(), { size: 30 })
</script>

<template>
    <img class="logo" src="/Waru-Favicon.svg" alt="" aria-hidden="true" :width="props.size" :height="props.size" :style="{
        borderRadius: `${props.size * 0.27}px`,
    }">
</template>

<style scoped>
    .logo {
        display: inline-block;
        flex-shrink: 0;
        user-select: none;
    }
</style>
