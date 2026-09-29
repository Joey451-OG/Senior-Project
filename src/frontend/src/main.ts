import { createApp } from 'vue'
import App from './App.vue'
import TemperatureReading from './components/TemperatureReading.vue'
import LoadReading from "./components/LoadReading.vue";
import UserProcs from './components/UserProc.vue';
const app = createApp(App)

app.component('temperature-reading', TemperatureReading)
app.component('load-reading', LoadReading)
app.component('user-procs', UserProcs)
app.mount('#app')
