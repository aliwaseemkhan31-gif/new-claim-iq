<script setup>
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppTopBar from '@/components/layout/AppTopBar.vue'
import ToastHost from '@/components/layout/ToastHost.vue'
</script>

<template>
  <div class="shell">
    <a class="skip-link" href="#main-content">Skip to content</a>

    <AppSidebar />

    <div class="shell__main">
      <AppTopBar />

      <main id="main-content" class="shell__content scroll-y" tabindex="-1">
        <RouterView v-slot="{ Component, route }">
          <component :is="Component" :key="route.path" />
        </RouterView>
      </main>
    </div>

    <ToastHost />
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  background: var(--color-canvas);
}

.shell__main {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}

.shell__content {
  flex: 1 1 auto;
  min-height: 0;
}

.shell__content:focus {
  outline: none;
}
</style>
