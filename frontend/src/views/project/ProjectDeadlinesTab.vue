<script setup>
import { computed, onMounted, ref } from 'vue'

import * as claimsApi from '@/api/claims'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { EM_DASH } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * The notice and submission deadlines this project's claims face.
 *
 * The standard form's periods, as the project's own contract changes them.
 * Changes read from the Particular Conditions arrive as suggestions and apply
 * only once confirmed here: a period misread off a scanned page would move
 * every deadline built on it.
 */
const props = defineProps({
  project: { type: Object, required: true },
})

const auth = useAuthStore()
const ui = useUiStore()

const OBLIGATIONS = {
  notice_of_claim: 'Notice of claim',
  detailed_claim: 'Detailed claim / particulars',
}
const APPLIES = { all: 'All claims', time: 'Claims for time', money: 'Claims for money' }

const effective = ref([])
const rows = ref([])
const loading = ref(true)
const error = ref(null)
const scanning = ref(false)
const lastScan = ref(null)
const busyId = ref(null)

const canEdit = computed(() => auth.canInProject(props.project.id, 'claim.edit'))
const suggestions = computed(() => rows.value.filter((row) => row.status === 'suggested'))
const confirmed = computed(() => rows.value.filter((row) => row.status === 'confirmed'))
const rejected = computed(() => rows.value.filter((row) => row.status === 'rejected'))

