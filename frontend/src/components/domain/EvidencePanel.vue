<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import * as claimsApi from '@/api/claims'
import * as documentsApi from '@/api/documents'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import Pager from '@/components/common/Pager.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useListQuery } from '@/composables/useListQuery'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatRelative } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * Evidence linked to claims.
 *
 * Contradicting evidence is first-class: a register that records only what
 * supports the claim is a case summary, not an assessment. Setting relevance is
 * a review, and the server attributes it to the person who did it.
 */
const props = defineProps({
  projectId: { type: String, default: null },
  claimId: { type: String, default: null },
  issues: { type: Array, default: () => [] },
  showClaim: { type: Boolean, default: false },
})

const emit = defineEmits(['changed'])

const auth = useAuthStore()
const ui = useUiStore()

const RELEVANCE = [
  ['supports', 'Supports'],
  ['contradicts', 'Contradicts'],
  ['neutral', 'Neutral'],
  ['unassessed', 'Unassessed'],
]

const WEIGHTS = [
  ['', 'Not weighed'],
  ['strong', 'Strong'],
  ['moderate', 'Moderate'],
  ['weak', 'Weak'],
]

const PAGE_SIZE = 25

const { relevance: relevanceFilter, page, snapshot } = useListQuery({ relevance: '', page: 1 })

const rows = ref([])
const total = ref(null)
const documents = ref([])
const loading = ref(true)
const error = ref(null)

const pageCount = computed(() =>
  total.value == null ? 1 : Math.max(1, Math.ceil(total.value / PAGE_SIZE))
)

const canManage = computed(() => auth.canInProject(props.projectId, 'evidence.manage'))

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = await claimsApi.listEvidence({
      claim: props.claimId || undefined,
      project: props.claimId ? undefined : props.projectId || undefined,
      relevance: relevanceFilter.value || undefined,
      page: page.value,
      page_size: PAGE_SIZE,
    })
    rows.value = rowsOf(data)
    total.value = Array.isArray(data) ? data.length : (data?.count ?? rows.value.length)
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

// The filter and page live in the URL, so they survive opening a record and
// coming back.
watch(snapshot, load)
watch(() => props.claimId, load)

// -- Adding -----------------------------------------------------------------

const addOpen = ref(false)
const saving = ref(false)
const fieldErrors = ref({})
const EMPTY = {
  title: '',
  description: '',
  document: '',
  page_number: '',
  excerpt: '',
  relevance: 'unassessed',
  weight: '',
  issue: '',
}
const form = ref({ ...EMPTY })

async function openAdd() {
  form.value = { ...EMPTY }
  fieldErrors.value = {}
  addOpen.value = true
  if (documents.value.length || !props.projectId) return
  try {
    const data = await documentsApi.listDocuments({ project: props.projectId, page_size: 100 })
    documents.value = rowsOf(data)
  } catch {
    documents.value = []
  }
}

async function submit() {
  if (saving.value || !form.value.title.trim() || !props.claimId) return
  saving.value = true
  fieldErrors.value = {}
  try {
    const payload = {
      claim: props.claimId,
      title: form.value.title.trim(),
      description: form.value.description.trim(),
      excerpt: form.value.excerpt.trim(),
      relevance: form.value.relevance,
      weight: form.value.weight,
    }
    if (form.value.document) payload.document = form.value.document
    if (form.value.page_number) payload.page_number = form.value.page_number
    if (form.value.issue) payload.issue = form.value.issue

    await claimsApi.createEvidence(payload)
    ui.notifySuccess('Evidence linked')
    addOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    fieldErrors.value = err?.fieldErrors ?? {}
    if (!Object.keys(fieldErrors.value).length) ui.notifyError(err, 'Could not link the evidence')
  } finally {
    saving.value = false
  }
}

async function setRelevance(item, relevance) {
  try {
    const updated = await claimsApi.updateEvidence(item.id, { relevance })
    Object.assign(item, updated)
    emit('changed')
  } catch (err) {
    ui.notifyError(err, 'Could not update the assessment')
  }
}

