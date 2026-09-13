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
        icon="pi pi-envelope"
        title="Correspondence not available yet"
        description="Letters, notices and instructions exchanged on this contract, threaded and linked to the clauses and claims they bear on. Requires the correspondence API."
        endpoint="/api/v1/correspondence/?project=<id>"
      />
    </div>
  </div>
</template>
