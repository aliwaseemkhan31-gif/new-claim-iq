<script setup>
import FeaturePending from '@/components/common/FeaturePending.vue'

defineProps({
  project: { type: Object, required: true },
})
</script>

<template>
  <div class="page">
    <div class="surface">
      <FeaturePending
        icon="pi pi-briefcase"
        title="Claims workspace not available yet"
        description="Claims raised under this contract, each with its clause basis, quantum, evidence chain and assessment history. Requires the claims API."
        endpoint="/api/v1/claims/?project=<id>"
      />
    </div>
  </div>
</template>
