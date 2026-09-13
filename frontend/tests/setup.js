import { config } from '@vue/test-utils'
import { vi } from 'vitest'

// jsdom implements neither matchMedia nor the ResizeObserver PrimeVue touches.
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

if (!global.ResizeObserver) {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// RouterLink is stubbed by default; tests that exercise routing install a real
// router explicitly.
config.global.stubs = {
  RouterLink: { template: '<a><slot /></a>' },
  Teleport: true,
}
