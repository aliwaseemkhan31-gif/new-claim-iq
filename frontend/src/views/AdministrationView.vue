<script setup>
import { computed, onMounted, ref } from 'vue'

import * as adminApi from '@/api/admin'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatDateTime, formatRelative } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * Administration: who has access, what this installation can do, and what the
 * background workers are doing.
 */
const ui = useUiStore()

const TABS = [
  ['users', 'Users'],
  ['system', 'System'],
  ['jobs', 'Jobs'],
]

const tab = ref('users')
const users = ref([])
const roles = ref([])
const system = ref(null)
const jobs = ref(null)
const loading = ref(true)
const error = ref(null)

const organizationRoles = computed(() => roles.value?.organization_roles ?? [])

async function load() {
  loading.value = true
  error.value = null
  try {
    if (tab.value === 'users') {
      const [userData, roleData] = await Promise.all([adminApi.listUsers(), adminApi.listRoles()])
      users.value = rowsOf(userData)
      roles.value = roleData
    } else if (tab.value === 'system') {
      system.value = await adminApi.fetchSystemStatus()
    } else {
      jobs.value = await adminApi.listJobs()
    }
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

function switchTab(next) {
  tab.value = next
  load()
}

// -- Users ------------------------------------------------------------------

const userOpen = ref(false)
const userSaving = ref(false)
const userErrors = ref({})
const userForm = ref({ email: '', full_name: '', job_title: '', role: 'organization_member', password: '' })

function openUser() {
  userForm.value = {
    email: '',
    full_name: '',
    job_title: '',
    role: 'organization_member',
    password: '',
  }
  userErrors.value = {}
  userOpen.value = true
}

async function createUser() {
  if (userSaving.value || !userForm.value.email.trim()) return
  userSaving.value = true
  userErrors.value = {}
  try {
    await adminApi.createUser({ ...userForm.value, email: userForm.value.email.trim() })
    ui.notifySuccess('User added')
    userOpen.value = false
    await load()
  } catch (err) {
    userErrors.value = err?.details?.errors
      ? { password: err.details.errors.join(' ') }
      : (err?.fieldErrors ?? {})
    if (!Object.keys(userErrors.value).length) ui.notifyError(err, 'Could not add the user')
  } finally {
    userSaving.value = false
  }
}

async function changeRole(user, role) {
  try {
    const updated = await adminApi.updateUser(user.id, { role })
    Object.assign(user, updated)
    ui.notifySuccess('Role updated', 'It takes effect on their next request.')
  } catch (err) {
    ui.notifyError(err, 'Could not change the role')
  }
}

async function toggleActive(user) {
  try {
    const updated = await adminApi.updateUser(user.id, { is_active: !user.is_active })
    Object.assign(user, updated)
  } catch (err) {
    ui.notifyError(err, 'Could not update the account')
  }
}

const passwordOpen = ref(false)
const passwordTarget = ref(null)
const passwordValue = ref('')
const passwordSaving = ref(false)
const passwordError = ref(null)

function openPassword(user) {
  passwordTarget.value = user
  passwordValue.value = ''
  passwordError.value = null
  passwordOpen.value = true
}

async function resetPassword() {
  if (!passwordValue.value) return
  passwordSaving.value = true
  passwordError.value = null
  try {
    await adminApi.resetUserPassword(passwordTarget.value.id, passwordValue.value)
    ui.notifySuccess('Password set', 'Any lockout has been cleared.')
    passwordOpen.value = false
    await load()
  } catch (err) {
    passwordError.value = err?.details?.errors ? err.details.errors.join(' ') : err.message
  } finally {
    passwordSaving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="stack gap-2">
      <h1 class="admin__title">Administration</h1>
      <nav class="admin__tabs" aria-label="Administration sections">
        <button
          v-for="[id, label] in TABS"
          :key="id"
          type="button"
          class="admin__tab"
          :class="{ 'is-active': tab === id }"
          @click="switchTab(id)"
        >
          {{ label }}
        </button>
      </nav>
    </header>

    <div v-if="loading" class="surface admin__pad"><LoadingSkeleton variant="table" :rows="6" /></div>
    <ErrorState v-else-if="error" :error="error" title="Could not load this section" @retry="load" />

    <!-- Users -->
    <template v-else-if="tab === 'users'">
      <div class="toolbar between">
        <p class="text-xs text-muted">
          Organization roles govern administration. Access to a project's contents still comes from
          project membership.
        </p>
        <AppButton variant="primary" size="sm" icon="pi pi-user-plus" label="Add user" @click="openUser" />
      </div>

      <div class="surface admin__panel">
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th scope="col">User</th>
                <th scope="col">Organization role</th>
                <th scope="col" class="cell-numeric">Projects</th>
                <th scope="col">Last sign-in</th>
                <th scope="col">Status</th>
                <th scope="col"><span class="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="user in users" :key="user.id">
                <td>
                  <p class="admin__name">{{ user.full_name || user.email }}</p>
                  <p class="text-xs text-muted">{{ user.email }}</p>
                </td>
                <td>
                  <select
                    class="field-input admin__role"
                    :value="user.role"
                    aria-label="Role"
                    @change="changeRole(user, $event.target.value)"
                  >
                    <option v-for="role in organizationRoles" :key="role.code" :value="role.code">
                      {{ role.label }}
                    </option>
                  </select>
                </td>
                <td class="cell-numeric">{{ user.projects }}</td>
                <td>{{ user.last_login ? formatRelative(user.last_login) : EM_DASH }}</td>
                <td>
                  <StatusBadge :status="user.is_active ? 'active' : 'inactive'" size="sm" />
                  <StatusBadge v-if="user.is_locked" tone="danger" label="Locked" size="sm" />
                </td>
                <td>
                  <div class="row gap-1">
                    <AppButton size="sm" variant="ghost" icon="pi pi-key" label="Password" @click="openPassword(user)" />
                    <AppButton
                      size="sm"
                      variant="ghost"
                      :icon="user.is_active ? 'pi pi-ban' : 'pi pi-check'"
                      :label="user.is_active ? 'Disable' : 'Enable'"
                      @click="toggleActive(user)"
                    />
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <!-- System -->
    <template v-else-if="tab === 'system'">
      <div class="admin__grid">
        <section class="surface admin__card">
          <p class="section-title">Checks</p>
          <ul class="admin__list">
            <li v-for="(value, name) in system.checks" :key="name" class="row between gap-2">
              <span>{{ name }}</span>
              <span class="row gap-2">
                <span class="text-xs text-muted">{{ value.version || value.error || (value.latency_ms != null ? value.latency_ms + ' ms' : '') }}</span>
                <StatusBadge :status="value.ok ? 'ok' : 'failed'" size="sm" />
              </span>
            </li>
          </ul>
        </section>

        <section class="surface admin__card">
          <p class="section-title">AI runtime</p>
          <ul class="admin__list">
            <li class="row between gap-2">
              <span>Runtime reachable</span>
              <StatusBadge :status="system.ai_runtime.reachable ? 'ok' : 'failed'" size="sm" />
            </li>
            <li class="row between gap-2">
              <span>Language model</span>
              <span class="row gap-2">
                <span class="text-xs text-muted">{{ system.ai_runtime.llm.configured || 'not configured' }}</span>
                <StatusBadge
                  :status="system.ai_runtime.llm.installed ? 'ok' : 'failed'"
                  :label="system.ai_runtime.llm.installed ? 'Installed' : 'Missing'"
                  size="sm"
                />
              </span>
            </li>
            <li class="row between gap-2">
              <span>Embedding model</span>
              <span class="row gap-2">
                <span class="text-xs text-muted">
                  {{ system.ai_runtime.embedding.configured || 'not configured' }}
                  ({{ system.ai_runtime.embedding.dimensions }} dims)
                </span>
                <StatusBadge
                  :status="system.ai_runtime.embedding.installed ? 'ok' : 'failed'"
                  :label="system.ai_runtime.embedding.installed ? 'Installed' : 'Missing'"
                  size="sm"
                />
              </span>
            </li>
            <li class="row between gap-2">
              <span>Hardware profile</span>
              <span class="text-xs text-muted">{{ system.ai_runtime.hardware_profile }}</span>
            </li>
          </ul>
        </section>

        <section class="surface admin__card">
          <p class="section-title">OCR and workers</p>
          <ul class="admin__list">
            <li class="row between gap-2">
              <span>RapidOCR</span>
              <StatusBadge :status="system.ocr.rapidocr ? 'ok' : 'failed'" size="sm" />
            </li>
            <li class="row between gap-2">
              <span>docTR</span>
              <StatusBadge :status="system.ocr.doctr ? 'ok' : 'inactive'" size="sm" />
            </li>
            <li class="row between gap-2">
              <span>Broker</span>
              <span class="text-xs text-muted">{{ system.workers.broker }}</span>
            </li>
            <li class="row between gap-2">
              <span>Tasks run in process</span>
              <span class="text-xs text-muted">{{ system.workers.tasks_run_in_process ? 'yes' : 'no' }}</span>
            </li>
          </ul>
          <p v-if="!system.ocr.rapidocr && !system.ocr.doctr" class="text-xs text-warning">
            No OCR engine is installed. Scanned documents will fail at the OCR stage rather than
            being reported as processed with no text.
          </p>
        </section>
      </div>
    </template>

    <!-- Jobs -->
    <template v-else>
      <section class="surface admin__panel">
        <p class="section-title admin__pad">Ingestion</p>
        <EmptyState v-if="!jobs.ingestion.length" compact icon="pi pi-cog" title="No ingestion jobs" />
        <div v-else class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th scope="col">Document</th>
                <th scope="col">Project</th>
                <th scope="col">Status</th>
                <th scope="col">Stage</th>
                <th scope="col" class="cell-numeric">Progress</th>
                <th scope="col">Started</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="job in jobs.ingestion" :key="job.id">
                <td>
                  <RouterLink :to="{ name: 'document-viewer', params: { documentId: job.document_id } }">
                    {{ job.document_title }}
                  </RouterLink>
                  <p v-if="job.error_message" class="text-xs text-danger">{{ job.error_message }}</p>
                </td>
                <td class="text-xs">
                  {{ job.project || (job.is_reference_document ? 'Knowledge base' : EM_DASH) }}
                </td>
                <td><StatusBadge :status="job.status" size="sm" /></td>
                <td class="text-xs">
                  {{ job.current_stage || EM_DASH }}
                  <!-- Whole-stage progress does not move during a long OCR run. -->
                  <span v-if="job.stage_detail" class="text-muted">— {{ job.stage_detail }}</span>
                </td>
                <td class="cell-numeric">{{ job.progress_percent }}%</td>
                <td>{{ formatDateTime(job.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="surface admin__panel">
        <p class="section-title admin__pad">Claim analysis</p>
        <EmptyState v-if="!jobs.analysis.length" compact icon="pi pi-sparkles" title="No analysis runs" />
        <div v-else class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th scope="col">Claim</th>
                <th scope="col">Project</th>
                <th scope="col">Status</th>
                <th scope="col" class="cell-numeric">Progress</th>
                <th scope="col">Started</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="run in jobs.analysis" :key="run.id">
                <td>
                  <RouterLink
                    :to="{ name: 'claim-detail', params: { claimId: run.claim_id }, query: { tab: 'analysis' } }"
                  >
                    {{ run.claim }}
                  </RouterLink>
                  <p v-if="run.error_message" class="text-xs text-danger">{{ run.error_message }}</p>
                </td>
                <td class="text-xs">{{ run.project }}</td>
                <td><StatusBadge :status="run.status" size="sm" /></td>
                <td class="cell-numeric">{{ run.progress_percent }}%</td>
                <td>{{ formatDateTime(run.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <AppDialog
      v-model="userOpen"
      title="Add a user"
      description="Set an initial password; they can change it from Settings."
      :busy="userSaving"
    >
      <div class="stack gap-4">
        <FormField label="Email" for-id="user-email" required :error="userErrors.email">
          <input id="user-email" v-model="userForm.email" class="field-input" type="email" />
        </FormField>
        <FormField label="Full name" for-id="user-name" :error="userErrors.full_name">
          <input id="user-name" v-model="userForm.full_name" class="field-input" type="text" />
        </FormField>
        <FormField label="Job title" for-id="user-job" :error="userErrors.job_title">
          <input id="user-job" v-model="userForm.job_title" class="field-input" type="text" />
        </FormField>
        <FormField label="Organization role" for-id="user-role" :error="userErrors.role">
          <select id="user-role" v-model="userForm.role" class="field-input">
            <option v-for="role in organizationRoles" :key="role.code" :value="role.code">
              {{ role.label }} — {{ role.description }}
            </option>
          </select>
        </FormField>
        <FormField label="Initial password" for-id="user-password" required :error="userErrors.password">
          <input id="user-password" v-model="userForm.password" class="field-input" type="password" />
        </FormField>
      </div>
      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="userSaving" @click="userOpen = false" />
        <AppButton variant="primary" label="Add user" :loading="userSaving" @click="createUser" />
      </template>
    </AppDialog>

    <AppDialog
      v-model="passwordOpen"
      title="Set a new password"
      :description="passwordTarget ? 'For ' + passwordTarget.email : null"
      :busy="passwordSaving"
    >
      <FormField label="New password" for-id="reset-password" required :error="passwordError">
        <input id="reset-password" v-model="passwordValue" class="field-input" type="password" />
      </FormField>
      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="passwordSaving" @click="passwordOpen = false" />
        <AppButton
          variant="primary"
          label="Set password"
          :loading="passwordSaving"
          :disabled="!passwordValue"
          @click="resetPassword"
        />
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.admin__title {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}

.admin__tabs {
  display: flex;
  gap: var(--space-1);
  border-bottom: 1px solid var(--color-border);
}

.admin__tab {
  position: relative;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--color-text-secondary);
}

.admin__tab.is-active {
  color: var(--color-accent);
}

.admin__tab.is-active::after {
  content: '';
  position: absolute;
  left: var(--space-2);
  right: var(--space-2);
  bottom: -1px;
  height: 2px;
  background: var(--color-accent);
}

.admin__pad {
  padding: var(--space-4);
}

.admin__panel {
  overflow: hidden;
}

.admin__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: var(--space-4);
}

.admin__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
}

.admin__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--text-sm);
}

.admin__name {
  font-weight: var(--weight-medium);
}

.admin__role {
  width: 220px;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
}
</style>
