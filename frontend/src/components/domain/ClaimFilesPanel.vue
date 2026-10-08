<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import * as claimsApi from '@/api/claims'
import AppButton from '@/components/common/AppButton.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import AttachDocumentDialog from '@/components/domain/AttachDocumentDialog.vue'
import ClaimBundleDialog from '@/components/domain/ClaimBundleDialog.vue'
import NoticeIntakeDialog from '@/components/domain/NoticeIntakeDialog.vue'
import { useUiStore } from '@/stores/ui'
import { formatDate } from '@/utils/format'

/**
 * The documents filed on a claim, in the three places they go.
 *
 * Each place answers a different question: the claim document is the claim
 * as submitted, a notice is checked against its deadline, and a supporting
 * document counts towards the element it proves. Filing a document here is
 * what makes the checklist and the deadline check see it.
 */
const props = defineProps({
  claimId: { type: String, required: true },
  projectId: { type: String, required: true },
  canEdit: { type: Boolean, default: false },
  /** Deadline findings, for the clause choices offered when filing. */
  findings: { type: Array, default: () => [] },
  /** Evidence elements, for "what it proves". */
  elements: { type: Array, default: () => [] },
  /** Opens the dialog for this role on mount, e.g. from a checklist link. */
  openRole: { type: String, default: '' },
  openElement: { type: String, default: '' },
})

const emit = defineEmits(['changed', 'opened'])

const ui = useUiStore()

const SECTIONS = [
  {
    role: 'claim_submission',
    title: 'Claim document',
    empty: 'No claim document yet. Add the submitted claim with the date it was sent.',
    add: 'Add claim document',
    icon: 'pi pi-file',
  },
  {
    role: 'notice',
    title: 'Notices',
    empty: 'No notices yet. Add each notice with the date it was sent and the clause it was given under.',
    add: 'Add notice',
    icon: 'pi pi-send',
  },
  {
    role: 'supporting',
    title: 'Supporting documents',
    empty: 'No supporting documents yet. Add the records that prove the event, its effect and its cost.',
    add: 'Add supporting document',
    icon: 'pi pi-paperclip',
  },
]

const OBLIGATION_LABELS = {
  notice_of_claim: 'Notice of claim',
  detailed_claim: 'Detailed claim / particulars',
}

const rows = ref([])
const loading = ref(true)
const error = ref(null)
const dialogRole = ref('')
const dialogElement = ref('')
const removing = ref(null)
const noticeOpen = ref(false)
const bundleOpen = ref(false)

async function onRead() {
  await load()
  emit('changed')
}

const dialogOpen = computed({
  get: () => Boolean(dialogRole.value),
  set: (open) => {
    if (!open) dialogRole.value = ''
  },
})

const byRole = computed(() => {
  const groups = Object.fromEntries(SECTIONS.map((section) => [section.role, []]))
  for (const row of rows.value) (groups[row.role] ??= []).push(row)
  return groups
})

const elementLabels = computed(() =>
  Object.fromEntries(props.elements.map((element) => [element.code, element.label])),
)

