<script setup>
import DocumentsPanel from '@/components/domain/DocumentsPanel.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Documents"
      description="The document corpus across your projects. A document is searchable once it finishes processing; until then it is not part of any answer."
    />
    <DocumentsPanel show-project />
  </div>
</template>
