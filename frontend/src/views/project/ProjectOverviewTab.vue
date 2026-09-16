<script setup>
import { computed, onMounted, ref } from 'vue'

import * as knowledgeApi from '@/api/knowledge'
import * as projectsApi from '@/api/projects'
import AppButton from '@/components/common/AppButton.vue'
import AppCard from '@/components/common/AppCard.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useAuthStore } from '@/stores/auth'
import { useProjectsStore } from '@/stores/projects'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatDate, formatNumber } from '@/utils/format'

/**
 * The project at a glance: what is in its corpus, who the parties are, and
 * whether the standard form it is governed by can actually be cited.
 */
const props = defineProps({
  project: { type: Object, required: true },
})

const auth = useAuthStore()
const projects = useProjectsStore()
const ui = useUiStore()

const PARTY_ROLES = [
  ['employer', 'Employer'],
  ['contractor', 'Contractor'],
  ['engineer', 'Engineer'],
  ['subcontractor', 'Subcontractor'],
  ['consultant', 'Consultant'],
  ['supplier', 'Supplier'],
  ['other', 'Other'],
]

const summary = ref(null)
const parties = ref([])
const members = ref([])
const editions = ref([])
const loading = ref(true)
const error = ref(null)

const canEdit = computed(() => auth.canInProject(props.project.id, 'project.edit'))

const tiles = computed(() => {
  const data = summary.value
  if (!data) return []
  return [
    { label: 'Documents', value: data.documents, icon: 'pi pi-file', to: 'project-documents' },
    { label: 'Claims', value: data.claims, icon: 'pi pi-briefcase', to: 'project-claims' },
    { label: 'Correspondence', value: data.correspondence, icon: 'pi pi-envelope', to: 'project-correspondence' },
    { label: 'Evidence', value: data.evidence, icon: 'pi pi-paperclip', to: 'project-evidence' },
    { label: 'Notices', value: data.notices, icon: 'pi pi-flag', to: 'project-correspondence' },
    { label: 'Findings to review', value: data.findings_unreviewed, icon: 'pi pi-sparkles', to: 'project-claims' },
  ]
})

