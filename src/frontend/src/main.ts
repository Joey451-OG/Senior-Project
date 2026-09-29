import { createApp } from 'vue'
import App from './App.vue'
import TemperatureReading from './components/TemperatureReading.vue'
import LoadReading from "./components/LoadReading.vue";
import UserSession from './components/UserSession.vue';
const app = createApp(App)

app.component('temperature-reading', TemperatureReading)
app.component('load-reading', LoadReading)
app.component('user-session', UserSession)
app.mount('#app')
