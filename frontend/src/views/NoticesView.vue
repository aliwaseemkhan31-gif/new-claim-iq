<script setup>
import { computed, ref, watch } from 'vue'

import * as claimsApi from '@/api/claims'
import * as correspondenceApi from '@/api/correspondence'
import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import NoticeIntakeDialog from '@/components/domain/NoticeIntakeDialog.vue'
import ProjectPicker from '@/components/domain/ProjectPicker.vue'
import { useProjectSelection } from '@/composables/useProjectSelection'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatDate } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * The notices register.
 *
 * Every notice given in a project, with the date on the letter, the clause it
 * was given under, the claim it belongs to and whether it met its deadline.
 * New notices are uploaded here and read on arrival.
 */
const auth = useAuthStore()
const ui = useUiStore()
const { projectId } = useProjectSelection()

const rows = ref([])
const claims = ref([])
const loading = ref(false)
const error = ref(null)
const uploadOpen = ref(false)
const linking = ref(null)
const filter = ref('')

const STATUS = {
  compliant: ['On time', 'success'],
  late: ['Late', 'warning'],
  not_given: ['Not given', 'danger'],
  indeterminate: ['Cannot tell yet', 'warning'],
  not_counted: ['Not the deciding notice', 'neutral'],
  unlinked: ['No claim yet', 'info'],
}

const OBLIGATION = {
  notice_of_claim: 'Notice of claim',
  detailed_claim: 'Detailed particulars',
}

const canManage = computed(
  () => projectId.value && auth.canInProject(projectId.value, 'correspondence.manage'),
)

const visible = computed(() => {
  if (!filter.value) return rows.value
  return rows.value.filter((row) => row.status === filter.value)
})

const counts = computed(() => {
  const result = {}
  for (const row of rows.value) result[row.status] = (result[row.status] || 0) + 1
  return result
})

