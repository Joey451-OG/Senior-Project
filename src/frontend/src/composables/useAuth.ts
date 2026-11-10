import { ref, shallowRef } from 'vue'
import { createGlobalState, useEventListener } from '@vueuse/core'
import { FirebaseError } from 'firebase/app'
import {
    createUserWithEmailAndPassword,
    getMultiFactorResolver,
    GoogleAuthProvider,
    multiFactor,
    onIdTokenChanged,
    sendEmailVerification,
    sendPasswordResetEmail,
    signInWithEmailAndPassword,
    signInWithPopup,
    signOut as firebaseSignOut,
    TotpMultiFactorGenerator,
    validatePassword,
    type MultiFactorError,
    type MultiFactorResolver,
    type PasswordValidationStatus,
    type TotpSecret,
    type User,
} from 'firebase/auth'
import { auth } from '@/globals/firebase'
import { type AuthStatus } from '@/globals/projectTypes'
import { domain, port } from '@/globals/projectVars'

const auth_url: string = `http://${domain}:${port}/auth`

/**
 * Where the signed-in user is on the way to the dashboard:
 *   signed-out     → sign-in form
 *   mfa-challenge  → signed in with a first factor, waiting for the TOTP code
 *   verify-email   → Firebase only allows a second factor once the email is verified
 *   enroll-mfa     → every account must set up an authenticator app before going further
 *   session-error  → signed in with Firebase, but the dashboard API couldn't be reached
 *   pending        → fully signed in, but not listed in /etc/waru/auth-accounts
 *   authorized     → listed, so the dashboard is shown
 */
export type AuthPhase =
    | 'loading' | 'signed-out' | 'mfa-challenge' | 'verify-email' | 'enroll-mfa'
    | 'session-error' | 'pending' | 'authorized'

export interface TotpEnrollment {
    // otpauth:// URL to render as a QR code
    otpauthUrl: string,
    // The same secret, for typing into an authenticator app by hand
    secretKey: string
}

// Popups the user closed on purpose aren't errors worth showing
const ignoredErrorCodes = new Set(['auth/popup-closed-by-user', 'auth/cancelled-popup-request'])

export const password_requirements_message = 'Please follow the password requirements.'

const readableErrors: Record<string, string> = {
    'auth/account-exists-with-different-credential': 'This email already uses a different sign-in method. Sign in with that one instead.',
    'auth/email-already-in-use': 'An account with this email already exists. Sign in instead.',
    'auth/invalid-credential': 'Incorrect email or password.',
    'auth/invalid-email': 'That email address isn\'t valid.',
    'auth/weak-password': password_requirements_message,
    'auth/password-does-not-meet-requirements': password_requirements_message,
    'auth/too-many-requests': 'Too many attempts. Wait a few minutes and try again.',
    'auth/popup-blocked': 'Your browser blocked the sign-in popup. Allow popups for this site and try again.',
    'auth/invalid-verification-code': 'That code didn\'t match. Check your authenticator app and try again.',
    'auth/missing-code': 'Enter the 6-digit code from your authenticator app.',
    'auth/unverified-email': 'Verify your email before setting up two-factor authentication.',
    'auth/requires-recent-login': 'Your sign-in is too old to change security settings. Sign in again.',
    'auth/unauthorized-domain': 'This site\'s domain isn\'t in the Firebase project\'s authorized domains.',
    'auth/operation-not-allowed': 'This sign-in method isn\'t enabled in the Firebase project.',
    'auth/network-request-failed': 'Couldn\'t reach Firebase. Check your connection.',
}

function describeError(err: unknown): string | null {
    if (err instanceof FirebaseError) {
        if (ignoredErrorCodes.has(err.code)) return null
        return readableErrors[err.code] ?? err.message
    }
    return err instanceof Error ? err.message : String(err)
}

// FastAPI errors carry a readable {"detail": "..."} body
async function readErrorDetail(response: Response, fallback: string): Promise<string> {
    const body = await response.json().catch(() => null)
    return typeof body?.detail === 'string' ? body.detail : fallback
}

