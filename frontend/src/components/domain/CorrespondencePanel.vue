<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import * as claimsApi from '@/api/claims'
import * as correspondenceApi from '@/api/correspondence'
import * as projectsApi from '@/api/projects'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatDate } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * Project correspondence, and the Notices asserted over it.
 *
 * Whether a letter is a Notice under a provision is a contested question, so it
 * is recorded as an assertion against a clause — with its own confirmation flag
 * — rather than as a property of the letter.
 */
const props = defineProps({
  projectId: { type: String, required: true },
  claimId: { type: String, default: null },
})

const emit = defineEmits(['changed'])

const auth = useAuthStore()
const ui = useUiStore()

const KINDS = [
  ['letter', 'Letter'],
  ['email', 'Email'],
  ['notice', 'Notice'],
  ['instruction', "Engineer's Instruction"],
  ['determination', 'Determination'],
  ['response', 'Response'],
  ['minutes', 'Meeting minutes'],
  ['rfi', 'Request for Information'],
  ['site_instruction', 'Site instruction'],
  ['other', 'Other'],
]

const DIRECTIONS = [
  ['', 'Not recorded'],
  ['inbound', 'Inbound'],
  ['outbound', 'Outbound'],
  ['internal', 'Internal'],
]

const rows = ref([])
const parties = ref([])
const claims = ref([])
const loading = ref(true)
const error = ref(null)
const kindFilter = ref('')

const canManage = computed(() => auth.canInProject(props.projectId, 'correspondence.manage'))

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = await correspondenceApi.listCorrespondence({
      project: props.projectId,
      claim: props.claimId || undefined,
      kind: kindFilter.value || undefined,
      page_size: 100,
    })
    rows.value = rowsOf(data)
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

watch(kindFilter, load)

async function loadReferenceData() {
  if (!parties.value.length) {
    try {
      parties.value = await projectsApi.listParties(props.projectId)
    } catch {
      parties.value = []
    }
  }
  if (!claims.value.length) {
    try {
      claims.value = rowsOf(await claimsApi.listClaims({ project: props.projectId, page_size: 100 }))
    } catch {
      claims.value = []
    }
  }
}

// -- Create -----------------------------------------------------------------

const createOpen = ref(false)
const saving = ref(false)
const fieldErrors = ref({})
const EMPTY = {
  reference: '',
  subject: '',
  kind: 'letter',
  direction: '',
  sender: '',
  recipient: '',
  sent_date: '',
  received_date: '',
  summary: '',
  clause_references: '',
}
const form = ref({ ...EMPTY })

async function openCreate() {
  form.value = { ...EMPTY }
  fieldErrors.value = {}
  createOpen.value = true
  await loadReferenceData()
}

async function submitCreate() {
  if (saving.value || !form.value.subject.trim()) return
  saving.value = true
  fieldErrors.value = {}
  try {
    const payload = {
      project: props.projectId,
      subject: form.value.subject.trim(),
      reference: form.value.reference.trim(),
      kind: form.value.kind,
      direction: form.value.direction,
      summary: form.value.summary.trim(),
      clause_references: form.value.clause_references
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean),
    }
    for (const field of ['sender', 'recipient', 'sent_date', 'received_date']) {
      if (form.value[field]) payload[field] = form.value[field]
    }
    await correspondenceApi.createCorrespondence(payload)
    ui.notifySuccess('Correspondence recorded')
    createOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    fieldErrors.value = err?.fieldErrors ?? {}
    if (!Object.keys(fieldErrors.value).length) ui.notifyError(err, 'Could not record it')
  } finally {
    saving.value = false
  }
}

// -- Notice -----------------------------------------------------------------

const noticeOpen = ref(false)
const noticeTarget = ref(null)
const noticeForm = ref({ clause_number: '', claim: '', is_confirmed_notice: false, notes: '' })
const noticeSaving = ref(false)
const noticeErrors = ref({})

async function openNotice(item) {
  noticeTarget.value = item
  noticeForm.value = {
    clause_number: '',
    claim: props.claimId || '',
    is_confirmed_notice: false,
    notes: '',
  }
  noticeErrors.value = {}
  noticeOpen.value = true
  await loadReferenceData()
}

