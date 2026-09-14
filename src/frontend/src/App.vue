<script setup lang="ts">
    const message = "Vue"
    const name = "Joey Garneau"
    const year = 2026
</script>

<template>
    <main>
	<h1>Hello {{ message }}</h1>
    </main>

    <footer>
	<p>&copy; {{ name }} {{ year }}</p>
    </footer>
</template>

<!--The scoped keyword here means this style only applies to this file-->
<style scoped>
    h1 {
	font-weight: bold;
	text-align: center;
    }

    footer {
	position: fixed;
	bottom: 0;
	left: 0;
	width: 100%;
	text-align: center;
	font-family: monospace;
    }
</style>
