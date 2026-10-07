<script setup lang="ts">
    import { ref, watch } from 'vue'
    import { useCrontabs, fetchCrontabSnapshot } from '@/composables/usePrivilegedAPI'
    import { type CrontabSnapshot } from '@/globals/projectTypes'

    const { listing, lastChange, error, status } = useCrontabs();

    // Pull /papi/cron/{username} for every user in the listing so that endpoint's data is shown too.
    const snapshots = ref<Record<string, CrontabSnapshot | string>>({})

    watch(listing, async (newListing) => {
        if (!newListing) return
        const newSnapshots: Record<string, CrontabSnapshot | string> = {}
        for (const crontab of newListing.crontabs) {
            try {
                newSnapshots[crontab.username] = await fetchCrontabSnapshot(crontab.username)
            } catch (err) {
                newSnapshots[crontab.username] = String(err)
            }
        }
        snapshots.value = newSnapshots
    })
</script>

<template>
    <div>
        <p>Event stream: {{ status }}</p>
        <p v-if="error">{{ error }}</p>
        <p>Last change: {{ lastChange }}</p>
        <p>Listing: {{ listing }}</p>
        <p>Snapshots: {{ snapshots }}</p>
    </div>
</template>
