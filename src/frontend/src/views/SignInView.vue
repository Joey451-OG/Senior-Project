<script setup lang="ts">
    import { computed, ref, shallowRef, watch } from 'vue'
    import QRCode from 'qrcode'
    import type { PasswordValidationStatus } from 'firebase/auth'
    import { useAuth, password_requirements_message, type TotpEnrollment } from '@/composables/useAuth'
    import WaruLogo from '@/components/WaruLogo.vue'

    const {
        phase, email, error, notice,
        signInWithEmail, signInWithGoogle, signUpWithEmail, sendPasswordReset, checkPassword,
        submitMfaCode, cancelMfaChallenge, resendVerification, recheckEmailVerified,
        startTotpEnrollment, finishTotpEnrollment, retrySession, signOut,
    } = useAuth()

    // Which email form is showing while signed out
    type FormMode = 'sign-in' | 'sign-up' | 'reset'
    const formMode = ref<FormMode>('sign-in')
    const emailInput = ref('')
    const passwordInput = ref('')
    const confirmPasswordInput = ref('')
    const codeInput = ref('')
    const formError = ref<string | null>(null)

    // Disables every button while a Firebase call is in flight
    const isBusy = ref(false)
    async function whileBusy(action: () => Promise<unknown>) {
        isBusy.value = true
        try {
            await action()
        } finally {
            isBusy.value = false
        }
    }

    const titles: Record<string, string> = {
        'sign-in': 'Sign in to Waru',
        'sign-up': 'Create an account',
        'reset': 'Reset your password',
        'mfa-challenge': 'Two-factor authentication',
        'verify-email': 'Verify your email',
        'enroll-mfa': 'Set up two-factor authentication',
        'session-error': 'Can\'t reach the dashboard',
    }
    const title = computed(() => titles[phase.value === 'signed-out' ? formMode.value : phase.value] ?? 'Waru')
    const shownError = computed(() => formError.value ?? error.value)

    function switchForm(mode: FormMode) {
        formMode.value = mode
        formError.value = null
        error.value = null
        notice.value = null
    }

    // --- Password policy (sign-up only; enforced again by Firebase when the account is created) ---

    const passwordStatus = shallowRef<PasswordValidationStatus | null>(null)
    // Checks finish out of order while typing; only the newest one may update the checklist
    let passwordCheckSequence = 0

    watch([passwordInput, formMode], async ([password, mode]) => {
        if (mode !== 'sign-up') return
        const sequence = ++passwordCheckSequence
        try {
            const status = await checkPassword(password)
            if (sequence === passwordCheckSequence) passwordStatus.value = status
        } catch (err) {
            console.error('Could not load the password policy', err)
        }
    }, { immediate: true })

    // Only the rules the policy turns on; Firebase leaves the others undefined
    const passwordRequirements = computed(() => {
        const status = passwordStatus.value
        if (!status) return []
        const options = status.passwordPolicy.customStrengthOptions
        const requirements = [
            { label: `At least ${options.minPasswordLength ?? 6} characters`, isMet: status.meetsMinPasswordLength },
            { label: 'An uppercase letter', isMet: status.containsUppercaseLetter },
            { label: 'A lowercase letter', isMet: status.containsLowercaseLetter },
            { label: 'A number', isMet: status.containsNumericCharacter },
            { label: 'A special character', isMet: status.containsNonAlphanumericCharacter },
        ]
        // The maximum is too large to be worth listing until it's actually exceeded
        if (status.meetsMaxPasswordLength === false) {
            requirements.push({ label: `At most ${options.maxPasswordLength} characters`, isMet: false })
        }
        return requirements.filter((requirement) => requirement.isMet !== undefined)
    })

    function submitEmailForm() {
        formError.value = null
        if (formMode.value === 'reset') return whileBusy(() => sendPasswordReset(emailInput.value))
        if (formMode.value === 'sign-up') {
            if (passwordStatus.value && !passwordStatus.value.isValid) {
                formError.value = password_requirements_message
                return
            }
            if (passwordInput.value !== confirmPasswordInput.value) {
                formError.value = 'Passwords don\'t match.'
                return
            }
            return whileBusy(() => signUpWithEmail(emailInput.value, passwordInput.value))
        }
        return whileBusy(() => signInWithEmail(emailInput.value, passwordInput.value))
    }

    // Authenticator codes are 6 digits; drop the spaces some apps display them with
    const cleanedCode = computed(() => codeInput.value.replace(/\s/g, ''))
    const isCodeComplete = computed(() => /^\d{6}$/.test(cleanedCode.value))

    async function submitCode() {
        if (!isCodeComplete.value) return
        await whileBusy(() => phase.value === 'enroll-mfa'
            ? finishTotpEnrollment(cleanedCode.value)
            : submitMfaCode(cleanedCode.value))
        codeInput.value = ''
    }

    // --- Authenticator enrollment ---

    const enrollment = ref<TotpEnrollment | null>(null)
    const qrCodeDataUrl = ref<string | null>(null)
    const isSecretCopied = ref(false)

    async function beginEnrollment() {
        enrollment.value = null
        qrCodeDataUrl.value = null
        await whileBusy(async () => {
            enrollment.value = await startTotpEnrollment()
            if (enrollment.value) {
                qrCodeDataUrl.value = await QRCode.toDataURL(enrollment.value.otpauthUrl, { margin: 1, width: 200 })
            }
        })
    }

    async function copySecret() {
        if (!enrollment.value) return
        await navigator.clipboard.writeText(enrollment.value.secretKey)
        isSecretCopied.value = true
        setTimeout(() => { isSecretCopied.value = false }, 2000)
    }

    // Leaving any step clears the half-typed code, and reaching enrollment starts it
    watch(phase, (newPhase) => {
        codeInput.value = ''
        formError.value = null
        if (newPhase === 'enroll-mfa') beginEnrollment()
    }, { immediate: true })