async function submitNotice() {
  if (noticeSaving.value || !noticeForm.value.clause_number.trim()) return
  noticeSaving.value = true
  noticeErrors.value = {}
  try {
    await correspondenceApi.createNotice(noticeTarget.value.id, {
      claim: noticeForm.value.claim || null,
      clauseNumber: noticeForm.value.clause_number.trim(),
      isConfirmedNotice: noticeForm.value.is_confirmed_notice,
      notes: noticeForm.value.notes,
    })
    ui.notifySuccess('Recorded as a Notice', 'Notice timing is recomputed from the recorded dates.')
    noticeOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    const field = err?.details?.field
    noticeErrors.value = field ? { [field]: err.message } : {}
    if (!field) ui.notifyError(err, 'Could not record the Notice')
  } finally {
    noticeSaving.value = false
  }
}

async function removeNotice(notice) {
  try {
    await correspondenceApi.deleteNotice(notice.id)
    await load()
    emit('changed')
  } catch (err) {
    ui.notifyError(err, 'Could not remove the Notice')
  }
}

onMounted(load)
</script>

<template>
  <div class="stack gap-4">
    <div class="toolbar between">
      <select v-model="kindFilter" class="field-input correspondence__filter" aria-label="Kind">
        <option value="">All correspondence</option>
        <option v-for="[value, label] in KINDS" :key="value" :value="value">{{ label }}</option>
      </select>
      <AppButton
        v-if="canManage"
        variant="primary"
        size="sm"
        icon="pi pi-plus"
        label="Record correspondence"
        @click="openCreate"
      />
    </div>

    <div class="surface correspondence__panel">
      <div v-if="loading" class="correspondence__pad">
        <LoadingSkeleton variant="table" :rows="5" :columns="5" />
      </div>

      <ErrorState v-else-if="error" :error="error" title="Could not load correspondence" @retry="load" />

      <EmptyState
        v-else-if="!rows.length"
        icon="pi pi-envelope"
        title="No correspondence recorded"
        description="Letters, emails, instructions and determinations. Recording them is what makes notice timing computable and a chronology possible."
        :action-label="canManage ? 'Record correspondence' : null"
        action-icon="pi pi-plus"
        @action="openCreate"
      />

      <div v-else class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Subject</th>
              <th scope="col">Kind</th>
              <th scope="col">From → to</th>
              <th scope="col">Sent</th>
              <th scope="col">Received</th>
              <th scope="col">Notices</th>
              <th v-if="canManage" scope="col"><span class="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in rows" :key="item.id">
              <td>
                <p class="correspondence__subject">{{ item.subject }}</p>
                <p v-if="item.reference" class="text-xs text-mono text-muted">{{ item.reference }}</p>
              </td>
              <td>{{ item.kind }}</td>
              <td class="text-xs">
                {{ item.sender_name || item.sender_raw || EM_DASH }} →
                {{ item.recipient_name || item.recipient_raw || EM_DASH }}
              </td>
              <td>{{ formatDate(item.sent_date) }}</td>
              <td>{{ formatDate(item.received_date) }}</td>
              <td>
                <div v-if="item.notices.length" class="stack gap-1">
                  <span v-for="notice in item.notices" :key="notice.id" class="row gap-1">
                    <StatusBadge
                      :tone="notice.is_confirmed_notice ? 'success' : 'warning'"
                      :label="'Clause ' + notice.clause_number"
                      size="sm"
                    />
                    <AppButton
                      v-if="canManage"
                      size="sm"
                      variant="ghost"
                      icon="pi pi-times"
                      aria-label="Remove notice"
                      @click="removeNotice(notice)"
                    />
                  </span>
                </div>
                <span v-else class="text-xs text-muted">Not asserted as a Notice</span>
              </td>
              <td v-if="canManage">
                <AppButton
                  size="sm"
                  variant="ghost"
                  icon="pi pi-flag"
                  label="Mark as Notice"
                  @click="openNotice(item)"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <AppDialog
      v-model="createOpen"
      title="Record correspondence"
      description="Both dates matter: most notice provisions turn on receipt, and where receipt is unknown the sent date is used and the assumption is recorded."
      size="lg"
      :busy="saving"
    >
      <div class="stack gap-4">
        <FormField label="Subject" for-id="corr-subject" required :error="fieldErrors.subject">
          <input id="corr-subject" v-model="form.subject" class="field-input" type="text" />
        </FormField>

        <div class="row gap-3 wrap">
          <FormField label="Reference" for-id="corr-reference" class="grow" :error="fieldErrors.reference">
            <input id="corr-reference" v-model="form.reference" class="field-input" type="text" />
          </FormField>
          <FormField label="Kind" for-id="corr-kind" class="grow">
            <select id="corr-kind" v-model="form.kind" class="field-input">
              <option v-for="[value, label] in KINDS" :key="value" :value="value">{{ label }}</option>
            </select>
          </FormField>
          <FormField label="Direction" for-id="corr-direction" class="grow">
            <select id="corr-direction" v-model="form.direction" class="field-input">
              <option v-for="[value, label] in DIRECTIONS" :key="value" :value="value">
                {{ label }}
              </option>
            </select>
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField label="From" for-id="corr-sender" class="grow" :error="fieldErrors.sender">
            <select id="corr-sender" v-model="form.sender" class="field-input">
              <option value="">Not recorded</option>
              <option v-for="party in parties" :key="party.id" :value="party.id">
                {{ party.name }} ({{ party.role }})
              </option>
            </select>
          </FormField>
          <FormField label="To" for-id="corr-recipient" class="grow" :error="fieldErrors.recipient">
            <select id="corr-recipient" v-model="form.recipient" class="field-input">
              <option value="">Not recorded</option>
              <option v-for="party in parties" :key="party.id" :value="party.id">
                {{ party.name }} ({{ party.role }})
              </option>
            </select>
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField label="Sent" for-id="corr-sent" class="grow" :error="fieldErrors.sent_date">
            <input id="corr-sent" v-model="form.sent_date" class="field-input" type="date" />
          </FormField>
          <FormField
            label="Received"
            for-id="corr-received"
            class="grow"
            :error="fieldErrors.received_date"
            hint="Preferred for notice timing where it is known."
          >
            <input id="corr-received" v-model="form.received_date" class="field-input" type="date" />
          </FormField>
        </div>

        <FormField
          label="Clauses referred to"
          for-id="corr-clauses"
          hint="Comma separated."
          :error="fieldErrors.clause_references"
        >
          <input id="corr-clauses" v-model="form.clause_references" class="field-input" type="text" />
        </FormField>

        <FormField label="Summary" for-id="corr-summary" :error="fieldErrors.summary">
          <textarea id="corr-summary" v-model="form.summary" class="field-input" rows="2" />
        </FormField>
      </div>

      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="saving" @click="createOpen = false" />
        <AppButton
          variant="primary"
          label="Record"
          :loading="saving"
          :disabled="!form.subject.trim()"
          @click="submitCreate"
        />
      </template>
    </AppDialog>

    <AppDialog
      v-model="noticeOpen"
      title="Record as a Notice"
      description="Name the provision this is said to be given under. A notice under one provision does not satisfy another."
      :busy="noticeSaving"
    >
      <div class="stack gap-4">
        <p class="text-sm">{{ noticeTarget?.subject }}</p>

        <FormField
          label="Given under clause"
          for-id="notice-clause"
          required
          :error="noticeErrors.clause_number"
          hint="For example 20.2.1"
        >
          <input id="notice-clause" v-model="noticeForm.clause_number" class="field-input" type="text" />
        </FormField>

        <FormField label="For claim" for-id="notice-claim" :error="noticeErrors.claim">
          <select id="notice-claim" v-model="noticeForm.claim" class="field-input">
            <option value="">Not tied to a claim</option>
            <option v-for="claim in claims" :key="claim.id" :value="claim.id">
              {{ claim.reference || claim.title }}
            </option>
          </select>
        </FormField>

        <label class="row gap-2 text-sm">
          <input v-model="noticeForm.is_confirmed_notice" type="checkbox" />
          A person has confirmed this satisfies the provision
        </label>
        <p class="text-xs text-muted">
          Until confirmed, compliance findings carry a caveat rather than treating it as a valid
          Notice.
        </p>

        <FormField label="Notes" for-id="notice-notes">
          <textarea id="notice-notes" v-model="noticeForm.notes" class="field-input" rows="2" />
        </FormField>
      </div>

      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="noticeSaving" @click="noticeOpen = false" />
        <AppButton
          variant="primary"
          label="Record Notice"
          :loading="noticeSaving"
          :disabled="!noticeForm.clause_number.trim()"
          @click="submitNotice"
        />
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.correspondence__panel {
  overflow: hidden;
}

.correspondence__pad {
  padding: var(--space-4);
}

.correspondence__filter {
  width: 220px;
}

.correspondence__subject {
  font-weight: var(--weight-medium);
}
</style>
