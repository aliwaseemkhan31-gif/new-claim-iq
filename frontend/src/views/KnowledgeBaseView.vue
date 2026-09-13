<script setup>
import FeaturePending from '@/components/common/FeaturePending.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Knowledge Base"
      description="The FIDIC editions ingested into this installation."
    />

    <div class="surface">
      <FeaturePending
        icon="pi pi-book"
        title="Knowledge base browser not available yet"
        description="Browse the ingested FIDIC editions clause by clause. Retrieval is edition-scoped by design — a 1987 Red Book clause is not an answer to a 2017 contract question. Requires the knowledge API."
        endpoint="/api/v1/knowledge/bases/"
      />
    </div>
  </div>
</template>
