<script setup>
import { computed, onMounted, ref } from 'vue'

import * as adminApi from '@/api/admin'
import AppButton from '@/components/common/AppButton.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { usePolling } from '@/composables/usePolling'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatBytes, formatDateTime } from '@/utils/format'

/**
 * Which models this installation uses, and a run that works it out.
 *
 * The detection measures the machine and then runs each installed model on a
 * piece of the work this application actually does — a schema-constrained
 * extraction over contract prose — and times it. Two things that follows from
 * and which this panel must not obscure:
 *
 *   - A model that cannot hold a JSON schema is shown, and shown as
 *     unusable. Every citation rests on schema-constrained output, so it is
 *     not a matter of degree.
 *   - The embedding model is gated on vector width and changing it
 *     invalidates every vector already stored. The warning is not a footnote.
 *
 * The selection is installation-wide. It is said so on the card rather than
 * left for an administrator to discover by surprising a colleague.
 */
const ui = useUiStore()

const ROLE_KEYS = {
  llm: 'llm',
  drafting_llm: 'drafting_llm',
  embedding: 'embedding',
}

const data = ref(null)
const loading = ref(true)
const error = ref(null)

const run = ref(null)
const starting = ref(false)
const applying = ref(false)
const includePulls = ref(false)
const selection = ref({ llm: '', drafting_llm: '', embedding: '' })
const confirmOpen = ref(false)
const confirmPayload = ref(null)

const installedNames = computed(() => (data.value?.installed ?? []).map((m) => m.name))

/**
 * The models a role may actually use. An embedding model offered as the
 * answering model generates nothing, and the failure shows up far from the
 * choice that caused it — on every question, not on the dropdown.
 */
function optionsFor(role) {
  const wanted = role === 'embedding' ? 'embedding' : 'generation'
  const installed = data.value?.installed ?? []
  const matching = installed.filter((m) => (m.kind || 'generation') === wanted)
  // An older backend that does not report `kind` must not leave empty selects.
  return (matching.length ? matching : installed).map((m) => m.name)
}

const activeRun = computed(() => run.value)

const isRunning = computed(
  () => activeRun.value && !activeRun.value.is_finished
)

const capacity = computed(() => activeRun.value?.host_capacity || data.value?.profile_estimate?.capacity)

const profileLine = computed(() => {
  const finished = activeRun.value?.is_finished && activeRun.value?.detected_profile
  const source = finished ? activeRun.value : data.value?.profile_estimate
  if (!source) return null
  return {
    profile: finished ? activeRun.value.detected_profile : source.profile,
    basis: finished ? activeRun.value.profile_basis : source.basis,
    explanation: finished ? activeRun.value.profile_explanation : source.explanation,
    measured: Boolean(finished),
  }
})

/** Recommendations from the finished run, keyed by role. */
const recommendations = computed(() => {
  // A failed or cancelled run has nothing to suggest, and three cards reading
  // "nothing suitable was found" would read as a verdict on the models rather
  // than as the run having stopped.
  if (activeRun.value?.status !== 'completed') return null
  return activeRun.value.recommendations || null
})

const probes = computed(() => activeRun.value?.probes || [])

const generationProbes = computed(() =>
  probes.value.filter((p) => p.embedding_dimensions == null)
)
const embeddingProbes = computed(() =>
  probes.value.filter((p) => p.embedding_dimensions != null)
)

/** True when the operator has picked something different from what is live. */
const hasChanges = computed(() => {
  const active = data.value?.active
  if (!active) return false
  return Object.keys(ROLE_KEYS).some((role) => {
    const chosen = selection.value[role]
    return chosen !== '' && !sameModel(chosen, active[role])
  })
})

const changingEmbedding = computed(() => {
  const chosen = selection.value.embedding
  return Boolean(chosen) && !sameModel(chosen, data.value?.active?.embedding)
})

const polling = usePolling(
  () => adminApi.fetchDetection(activeRun.value.id),
  {
    interval: 2500,
    isDone: (payload) => Boolean(payload?.is_finished),
    onData: (payload) => {
      run.value = payload
      if (payload?.is_finished) onRunFinished(payload)
    },
  }
)

