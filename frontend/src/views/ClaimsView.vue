<script setup>
import { computed, ref } from 'vue'

import AppButton from '@/components/common/AppButton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import ClaimBundleDialog from '@/components/domain/ClaimBundleDialog.vue'
import ClaimsPanel from '@/components/domain/ClaimsPanel.vue'
import ProjectPicker from '@/components/domain/ProjectPicker.vue'
import { useProjectSelection } from '@/composables/useProjectSelection'
import { useAuthStore } from '@/stores/auth'

/**
 * Every claim on the projects the user can see.
 *
 * Choosing a project narrows the list to it and is what makes uploading
 * possible: a claim belongs to one project's contract.
 */
const auth = useAuthStore()
// All projects by default; the register is the one global list of claims.
const { projectId } = useProjectSelection('project', { carryAmbient: false })

const bundleOpen = ref(false)
const panelKey = ref(0)

const canUpload = computed(
  () => Boolean(projectId.value) && auth.canInProject(projectId.value, 'claim.create'),
)

async function openUpload() {
  if (!projectId.value) return
  await auth.loadProjectPermissions(projectId.value)
  bundleOpen.value = true
}
</script>

<template>
  <div class="page">
    <PageHeader
      title="Claims"
      description="Every claim on the projects you can see. Upload a claim submission — the claim letter and its supporting documents — and it is split and filed for you."
    >
      <template #actions>
        <ProjectPicker v-model="projectId" />
        <AppButton
          variant="primary"
          icon="pi pi-upload"
          label="Upload claim"
          :disabled="!projectId"
          :title="projectId ? 'Upload the claim letter with its supporting documents' : 'Choose a project first'"
          @click="openUpload"
        />
      </template>
    </PageHeader>
    <p v-if="!projectId" class="text-xs text-muted">Choose a project to upload a claim into it.</p>

    <ClaimsPanel
      :key="`${projectId || 'all'}-${panelKey}`"
      :project-id="projectId"
      :show-project="!projectId"
      :allow-upload="false"
    />

    <ClaimBundleDialog
      v-if="projectId && canUpload"
      v-model="bundleOpen"
      :project-id="projectId"
      @applied="panelKey++"
    />
  </div>
</template>
