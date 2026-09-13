<script setup>
import FeaturePending from '@/components/common/FeaturePending.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Correspondence"
      description="Letters, notices and instructions across your projects."
    />

    <div class="surface">
      <FeaturePending
        icon="pi pi-envelope"
        title="Correspondence not available yet"
        description="Threaded contractual correspondence, linked to the clauses and claims each item bears on. Requires the correspondence API."
        endpoint="/api/v1/correspondence/"
      />
    </div>
  </div>
</template>