</script>

<template>
    <main class="sign-in-page">
        <section class="card sign-in-card">
            <header class="sign-in-header">
                <WaruLogo :size="48" />
                <h1>{{ title }}</h1>
            </header>

            <p v-if="shownError" class="notice error" role="alert">{{ shownError }}</p>
            <p v-if="notice" class="notice ok" role="status">{{ notice }}</p>

            <!-- Signed out: email form plus the Google button -->
            <template v-if="phase === 'signed-out'">
                <form class="form" @submit.prevent="submitEmailForm">
                    <label>
                        Email
                        <input v-model="emailInput" type="email" autocomplete="email" required />
                    </label>
                    <label v-if="formMode !== 'reset'">
                        Password
                        <input v-model="passwordInput" type="password" required
                               :autocomplete="formMode === 'sign-up' ? 'new-password' : 'current-password'" />
                    </label>
                    <ul v-if="formMode === 'sign-up' && passwordRequirements.length" class="requirements"
                        aria-label="Password requirements">
                        <li v-for="requirement in passwordRequirements" :key="requirement.label"
                            :class="{ met: requirement.isMet }">
                            <span aria-hidden="true">{{ requirement.isMet ? '✓' : '○' }}</span>
                            {{ requirement.label }}
                        </li>
                    </ul>
                    <label v-if="formMode === 'sign-up'">
                        Confirm password
                        <input v-model="confirmPasswordInput" type="password" autocomplete="new-password" required />
                    </label>
                    <button type="submit" class="primary" :disabled="isBusy">
                        {{ formMode === 'sign-up' ? 'Create account' : formMode === 'reset' ? 'Send reset link' : 'Sign in' }}
                    </button>
                </form>

                <p class="form-links">
                    <template v-if="formMode === 'sign-in'">
                        <button type="button" class="link" @click="switchForm('sign-up')">Create an account</button>
                        <button type="button" class="link" @click="switchForm('reset')">Forgot password?</button>
                    </template>
                    <button v-else type="button" class="link" @click="switchForm('sign-in')">Back to sign in</button>
                </p>

                <div class="divider"><span>or</span></div>

                <div class="providers">
                    <button type="button" class="secondary" :disabled="isBusy" @click="whileBusy(signInWithGoogle)">
                        Continue with Google
                    </button>
                </div>
            </template>

            <!-- Signed in with a first factor; Firebase is waiting for the TOTP code -->
            <form v-else-if="phase === 'mfa-challenge'" class="form" @submit.prevent="submitCode">
                <p class="muted">Enter the 6-digit code from your authenticator app.</p>
                <input v-model="codeInput" class="code-input mono" inputmode="numeric" autocomplete="one-time-code"
                       maxlength="7" placeholder="123456" aria-label="Authentication code" autofocus />
                <button type="submit" class="primary" :disabled="isBusy || !isCodeComplete">Verify</button>
                <p class="form-links">
                    <button type="button" class="link" @click="cancelMfaChallenge">Use a different account</button>
                </p>
            </form>

            <!-- Firebase only allows a second factor on accounts with a verified email -->
            <div v-else-if="phase === 'verify-email'" class="form">
                <p class="muted">
                    Open the verification link sent to <span class="mono">{{ email }}</span>, then continue.
                    Your account needs a verified email before two-factor authentication can be set up.
                </p>
                <button type="button" class="primary" :disabled="isBusy" @click="whileBusy(recheckEmailVerified)">
                    I've verified my email
                </button>
                <button type="button" class="secondary" :disabled="isBusy" @click="whileBusy(resendVerification)">
                    Send verification email
                </button>
                <p class="form-links">
                    <button type="button" class="link" @click="signOut()">Sign out</button>
                </p>
            </div>

            <!-- Every account must enroll an authenticator app before reaching the dashboard -->
            <form v-else-if="phase === 'enroll-mfa'" class="form" @submit.prevent="submitCode">
                <p class="muted">
                    Waru requires two-factor authentication. Scan this code with an authenticator app
                    (Google Authenticator, Authy, 1Password, ...), then enter the 6-digit code it shows.
                </p>
                <div class="qr">
                    <img v-if="qrCodeDataUrl" :src="qrCodeDataUrl" width="200" height="200" alt="Authenticator QR code" />
                    <span v-else class="muted">{{ isBusy ? 'Generating…' : '' }}</span>
                </div>
                <div v-if="enrollment" class="secret">
                    <span class="muted">Or enter this key:</span>
                    <code class="mono">{{ enrollment.secretKey }}</code>
                    <button type="button" class="link" @click="copySecret">{{ isSecretCopied ? 'Copied' : 'Copy' }}</button>
                </div>
                <button v-if="!enrollment && !isBusy" type="button" class="secondary" @click="beginEnrollment">
                    Try again
                </button>
                <input v-model="codeInput" class="code-input mono" inputmode="numeric" autocomplete="one-time-code"
                       maxlength="7" placeholder="123456" aria-label="Authentication code" :disabled="!enrollment" />
                <button type="submit" class="primary" :disabled="isBusy || !enrollment || !isCodeComplete">
                    Turn on two-factor authentication
                </button>
                <p class="form-links">
                    <button type="button" class="link" @click="signOut()">Sign out</button>
                </p>
            </form>

            <!-- Signed in with Firebase, but the dashboard API didn't answer -->
            <div v-else-if="phase === 'session-error'" class="form">
                <button type="button" class="primary" :disabled="isBusy" @click="whileBusy(retrySession)">Try again</button>
                <p class="form-links">
                    <button type="button" class="link" @click="signOut()">Sign out</button>
                </p>
            </div>
        </section>
    </main>
