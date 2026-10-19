import { createApp } from 'vue'
import App from './App.vue'
import CpuGauge from './components/CpuGauge.vue'
import UserProcs from './components/UserProc.vue';
import CrontabList from './components/CrontabList.vue';
import './assets/main.css'
const app = createApp(App)

app.component('cpu-gauge', CpuGauge)
app.component('user-procs', UserProcs)
app.component('crontab-list', CrontabList)
app.mount('#app')