async function load() {
  loading.value = true
  error.value = null
  try {
    const [effectiveData, rowData] = await Promise.all([
      claimsApi.fetchEffectiveDeadlines(props.project.id),
      claimsApi.listContractDeadlines({ project: props.project.id, page_size: 200 }),
    ])
    effective.value = effectiveData.results ?? []
    rows.value = rowsOf(rowData)
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

async function scan() {
  scanning.value = true
  try {
    lastScan.value = await claimsApi.scanContractDeadlines(props.project.id)
    const found = lastScan.value.created
    ui.notifySuccess(
      found ? `${found} change${found === 1 ? '' : 's'} found` : 'No changes found',
      found
        ? 'Check each one against the page it came from, then confirm or reject it.'
        : 'The contract documents restate the standard periods, or do not mention them.',
    )
    await load()
  } catch (err) {
    ui.notifyError(err, 'Could not read the contract documents')
  } finally {
    scanning.value = false
  }
}

async function setStatus(row, status) {
  busyId.value = row.id
  try {
    await claimsApi.updateContractDeadline(row.id, { status })
    ui.notifySuccess(
      status === 'confirmed' ? 'Applied to every claim in the project' : 'Not applied',
      status === 'confirmed' ? 'Checklists and screening now use the contract’s term.' : undefined,
    )
    await load()
  } catch (err) {
    ui.notifyError(err, 'Could not update the deadline')
  } finally {
    busyId.value = null
  }
}

async function remove(row) {
  busyId.value = row.id
  try {
    // A reading from a document is kept as rejected, so the next scan does
    // not propose it again; one typed by hand is simply removed.
    if (row.source_document) await claimsApi.updateContractDeadline(row.id, { status: 'rejected' })
    else await claimsApi.deleteContractDeadline(row.id)
    await load()
  } catch (err) {
    ui.notifyError(err, 'Could not remove the amendment')
  } finally {
    busyId.value = null
  }
}

function describeChange(row) {
  const parts = []
  if (row.action === 'remove') return 'Deletes this requirement'
  if (row.period_days) parts.push(`${row.period_days} ${row.day_count || 'calendar'} days`)
  if (row.runs_from === 'notice') parts.push(`running from the notice under Clause ${row.runs_from_clause}`)
  if (row.recipient) parts.push(`to ${row.recipient}`)
  if (row.is_condition_precedent === true) parts.push('as a condition precedent')
  if (row.is_condition_precedent === false) parts.push('not a condition precedent')
  if (row.late_consequence) parts.push(`consequence: ${row.late_consequence}`)
  return parts.join('; ') || EM_DASH
}

// -- Recording an amendment by hand --------------------------------------------

const addOpen = ref(false)
const saving = ref(false)
const fieldErrors = ref({})
const EMPTY = {
  clause_number: '',
  obligation: 'notice_of_claim',
  action: 'amend',
  period_days: '',
  day_count: 'calendar',
  runs_from: '',
  runs_from_clause: '',
  applies_to: '',
  is_condition_precedent: '',
  recipient: '',
  late_consequence: '',
  note: '',
}
const form = ref({ ...EMPTY })

function openAdd(base = null) {
  form.value = base
    ? { ...EMPTY, clause_number: base.clause_number, obligation: base.obligation }
    : { ...EMPTY }
  fieldErrors.value = {}
  addOpen.value = true
}

async function submitAdd() {
  saving.value = true
  fieldErrors.value = {}
  try {
    const f = form.value
    await claimsApi.createContractDeadline({
      project: props.project.id,
      clause_number: f.clause_number.trim(),
      obligation: f.obligation,
      action: f.action,
      period_days: f.period_days === '' ? null : Number(f.period_days),
      day_count: f.period_days === '' ? '' : f.day_count,
      runs_from: f.runs_from,
      runs_from_clause: f.runs_from === 'notice' ? f.runs_from_clause.trim() : '',
      applies_to: f.applies_to,
      is_condition_precedent: f.is_condition_precedent === '' ? null : f.is_condition_precedent === 'yes',
      recipient: f.recipient.trim(),
      late_consequence: f.late_consequence.trim(),
      note: f.note.trim(),
    })
    ui.notifySuccess('Amendment recorded', 'Applied to every claim in the project.')
    addOpen.value = false
    await load()
  } catch (err) {
    fieldErrors.value = err?.fieldErrors ?? {}
    if (!Object.keys(fieldErrors.value).length) ui.notifyError(err, 'Could not record the amendment')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <EmptyState
      v-if="!project.contract_edition"
      icon="pi pi-book"
      title="No conditions of contract declared"
      description="Set the project's contract edition first. The standard periods every amendment is measured against depend on it."
    />

    <template v-else>
      <section class="surface deadlines__section">
        <div class="row between gap-3 wrap">
          <div class="stack gap-1">
            <p class="section-title">Notice and claim deadlines</p>
            <p class="text-xs text-muted">
              The periods every claim in this project is checked against: the standard form's,
              unless this contract changes them. Where the Particular Conditions differ, they govern.
            </p>
          </div>
          <div v-if="canEdit" class="row gap-2">
            <AppButton
              size="sm"
              variant="secondary"
              icon="pi pi-search"
              label="Read the contract documents"
              :loading="scanning"
              @click="scan"
            />
            <AppButton size="sm" variant="ghost" icon="pi pi-plus" label="Record an amendment" @click="openAdd()" />
          </div>
        </div>

        <p v-if="lastScan" class="text-xs text-muted">
          Read {{ lastScan.documents_read }} contract document{{ lastScan.documents_read === 1 ? '' : 's' }}
          ({{ lastScan.passages_read }} passages): {{ lastScan.found }} difference{{ lastScan.found === 1 ? '' : 's' }}
          from the standard form, {{ lastScan.created }} new.
        </p>

        <LoadingSkeleton v-if="loading" variant="block" height="160px" />
        <ErrorState v-else-if="error" :error="error" title="Could not load the deadlines" @retry="load" />

        <div v-else class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th scope="col">Clause</th>
                <th scope="col">Requirement</th>
                <th scope="col">Period</th>
                <th scope="col">Applies to</th>
                <th scope="col">Source</th>
                <th v-if="canEdit" scope="col"><span class="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in effective" :key="`${row.clause_number}:${row.obligation}`">
                <td class="text-mono">{{ row.clause_number }}</td>
                <td>
                  <strong>{{ row.title }}</strong>
                  <p class="text-xs text-muted">{{ OBLIGATIONS[row.obligation] }}<template v-if="row.recipient"> · to {{ row.recipient }}</template><template v-if="row.is_condition_precedent"> · condition precedent</template></p>
                  <p v-if="row.late_consequence" class="text-xs text-muted deadlines__consequence">
                    If late: {{ row.late_consequence }}
                  </p>
                </td>
                <td>
                  <template v-if="row.period_days !== null">
                    <strong>{{ row.period_days }} {{ row.day_count }} days</strong>
                    <span
                      v-if="row.standard_period_days && row.standard_period_days !== row.period_days"
                      class="text-xs text-muted deadlines__was"
                    >
                      standard {{ row.standard_period_days }}
                    </span>
                    <p class="text-xs text-muted">
                      from {{ row.runs_from === 'notice' ? `the notice under ${row.runs_from_clause}` : 'the event / awareness' }}
                    </p>
                  </template>
                  <span v-else class="text-danger">Deleted</span>
                </td>
                <td class="text-xs">{{ APPLIES[row.applies_to] || row.applies_to }}</td>
                <td>
                  <StatusBadge v-if="row.is_amended" status="amended" :label="row.source" size="sm" />
                  <span v-else class="text-xs text-muted">Standard form</span>
                </td>
                <td v-if="canEdit">
                  <AppButton size="sm" variant="ghost" icon="pi pi-pencil" aria-label="Amend" title="Record an amendment to this" @click="openAdd(row)" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="!loading && suggestions.length" class="surface deadlines__section">
        <p class="section-title">Found in the contract documents — check before applying</p>
        <p class="text-xs text-muted">
          Read automatically, not yet applied. Open the page each came from, and confirm only what the
          contract actually says.
        </p>
        <article v-for="row in suggestions" :key="row.id" class="deadlines__suggestion">
          <div class="row between gap-2 wrap">
            <p class="text-sm">
              <strong>Clause {{ row.clause_number }}</strong> · {{ OBLIGATIONS[row.obligation] }}:
              {{ describeChange(row) }}
            </p>
            <div v-if="canEdit" class="row gap-2">
              <AppButton
                size="sm"
                variant="ghost"
                label="Reject"
                :disabled="busyId === row.id"
                @click="setStatus(row, 'rejected')"
              />
              <AppButton
                size="sm"
                variant="primary"
                icon="pi pi-check"
                label="Confirm and apply"
                :loading="busyId === row.id"
                @click="setStatus(row, 'confirmed')"
              />
            </div>
          </div>
          <p v-if="row.note" class="text-xs text-warning">{{ row.note }}</p>
          <blockquote class="deadlines__excerpt">{{ row.source_excerpt }}</blockquote>
          <RouterLink
            v-if="row.source_document"
            class="text-xs"
            :to="{
              name: 'document-viewer',
              params: { documentId: row.source_document },
              query: row.source_page ? { page: String(row.source_page) } : {},
            }"
          >
            {{ row.source_document_title }}<template v-if="row.source_page">, page {{ row.source_page }}</template>
            <i class="pi pi-external-link" aria-hidden="true" />
          </RouterLink>
        </article>
      </section>

      <section v-if="!loading && (confirmed.length || rejected.length)" class="surface deadlines__section">
        <p class="section-title">Amendments on record</p>
        <ul class="deadlines__list">
          <li v-for="row in [...confirmed, ...rejected]" :key="row.id" class="row between gap-2 wrap">
            <span class="text-sm">
              <StatusBadge :status="row.status" size="sm" />
              Clause {{ row.clause_number }} · {{ OBLIGATIONS[row.obligation] }}: {{ describeChange(row) }}
              <span class="text-xs text-muted">
                — {{ row.source_document_title ? `${row.source_document_title}${row.source_page ? `, p. ${row.source_page}` : ''}` : 'recorded by hand' }}<template v-if="row.confirmed_by_name">, confirmed by {{ row.confirmed_by_name }}</template>
              </span>
            </span>
            <div v-if="canEdit" class="row gap-2">
              <AppButton
                v-if="row.status === 'rejected'"
                size="sm"
                variant="ghost"
                label="Apply after all"
                :disabled="busyId === row.id"
                @click="setStatus(row, 'confirmed')"
              />
              <AppButton
                v-else
                size="sm"
                variant="ghost"
                icon="pi pi-times"
                label="Stop applying"
                :loading="busyId === row.id"
                @click="remove(row)"
              />
            </div>
          </li>
        </ul>
      </section>
    </template>

    <AppDialog
      v-model="addOpen"
      title="Record an amendment from the contract"
      description="What this contract says, where it differs from the standard form. Applied to every claim in the project as soon as it is saved."
      size="lg"
      :busy="saving"
    >
      <div class="stack gap-4">
        <div class="row gap-3 wrap">
          <FormField label="Clause" for-id="cd-clause" class="grow" required :error="fieldErrors.clause_number">
            <input id="cd-clause" v-model="form.clause_number" class="field-input" type="text" placeholder="e.g. 53.1" />
          </FormField>
          <FormField label="Requirement" for-id="cd-obligation" class="grow" :error="fieldErrors.obligation">
            <select id="cd-obligation" v-model="form.obligation" class="field-input">
              <option v-for="(label, value) in OBLIGATIONS" :key="value" :value="value">{{ label }}</option>
            </select>
          </FormField>
          <FormField label="The contract" for-id="cd-action" class="grow" :error="fieldErrors.action">
            <select id="cd-action" v-model="form.action" class="field-input">
              <option value="amend">Changes the standard requirement</option>
              <option value="add">Adds a requirement</option>
              <option value="remove">Deletes the requirement</option>
            </select>
          </FormField>
        </div>

        <template v-if="form.action !== 'remove'">
          <div class="row gap-3 wrap">
            <FormField
              label="Period (days)"
              for-id="cd-period"
              class="grow"
              :required="form.action === 'add'"
              :error="fieldErrors.period_days"
              :hint="form.action === 'amend' ? 'Leave blank to keep the standard period.' : null"
            >
              <input id="cd-period" v-model="form.period_days" class="field-input" type="number" min="1" />
            </FormField>
            <FormField label="Counted in" for-id="cd-daycount" class="grow">
              <select id="cd-daycount" v-model="form.day_count" class="field-input">
                <option value="calendar">Calendar days</option>
                <option value="working">Working days</option>
              </select>
            </FormField>
            <FormField label="Runs from" for-id="cd-runs" class="grow" :error="fieldErrors.runs_from_clause">
              <select id="cd-runs" v-model="form.runs_from" class="field-input">
                <option value="">Unchanged</option>
                <option value="awareness">The event / awareness of it</option>
                <option value="notice">A notice under another clause</option>
              </select>
              <input
                v-if="form.runs_from === 'notice'"
                v-model="form.runs_from_clause"
                class="field-input deadlines__inline"
                type="text"
                placeholder="Clause of that notice, e.g. 53.1"
                aria-label="Clause of the notice it runs from"
              />
            </FormField>
          </div>

          <div class="row gap-3 wrap">
            <FormField label="Applies to" for-id="cd-applies" class="grow">
              <select id="cd-applies" v-model="form.applies_to" class="field-input">
                <option value="">Unchanged</option>
                <option v-for="(label, value) in APPLIES" :key="value" :value="value">{{ label }}</option>
              </select>
            </FormField>
            <FormField label="Condition precedent?" for-id="cd-cp" class="grow" hint="Does late notice bar the claim?">
              <select id="cd-cp" v-model="form.is_condition_precedent" class="field-input">
                <option value="">Unchanged</option>
                <option value="yes">Yes — late notice bars the claim</option>
                <option value="no">No</option>
              </select>
            </FormField>
            <FormField label="Given to" for-id="cd-recipient" class="grow">
              <input id="cd-recipient" v-model="form.recipient" class="field-input" type="text" placeholder="e.g. the Engineer" />
            </FormField>
          </div>

          <FormField
            label="What the contract says follows from lateness"
            for-id="cd-consequence"
            hint="Optional. Shown whenever this deadline is missed."
          >
            <textarea id="cd-consequence" v-model="form.late_consequence" class="field-input" rows="2" />
          </FormField>
        </template>

        <FormField label="Where in the contract" for-id="cd-note" hint="E.g. Particular Conditions, page 12.">
          <input id="cd-note" v-model="form.note" class="field-input" type="text" />
        </FormField>
      </div>

      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="saving" @click="addOpen = false" />
        <AppButton
          variant="primary"
          label="Record and apply"
          :loading="saving"
          :disabled="!form.clause_number.trim()"
          @click="submitAdd"
        />
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.deadlines__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
}

.deadlines__was {
  margin-left: var(--space-1);
  text-decoration: line-through;
}

.deadlines__consequence {
  max-width: 520px;
}

.deadlines__suggestion {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-surface-sunken);
  border-radius: var(--radius-md);
}

.deadlines__excerpt {
  margin: 0;
  padding-left: var(--space-3);
  font-size: var(--text-xs);
  line-height: 1.5;
  color: var(--color-text-secondary);
  border-left: 3px solid var(--color-border);
}

.deadlines__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.deadlines__inline {
  margin-top: var(--space-2);
}
</style>