</template>

<style scoped>
    .sign-in-page {
        display: grid;
        place-items: center;
        min-height: 100vh;
        padding: 24px 16px;
    }

    .sign-in-card {
        width: min(400px, 100%);
        padding: 28px;
    }

    .sign-in-header {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 12px;
        margin-bottom: 20px;
        text-align: center;
    }

    h1 {
        margin: 0;
        font-size: 1.25rem;
        font-weight: 650;
    }

    .form {
        display: flex;
        flex-direction: column;
        gap: 12px;
    }

    .form p {
        margin: 0;
    }

    label {
        display: flex;
        flex-direction: column;
        gap: 4px;
        font-size: 0.85rem;
        font-weight: 600;
    }

    input {
        padding: 8px 10px;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface-2);
        color: var(--text);
        font: inherit;
        font-weight: 400;
    }

    input:focus {
        outline: 2px solid color-mix(in srgb, var(--accent) 50%, transparent);
        border-color: var(--accent);
    }

    .requirements {
        list-style: none;
        margin: -4px 0 0;
        padding: 0;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 2px 12px;
        font-size: 0.8rem;
        color: var(--text-muted);
    }

    .requirements li.met {
        color: var(--ok);
    }

    .code-input {
        text-align: center;
        font-size: 1.4rem;
        letter-spacing: 0.3em;
    }

    button.primary,
    button.secondary {
        padding: 9px 14px;
        border-radius: 8px;
        font: inherit;
        font-size: 0.9rem;
        font-weight: 600;
        cursor: pointer;
    }

    button.primary {
        border: 1px solid var(--accent);
        background: var(--accent);
        color: #fff;
    }

    button.secondary {
        border: 1px solid var(--border);
        background: var(--surface);
        color: var(--text);
    }

    button.secondary:hover:not(:disabled) {
        border-color: var(--accent);
        color: var(--accent);
    }

    button:disabled {
        cursor: default;
        opacity: 0.5;
    }

    .link {
        padding: 0;
        border: none;
        background: none;
        color: var(--accent);
        font: inherit;
        font-size: 0.85rem;
        cursor: pointer;
    }

    .link:hover {
        text-decoration: underline;
    }

    .form-links {
        display: flex;
        justify-content: space-between;
        margin: 12px 0 0;
    }

    .form-links:has(> :only-child) {
        justify-content: center;
    }

    .divider {
        display: flex;
        align-items: center;
        gap: 12px;
        margin: 18px 0;
        color: var(--text-muted);
        font-size: 0.8rem;
    }

    .divider::before,
    .divider::after {
        content: "";
        flex: 1;
        border-top: 1px solid var(--border);
    }

    .providers {
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    /* Always dark-on-white so authenticator apps can scan it in either theme */
    .qr {
        display: grid;
        place-items: center;
        align-self: center;
        width: 216px;
        height: 216px;
        padding: 8px;
        border-radius: 8px;
        background: #fff;
    }

    .secret {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        justify-content: center;
        gap: 6px;
        font-size: 0.8rem;
    }

    .secret code {
        overflow-wrap: anywhere;
    }
</style>
