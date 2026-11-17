import { initializeApp } from 'firebase/app'
import { getAuth, type Auth } from 'firebase/auth'
import { type FirebaseWebConfig } from '@/globals/projectTypes'
import { auth_url } from '@/globals/projectVars'

// The config comes from the backend at startup (GET /auth/config, filled from the server's
// environment file), so one build works with any Firebase project. None of it is secret.
let auth_instance: Auth | undefined

// Throws with a message fit to show on the startup error screen
export async function loadFirebaseConfig(): Promise<FirebaseWebConfig> {
    let response: Response
    try {
        response = await fetch(`${auth_url}/config`, { cache: 'no-cache' })
    } catch {
        throw new Error('Can\'t reach the Waru server.')
    }
    if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : `The Waru server answered ${response.status}.`)
    }
    return await response.json() as FirebaseWebConfig
}

// main.ts calls this once, before the app mounts
export function initFirebase(config: FirebaseWebConfig) {
    auth_instance = getAuth(initializeApp({
        apiKey: config.api_key,
        authDomain: config.auth_domain,
        projectId: config.project_id,
        appId: config.app_id,
    }))
}

export function firebaseAuth(): Auth {
    if (!auth_instance) throw new Error('Firebase used before initFirebase()')
    return auth_instance
}