const knowledge = computed(() => summary.value?.knowledge_base ?? null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const [summaryData, partyData, memberData] = await Promise.all([
      projectsApi.fetchProjectSummary(props.project.id),
      projectsApi.listParties(props.project.id),
      projectsApi.listProjectMembers(props.project.id).catch(() => []),
    ])
    summary.value = summaryData
    parties.value = partyData
    members.value = memberData
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

// -- Parties ----------------------------------------------------------------

const partyOpen = ref(false)
const partySaving = ref(false)
const partyErrors = ref({})
const partyForm = ref({ id: null, name: '', role: 'contractor', short_name: '', contact_email: '' })

function openParty(party) {
  partyForm.value = party
    ? { ...party }
    : { id: null, name: '', role: 'contractor', short_name: '', contact_email: '' }
  partyErrors.value = {}
  partyOpen.value = true
}

async function saveParty() {
  if (partySaving.value || !partyForm.value.name.trim()) return
  partySaving.value = true
  partyErrors.value = {}
  try {
    const payload = {
      name: partyForm.value.name.trim(),
      role: partyForm.value.role,
      short_name: partyForm.value.short_name || '',
      contact_email: partyForm.value.contact_email || '',
    }
    if (partyForm.value.id) {
      await projectsApi.updateParty(props.project.id, partyForm.value.id, payload)
    } else {
      await projectsApi.createParty(props.project.id, payload)
    }
    partyOpen.value = false
    parties.value = await projectsApi.listParties(props.project.id)
  } catch (err) {
    partyErrors.value = err?.fieldErrors ?? {}
    if (!Object.keys(partyErrors.value).length) ui.notifyError(err, 'Could not save the party')
  } finally {
    partySaving.value = false
  }
}

async function removeParty(party) {
  try {
    await projectsApi.deleteParty(props.project.id, party.id)
    parties.value = parties.value.filter((row) => row.id !== party.id)
  } catch (err) {
    ui.notifyError(err, 'Could not remove the party')
  }
}

// -- Edition ----------------------------------------------------------------

const editionOpen = ref(false)
const editionValue = ref('')
const editionSaving = ref(false)

async function openEdition() {
  editionValue.value = props.project.contract_edition || ''
  editionOpen.value = true
  if (editions.value.length) return
  try {
    const data = await knowledgeApi.listEditions()
    editions.value = data.editions ?? []
  } catch (err) {
    ui.notifyError(err, 'Could not load editions')
  }
}

async function saveEdition() {
  editionSaving.value = true
  try {
    const edition = editions.value.find((item) => item.code === editionValue.value)
    const updated = await projectsApi.updateProject(props.project.id, {
      contract_edition: editionValue.value,
      contract_form: edition?.form_code ?? '',
    })
    projects.setActiveProject(updated)
    ui.notifySuccess('Conditions of contract set', updated.edition_label || 'Cleared')
    editionOpen.value = false
    await load()
  } catch (err) {
    ui.notifyError(err, 'Could not set the conditions of contract')
  } finally {
    editionSaving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div v-if="loading" class="grid-metrics"><LoadingSkeleton variant="metrics" :columns="4" /></div>

    <ErrorState v-else-if="error" :error="error" title="Could not load the project" @retry="load" />

    <template v-else>
      <div class="grid-metrics">
        <RouterLink
          v-for="tile in tiles"
          :key="tile.label"
          class="tile surface"
          :to="{ name: tile.to, params: { projectId: project.id } }"
        >
          <div class="row between">
            <span class="text-overline">{{ tile.label }}</span>
            <i :class="tile.icon" class="tile__icon" aria-hidden="true" />
          </div>
          <p class="tile__value">
            {{ tile.value === null || tile.value === undefined ? EM_DASH : formatNumber(tile.value) }}
          </p>
        </RouterLink>
      </div>

      <div class="overview__grid">
        <AppCard title="Conditions of contract" subtitle="What the AI may cite for this project">
          <template #actions>
            <AppButton
              v-if="canEdit"
              size="sm"
              variant="ghost"
              icon="pi pi-pencil"
              label="Set"
              @click="openEdition"
            />
          </template>

          <div class="stack gap-2">
            <p v-if="project.edition_label" class="text-sm">{{ project.edition_label }}</p>
            <p v-else class="text-sm text-warning">
              Not declared. Standard-form retrieval is refused until it is set — an edition is never
              assumed.
            </p>

            <div v-if="knowledge" class="row gap-2 wrap">
              <StatusBadge :status="knowledge.status" size="sm" />
              <RouterLink
                class="text-xs"
                :to="{ name: 'knowledge-base-detail', params: { knowledgeBaseId: knowledge.id } }"
              >
                Open the knowledge base
              </RouterLink>
              <span v-if="!knowledge.is_retrievable" class="text-xs text-warning">
                Not published, so its text cannot be cited yet.
              </span>
            </div>
            <p v-else-if="project.contract_edition" class="text-xs text-warning">
              No knowledge base exists for this edition. Answers will say that standard-form text is
              unavailable rather than guess.
            </p>
          </div>
        </AppCard>

        <AppCard title="Parties" subtitle="Who the contract is between">
          <template #actions>
            <AppButton
              v-if="canEdit"
              size="sm"
              variant="ghost"
              icon="pi pi-plus"
              label="Add"
              @click="openParty(null)"
            />
          </template>

          <ul v-if="parties.length" class="overview__list">
            <li v-for="party in parties" :key="party.id" class="row between gap-2">
              <span>
                {{ party.name }}
                <span class="text-xs text-muted">{{ party.role }}</span>
              </span>
              <span v-if="canEdit" class="row gap-1">
                <AppButton size="sm" variant="ghost" icon="pi pi-pencil" aria-label="Edit" @click="openParty(party)" />
                <AppButton size="sm" variant="ghost" icon="pi pi-trash" aria-label="Remove" @click="removeParty(party)" />
              </span>
            </li>
          </ul>
          <p v-else class="text-sm text-muted">
            No parties recorded. Claims and correspondence are attributed to these.
          </p>
        </AppCard>

        <AppCard title="Contract" subtitle="Key dates and value">
          <dl class="overview__facts">
            <div><dt>Code</dt><dd>{{ project.code || EM_DASH }}</dd></div>
            <div><dt>Location</dt><dd>{{ project.location || EM_DASH }}</dd></div>
            <div>
              <dt>Value</dt>
              <dd>
                {{ project.contract_value ? project.currency + ' ' + formatNumber(Number(project.contract_value)) : EM_DASH }}
              </dd>
            </div>
            <div><dt>Commenced</dt><dd>{{ formatDate(project.commencement_date) }}</dd></div>
            <div><dt>Completion</dt><dd>{{ formatDate(project.completion_date) }}</dd></div>
            <div><dt>Actual completion</dt><dd>{{ formatDate(project.actual_completion_date) }}</dd></div>
          </dl>
        </AppCard>

        <AppCard title="Team" :subtitle="members.length + ' member(s)'">
          <ul v-if="members.length" class="overview__list">
            <li v-for="member in members" :key="member.id" class="row between gap-2">
              <span>{{ member.user_name || member.user_email }}</span>
              <span class="text-xs text-muted">{{ member.role }}</span>
            </li>
          </ul>
          <p v-else class="text-sm text-muted">No project members.</p>
        </AppCard>
      </div>
    </template>

    <AppDialog
      v-model="partyOpen"
      :title="partyForm.id ? 'Edit party' : 'Add a party'"
      :busy="partySaving"
    >
      <div class="stack gap-4">
        <FormField label="Name" for-id="party-name" required :error="partyErrors.name">
          <input id="party-name" v-model="partyForm.name" class="field-input" type="text" />
        </FormField>
        <FormField label="Role" for-id="party-role" :error="partyErrors.role">
          <select id="party-role" v-model="partyForm.role" class="field-input">
            <option v-for="[value, label] in PARTY_ROLES" :key="value" :value="value">{{ label }}</option>
          </select>
        </FormField>
        <FormField label="Short name" for-id="party-short" :error="partyErrors.short_name">
          <input id="party-short" v-model="partyForm.short_name" class="field-input" type="text" />
        </FormField>
        <FormField label="Contact email" for-id="party-email" :error="partyErrors.contact_email">
          <input id="party-email" v-model="partyForm.contact_email" class="field-input" type="email" />
        </FormField>
      </div>
      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="partySaving" @click="partyOpen = false" />
        <AppButton
          variant="primary"
          label="Save"
          :loading="partySaving"
          :disabled="!partyForm.name.trim()"
          @click="saveParty"
        />
      </template>
    </AppDialog>

    <AppDialog
      v-model="editionOpen"
      title="Conditions of contract"
      description="Editions differ materially: the claims procedure, notice periods and their consequences all move. Retrieval is scoped to this edition and never mixes others in."
      :busy="editionSaving"
    >
      <FormField label="Edition" for-id="overview-edition">
        <select id="overview-edition" v-model="editionValue" class="field-input">
          <option value="">Not declared</option>
          <option v-for="edition in editions" :key="edition.code" :value="edition.code">
            {{ edition.label }}
          </option>
        </select>
      </FormField>
      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="editionSaving" @click="editionOpen = false" />
        <AppButton variant="primary" label="Save" :loading="editionSaving" @click="saveEdition" />
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.overview__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--space-4);
}

.tile {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
}

.tile__icon {
  color: var(--color-text-muted);
}

.tile__value {
  font-size: var(--text-2xl);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.overview__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--text-sm);
}

.overview__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: var(--space-3);
  margin: 0;
}

.overview__facts dt {
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.overview__facts dd {
  margin: 0;
  font-size: var(--text-sm);
}
</style>
