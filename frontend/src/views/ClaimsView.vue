<script setup>
import FeaturePending from '@/components/common/FeaturePending.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Claims"
      description="Claims across every project you can access."
    />

    <div class="surface">
      <FeaturePending
        icon="pi pi-briefcase"
        title="Claims register not available yet"
        description="The cross-project claims register shows each claim with its contractual basis, quantum, status and assessment. Requires the claims API."
        endpoint="/api/v1/claims/"
      />
    </div>
  </div>
</template>
