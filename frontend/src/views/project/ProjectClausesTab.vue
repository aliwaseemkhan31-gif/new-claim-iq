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
        icon="pi pi-book"
        title="Clause map not available yet"
        description="The clauses of this contract, matched against the FIDIC edition in use, with particular conditions and amendments shown against the standard text. Requires the clauses API."
        endpoint="/api/v1/clauses/?project=<id>"
      />
    </div>
  </div>
</template>
