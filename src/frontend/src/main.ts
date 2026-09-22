import { createApp } from 'vue'
import App from './App.vue'
import TemperatureReading from './components/TemperatureReading.vue'
import LoadReading from "./components/LoadReading.vue";

const app = createApp(App)

app.component('temperature-reading', TemperatureReading)
app.component('load-reading', LoadReading)
app.mount('#app')