async function load() {
  if (!projectId.value) {
    rows.value = []
    return
  }
  loading.value = true
  error.value = null
  try {
    await auth.loadProjectPermissions(projectId.value)
    const [register, claimRows] = await Promise.all([
      correspondenceApi.fetchNoticeRegister(projectId.value),
      claimsApi.listClaims({ project: projectId.value, page_size: 200 }),
    ])
    rows.value = register.results ?? []
    claims.value = rowsOf(claimRows)
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

async function link(row, claimId) {
  if (!claimId) return
  linking.value = row.id
  try {
    await correspondenceApi.linkNotice(row.id, claimId)
    ui.notifySuccess('Notice linked', "Filed on the claim; its deadlines have been rechecked.")
    await load()
  } catch (err) {
    ui.notifyError(err, 'Could not link the notice')
  } finally {
    linking.value = null
  }
}

async function confirm(row) {
  try {
    await correspondenceApi.updateNotice(row.id, { is_confirmed_notice: true })
    row.is_confirmed_notice = true
  } catch (err) {
    ui.notifyError(err, 'Could not confirm the notice')
  }
}

watch(projectId, load, { immediate: true })
</script>

<template>
  <div class="page">
    <PageHeader
      title="Notices"
      description="Every notice given on the project: the date on the letter, the clause it was given under, the claim it belongs to, and whether it met its deadline. Upload a notice and it is read for you."
    >
      <template #actions>
        <ProjectPicker v-model="projectId" />
        <AppButton
          v-if="canManage"
          variant="primary"
          icon="pi pi-upload"
          label="Upload notice"
          @click="uploadOpen = true"
        />
      </template>
    </PageHeader>

    <EmptyState
      v-if="!projectId"
      icon="pi pi-folder-open"
      title="Choose a project"
      description="Notices belong to one project's contract, so the register works one project at a time."
    />

    <LoadingSkeleton v-else-if="loading" variant="block" height="280px" />
    <ErrorState v-else-if="error" :error="error" title="Could not load the notices" @retry="load" />

    <EmptyState
      v-else-if="!rows.length"
      icon="pi pi-send"
      title="No notices yet"
      description="Upload a notice letter. Its date, reference and clause are read from the letter, and you check them before it is saved."
      :action-label="canManage ? 'Upload notice' : null"
      action-icon="pi pi-upload"
      @action="uploadOpen = true"
    />

    <template v-else>
      <div class="row gap-2 wrap notices__filters">
        <button type="button" class="notices__chip" :class="{ 'is-active': !filter }" @click="filter = ''">
          All {{ rows.length }}
        </button>
        <template v-for="(meta, code) in STATUS" :key="code">
          <button
            v-if="counts[code]"
            type="button"
            class="notices__chip"
            :class="{ 'is-active': filter === code }"
            @click="filter = filter === code ? '' : code"
          >
            {{ meta[0] }} {{ counts[code] }}
          </button>
        </template>
      </div>

      <div class="surface table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Date on letter</th>
              <th scope="col">Notice</th>
              <th scope="col">Clause</th>
              <th scope="col">Claim</th>
              <th scope="col">Deadline</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in visible" :key="row.id">
              <td class="notices__date">
                <strong>{{ row.letter_date ? formatDate(row.letter_date) : EM_DASH }}</strong>
                <p v-if="row.received_date" class="text-xs text-muted">received {{ formatDate(row.received_date) }}</p>
              </td>
              <td>
                <RouterLink
                  v-if="row.document"
                  :to="{ name: 'document-viewer', params: { documentId: row.document } }"
                  class="notices__subject"
                >
                  {{ row.subject || row.document_title }}
                </RouterLink>
                <span v-else class="notices__subject">{{ row.subject }}</span>
                <p class="text-xs text-muted">
                  {{ OBLIGATION[row.obligation] || 'Notice' }}
                  <template v-if="row.reference"> · {{ row.reference }}</template>
                  <template v-if="row.sender"> · from {{ row.sender }}</template>
                  <template v-if="row.recipient"> · to {{ row.recipient }}</template>
                </p>
                <p v-if="!row.is_confirmed_notice" class="text-xs text-warning">
                  Not yet checked by a person.
                  <button v-if="canManage" type="button" class="notices__link" @click="confirm(row)">
                    Mark as checked
                  </button>
                </p>
              </td>
              <td class="text-mono">{{ row.clause_number || EM_DASH }}</td>
              <td>
                <RouterLink
                  v-if="row.claim"
                  :to="{ name: 'claim-detail', params: { claimId: row.claim }, query: { tab: 'checklist' } }"
                  class="text-sm"
                >
                  {{ row.claim_reference ? row.claim_reference + ' · ' : '' }}{{ row.claim_title }}
                </RouterLink>
                <select
                  v-else-if="canManage && claims.length"
                  class="field-input notices__select"
                  :disabled="linking === row.id"
                  aria-label="Link to a claim"
                  @change="link(row, $event.target.value)"
                >
                  <option value="">Link to a claim…</option>
                  <option v-for="claim in claims" :key="claim.id" :value="claim.id">
                    {{ claim.reference ? claim.reference + ' · ' : '' }}{{ claim.title }}
                  </option>
                </select>
                <span v-else class="text-xs text-muted">No claim</span>
              </td>
              <td class="notices__status">
                <StatusBadge
                  :label="(STATUS[row.status] || [row.status])[0]"
                  :tone="(STATUS[row.status] || [null, 'neutral'])[1]"
                  size="sm"
                />
                <p class="text-xs text-muted">{{ row.detail }}</p>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <NoticeIntakeDialog
      v-if="projectId"
      v-model="uploadOpen"
      :project-id="projectId"
      @saved="load"
    />
  </div>
</template>

<style scoped>
.notices__filters {
  margin-bottom: var(--space-1);
}

.notices__chip {
  padding: 2px var(--space-3);
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
}

.notices__chip.is-active {
  color: var(--color-accent);
  border-color: var(--color-accent);
  background: var(--color-surface-selected);
}

.notices__date {
  white-space: nowrap;
}

.notices__subject {
  font-weight: var(--weight-medium);
  overflow-wrap: anywhere;
}

.notices__select {
  min-width: 200px;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
}

.notices__status {
  max-width: 320px;
}

.notices__link {
  margin-left: var(--space-1);
  color: var(--color-accent);
  text-decoration: underline;
}
</style>