async function load() {
  loading.value = true
  error.value = null
  try {
    const payload = await adminApi.fetchModelAdministration()
    data.value = payload
    run.value = payload.latest_run
    prefillSelection(payload)
    if (payload.latest_run && !payload.latest_run.is_finished) polling.start()
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

/**
 * Ollama treats a bare `bge-m3` as `bge-m3:latest` and `/api/tags` reports the
 * explicit form, so a configured bare name matches no option and the select
 * renders blank — which reads as "nothing is set" when something is.
 */
function canonical(name) {
  const base = String(name || '').trim().toLowerCase()
  if (!base) return ''
  return base.includes(':') ? base : `${base}:latest`
}

function sameModel(a, b) {
  return Boolean(a) && Boolean(b) && canonical(a) === canonical(b)
}

/** The installed option that is this model, so the select can show it. */
function asOption(name) {
  if (!name) return ''
  return installedNames.value.find((option) => sameModel(option, name)) || name
}

function prefillSelection(payload) {
  const active = payload?.active || {}
  selection.value = {
    llm: asOption(active.llm),
    drafting_llm: asOption(active.drafting_llm),
    embedding: asOption(active.embedding),
  }
}

function onRunFinished(payload) {
  if (payload.status === 'completed') {
    // Preselect what the run recommends, so accepting it is one click and
    // overriding it is still a deliberate act.
    const next = { ...selection.value }
    for (const role of Object.keys(ROLE_KEYS)) {
      const recommended = payload.recommendations?.[role]?.recommended
      if (recommended) next[role] = asOption(recommended)
    }
    selection.value = next
    ui.notifySuccess('Detection finished', 'Review the suggestion before applying it.')
  } else if (payload.status === 'failed') {
    ui.notifyError(
      { message: payload.error_message || 'The detection failed.' },
      'Detection failed'
    )
  }
}

async function startDetection() {
  if (starting.value || isRunning.value) return
  starting.value = true
  try {
    run.value = await adminApi.startModelDetection({ includePulls: includePulls.value })
    polling.start()
  } catch (err) {
    ui.notifyError(err, 'Could not start the detection')
  } finally {
    starting.value = false
  }
}

async function cancel() {
  try {
    run.value = await adminApi.cancelDetection(activeRun.value.id)
    ui.notifySuccess('Cancelling', 'The run stops after the model it is measuring.')
  } catch (err) {
    ui.notifyError(err, 'Could not cancel the detection')
  }
}

function requestApply() {
  const payload = {}
  for (const role of Object.keys(ROLE_KEYS)) {
    if (selection.value[role] !== '') payload[role] = selection.value[role]
  }
  if (activeRun.value?.is_finished && activeRun.value.status === 'completed') {
    payload.from_run = activeRun.value.id
    if (activeRun.value.detected_profile) {
      payload.hardware_profile = activeRun.value.detected_profile
    }
  }
  confirmPayload.value = payload
  // An embedding change invalidates stored vectors, so it is confirmed
  // explicitly. Changing a language model is reversible in one click.
  if (changingEmbedding.value) {
    confirmOpen.value = true
    return
  }
  apply(payload)
}

async function apply(payload) {
  applying.value = true
  confirmOpen.value = false
  try {
    const result = await adminApi.applyModelSelection(payload)
    ui.notifySuccess('Models applied', 'They take effect on the next request.')
    // Warnings are not failures, and they are not footnotes either: an
    // embedding change that needs a re-ingest has to be seen.
    for (const warning of result.warnings || []) {
      ui.notify({ severity: 'warn', summary: 'Worth knowing', detail: warning, ttl: 20000 })
    }
    await load()
  } catch (err) {
    ui.notifyError(err, 'Could not apply the selection')
  } finally {
    applying.value = false
  }
}

function roleLabel(role) {
  return (data.value?.roles || []).find((r) => r.role === role)?.label || role
}

function recommendationFor(role) {
  return recommendations.value?.[role] || null
}

function verdictFor(probe, role) {
  const candidates = recommendations.value?.[role]?.candidates || []
  return candidates.find((c) => c.model === probe.name) || null
}

function seconds(value) {
  if (value == null) return EM_DASH
  if (value < 60) return `${Math.round(value)} s`
  return `${(value / 60).toFixed(1)} min`
}

function gpuLabel(fraction) {
  if (fraction == null) return EM_DASH
  if (fraction >= 0.95) return 'GPU'
  if (fraction <= 0) return 'Processor'
  return `${Math.round(fraction * 100)}% GPU`
}

onMounted(load)
</script>

<template>
  <section class="surface models">
    <header class="models__head">
      <div>
        <p class="section-title">Model selection</p>
        <p class="text-xs text-muted">
          Applies to the whole installation, not to your account — there is one
          model runtime and one vector column behind every organization.
        </p>
      </div>
      <div class="row gap-2">
        <label class="models__toggle">
          <input v-model="includePulls" type="checkbox" :disabled="isRunning" />
          <span>Download missing recommendations</span>
        </label>
        <AppButton
          v-if="isRunning"
          size="sm"
          variant="ghost"
          icon="pi pi-times"
          label="Cancel"
          @click="cancel"
        />
        <AppButton
          v-else
          size="sm"
          variant="primary"
          icon="pi pi-bolt"
          label="Detect best models"
          :loading="starting"
          :disabled="!data?.runtime_reachable"
          @click="startDetection"
        />
      </div>
    </header>

    <div v-if="loading" class="models__pad"><LoadingSkeleton variant="table" :rows="4" /></div>
    <ErrorState
      v-else-if="error"
      :error="error"
      title="Could not read the model configuration"
      @retry="load"
    />

    <template v-else>
      <p v-if="!data.runtime_reachable" class="models__notice is-warning" role="alert">
        The model runtime is not reachable, so nothing can be measured and no
        selection can be verified. Start the runtime, then run a detection.
      </p>

      <!-- What is live now -->
      <dl class="models__facts">
        <div v-for="role in Object.keys(ROLE_KEYS)" :key="role" class="models__fact">
          <dt>{{ roleLabel(role) }}</dt>
          <dd>
            <span class="text-mono">{{ data.active[role] || 'not configured' }}</span>
            <span v-if="data.configured[role]" class="models__tag">chosen here</span>
            <span v-else-if="data.active[role]" class="models__tag is-muted">from the environment</span>
          </dd>
        </div>
        <div class="models__fact">
          <dt>Hardware profile</dt>
          <dd>
            <span class="text-mono">{{ data.active.hardware_profile }}</span>
            <span
              v-if="profileLine && !profileLine.measured && !data.profile_estimate.matches_active"
              class="models__tag is-warning"
            >
              this host looks like {{ profileLine.profile }}
            </span>
          </dd>
        </div>
      </dl>

      <!-- What the machine is -->
      <div v-if="profileLine" class="models__capacity">
        <p class="models__capacity-line">
          <strong>{{ profileLine.measured ? 'Measured' : 'Estimated' }}:</strong>
          {{ profileLine.explanation }}
        </p>
        <ul v-if="capacity" class="models__capacity-list">
          <li v-if="capacity.cpu_count">{{ capacity.cpu_count }} processors</li>
          <li v-if="capacity.total_ram_gb">{{ capacity.total_ram_gb }} GB memory</li>
          <li v-for="gpu in capacity.gpus || []" :key="gpu.name">
            {{ gpu.name }} — {{ gpu.total_vram_gb }} GB
          </li>
          <li v-if="capacity.runtime_total_vram_gb">
            Runtime GPU memory {{ capacity.runtime_total_vram_gb }} GB
          </li>
        </ul>
        <p
          v-for="note in capacity?.notes || []"
          :key="note"
          class="text-xs text-muted models__note"
        >
          {{ note }}
        </p>
      </div>

      <!-- A run in progress -->
      <div v-if="isRunning" class="models__running" aria-live="polite">
        <div class="row between gap-2">
          <span class="text-sm">{{ activeRun.current_step || 'Starting…' }}</span>
          <span class="text-xs text-muted">{{ activeRun.progress_percent }}%</span>
        </div>
        <div class="models__bar" role="progressbar" :aria-valuenow="activeRun.progress_percent">
          <div class="models__bar-fill" :style="{ width: `${activeRun.progress_percent}%` }" />
        </div>
        <p class="text-xs text-muted">
          Each model runs a real schema-constrained extraction, so this takes
          minutes on a GPU and can take far longer on a processor. You can
          leave this page; the run continues.
        </p>
      </div>

      <p v-if="polling.error.value" class="models__notice is-warning" role="alert">
        Progress updates stopped: {{ polling.error.value.message }}
        <AppButton size="sm" variant="ghost" label="Reload" @click="load" />
      </p>

      <!-- A finished run -->
      <template v-if="activeRun?.is_finished">
        <div class="models__run-head">
          <StatusBadge :status="activeRun.status" size="sm" />
          <span class="text-xs text-muted">
            Run {{ formatDateTime(activeRun.created_at) }}
            <template v-if="activeRun.requested_by">by {{ activeRun.requested_by }}</template>
          </span>
          <span v-if="activeRun.runtime_version" class="text-xs text-muted">
            runtime {{ activeRun.runtime_version }}
          </span>
        </div>

        <p v-if="activeRun.error_message" class="models__notice is-danger" role="alert">
          {{ activeRun.error_message }}
        </p>
        <p
          v-for="warning in activeRun.warnings || []"
          :key="warning"
          class="models__notice is-warning"
        >
          {{ warning }}
        </p>

        <!-- Suggestions -->
        <div v-if="recommendations" class="models__suggestions">
          <article v-for="role in Object.keys(ROLE_KEYS)" :key="role" class="models__suggestion">
            <p class="models__suggestion-role">{{ roleLabel(role) }}</p>
            <template v-if="recommendationFor(role)?.recommended">
              <p class="models__suggestion-name text-mono">
                {{ recommendationFor(role).recommended }}
              </p>
              <p class="text-xs text-muted">{{ recommendationFor(role).rationale }}</p>
              <p
                v-for="warning in recommendationFor(role).warnings || []"
                :key="warning"
                class="text-xs text-warning"
              >
                {{ warning }}
              </p>
            </template>
            <p v-else class="text-xs text-muted">
              {{ recommendationFor(role)?.rationale || 'Nothing suitable was found.' }}
            </p>
          </article>
        </div>

        <!-- Measurements -->
        <div v-if="generationProbes.length" class="table-scroll">
          <table class="data-table">
            <caption class="sr-only">Language models measured in this run</caption>
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col" class="cell-numeric">Size</th>
                <th scope="col" class="cell-numeric">Tokens/s</th>
                <th scope="col" class="cell-numeric">Per answer</th>
                <th scope="col" class="cell-numeric">Per document</th>
                <th scope="col">Where it ran</th>
                <th scope="col">Schema</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="probe in generationProbes" :key="probe.name">
                <td>
                  <p class="text-mono models__cell-name">{{ probe.name }}</p>
                  <p v-if="probe.was_pulled" class="text-xs text-muted">downloaded by this run</p>
                  <p v-if="probe.error" class="text-xs text-danger">{{ probe.error }}</p>
                  <p v-else-if="probe.timed_out" class="text-xs text-danger">
                    did not finish in the time allowed
                  </p>
                </td>
                <td class="cell-numeric">
                  {{ probe.parameter_billions ? probe.parameter_billions + 'B' : EM_DASH }}
                  <span v-if="probe.quantization" class="text-xs text-muted">
                    {{ probe.quantization }}
                  </span>
                  <span v-if="probe.size_bytes" class="text-xs text-muted">
                    {{ formatBytes(probe.size_bytes) }} on disk
                  </span>
                </td>
                <td class="cell-numeric">
                  {{ probe.output_tokens_per_second ?? EM_DASH }}
                </td>
                <td class="cell-numeric">{{ seconds(probe.estimated_seconds?.llm) }}</td>
                <td class="cell-numeric">
                  {{ seconds(probe.estimated_seconds?.drafting_llm) }}
                </td>
                <td class="text-xs">{{ gpuLabel(probe.gpu_resident_fraction) }}</td>
                <td>
                  <StatusBadge
                    v-if="probe.structured_output_ok === true"
                    status="ok"
                    :label="probe.fields_read != null ? `held, read ${probe.fields_read}/5` : 'held'"
                    size="sm"
                  />
                  <StatusBadge
                    v-else-if="probe.structured_output_ok === false"
                    tone="danger"
                    label="not held"
                    size="sm"
                  />
                  <span v-else class="text-xs text-muted">not checked</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="embeddingProbes.length" class="table-scroll">
          <table class="data-table">
            <caption class="sr-only">Embedding models measured in this run</caption>
            <thead>
              <tr>
                <th scope="col">Embedding model</th>
                <th scope="col" class="cell-numeric">Dimensions</th>
                <th scope="col" class="cell-numeric">Batch latency</th>
                <th scope="col">Usable</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="probe in embeddingProbes" :key="probe.name">
                <td class="text-mono models__cell-name">{{ probe.name }}</td>
                <td class="cell-numeric">{{ probe.embedding_dimensions }}</td>
                <td class="cell-numeric">
                  {{ probe.embedding_latency_ms != null ? probe.embedding_latency_ms + ' ms' : EM_DASH }}
                </td>
                <td>
                  <StatusBadge
                    v-if="verdictFor(probe, 'embedding')?.eligible"
                    status="ok"
                    label="Right width"
                    size="sm"
                  />
                  <span v-else class="text-xs text-danger">
                    {{ verdictFor(probe, 'embedding')?.rationale || 'Wrong vector width' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <EmptyState
        v-else-if="!isRunning"
        compact
        icon="pi pi-bolt"
        title="No detection has been run"
        description="A detection measures this machine, runs each installed model on a real
          extraction, and says which one each job should use."
      />

      <!-- Choose and apply -->
      <div class="models__apply">
        <div class="models__choices">
          <label v-for="role in Object.keys(ROLE_KEYS)" :key="role" class="models__choice">
            <span class="models__choice-label">{{ roleLabel(role) }}</span>
            <select
              v-model="selection[role]"
              class="field-input"
              :disabled="applying || isRunning"
            >
              <option value="">
                {{ role === 'drafting_llm' ? 'Same as answering' : 'Leave unchanged' }}
              </option>
              <option v-for="name in optionsFor(role)" :key="name" :value="name">
                {{ name }}
              </option>
            </select>
          </label>
        </div>
        <div class="row gap-2">
          <AppButton
            variant="primary"
            size="sm"
            icon="pi pi-check"
            label="Apply to this installation"
            :loading="applying"
            :disabled="!hasChanges || isRunning"
            @click="requestApply"
          />
        </div>
        <p v-if="changingEmbedding" class="models__notice is-warning">
          Changing the embedding model invalidates every vector already stored.
          Search stays wrong until each document has been reprocessed.
        </p>
      </div>
    </template>

    <ConfirmDialog
      v-model="confirmOpen"
      title="Change the embedding model?"
      :message="`Vectors already stored were produced by a different model. Until every
        document has been reprocessed, search will compare vectors that are not
        comparable — which reads as poor results rather than as an error.`"
      confirm-label="Change it"
      variant="danger"
      @confirm="apply(confirmPayload)"
    />
  </section>
</template>

<style scoped>
.models {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
}

.models__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.models__pad {
  padding: var(--space-2) 0;
}

.models__toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.models__notice {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-sunken);
}

.models__notice.is-warning {
  color: var(--color-text);
  border-color: var(--color-warning, var(--color-border-strong));
}

.models__notice.is-danger {
  color: var(--color-text);
  border-color: var(--color-danger, var(--color-border-strong));
}

.models__facts {
  display: grid;
  gap: var(--space-2);
  margin: 0;
}

.models__fact {
  display: grid;
  grid-template-columns: minmax(180px, 240px) 1fr;
  gap: var(--space-3);
  align-items: baseline;
}

.models__fact dt {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.models__fact dd {
  margin: 0;
  font-size: var(--text-sm);
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.models__tag {
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
}

.models__tag.is-muted {
  color: var(--color-text-muted);
}

.models__tag.is-warning {
  border-color: var(--color-warning, var(--color-border-strong));
}

.models__capacity {
  padding: var(--space-3);
  background: var(--color-surface-sunken);
  border-radius: var(--radius-md);
}

.models__capacity-line {
  font-size: var(--text-xs);
}

.models__capacity-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-2);
  padding: 0;
  list-style: none;
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.models__note {
  margin-top: var(--space-2);
}

.models__running {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.models__bar {
  height: 4px;
  background: var(--color-surface-sunken);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.models__bar-fill {
  height: 100%;
  background: var(--color-accent);
  transition: width var(--duration-normal, 200ms) var(--ease-standard);
}

.models__run-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.models__suggestions {
  display: grid;
  gap: var(--space-3);
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.models__suggestion {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.models__suggestion-role {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.models__suggestion-name {
  margin-top: 2px;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.models__cell-name {
  /* Break between the repository and the tag, not mid-word: `qwen2.5:3b-instr
     uct` is unreadable and these names are what the operator matches against
     what Ollama reports. */
  overflow-wrap: anywhere;
  word-break: normal;
  min-width: 11ch;
}

.models__apply {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-subtle);
}

.models__choices {
  display: grid;
  gap: var(--space-3);
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.models__choice {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.models__choice-label {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
</style>
