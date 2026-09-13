<script setup>
import { computed, ref } from 'vue'

import { useRoute, useRouter } from 'vue-router'

import AppButton from '@/components/common/AppButton.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const email = ref('')
const password = ref('')
const showPassword = ref(false)
const submitting = ref(false)
const formError = ref(null)

const fieldErrors = computed(() => formError.value?.fieldErrors ?? {})

const canSubmit = computed(
  () => email.value.trim().length > 0 && password.value.length > 0 && !submitting.value
)

/** Only ever redirect to a path within this app. */
function safeRedirect() {
  const target = route.query.redirect
  if (typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')) {
    return target
  }
  return { name: 'dashboard' }
}

async function onSubmit() {
  if (!canSubmit.value) return
  submitting.value = true
  formError.value = null
  try {
    await auth.login({ email: email.value.trim(), password: password.value })
    await router.replace(safeRedirect())
  } catch (error) {
    formError.value = error
    password.value = ''
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login">
    <div class="login__intro">
      <h1 class="login__title">Sign in</h1>
      <p class="login__subtitle">Use the credentials issued by your ClaimIQ administrator.</p>
    </div>

    <form class="login__form" novalidate @submit.prevent="onSubmit">
      <div
        v-if="formError"
        class="login__error"
        role="alert"
        data-testid="login-error"
      >
        <i class="pi pi-exclamation-circle" aria-hidden="true" />
        <div>
          <p class="login__error-message">{{ formError.message }}</p>
          <p v-if="formError.requestId" class="login__error-meta text-mono">
            Request {{ formError.requestId }}
          </p>
        </div>
      </div>

      <div class="login__field">
        <label for="email">Email</label>
        <input
          id="email"
          v-model="email"
          class="field-input"
          type="email"
          name="email"
          autocomplete="email"
          autocapitalize="none"
          spellcheck="false"
          required
          :aria-invalid="Boolean(fieldErrors.email) || undefined"
          :disabled="submitting"
        />
        <p v-if="fieldErrors.email" class="login__field-error">
          {{ [].concat(fieldErrors.email).join(' ') }}
        </p>
      </div>

      <div class="login__field">
        <label for="password">Password</label>
        <div class="login__password">
          <input
            id="password"
            v-model="password"
            class="field-input"
            :type="showPassword ? 'text' : 'password'"
            name="password"
            autocomplete="current-password"
            required
            :aria-invalid="Boolean(fieldErrors.password) || undefined"
            :disabled="submitting"
          />
          <button
            type="button"
            class="login__reveal"
            :aria-label="showPassword ? 'Hide password' : 'Show password'"
            @click="showPassword = !showPassword"
          >
            <i :class="showPassword ? 'pi pi-eye-slash' : 'pi pi-eye'" aria-hidden="true" />
          </button>
        </div>
        <p v-if="fieldErrors.password" class="login__field-error">
          {{ [].concat(fieldErrors.password).join(' ') }}
        </p>
      </div>

      <AppButton
        type="submit"
        variant="primary"
        size="lg"
        block
        :loading="submitting"
        :disabled="!canSubmit"
        label="Sign in"
      />
    </form>

    <p class="login__help">
      Locked out? Only an organization administrator can reset a password on an offline
      installation.
    </p>
  </div>
</template>

<style scoped>
.login {
  padding: var(--space-6);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-md);
}

.login__intro {
  margin-bottom: var(--space-5);
}

.login__title {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}

.login__subtitle {
  margin-top: var(--space-1);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

.login__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.login__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.login__password {
  position: relative;
}

.login__password .field-input {
  padding-right: var(--space-8);
}

.login__reveal {
  position: absolute;
  top: 50%;
  right: var(--space-2);
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  font-size: 13px;
  color: var(--color-text-muted);
  border-radius: var(--radius-sm);
}

.login__reveal:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

.login__error {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-danger);
  background: var(--color-danger-subtle);
  border: 1px solid var(--color-danger-border);
  border-radius: var(--radius-md);
}

.login__error-message {
  font-weight: var(--weight-medium);
}

.login__error-meta {
  margin-top: var(--space-1);
  color: var(--color-text-muted);
}

.login__field-error {
  font-size: var(--text-xs);
  color: var(--color-danger);
}

.login__help {
  margin-top: var(--space-5);
  padding-top: var(--space-4);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  border-top: 1px solid var(--color-border-subtle);
}
</style>
