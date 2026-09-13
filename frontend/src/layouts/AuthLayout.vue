<script setup>
import ThemeToggle from '@/components/layout/ThemeToggle.vue'
import ToastHost from '@/components/layout/ToastHost.vue'
</script>

<template>
  <div class="auth">
    <div class="auth__theme">
      <ThemeToggle />
    </div>

    <div class="auth__panel">
      <div class="auth__brand">
        <div class="auth__mark" aria-hidden="true">CQ</div>
        <div>
          <p class="auth__product">ClaimIQ</p>
          <p class="auth__edition">Enterprise</p>
        </div>
      </div>

      <RouterView />
    </div>

    <footer class="auth__footer">
      <p>Contract &amp; Claims Intelligence Platform</p>
      <p class="auth__offline">
        <i class="pi pi-lock" aria-hidden="true" />
        Operates fully offline. No data leaves this installation.
      </p>
    </footer>

    <ToastHost />
  </div>
</template>

<style scoped>
.auth {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-8);
  height: 100%;
  padding: var(--space-6);
  overflow-y: auto;
  background:
    radial-gradient(1200px 600px at 50% -10%, var(--color-accent-subtle), transparent 70%),
    var(--color-canvas);
}

.auth__theme {
  position: absolute;
  top: var(--space-4);
  right: var(--space-4);
}

.auth__panel {
  width: 100%;
  max-width: 380px;
}

.auth__brand {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-6);
}

.auth__mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  font-size: var(--text-xs);
  font-weight: var(--weight-bold);
  color: var(--color-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-lg);
}

.auth__product {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  line-height: 1.15;
}

.auth__edition {
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.auth__footer {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  text-align: center;
}

.auth__offline {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-2xs);
}
</style>
