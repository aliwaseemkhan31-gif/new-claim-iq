<script setup>
import EvidencePanel from '@/components/domain/EvidencePanel.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Evidence"
      description="Records linked to claims, with what each is said to establish and whether a person has reviewed it."
    />
    <EvidencePanel show-claim />
  </div>
</template>
