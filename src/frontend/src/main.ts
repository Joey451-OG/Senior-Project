import { createApp } from 'vue'
import App from './App.vue'
import ResourceGauge from './components/ResourceGauge.vue'
import UserProcs from './components/UserProc.vue';
import CrontabList from './components/CrontabList.vue';
import AdminCommands from './components/AdminCommands.vue';
import LoggedInUsers from './components/LoggedInUsers.vue';
import StartupErrorView from './views/StartupErrorView.vue'
import { initFirebase, loadFirebaseConfig } from './globals/firebase'
import './assets/main.css'

// Firebase has to be configured (from the backend's GET /auth/config) before anything uses it,
// so the app only mounts once that has worked; otherwise the page explains what went wrong.
async function start() {
    try {
        initFirebase(await loadFirebaseConfig())
    } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        createApp(StartupErrorView, { message }).mount('#app')
        return
    }

    const app = createApp(App)

    app.component('resource-gauge', ResourceGauge)
    app.component('user-procs', UserProcs)
    app.component('crontab-list', CrontabList)
    app.component('admin-commands', AdminCommands)
    app.component('logged-in-users', LoggedInUsers)
    app.mount('#app')
}

start()
