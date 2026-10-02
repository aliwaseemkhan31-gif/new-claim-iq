import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AppDialog from '../AppDialog.vue'

const FORM = `
  <input id="first" />
  <input id="second" />
  <button id="last">Save</button>
`

function open(props = {}) {
  return mount(AppDialog, {
    attachTo: document.body,
    props: { modelValue: true, title: 'Add a user', ...props },
    slots: { default: FORM },
  })
}

function tab(wrapper, { shift = false } = {}) {
  return wrapper.find('.dialog__overlay').trigger('keydown', { key: 'Tab', shiftKey: shift })
}

describe('AppDialog', () => {
  it('opens focus on the first field, not the close button', async () => {
    const wrapper = open()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(document.activeElement.id).toBe('first')
    wrapper.unmount()
  })

  it('keeps Tab inside the dialog at the last control', async () => {
    const wrapper = open()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    document.getElementById('last').focus()
    await tab(wrapper)
    // Without a trap this walked out into the page behind the overlay.
    expect(wrapper.element.contains(document.activeElement)).toBe(true)
    wrapper.unmount()
  })

  it('wraps Shift+Tab from the first control to the last', async () => {
    const wrapper = open()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    const focusables = wrapper.findAll('button, input')
    const lastId = focusables[focusables.length - 1].element.id
    focusables[0].element.focus()
    await tab(wrapper, { shift: true })
    expect(document.activeElement.id).toBe(lastId)
    wrapper.unmount()
  })

  it('closes on Escape from anywhere the dialog renders', async () => {
    const wrapper = open()
    await wrapper.find('#second').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted()['update:modelValue']?.at(-1)).toEqual([false])
    wrapper.unmount()
  })

  it('refuses Escape mid-submit, so the outcome is never left in doubt', async () => {
    const wrapper = open({ busy: true })
    await wrapper.find('#second').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted()['update:modelValue']).toBeUndefined()
    wrapper.unmount()
  })

  it('locks page scroll while open and releases it on close', async () => {
    const wrapper = open()
    await wrapper.vm.$nextTick()
    expect(document.body.style.overflow).toBe('hidden')
    await wrapper.setProps({ modelValue: false })
    expect(document.body.style.overflow).not.toBe('hidden')
    wrapper.unmount()
  })

  it('releases page scroll if it is unmounted while still open', async () => {
    const wrapper = open()
    await wrapper.vm.$nextTick()
    wrapper.unmount()
    expect(document.body.style.overflow).not.toBe('hidden')
  })

  it('returns focus to whatever opened it', async () => {
    const trigger = document.createElement('button')
    document.body.appendChild(trigger)
    trigger.focus()

    const wrapper = open()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(document.activeElement).not.toBe(trigger)

    await wrapper.setProps({ modelValue: false })
    expect(document.activeElement).toBe(trigger)

    wrapper.unmount()
    trigger.remove()
  })
})