/**
 * Which issue the evidence goes to, editable after the fact.
 *
 * This is what ties evidence to a required element, so getting it wrong at
 * creation would otherwise mean deleting the record and filing it again.
 */
async function setIssue(item, issue) {
  try {
    const updated = await claimsApi.updateEvidence(item.id, { issue: issue || null })
    Object.assign(item, updated)
    emit('changed')
  } catch (err) {
    ui.notifyError(err, 'Could not change what the evidence shows')
  }
}

async function remove(item) {
  try {
    await claimsApi.deleteEvidence(item.id)
    rows.value = rows.value.filter((row) => row.id !== item.id)
    emit('changed')
  } catch (err) {
    ui.notifyError(err, 'Could not remove the evidence')
  }
}

onMounted(load)
</script>

<template>
  <div class="stack gap-4">
    <div class="toolbar between">
      <select v-model="relevanceFilter" class="field-input evidence__filter" aria-label="Relevance">
        <option value="">All evidence</option>
        <option v-for="[value, label] in RELEVANCE" :key="value" :value="value">{{ label }}</option>
      </select>
      <AppButton
        v-if="canManage && claimId"
        variant="primary"
        size="sm"
        icon="pi pi-plus"
        label="Link evidence"
        @click="openAdd"
      />
    </div>

    <div class="surface evidence__panel">
      <div v-if="loading" class="evidence__pad">
        <LoadingSkeleton variant="table" :rows="4" :columns="4" />
      </div>

      <ErrorState v-else-if="error" :error="error" title="Could not load evidence" @retry="load" />

      <EmptyState
        v-else-if="!rows.length"
        icon="pi pi-paperclip"
        title="No evidence recorded"
        description="Link the records that establish what happened — programmes, site diaries, correspondence, measurements. A claim is not proven because it is asserted."
        :action-label="canManage && claimId ? 'Link evidence' : null"
        action-icon="pi pi-plus"
        @action="openAdd"
      />

      <div v-else class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Evidence</th>
              <th v-if="showClaim" scope="col">Claim</th>
              <th scope="col">Source</th>
              <th v-if="issues.length" scope="col">What it shows</th>
              <th scope="col">Assessment</th>
              <th scope="col">Reviewed</th>
              <th v-if="canManage" scope="col"><span class="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in rows" :key="item.id">
              <td>
                <p class="evidence__title">{{ item.title }}</p>
                <p v-if="item.excerpt" class="evidence__excerpt">{{ item.excerpt }}</p>
              </td>
              <td v-if="showClaim" class="text-xs">
                {{ item.claim_reference || item.claim_title || EM_DASH }}
              </td>
              <td>
                <RouterLink
                  v-if="item.document"
                  class="text-xs"
                  :to="{
                    name: 'document-viewer',
                    params: { documentId: item.document },
                    query: item.page_number ? { page: String(item.page_number) } : {},
                  }"
                >
                  Open document<template v-if="item.page_number">, p.{{ item.page_number }}</template>
                </RouterLink>
                <span v-else class="text-xs text-muted">No document linked</span>
              </td>
              <td v-if="issues.length">
                <select
                  v-if="canManage"
                  class="field-input evidence__relevance"
                  :value="item.issue || ''"
                  aria-label="What it shows"
                  @change="setIssue(item, $event.target.value)"
                >
                  <option value="">Not tied to an issue</option>
                  <option v-for="issue in issues" :key="issue.id" :value="issue.id">
                    {{ issue.title }}
                  </option>
                </select>
                <span v-else class="text-xs text-muted">
                  {{ issues.find((issue) => issue.id === item.issue)?.title || 'Not tied to an issue' }}
                </span>
              </td>
              <td>
                <select
                  v-if="canManage"
                  class="field-input evidence__relevance"
                  :value="item.relevance"
                  aria-label="Relevance"
                  @change="setRelevance(item, $event.target.value)"
                >
                  <option v-for="[value, label] in RELEVANCE" :key="value" :value="value">
                    {{ label }}
                  </option>
                </select>
                <StatusBadge v-else :status="item.relevance" size="sm" />
              </td>
              <td>
                <span v-if="item.reviewed_at" class="text-xs text-muted">
                  {{ formatRelative(item.reviewed_at) }}
                </span>
                <StatusBadge v-else tone="warning" label="Not reviewed" size="sm" />
              </td>
              <td v-if="canManage">
                <AppButton
                  size="sm"
                  variant="ghost"
                  icon="pi pi-trash"
                  aria-label="Remove"
                  @click="remove(item)"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <Pager
        v-if="!loading && !error && rows.length"
        :page="page"
        :page-count="pageCount"
        :total="total"
        unit="records"
        @update:page="page = $event"
      />
    </div>

    <AppDialog
      v-model="addOpen"
      title="Link evidence"
      description="Point at the record that establishes the fact, and say whether it supports or contradicts the claim."
      :busy="saving"
    >
      <div class="stack gap-4">
        <FormField label="Title" for-id="evidence-title" required :error="fieldErrors.title">
          <input id="evidence-title" v-model="form.title" class="field-input" type="text" />
        </FormField>

        <div class="row gap-3 wrap">
          <FormField label="Document" for-id="evidence-document" class="grow" :error="fieldErrors.document">
            <select id="evidence-document" v-model="form.document" class="field-input">
              <option value="">Not linked to a document</option>
              <option v-for="document in documents" :key="document.id" :value="document.id">
                {{ document.title }}
              </option>
            </select>
          </FormField>
          <FormField label="Page" for-id="evidence-page" class="grow" :error="fieldErrors.page_number">
            <input id="evidence-page" v-model="form.page_number" class="field-input" type="number" min="1" />
          </FormField>
        </div>

        <FormField
          label="What it shows"
          for-id="evidence-issue"
          :error="fieldErrors.issue"
          hint="Ties the evidence to one issue, which is how gaps are computed."
        >
          <select id="evidence-issue" v-model="form.issue" class="field-input">
            <option value="">Not tied to an issue</option>
            <option v-for="issue in issues" :key="issue.id" :value="issue.id">
              {{ issue.title }}
            </option>
          </select>
        </FormField>

        <div class="row gap-3 wrap">
          <FormField label="Relevance" for-id="evidence-relevance" class="grow">
            <select id="evidence-relevance" v-model="form.relevance" class="field-input">
              <option v-for="[value, label] in RELEVANCE" :key="value" :value="value">
                {{ label }}
              </option>
            </select>
          </FormField>
          <FormField label="Weight" for-id="evidence-weight" class="grow">
            <select id="evidence-weight" v-model="form.weight" class="field-input">
              <option v-for="[value, label] in WEIGHTS" :key="value" :value="value">
                {{ label }}
              </option>
            </select>
          </FormField>
        </div>

        <FormField label="Excerpt" for-id="evidence-excerpt" :error="fieldErrors.excerpt">
          <textarea id="evidence-excerpt" v-model="form.excerpt" class="field-input" rows="2" />
        </FormField>

        <FormField label="Note" for-id="evidence-description" :error="fieldErrors.description">
          <textarea id="evidence-description" v-model="form.description" class="field-input" rows="2" />
        </FormField>
      </div>

      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="saving" @click="addOpen = false" />
        <AppButton
          variant="primary"
          label="Link evidence"
          :loading="saving"
          :disabled="!form.title.trim()"
          @click="submit"
        />
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.evidence__panel {
  overflow: hidden;
}

.evidence__pad {
  padding: var(--space-4);
}

.evidence__filter {
  width: 200px;
}

.evidence__relevance {
  width: 150px;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
}

.evidence__title {
  font-weight: var(--weight-medium);
}

.evidence__excerpt {
  max-width: 420px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
