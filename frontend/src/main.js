import { createApp } from 'vue'

import Aura from '@primevue/themes/aura'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import Tooltip from 'primevue/tooltip'

import App from './App.vue'
import router from './router'
import './assets/styles/index.css'
import 'primeicons/primeicons.css'

const app = createApp(App)

app.use(createPinia())

app.use(PrimeVue, {
  ripple: false, // enterprise density: no material ripple
  theme: {
    preset: Aura,
    options: {
      // The shell owns the theme; PrimeVue follows the same attribute.
      darkModeSelector: '[data-theme="dark"]',
      cssLayer: {
        name: 'primevue',
        // Our own utilities must be able to win without !important.
        order: 'base, primevue, utilities',
      },
    },
  },
})

app.use(ConfirmationService)
app.directive('tooltip', Tooltip)

app.use(router)

app.mount('#app')