async function load() {
  loading.value = true
  error.value = null
  try {
    rows.value = (await claimsApi.listClaimFiles(props.claimId)).results ?? []
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

function open(role, element = '') {
  dialogElement.value = element
  dialogRole.value = role
}

async function onAttached(result) {
  rows.value = result.results ?? rows.value
  emit('changed')
}

async function remove(row) {
  removing.value = row.id
  try {
    await claimsApi.detachClaimFile(props.claimId, row.id)
    rows.value = rows.value.filter((item) => item.id !== row.id)
    ui.notifySuccess('Removed from the claim', 'The document is still in the project.')
    emit('changed')
  } catch (err) {
    ui.notifyError(err, 'Could not remove the document')
  } finally {
    removing.value = null
  }
}

function evidenceLabel(item) {
  const category = item.issue_category
  const element = category === 'notice_compliance' ? 'notice' : category === 'entitlement' ? 'responsibility' : category
  return elementLabels.value[element] || item.issue_title || 'Not tied to an element'
}

watch(() => props.claimId, load)

onMounted(async () => {
  await load()
  if (props.openRole && props.canEdit) {
    open(props.openRole, props.openElement)
    emit('opened')
  }
})

defineExpose({ open, reload: load })
</script>

<template>
  <div class="stack gap-4">
    <LoadingSkeleton v-if="loading" variant="block" height="240px" />
    <ErrorState v-else-if="error" :error="error" title="Could not load the claim's documents" @retry="load" />

    <template v-else>
      <section v-if="canEdit" class="surface files__quick">
        <div class="stack gap-1">
          <p class="section-title">Let the documents be read for you</p>
          <p class="text-xs text-muted">
            Upload the whole claim submission and it is split into the claim letter and its
            supporting documents. Upload a notice and its date and clause are read off the letter.
            You check every reading before it is saved.
          </p>
        </div>
        <div class="row gap-2 wrap">
          <AppButton size="sm" variant="primary" icon="pi pi-sparkles" label="Upload whole claim" @click="bundleOpen = true" />
          <AppButton size="sm" variant="secondary" icon="pi pi-sparkles" label="Upload notice" @click="noticeOpen = true" />
        </div>
      </section>

      <section v-for="section in SECTIONS" :key="section.role" class="surface files__section">
        <div class="row between gap-2 wrap">
          <div class="row gap-2">
            <i :class="section.icon" class="text-muted" aria-hidden="true" />
            <p class="section-title">{{ section.title }}</p>
            <span class="text-xs text-muted">{{ byRole[section.role].length }}</span>
          </div>
          <AppButton
            v-if="canEdit"
            size="sm"
            variant="secondary"
            icon="pi pi-upload"
            :label="section.add"
            @click="open(section.role)"
          />
        </div>

        <p v-if="!byRole[section.role].length" class="text-sm text-muted">{{ section.empty }}</p>

        <ul v-else class="files__list">
          <li v-for="row in byRole[section.role]" :key="row.id" class="files__item">
            <div class="files__main">
              <RouterLink
                class="files__title"
                :to="{ name: 'document-viewer', params: { documentId: row.document.id } }"
              >
                {{ row.document.title }}
              </RouterLink>

              <p v-for="notice in row.notices" :key="notice.id" class="text-xs text-muted">
                {{ OBLIGATION_LABELS[notice.obligation] || 'Notice' }} under Clause
                {{ notice.clause_number }} · sent {{ formatDate(notice.sent_date) }}
                <template v-if="notice.received_date">
                  · received {{ formatDate(notice.received_date) }}
                </template>
                <span v-if="!notice.is_confirmed_notice" class="text-warning">
                  · not confirmed as a contractual notice
                </span>
              </p>

              <p v-for="item in row.evidence" :key="item.id" class="text-xs text-muted">
                <template v-if="item.page_number">{{ item.title }} — </template>
                Proves: {{ evidenceLabel(item) }} ·
                <span :class="{ 'text-warning': item.relevance === 'contradicts' }">{{ item.relevance }}</span>
              </p>
              <p v-if="row.note" class="text-xs text-muted">{{ row.note }}</p>
            </div>

            <div class="row gap-2">
              <StatusBadge
                v-if="row.document.processing_status && row.document.processing_status !== 'completed'"
                :status="row.document.processing_status"
                size="sm"
              />
              <AppButton
                v-if="canEdit"
                size="sm"
                variant="ghost"
                icon="pi pi-times"
                aria-label="Remove from this claim"
                title="Remove from this claim (the document stays in the project)"
                :loading="removing === row.id"
                @click="remove(row)"
              />
            </div>
          </li>
        </ul>
      </section>
    </template>

    <NoticeIntakeDialog
      v-model="noticeOpen"
      :project-id="projectId"
      :claim-id="claimId"
      @saved="onRead"
    />
    <ClaimBundleDialog
      v-model="bundleOpen"
      :project-id="projectId"
      :claim-id="claimId"
      @applied="onRead"
    />

    <AttachDocumentDialog
      v-model="dialogOpen"
      :claim-id="claimId"
      :project-id="projectId"
      :role="dialogRole || 'supporting'"
      :findings="findings"
      :elements="elements"
      :element="dialogElement"
      @attached="onAttached"
    />
  </div>
</template>

<style scoped>
.files__quick {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px dashed var(--color-accent);
}

.files__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
}

.files__list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.files__item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border-top: 1px solid var(--color-border-subtle);
}

.files__item:first-child {
  border-top: none;
}

.files__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.files__title {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  overflow-wrap: anywhere;
}
</style>