// One instance for the whole app, so every component sees the same sign-in state
export const useAuth = createGlobalState(() => {
    const phase = ref<AuthPhase>('loading')
    const user = shallowRef<User | null>(null)
    const email = ref<string | null>(null)
    const error = ref<string | null>(null)
    // Non-error feedback, e.g. "verification email sent"
    const notice = ref<string | null>(null)

    let mfaResolver: MultiFactorResolver | null = null
    let totpSecret: TotpSecret | null = null
    // Token changes can overlap (hourly refresh, re-checks); only the newest one may set the phase
    let syncSequence = 0

    function applyAuthStatus(authStatus: AuthStatus) {
        email.value = authStatus.email
        phase.value = authStatus.is_administrator ? 'authorized' : 'pending'
    }

    // Hands the current ID token to the API, which keeps it as the session cookie
    async function postSession(currentUser: User, sequence: number) {
        try {
            const response = await fetch(`${auth_url}/session`, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id_token: await currentUser.getIdToken() }),
            })
            if (sequence !== syncSequence) return

            if (response.ok) {
                applyAuthStatus(await response.json() as AuthStatus)
                error.value = null
            } else if (response.status === 401 || response.status === 403) {
                // The API rejected this sign-in outright (e.g. no second factor), so start over
                error.value = await readErrorDetail(response, `Sign-in refused (${response.status})`)
                await firebaseSignOut(auth)
            } else {
                error.value = await readErrorDetail(response, `POST /auth/session failed: ${response.status}`)
                phase.value = 'session-error'
            }
        } catch (err) {
            if (sequence !== syncSequence) return
            const isAlreadyIn = phase.value === 'authorized' || phase.value === 'pending'
            if (isAlreadyIn) {
                // A failed hourly refresh shouldn't tear the dashboard down; the next one may work
                console.error('Failed to refresh the dashboard session', err)
                return
            }
            error.value = `Couldn't reach the dashboard API: ${describeError(err)}`
            phase.value = 'session-error'
        }
    }

    async function syncPhase(currentUser: User | null) {
        const sequence = ++syncSequence
        user.value = currentUser
        email.value = currentUser?.email ?? null

        if (!currentUser) {
            // While waiting for a TOTP code, Firebase reports nobody signed in yet
            if (phase.value !== 'mfa-challenge') phase.value = 'signed-out'
            return
        }
        if (!currentUser.emailVerified) {
            phase.value = 'verify-email'
            return
        }
        if (multiFactor(currentUser).enrolledFactors.length === 0) {
            phase.value = 'enroll-mfa'
            return
        }
        await postSession(currentUser, sequence)
    }

    // Fires on sign-in, sign-out and every token refresh (about hourly while the page is open)
    onIdTokenChanged(auth, syncPhase)

    // A laptop waking from sleep may hold an expired token; asking for it refreshes it if so,
    // which fires onIdTokenChanged and renews the session cookie
    useEventListener(document, 'visibilitychange', () => {
        if (document.visibilityState === 'visible') auth.currentUser?.getIdToken().catch(() => {})
    })

    // Runs a Firebase action, turning a failure into a readable error message
    async function attempt(action: () => Promise<unknown>): Promise<boolean> {
        error.value = null
        notice.value = null
        try {
            await action()
            return true
        } catch (err) {
            error.value = describeError(err)
            return false
        }
    }

    // A sign-in that needs the second factor fails with multi-factor-auth-required
    async function signInWith(signIn: () => Promise<unknown>) {
        await attempt(async () => {
            try {
                await signIn()
            } catch (err) {
                if (!(err instanceof FirebaseError) || err.code !== 'auth/multi-factor-auth-required') throw err
                mfaResolver = getMultiFactorResolver(auth, err as MultiFactorError)
                phase.value = 'mfa-challenge'
            }
        })
    }

    function signInWithEmail(emailAddress: string, password: string) {
        return signInWith(() => signInWithEmailAndPassword(auth, emailAddress, password))
    }

    function signInWithGoogle() {
        return signInWith(() => signInWithPopup(auth, new GoogleAuthProvider()))
    }

    // Checks a new password against the password policy set in the Firebase console
    // (fetched once, then checked locally), so the form's rules always match the server's
    function checkPassword(password: string): Promise<PasswordValidationStatus> {
        return validatePassword(auth, password)
    }

    async function signUpWithEmail(emailAddress: string, password: string) {
        const isCreated = await attempt(async () => {
            const passwordStatus = await checkPassword(password)
            if (!passwordStatus.isValid) throw new Error(password_requirements_message)
            const credential = await createUserWithEmailAndPassword(auth, emailAddress, password)
            await sendEmailVerification(credential.user)
        })
        if (isCreated) notice.value = `Verification email sent to ${emailAddress}.`
    }

    async function sendPasswordReset(emailAddress: string) {
        const isSent = await attempt(() => sendPasswordResetEmail(auth, emailAddress))
        if (isSent) notice.value = `If ${emailAddress} has an account, a reset link is on its way.`
    }

    async function submitMfaCode(code: string) {
        const resolver = mfaResolver
        if (!resolver) return
        await attempt(async () => {
            const totpHint = resolver.hints.find((hint) => hint.factorId === TotpMultiFactorGenerator.FACTOR_ID)
            if (!totpHint) throw new Error('This account has no authenticator app set up.')
            await resolver.resolveSignIn(TotpMultiFactorGenerator.assertionForSignIn(totpHint.uid, code))
            // onIdTokenChanged takes it from here
            mfaResolver = null
            phase.value = 'loading'
        })
    }

    function cancelMfaChallenge() {
        mfaResolver = null
        error.value = null
        phase.value = 'signed-out'
    }

    async function resendVerification() {
        const currentUser = auth.currentUser
        if (!currentUser) return
        const isSent = await attempt(() => sendEmailVerification(currentUser))
        if (isSent) notice.value = `Verification email sent to ${currentUser.email}.`
    }

    async function recheckEmailVerified() {
        const currentUser = auth.currentUser
        if (!currentUser) return
        await attempt(async () => {
            await currentUser.reload()
            if (!currentUser.emailVerified) throw new Error('That email isn\'t verified yet. Open the link in the email first.')
            // The current token still says email_verified: false, and the API trusts only the token
            await currentUser.getIdToken(true)
            await syncPhase(currentUser)
        })
    }

    async function startTotpEnrollment(): Promise<TotpEnrollment | null> {
        const currentUser = auth.currentUser
        if (!currentUser) return null
        let enrollment: TotpEnrollment | null = null
        const isStarted = await attempt(async () => {
            const session = await multiFactor(currentUser).getSession()
            totpSecret = await TotpMultiFactorGenerator.generateSecret(session)
            enrollment = {
                otpauthUrl: totpSecret.generateQrCodeUrl(currentUser.email ?? undefined, 'Waru'),
                secretKey: totpSecret.secretKey,
            }
        })
        if (!isStarted && error.value === readableErrors['auth/requires-recent-login']) await signOut()
        return enrollment
    }

    async function finishTotpEnrollment(code: string) {
        const currentUser = auth.currentUser
        const secret = totpSecret
        if (!currentUser || !secret) return
        await attempt(async () => {
            await multiFactor(currentUser).enroll(
                TotpMultiFactorGenerator.assertionForEnrollment(secret, code), 'Authenticator app'
            )
            totpSecret = null
            // Enrolling issues a token that carries the second-factor claim the API requires
            await syncPhase(currentUser)
        })
    }

    // Lets the "not yet an administrator" page notice when the account is added to the file
    async function recheckAdministrator() {
        const currentUser = auth.currentUser
        if (!currentUser) return
        try {
            const response = await fetch(`${auth_url}/me`, { credentials: 'include' })
            if (response.ok) {
                applyAuthStatus(await response.json() as AuthStatus)
                return
            }
        } catch (err) {
            console.error('GET /auth/me failed', err)
            return
        }
        // The session cookie expired or went missing; post the current token again
        await syncPhase(currentUser)
    }

    async function retrySession() {
        error.value = null
        phase.value = 'loading'
        await syncPhase(auth.currentUser)
    }

    async function signOut() {
        mfaResolver = null
        totpSecret = null
        notice.value = null
        try {
            await fetch(`${auth_url}/logout`, { method: 'POST', credentials: 'include' })
        } catch (err) {
            // The cookie expires on its own within the hour; signing out of Firebase matters more
            console.error('POST /auth/logout failed', err)
        }
        await firebaseSignOut(auth)
    }

    return {
        phase, user, email, error, notice,
        signInWithEmail, signInWithGoogle, signUpWithEmail, sendPasswordReset, checkPassword,
        submitMfaCode, cancelMfaChallenge, resendVerification, recheckEmailVerified,
        startTotpEnrollment, finishTotpEnrollment, recheckAdministrator, retrySession, signOut,
    }
})
